"""Tushare 配额识别与按接口冷却（修复 “频率超限(1次/小时)” 未被识别、concept 列表未缓存）。"""
import pandas as pd
import pytest

from open_stock_data.cache import CacheStore
from open_stock_data.data_provider.base import BaseFetcher
from open_stock_data.data_provider.tushare_fetcher import TushareFetcher


HOURLY_MSG = "抱歉，您访问接口(concept)频率超限(1次/小时)，具体频次详情：https://tushare.pro/document/1?doc_id=108。"
MINUTE_MSG = "抱歉，您每分钟最多访问该接口50次，权限的具体详情访问：https://tushare.pro/document/1?doc_id=108。"


class _FakeApi:
    def __init__(self, concept_error=None):
        self.concept_calls = 0
        self.concept_detail_calls = 0
        self.concept_error = concept_error

    def concept(self):
        self.concept_calls += 1
        if self.concept_error is not None:
            raise Exception(self.concept_error)
        return pd.DataFrame([{"code": "TS1", "name": "华为概念", "src": "ts"}])

    def concept_detail(self, id):
        self.concept_detail_calls += 1
        return pd.DataFrame([{"ts_code": "002230.SZ", "name": "科大讯飞", "in_date": None, "out_date": None}])


def _build_fetcher(api=None) -> TushareFetcher:
    fetcher = object.__new__(TushareFetcher)
    BaseFetcher.__init__(fetcher)
    fetcher._available = True
    fetcher._api = api if api is not None else object()
    return fetcher


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch):
    CacheStore.clear_all()
    TushareFetcher._reset_quota_state()
    monkeypatch.setattr(TushareFetcher, "_check_rate_limit", lambda self: None)
    yield
    CacheStore.clear_all()
    TushareFetcher._reset_quota_state()


# ---------------------------------------------------------------------------
# 文案分类
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "message, expected_type, expected_wait",
    [
        (HOURLY_MSG, "hourly_limit", 3600),
        (MINUTE_MSG, "minute_limit", 120),
        ("抱歉，您没有访问该接口的权限", "no_permission", 3600),
        ("抱歉，您访问接口(daily)频率超限", "unknown", 300),
    ],
)
def test_rate_limit_messages_are_classified(message, expected_type, expected_wait):
    is_limit, limit_type, wait = TushareFetcher._is_rate_limit_error(message)
    assert is_limit is True
    assert limit_type == expected_type
    assert wait == expected_wait


def test_daily_limit_cools_until_beijing_midnight_not_fixed_6h():
    """日配额冷却到北京时间次日 0 点（+60s 缓冲），而非固定 21600s：
    白天触发时 6 小时后仍在同一天，重试必败还白烧配额。"""
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from open_stock_data.data_provider.tushare_fetcher import seconds_until_beijing_midnight

    is_limit, limit_type, wait = TushareFetcher._is_rate_limit_error("抱歉，您每天最多访问该接口500次")
    assert is_limit is True
    assert limit_type == "daily_limit"
    assert 60.0 <= wait <= 24 * 3600 + 120.0

    #  pin 住两个时刻验证公式：北京时间 10:00 → 当天剩余 14h+缓冲；23:59 → 约 60s+缓冲
    bj = ZoneInfo("Asia/Shanghai")
    morning = datetime(2026, 9, 21, 10, 0, tzinfo=bj)
    assert seconds_until_beijing_midnight(morning) == pytest.approx(14 * 3600 + 60, abs=2)
    late = datetime(2026, 9, 21, 23, 59, tzinfo=bj)
    assert seconds_until_beijing_midnight(late) == pytest.approx(120, abs=2)


@pytest.mark.parametrize(
    "message",
    [
        "trade_date 日期格式错误",       # 含“日”但不是配额
        "网络连接失败",
        "KeyError: 'ts_code'",
    ],
)
def test_ordinary_errors_are_not_rate_limits(message):
    assert TushareFetcher._is_rate_limit_error(message)[0] is False


# ---------------------------------------------------------------------------
# 按接口冷却 + 概念列表缓存
# ---------------------------------------------------------------------------


def test_concept_hourly_limit_blocks_only_concept_api_and_returns_quota_empty():
    """配额冷却返回带原因的空结果（attrs["empty_reason"]），路由记 EMPTY 可排查。"""
    api = _FakeApi(concept_error=HOURLY_MSG)
    fetcher = _build_fetcher(api)

    first = fetcher.get_board_cons("华为概念", "concept")
    assert first is not None and first.empty
    assert "concept" in first.attrs.get("empty_reason", "")
    assert api.concept_calls == 1

    # 冷却期内再次调用：不再打接口，同样带原因
    second = fetcher.get_board_cons("新能源", "concept")
    assert second is not None and second.empty
    assert "concept" in second.attrs.get("empty_reason", "")
    assert api.concept_calls == 1

    # 只冷却 concept；其它接口不受影响
    assert fetcher._quota_available("concept") is False
    assert fetcher._quota_available("stock_basic") is True
    assert fetcher._quota_available("daily") is True


def test_concept_list_is_cached_across_board_lookups():
    api = _FakeApi()
    fetcher = _build_fetcher(api)

    first = fetcher.get_board_cons("华为概念", "concept")
    assert first is not None and not first.empty
    assert api.concept_calls == 1
    assert api.concept_detail_calls == 1

    # 另一个板块名：列表命中缓存，只多一次 concept_detail
    assert fetcher.get_board_cons("华为", "concept") is not None
    assert api.concept_calls == 1
    assert api.concept_detail_calls == 2

    # 未匹配的板块名：不调 concept_detail
    assert fetcher.get_board_cons("不存在的板块", "concept") is None
    assert api.concept_detail_calls == 2


def test_quota_block_expires(monkeypatch):
    fetcher = _build_fetcher()
    now = [1000.0]
    monkeypatch.setattr("open_stock_data.data_provider.tushare_fetcher.time.monotonic", lambda: now[0])

    assert fetcher._note_rate_limit(Exception(MINUTE_MSG), "x", api="daily") is True
    assert fetcher._quota_available("daily") is False
    now[0] += 121
    assert fetcher._quota_available("daily") is True


def test_note_rate_limit_ignores_ordinary_errors():
    fetcher = _build_fetcher()
    assert fetcher._note_rate_limit(Exception("boom"), "x", api="daily") is False
    assert fetcher._quota_available("daily") is True


def test_belong_board_returns_unified_schema():
    fetcher = _build_fetcher()
    snapshot = pd.DataFrame([
        {"ts_code": "601298.SH", "symbol": "601298", "name": "青岛港", "industry": "港口", "market": "主板", "list_date": "20190121"},
    ])
    fetcher._board_store.set("tushare_board_stock_basic_snapshot", snapshot, expire=86400)

    result = fetcher.get_belong_board("601298")

    assert list(result.columns)[:3] == ["板块名称", "板块代码", "板块类型"]
    assert result.iloc[0]["板块名称"] == "港口"
    assert result.iloc[0]["板块类型"] == "industry"
    assert result.iloc[0]["股票代码"] == "601298.SH"
    assert result.iloc[0]["行业"] == "港口"
