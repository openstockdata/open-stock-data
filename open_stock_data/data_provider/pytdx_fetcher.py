"""
PytdxFetcher - 通达信数据源 (Priority 2.5)

数据来源：通达信行情服务器（pytdx 库）
特点：免费、无需 Token、直连行情服务器、支持实时行情和历史K线
"""

import logging
import os
from contextlib import contextmanager
from datetime import datetime as dt
from typing import Optional, List, Tuple

import pandas as pd
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from .base import BaseFetcher, DataFetchError, NETWORK_EXCEPTIONS
from .types import (
    UnifiedRealtimeQuote,
    RealtimeSource,
    safe_float,
)
from .columns import STANDARD_COLUMNS
from .stock_code import is_hk_code, is_us_code, is_etf_code, is_a_stock_code

_LOGGER = logging.getLogger(__name__)

# pytdx 连接失败视为网络异常
_PYTDX_NETWORK_EXCEPTIONS = (*NETWORK_EXCEPTIONS, OSError)

# 通达信默认行情服务器列表
DEFAULT_HOSTS = [
    ("119.147.212.81", 7709),
    ("112.74.214.43", 7727),
    ("221.231.141.60", 7709),
    ("101.227.73.20", 7709),
    ("101.227.77.254", 7709),
    ("14.215.128.18", 7709),
    ("59.173.18.140", 7709),
    ("180.153.39.51", 7709),
]


def _parse_hosts_from_env() -> Optional[List[Tuple[str, int]]]:
    """从环境变量 PYTDX_SERVERS 或 PYTDX_HOST+PYTDX_PORT 构建服务器列表"""
    servers = os.getenv("PYTDX_SERVERS", "").strip()
    if servers:
        result = []
        for part in servers.split(","):
            part = part.strip()
            if ":" in part:
                host, port_str = part.rsplit(":", 1)
                try:
                    result.append((host.strip(), int(port_str.strip())))
                except ValueError:
                    pass
        if result:
            return result

    host = os.getenv("PYTDX_HOST", "").strip()
    port_str = os.getenv("PYTDX_PORT", "").strip()
    if host and port_str:
        try:
            return [(host, int(port_str))]
        except ValueError:
            pass

    return None


