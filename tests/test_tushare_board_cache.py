import pandas as pd

from open_stock_data.cache import CacheStore
from open_stock_data.data_provider.base import BaseFetcher
from open_stock_data.data_provider.tushare_fetcher import TushareFetcher


def _reset_tushare_rate_limit_state():
    with TushareFetcher._rate_limit_lock:
        TushareFetcher._request_timestamps.clear()


def _build_fetcher() -> TushareFetcher:
    fetcher = object.__new__(TushareFetcher)
    BaseFetcher.__init__(fetcher)
    fetcher._available = True
    fetcher._api = object()
    return fetcher


def test_get_board_cons_industry_uses_offline_snapshot(monkeypatch):
    CacheStore.clear_all()
    fetcher = _build_fetcher()

    snapshot = pd.DataFrame([
        {"ts_code": "601398.SH", "symbol": "601398", "name": "工商银行", "industry": "单元测试银行行业", "market": "主板", "list_date": "20061027"},
        {"ts_code": "600036.SH", "symbol": "600036", "name": "招商银行", "industry": "单元测试银行行业", "market": "主板", "list_date": "20020409"},
        {"ts_code": "600030.SH", "symbol": "600030", "name": "中信证券", "industry": "证券", "market": "主板", "list_date": "20030106"},
    ])
    fetcher._board_store.set("tushare_board_stock_basic_snapshot", snapshot, expire=86400)

    monkeypatch.setattr(fetcher, "_check_rate_limit", lambda: (_ for _ in ()).throw(AssertionError("should not hit online stock_basic")))

    result = fetcher.get_board_cons("单元测试银行行业", "industry")

    assert result is not None
    assert list(result["股票代码"]) == ["601398", "600036"]
    assert list(result["名称"]) == ["工商银行", "招商银行"]

    CacheStore.clear_all()


def test_get_belong_board_uses_offline_snapshot(monkeypatch):
    CacheStore.clear_all()
    fetcher = _build_fetcher()

    snapshot = pd.DataFrame([
        {"ts_code": "601398.SH", "symbol": "601398", "name": "工商银行", "industry": "银行", "market": "主板", "list_date": "20061027"},
    ])
    fetcher._board_store.set("tushare_board_stock_basic_snapshot", snapshot, expire=86400)

    monkeypatch.setattr(fetcher, "_check_rate_limit", lambda: (_ for _ in ()).throw(AssertionError("should not hit online stock_basic")))

    result = fetcher.get_belong_board("601398")

    assert result is not None
    assert result.iloc[0]["股票代码"] == "601398.SH"
    assert result.iloc[0]["行业"] == "银行"

    CacheStore.clear_all()


def test_get_belong_board_does_not_write_symbol_cache(monkeypatch):
    CacheStore.clear_all()
    fetcher = _build_fetcher()

    snapshot = pd.DataFrame([
        {"ts_code": "601398.SH", "symbol": "601398", "name": "工商银行", "industry": "银行", "market": "主板", "list_date": "20061027"},
    ])
    fetcher._board_store.set("tushare_board_stock_basic_snapshot", snapshot, expire=86400)

    result = fetcher.get_belong_board("601398")

    assert result is not None
    # belong_board 由 DataFetcherManager 缓存，TushareFetcher 本身不写缓存
    assert fetcher._board_store.get("tushare_belong_board_601398") is None

    CacheStore.clear_all()


def test_check_rate_limit_is_shared_across_fetcher_instances(monkeypatch):
    _reset_tushare_rate_limit_state()

    current_time = 1000.0
    sleep_calls = []

    def fake_monotonic():
        return current_time

    def fake_sleep(seconds: float):
        nonlocal current_time
        sleep_calls.append(seconds)
        current_time += seconds

    monkeypatch.setattr("open_stock_data.data_provider.tushare_fetcher.time.monotonic", fake_monotonic)
    monkeypatch.setattr("open_stock_data.data_provider.tushare_fetcher.time.sleep", fake_sleep)

    fetcher_a = _build_fetcher()
    fetcher_b = _build_fetcher()

    for _ in range(TushareFetcher.RATE_LIMIT):
        fetcher_a._check_rate_limit()

    fetcher_b._check_rate_limit()

    assert len(sleep_calls) == 1
    assert sleep_calls[0] == TushareFetcher.RATE_WINDOW
    assert len(TushareFetcher._request_timestamps) == TushareFetcher.RATE_LIMIT

    _reset_tushare_rate_limit_state()


def test_check_rate_limit_prunes_requests_outside_window(monkeypatch):
    _reset_tushare_rate_limit_state()

    current_time = 2000.0

    def fake_monotonic():
        return current_time

    monkeypatch.setattr("open_stock_data.data_provider.tushare_fetcher.time.monotonic", fake_monotonic)

    fetcher = _build_fetcher()

    for _ in range(TushareFetcher.RATE_LIMIT):
        fetcher._check_rate_limit()

    current_time += TushareFetcher.RATE_WINDOW + 1
    fetcher._check_rate_limit()

    assert len(TushareFetcher._request_timestamps) == 1

    _reset_tushare_rate_limit_state()
