import pandas as pd

from open_stock_data.data_provider.base import BaseFetcher, DataFetcherManager
from open_stock_data.data_provider.akshare_fetcher import AkshareFetcher
from open_stock_data.data_provider.efinance_fetcher import EfinanceFetcher
from open_stock_data.data_provider.types import RealtimeSource, UnifiedRealtimeQuote
from open_stock_data.data_provider.circuit_breaker import get_circuit_breaker


class DummyScopedFetcher(BaseFetcher):
    def __init__(
        self,
        name: str,
        priority: int,
        backend_group: str,
        scopes: dict[str, str],
        *,
        board_df=None,
        board_error=None,
        realtime_quote=None,
        realtime_error=None,
        daily_df=None,
        daily_error=None,
        raw_daily_df=None,
        raw_daily_error=None,
    ):
        super().__init__()
        self.name = name
        self.priority = priority
        self.backend_group = backend_group
        self._scopes = scopes
        self._board_df = board_df
        self._board_error = board_error
        self._realtime_quote = realtime_quote
        self._realtime_error = realtime_error
        self._daily_df = daily_df
        self._daily_error = daily_error
        self._raw_daily_df = raw_daily_df
        self._raw_daily_error = raw_daily_error
        self.calls: list[tuple[str, str]] = []

    def _fetch_raw_data(self, stock_code: str, start_date: str, end_date: str):
        return None

    def _normalize_data(self, df: pd.DataFrame, stock_code: str) -> pd.DataFrame:
        return df

    def get_backend_failure_scope(self, method_name: str, *args, **kwargs):
        scope = self._scopes.get(method_name)
        if scope:
            return scope
        return super().get_backend_failure_scope(method_name, *args, **kwargs)

    def get_belong_board(self, stock_code: str):
        self.calls.append(("get_belong_board", stock_code))
        if self._board_error is not None:
            raise self._board_error
        return self._board_df

    def get_realtime_quote(self, stock_code: str):
        self.calls.append(("get_realtime_quote", stock_code))
        if self._realtime_error is not None:
            raise self._realtime_error
        return self._realtime_quote

    def get_daily_data(self, stock_code: str, start_date=None, end_date=None, days: int = 30):
        self.calls.append(("get_daily_data", stock_code))
        if self._daily_error is not None:
            raise self._daily_error
        return self._daily_df

    def get_raw_daily_data(self, stock_code: str, start_date=None, end_date=None, days: int = 30):
        self.calls.append(("get_raw_daily_data", stock_code))
        if self._raw_daily_error is not None:
            raise self._raw_daily_error
        return self._raw_daily_df


def _make_manager(fetchers):
    manager = DataFetcherManager(auto_init=False)
    manager._fetchers = fetchers
    return manager


def _make_quote(code: str) -> UnifiedRealtimeQuote:
    return UnifiedRealtimeQuote(
        code=code,
        name="测试标的",
        source=RealtimeSource.FALLBACK,
        price=10.0,
        change_pct=1.0,
    )


def test_different_backend_scope_does_not_skip_other_fetcher():
    get_circuit_breaker("board").reset()
    cache = DataFetcherManager(auto_init=False)._store
    cache.delete("belong_board:601778")
    failing = DummyScopedFetcher(
        "FailingBoardFetcher",
        0,
        "eastmoney",
        {"get_belong_board": "eastmoney:push2:slist_get"},
        board_error=ConnectionError("push2 slist down"),
    )
    success_df = pd.DataFrame([{"股票代码": "601778", "板块名称": "电力"}])
    succeeding = DummyScopedFetcher(
        "FallbackBoardFetcher",
        1,
        "eastmoney",
        {"get_belong_board": "eastmoney:push2:board_index_list"},
        board_df=success_df,
    )
    manager = _make_manager([failing, succeeding])

    result = manager.get_belong_board("601778")

    assert result is not None
    assert list(result["板块名称"]) == ["电力"]
    assert failing.calls == [("get_belong_board", "601778")]
    assert succeeding.calls == [("get_belong_board", "601778")]