class PytdxFetcher(BaseFetcher):
    """
    通达信数据源

    优先级：2（Akshare 之后，Baostock 之前）
    支持：A股日K线、实时行情
    不支持：港股、美股、ETF 实时行情
    """

    name = "PytdxFetcher"
    priority = 2
    backend_group = "pytdx"

    SECURITY_LIST_PAGE_SIZE = 1000

    def __init__(self, hosts: Optional[List[Tuple[str, int]]] = None):
        super().__init__()
        env_hosts = _parse_hosts_from_env()
        self._hosts = hosts or env_hosts or DEFAULT_HOSTS
        self._current_host_idx = 0
        self._stock_name_cache: dict = {}
        self._security_list_loaded_markets: set[int] = set()

        try:
            from pytdx.hq import TdxHq_API
            self._tdx_api_class = TdxHq_API
        except ImportError:
            _LOGGER.warning("pytdx 未安装，PytdxFetcher 不可用")
            self._tdx_api_class = None
            self._available = False

    @contextmanager
    def _session(self):
        """通达信连接上下文管理器，自动选择服务器"""
        if self._tdx_api_class is None:
            raise DataFetchError("pytdx 未安装")

        api = self._tdx_api_class()
        connected = False

        try:
            for i in range(len(self._hosts)):
                idx = (self._current_host_idx + i) % len(self._hosts)
                host, port = self._hosts[idx]
                try:
                    if api.connect(host, port, time_out=5):
                        connected = True
                        self._current_host_idx = idx
                        _LOGGER.debug(f"Pytdx 连接: {host}:{port}")
                        break
                except Exception:
                    continue

            if not connected:
                raise ConnectionError("Pytdx 无法连接任何服务器")

            yield api
        finally:
            try:
                api.disconnect()
            except Exception:
                pass

    @staticmethod
    def _get_market_code(stock_code: str) -> Tuple[int, str]:
        """根据代码判断 pytdx 市场 (0=深圳, 1=上海)"""
        code = stock_code.strip().split(".")[0]
        for suffix in (".SH", ".SZ", ".sh", ".sz"):
            code = code.replace(suffix, "")
        # 上海: 60xxxx, 68xxxx, 11xxxx(可转债)
        if code.startswith(("60", "68", "11")):
            return 1, code
        return 0, code

    def _load_security_list_cache(self, market: int) -> None:
        """使用 get_security_list 拉取证券列表并缓存 code->name。"""
        if market in self._security_list_loaded_markets:
            return

        try:
            with self._session() as api:
                start = 0
                while True:
                    rows = api.get_security_list(market, start)
                    if not rows:
                        break

                    for row in rows:
                        code = str(row.get("code", "")).strip()
                        name = str(row.get("name", "")).strip()
                        if code and name and code not in self._stock_name_cache:
                            self._stock_name_cache[code] = name

                    if len(rows) < self.SECURITY_LIST_PAGE_SIZE:
                        break
                    start += len(rows)
        except Exception as e:
            _LOGGER.debug(f"Pytdx get_security_list 拉取失败(market={market}): {e}")
        finally:
            self._security_list_loaded_markets.add(market)

    @retry(
        stop=stop_after_attempt(2),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type(_PYTDX_NETWORK_EXCEPTIONS),
        reraise=True,
    )
    def _fetch_raw_data(self, stock_code: str, start_date: str, end_date: str) -> Optional[pd.DataFrame]:
        if is_us_code(stock_code) or is_hk_code(stock_code):
            raise DataFetchError(f"PytdxFetcher 不支持 {stock_code}")

        market, code = self._get_market_code(stock_code)

        start_dt = dt.strptime(start_date, "%Y%m%d")
        end_dt = dt.strptime(end_date, "%Y%m%d")
        days = (end_dt - start_dt).days
        count = min(max(days * 5 // 7 + 10, 30), 800)

        with self._session() as api:
            try:
                # category=9 日线
                data = api.get_security_bars(9, market, code, 0, count)
                if not data:
                    return None
                df = api.to_df(data)
                df["datetime"] = pd.to_datetime(df["datetime"])
                df = df[(df["datetime"] >= start_date) & (df["datetime"] <= end_date)]
                return df if not df.empty else None
            except _PYTDX_NETWORK_EXCEPTIONS:
                raise
            except Exception as e:
                raise DataFetchError(f"Pytdx 获取数据失败: {e}") from e

    def _fetch_raw_daily_data(self, stock_code: str, start_date: str, end_date: str) -> Optional[pd.DataFrame]:
        """pytdx 本身就是未复权数据"""
        return self._fetch_raw_data(stock_code, start_date, end_date)

    def _normalize_data(self, df: pd.DataFrame, stock_code: str) -> pd.DataFrame:
        df = df.copy()
        df = df.rename(columns={"datetime": "date", "vol": "volume"})

        if "pct_chg" not in df.columns and "close" in df.columns:
            df["pct_chg"] = df["close"].pct_change() * 100
            df["pct_chg"] = df["pct_chg"].fillna(0).round(2)

        keep_cols = [c for c in STANDARD_COLUMNS if c in df.columns]
        return df[keep_cols]

    @retry(
        stop=stop_after_attempt(2),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type(_PYTDX_NETWORK_EXCEPTIONS),
        reraise=True,
    )
    def get_realtime_quote(self, stock_code: str) -> Optional[UnifiedRealtimeQuote]:
        if not self._available:
            return None
        if is_us_code(stock_code) or is_hk_code(stock_code):
            return None
        if is_etf_code(stock_code):
            return None

        market, code = self._get_market_code(stock_code)

        try:
            with self._session() as api:
                data = api.get_security_quotes([(market, code)])
                if not data:
                    return None

                q = data[0]
                price = safe_float(q.get("price"))
                pre_close = safe_float(q.get("last_close"))
                change_pct = None
                change_amount = None
                if price and pre_close and pre_close > 0:
                    change_amount = round(price - pre_close, 4)
                    change_pct = round(change_amount / pre_close * 100, 2)

                name = q.get("name", "")
                if not name:
                    name = self.get_stock_name(stock_code) or ""

                return UnifiedRealtimeQuote(
                    code=stock_code,
                    name=name,
                    source=RealtimeSource.FALLBACK,
                    price=price,
                    change_pct=change_pct,
                    change_amount=change_amount,
                    volume=safe_float(q.get("vol")),
                    amount=safe_float(q.get("amount")),
                    open=safe_float(q.get("open")),
                    high=safe_float(q.get("high")),
                    low=safe_float(q.get("low")),
                    pre_close=pre_close,
                )
        except _PYTDX_NETWORK_EXCEPTIONS:
            raise
        except Exception as e:
            _LOGGER.warning(f"Pytdx 获取实时行情失败 {stock_code}: {e}")
            return None

    def get_stock_name(self, stock_code: str) -> Optional[str]:
        """获取股票名称"""
        market, code = self._get_market_code(stock_code)

        if stock_code in self._stock_name_cache:
            return self._stock_name_cache[stock_code]
        if code in self._stock_name_cache:
            return self._stock_name_cache[code]

        try:
            with self._session() as api:
                finance_info = api.get_finance_info(market, code)
                if finance_info and "name" in finance_info:
                    name = str(finance_info["name"]).strip()
                    if name:
                        self._stock_name_cache[stock_code] = name
                        self._stock_name_cache[code] = name
                        return name
        except Exception as e:
            _LOGGER.debug(f"Pytdx get_finance_info 获取股票名称失败 {stock_code}: {e}")

        self._load_security_list_cache(market)
        cached_name = self._stock_name_cache.get(code)
        if cached_name:
            self._stock_name_cache[stock_code] = cached_name
            return cached_name

        return None
