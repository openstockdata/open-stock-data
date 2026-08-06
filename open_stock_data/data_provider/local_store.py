"""本地长期数据存储（SQLite）。

与 route 层的 diskcache 短 TTL 热缓存不同，这里存放**长期不过期**的历史事实数据：

- ``daily_bars``   前复权日K线（按 (symbol, date) 主键增量 upsert）
- ``fact_frames``  低频历史事实快照（分红/股东/板块/财报等，按 (kind, key) 整帧存放）
- ``sync_meta``    每个 (kind, key) 的最后同步时间与最后校验日

复权防护：日K线为前复权口径，除权后**全历史**会变化。``upsert_daily`` 对新旧数据的
重叠日期比对收盘价，一旦发现偏差即判定发生除权事件——清空该 symbol 并返回
``"conflict"``，由调用方对**该 symbol 单独**全量重拉（而非全库失效）。
重叠比对通过则刷新 ``sync_meta.last_verified``（"最后校验日"）。
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import time
from datetime import datetime, timedelta
from io import StringIO
from typing import Any, Optional

import pandas as pd

from ..cache import CacheStore

_LOGGER = logging.getLogger(__name__)

# 收盘价重叠比对的相对容差（超过即判定除权/数据修正）
_CLOSE_TOLERANCE = 1e-3

_DAILY_COLUMNS = ["date", "open", "high", "low", "close", "volume", "amount", "pct_chg", "turnover_rate"]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS daily_bars (
    symbol TEXT NOT NULL,
    date TEXT NOT NULL,
    open REAL, high REAL, low REAL, close REAL,
    volume REAL, amount REAL, pct_chg REAL, turnover_rate REAL,
    PRIMARY KEY (symbol, date)
);
CREATE TABLE IF NOT EXISTS fact_frames (
    kind TEXT NOT NULL,
    key TEXT NOT NULL,
    payload TEXT NOT NULL,
    source TEXT,
    fetched_at REAL NOT NULL,
    PRIMARY KEY (kind, key)
);
CREATE TABLE IF NOT EXISTS sync_meta (
    kind TEXT NOT NULL,
    key TEXT NOT NULL,
    last_synced REAL,
    last_verified REAL,
    PRIMARY KEY (kind, key)
);
"""


def last_expected_trade_date(now: Optional[datetime] = None) -> str:
    """最近一个"预期已有收盘数据"的工作日（YYYY-MM-DD）。

    简化规则：周一~周五视为交易日；当天 16:00 前视为尚未收盘，用前一工作日。
    节假日会被误判为交易日——后果只是多发起一次小的增量网络请求（拉不到新数据），
    宁可多拉一次也不给出过期数据。
    """
    now = now or datetime.now()
    day = now.date()
    if now.hour < 16:
        day = day - timedelta(days=1)
    while day.weekday() >= 5:  # 5=Sat 6=Sun
        day = day - timedelta(days=1)
    return day.strftime("%Y-%m-%d")


