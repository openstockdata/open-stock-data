from open_stock_data.cache import CacheStore
from open_stock_data.data_provider.base import BaseFetcher, DataFetcherManager
from open_stock_data.data_provider.types import RealtimeSource, UnifiedRealtimeQuote


class DummyBatchFetcher(BaseFetcher):
    def __init__(self):
        super().__init__()
        self.name = "EfinanceFetcher"
        self.priority = 0
        self.backend_group = "dummy"
        self.batch_calls: list[list[str]] = []
        self.single_calls: list[str] = []

    def _fetch_raw_data(self, stock_code: str, start_date: str, end_date: str):
        return None

    def _normalize_data(self, df, stock_code: str):
        return df

    def get_batch_realtime_quotes(self, stock_codes):
        self.batch_calls.append(list(stock_codes))
        return {
            code: UnifiedRealtimeQuote(
                code=code,
                name=f"批量{code}",
                source=RealtimeSource.FALLBACK,
                price=10.0,
                change_pct=1.0,
            )
            for code in stock_codes
        }

    def get_realtime_quote(self, stock_code: str):
        self.single_calls.append(stock_code)
        return UnifiedRealtimeQuote(
            code=stock_code,
            name=f"单票{stock_code}",
            source=RealtimeSource.FALLBACK,
            price=10.0,
            change_pct=1.0,
        )


def test_prefetch_small_batch_skips_bulk_fetch_path():
    CacheStore.clear_all()
    manager = DataFetcherManager(auto_init=False)
    fetcher = DummyBatchFetcher()
    manager._fetchers = [fetcher]
    manager._batch_realtime_min_size = 8
    codes = ["600001-test-small", "000001-test-small"]

    result = manager.prefetch_realtime_quotes(codes)

    assert sorted(result) == sorted(codes)
    assert fetcher.batch_calls == []
    assert fetcher.single_calls == codes


def test_prefetch_large_batch_still_uses_bulk_fetch_path():
    CacheStore.clear_all()
    manager = DataFetcherManager(auto_init=False)
    fetcher = DummyBatchFetcher()
    manager._fetchers = [fetcher]
    manager._batch_realtime_min_size = 3
    codes = ["600001-test-large", "000001-test-large", "600002-test-large"]

    result = manager.prefetch_realtime_quotes(codes)

    assert sorted(result) == sorted(codes)
    assert len(fetcher.batch_calls) == 1
    assert sorted(fetcher.batch_calls[0]) == sorted(codes)
    assert fetcher.single_calls == []
