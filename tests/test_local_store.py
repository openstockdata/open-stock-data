"""LocalStore（SQLite 长期存储）单元测试：增量 upsert、除权检测、快照读写、新鲜度窗口。"""
import time

import pandas as pd
import pytest

from open_stock_data.data_provider.local_store import LocalStore, last_expected_trade_date


@pytest.fixture
def store():
    s = LocalStore(path=":memory:")
    yield s
    s.close()


def _bars(dates, close_base=10.0):
    return pd.DataFrame([
        {"date": d, "open": close_base, "high": close_base + 1, "low": close_base - 1,
         "close": close_base + i * 0.1, "volume": 100 + i, "amount": 1000.0, "pct_chg": 0.5}
        for i, d in enumerate(dates)
    ])


def test_upsert_and_load_daily_roundtrip(store):
    assert store.daily_coverage("600519") is None

    result = store.upsert_daily("600519", _bars(["2026-07-01", "2026-07-02", "2026-07-03"]))

    assert result == "ok"
    assert store.daily_coverage("600519") == ("2026-07-01", "2026-07-03", 3)
    df = store.load_daily("600519", days=2)
    assert df["date"].tolist() == ["2026-07-02", "2026-07-03"]  # 升序、最近2行


def test_incremental_upsert_merges_by_date(store):
    store.upsert_daily("600519", _bars(["2026-07-01", "2026-07-02"]))
    # 增量段与本地重叠 1 行（07-02 收盘一致）
    inc = _bars(["2026-07-02", "2026-07-03"])
    inc.loc[0, "close"] = 10.1  # 与本地 07-02 的 close 相同

    assert store.upsert_daily("600519", inc) == "ok"
    assert store.daily_coverage("600519")[2] == 3


def test_adjustment_conflict_clears_symbol_only(store):
    store.upsert_daily("600519", _bars(["2026-07-01", "2026-07-02"]))
    store.upsert_daily("000001", _bars(["2026-07-01", "2026-07-02"]))
    # 前复权除权：重叠日期收盘价大幅偏离
    shifted = _bars(["2026-07-02", "2026-07-03"])
    shifted.loc[0, "close"] = 8.88

    assert store.upsert_daily("600519", shifted) == "conflict"
    assert store.daily_coverage("600519") is None       # 受影响 symbol 被清空
    assert store.daily_coverage("000001")[2] == 2       # 其它 symbol 不受影响


def test_save_and_load_fact_frame(store):
    frame = pd.DataFrame([{"公告日期": "2026-01-01", "派息": 2.5}])
    store.save_fact("dividend_history", "600519", frame, source="TushareFetcher")

    loaded = store.load_fact("dividend_history", "600519")
    assert loaded is not None
    data, source, fetched_at = loaded
    assert source == "TushareFetcher"
    assert list(data.columns) == ["公告日期", "派息"]
    assert float(data.iloc[0]["派息"]) == 2.5


def test_load_fact_respects_freshness_window(store):
    store.save_fact("top10_holders", "600519:main", {"a": 1})
    assert store.load_fact("top10_holders", "600519:main", max_age_seconds=60) is not None
    assert store.load_fact("top10_holders", "600519:main", max_age_seconds=-1) is None  # 已"过期"


def test_save_fact_dict_roundtrip(store):
    store.save_fact("us_overview", "PDD", {"Name": "PDD Holdings", "PERatio": "12.5"})
    data, _, _ = store.load_fact("us_overview", "PDD")
    assert data["Name"] == "PDD Holdings"


def test_fact_frame_preserves_stock_code_strings(store):
    """回归：code 列含 leading zeros（如 000001、000002）必须保留字符串，
    否则下游 df['代码'] == '000001' 匹配失败。"""
    frame = pd.DataFrame([
        {"代码": "000001", "名称": "平安银行", "板块名称": "银行"},
        {"代码": "000002", "名称": "万科A", "板块名称": "地产"},
        {"代码": "600519", "名称": "贵州茅台", "板块名称": "白酒"},
    ])
    store.save_fact("board_cons", "银行:industry", frame, source="AkshareFetcher")

    loaded = store.load_fact("board_cons", "银行:industry")
    assert loaded is not None
    data, _, _ = loaded

    # dtype 必须是字符串类型（object 或 pandas StringDtype），不能是 int
    assert data["代码"].dtype.kind in ("O", "U", "S")  # object / unicode / string
    assert data["代码"].iloc[0] == "000001"
    assert data["代码"].iloc[1] == "000002"
    assert data["代码"].iloc[2] == "600519"

    # 关键：字符串匹配必须命中
    assert not data[data["代码"] == "000001"].empty
    assert not data[data["代码"] == "000002"].empty
    assert not data[data["代码"] == "600519"].empty


def test_last_expected_trade_date_weekday_logic():
    from datetime import datetime
    # 周三 17:00 → 当天
    assert last_expected_trade_date(datetime(2026, 7, 22, 17, 0)) == "2026-07-22"
    # 周三 09:00（未收盘）→ 前一工作日
    assert last_expected_trade_date(datetime(2026, 7, 22, 9, 0)) == "2026-07-21"
    # 周日 → 周五
    assert last_expected_trade_date(datetime(2026, 7, 26, 17, 0)) == "2026-07-24"
    # 周一 09:00 → 上周五
    assert last_expected_trade_date(datetime(2026, 7, 27, 9, 0)) == "2026-07-24"