class LocalStore:
    """SQLite 长期存储。线程安全（单连接 + RLock，WAL）。"""

    def __init__(self, path: Optional[str] = None):
        if path is None:
            path = os.getenv("LOCAL_STORE_PATH", "").strip() or str(
                CacheStore.get_cache_dir() / "local_store.db"
            )
        if path != ":memory:":
            os.makedirs(os.path.dirname(path), exist_ok=True)
        self._path = path
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    # ==================== 日K线 ====================

    def daily_coverage(self, symbol: str) -> Optional[tuple[str, str, int]]:
        """返回 (min_date, max_date, rows)；无数据返回 None。"""
        with self._lock:
            row = self._conn.execute(
                "SELECT MIN(date), MAX(date), COUNT(*) FROM daily_bars WHERE symbol=?",
                (symbol,),
            ).fetchone()
        if not row or row[2] == 0:
            return None
        return row[0], row[1], row[2]

    def load_daily(self, symbol: str, days: int) -> Optional[pd.DataFrame]:
        """读取最近 days 行（英文列，按日期升序）；无数据返回 None。"""
        with self._lock:
            cur = self._conn.execute(
                f"SELECT {', '.join(_DAILY_COLUMNS)} FROM daily_bars"
                " WHERE symbol=? ORDER BY date DESC LIMIT ?",
                (symbol, int(days)),
            )
            rows = cur.fetchall()
        if not rows:
            return None
        df = pd.DataFrame(rows, columns=_DAILY_COLUMNS)
        df = df.iloc[::-1].reset_index(drop=True)
        # 丢弃从未写入过数据的全空列（date/close 因 upsert 前置校验必有值）
        return df.dropna(axis=1, how="all")

    def upsert_daily(self, symbol: str, df: pd.DataFrame, source: str = "") -> str:
        """增量写入日K线（英文列，需含 date/close）。

        返回:
            "ok"       正常合并（重叠行校验通过或无重叠）
            "conflict" 重叠行收盘价偏差超容差（除权/修正）——已清空该 symbol，
                       调用方应对该 symbol 全量重拉后再次 upsert
        """
        if df is None or df.empty or "date" not in df.columns or "close" not in df.columns:
            return "ok"
        work = df.copy()
        work["date"] = work["date"].astype(str).str.slice(0, 10)
        work = work.dropna(subset=["date", "close"]).drop_duplicates("date")

        with self._lock:
            dates = tuple(work["date"].tolist())
            placeholders = ",".join("?" for _ in dates)
            existing = {}
            if dates:
                for d, c in self._conn.execute(
                    f"SELECT date, close FROM daily_bars WHERE symbol=? AND date IN ({placeholders})",
                    (symbol, *dates),
                ).fetchall():
                    existing[d] = c

            # 复权防护：重叠日期收盘价比对
            for _, row in work.iterrows():
                old = existing.get(row["date"])
                if old is None:
                    continue
                new = float(row["close"])
                if old and abs(new - old) / abs(old) > _CLOSE_TOLERANCE:
                    _LOGGER.info(
                        "[local_store] %s 检测到除权/数据修正 (date=%s local=%.4f fetched=%.4f)，清空该 symbol 待全量重拉",
                        symbol, row["date"], old, new,
                    )
                    self._conn.execute("DELETE FROM daily_bars WHERE symbol=?", (symbol,))
                    self._conn.execute(
                        "INSERT OR REPLACE INTO sync_meta(kind,key,last_synced,last_verified) VALUES('daily',?,?,NULL)",
                        (symbol, time.time()),
                    )
                    self._conn.commit()
                    return "conflict"

            records = []
            for _, row in work.iterrows():
                records.append(tuple(
                    [symbol, row["date"]]
                    + [None if pd.isna(row.get(col)) else float(row.get(col)) for col in _DAILY_COLUMNS[1:]]
                ))
            self._conn.executemany(
                f"INSERT OR REPLACE INTO daily_bars(symbol, {', '.join(_DAILY_COLUMNS)})"
                f" VALUES (?,?,?,?,?,?,?,?,?,?)",
                records,
            )
            now = time.time()
            self._conn.execute(
                "INSERT OR REPLACE INTO sync_meta(kind,key,last_synced,last_verified) VALUES('daily',?,?,?)",
                (symbol, now, now),
            )
            self._conn.commit()
        return "ok"

    def clear_daily(self, symbol: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM daily_bars WHERE symbol=?", (symbol,))
            self._conn.commit()

    # ==================== 低频历史事实（整帧快照） ====================

    def save_fact(self, kind: str, key: str, data: Any, source: str = "") -> None:
        """保存 DataFrame 或 dict 快照（整帧替换，永不过期）。"""
        if isinstance(data, pd.DataFrame):
            payload = json.dumps({
                "format": "frame",
                "frame": json.loads(data.to_json(orient="split", force_ascii=False, date_format="iso")),
            }, ensure_ascii=False)
        elif isinstance(data, dict):
            payload = json.dumps({"format": "dict", "data": data}, ensure_ascii=False, default=str)
        else:
            return  # 其它类型（如 dataclass）暂不落库
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO fact_frames(kind,key,payload,source,fetched_at) VALUES(?,?,?,?,?)",
                (kind, key, payload, source, time.time()),
            )
            self._conn.commit()

    def load_fact(self, kind: str, key: str, max_age_seconds: Optional[float] = None):
        """读取快照。返回 (data, source, fetched_at)；缺失或超过新鲜度窗口返回 None。"""
        with self._lock:
            row = self._conn.execute(
                "SELECT payload, source, fetched_at FROM fact_frames WHERE kind=? AND key=?",
                (kind, key),
            ).fetchone()
        if row is None:
            return None
        payload, source, fetched_at = row
        if max_age_seconds is not None and time.time() - fetched_at > max_age_seconds:
            return None
        obj = json.loads(payload)
        if obj.get("format") == "frame":
            data = pd.read_json(StringIO(json.dumps(obj["frame"])), orient="split")
        else:
            data = obj.get("data")
        return data, source, fetched_at

    def close(self) -> None:
        with self._lock:
            self._conn.close()