def test_same_backend_scope_skips_second_realtime_fetcher_after_network_failure():
    get_circuit_breaker("realtime").reset()
    cache = DataFetcherManager(auto_init=False)._store
    cache.delete("realtime:601778")
    failing = DummyScopedFetcher(
        "FailingRealtimeFetcher",
        0,
        "eastmoney",
        {"get_realtime_quote": "eastmoney:push2:realtime_quotes"},
        realtime_error=ConnectionError("push2 realtime down"),
    )
    skipped = DummyScopedFetcher(
        "SkippedRealtimeFetcher",
        1,
        "eastmoney",
        {"get_realtime_quote": "eastmoney:push2:realtime_quotes"},
        realtime_quote=_make_quote("601778"),
    )
    manager = _make_manager([failing, skipped])

    result = manager.get_realtime_quote("601778")

    assert result is None
    assert failing.calls == [("get_realtime_quote", "601778")]
    assert skipped.calls == []

def test_real_fetchers_use_distinct_fund_flow_backend_scopes():
    assert EfinanceFetcher().get_backend_failure_scope("get_fund_flow") == "eastmoney:http:push2his:fund_flow"
    assert AkshareFetcher().get_backend_failure_scope("get_fund_flow") == "eastmoney:https:push2his:fund_flow"


def test_fetch_akshare_does_not_pass_cache_only_kwargs():
    manager = DataFetcherManager(auto_init=False)
    received = {}

    def fake_akshare(symbol: str, start_year: str):
        received["symbol"] = symbol
        received["start_year"] = start_year
        return {"ok": True}

    result = manager.fetch_akshare(
        fake_akshare,
        symbol="600487",
        start_year="2024",
        ttl=60,
        ttl2=7776000,
    )

    assert result == {"ok": True}
    assert received == {"symbol": "600487", "start_year": "2024"}


def test_same_backend_scope_skips_second_fetcher_for_daily_after_network_failure():
    get_circuit_breaker("daily").reset()
    failing = DummyScopedFetcher(
        "EfinanceFetcher",
        0,
        "eastmoney",
        {"get_daily_data": "eastmoney:get_daily_data"},
        daily_error=ConnectionError("eastmoney daily down"),
    )
    skipped = DummyScopedFetcher(
        "AkshareFetcher",
        1,
        "eastmoney",
        {"get_daily_data": "eastmoney:get_daily_data"},
        daily_df=pd.DataFrame([{"date": "2026-01-02", "close": 10.0}]),
    )
    fallback = DummyScopedFetcher(
        "TushareFetcher",
        2,
        "tushare",
        {"get_daily_data": "tushare:get_daily_data"},
        daily_df=pd.DataFrame([{"date": "2026-01-02", "close": 10.5}]),
    )
    manager = _make_manager([failing, skipped, fallback])

    result = manager.get_daily_data("601778", days=5)

    assert result is not None
    assert list(result["close"]) == [10.5]
    assert failing.calls == [("get_daily_data", "601778")]
    assert skipped.calls == []
    assert fallback.calls == [("get_daily_data", "601778")]
    assert result.attrs.get("source") == "TushareFetcher"


def test_same_backend_scope_skips_second_fetcher_for_raw_daily_after_network_failure():
    get_circuit_breaker("daily").reset()
    failing = DummyScopedFetcher(
        "EfinanceFetcher",
        0,
        "eastmoney",
        {"get_raw_daily_data": "eastmoney:get_raw_daily_data"},
        raw_daily_error=ConnectionError("eastmoney raw daily down"),
    )
    skipped = DummyScopedFetcher(
        "AkshareFetcher",
        1,
        "eastmoney",
        {"get_raw_daily_data": "eastmoney:get_raw_daily_data"},
        raw_daily_df=pd.DataFrame([{"date": "2026-01-02", "close": 10.0}]),
    )
    manager = _make_manager([failing, skipped])

    result = manager.get_raw_daily_data("601778", days=5)

    assert result is None
    assert failing.calls == [("get_raw_daily_data", "601778")]
    assert skipped.calls == []