_default_store: Optional[LocalStore] = None
_default_store_lock = threading.Lock()


def get_local_store() -> LocalStore:
    global _default_store
    if _default_store is None:
        with _default_store_lock:
            if _default_store is None:
                _default_store = LocalStore()
    return _default_store


# ==================== 本地优先 provider ====================

# 低频事实路由的新鲜度窗口（秒）：命中且未超窗才用本地，否则回退网络并回写。
_FACT_FRESHNESS = {
    "dividend_history": 7 * 86400,
    "fund_holder": 7 * 86400,
    "top10_holders": 7 * 86400,
    "belong_board": 30 * 86400,
    "board_cons": 30 * 86400,
    "industry_pe": 180 * 86400,   # 指定交易日的行业PE为不可变历史
    "us_overview": 7 * 86400,
    "us_balance_sheet": 7 * 86400,
    "us_income_statement": 7 * 86400,
    "us_cash_flow": 7 * 86400,
    "us_earnings": 7 * 86400,
    "us_insider": 7 * 86400,
}


def _q(quarterly: bool) -> str:
    return "q" if quarterly else "a"


class LocalStoreFetcher:
    """本地长期存储读取器：排在各事实路由 providers 首位，命中即免网络。

    只读；任何异常都返回 None（视为未命中，回退网络），绝不因本地故障拖垮取数。
    不是 BaseFetcher 子类——只需 RouteExecutor 用到的鸭子接口（name/priority/
    backend_group/is_available/get_backend_failure_scope + 各事实方法）。
    """

    name = "LocalStoreFetcher"
    priority = -1
    backend_group = ""

    def __init__(self, store: Optional[LocalStore] = None):
        self._store = store or get_local_store()

    @property
    def is_available(self) -> bool:
        return True

    def get_backend_failure_scope(self, method_name: str, *args, **kwargs) -> Optional[str]:
        return None

    def _read(self, kind: str, key: str):
        try:
            hit = self._store.load_fact(kind, key, max_age_seconds=_FACT_FRESHNESS.get(kind))
        except Exception as exc:  # noqa: BLE001
            _LOGGER.debug("[local_store] 读取 %s/%s 失败: %s", kind, key, exc)
            return None
        return hit[0] if hit else None

    # 键公式与 client 的 cache_key / persist 写入键保持一致
    def get_dividend_history(self, symbol: str):
        return self._read("dividend_history", symbol)

    def get_fund_holder(self, symbol: str, date: str = ""):
        return self._read("fund_holder", f"{symbol}:{date}")

    def get_top10_holders(self, symbol: str, holder_type: str = "main"):
        return self._read("top10_holders", f"{symbol}:{holder_type}")

    def get_belong_board(self, symbol: str):
        return self._read("belong_board", symbol)

    def get_board_cons(self, board_name: str, board_type: str = "industry"):
        return self._read("board_cons", f"{board_name}:{board_type}")

    def get_industry_pe(self, date: str = ""):
        return self._read("industry_pe", date or "latest")

    def get_company_overview(self, symbol: str):
        return self._read("us_overview", symbol.upper())

    def get_balance_sheet(self, symbol: str, quarterly: bool = True):
        return self._read("us_balance_sheet", f"{symbol.upper()}:{_q(quarterly)}")

    def get_income_statement(self, symbol: str, quarterly: bool = True):
        return self._read("us_income_statement", f"{symbol.upper()}:{_q(quarterly)}")

    def get_cash_flow(self, symbol: str, quarterly: bool = True):
        return self._read("us_cash_flow", f"{symbol.upper()}:{_q(quarterly)}")

    def get_earnings(self, symbol: str):
        return self._read("us_earnings", symbol.upper())

    def get_insider_transactions(self, symbol: str):
        return self._read("us_insider", symbol.upper())

