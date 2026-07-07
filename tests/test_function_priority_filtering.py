import pandas as pd

from open_stock_data.data_provider.base import BaseFetcher, DataFetcherManager


class DummyOnlyCctvFetcher(BaseFetcher):
    def __init__(self, name: str, priority: int):
        super().__init__()
        self.name = name
        self.priority = priority
        self.calls: list[str] = []

    def _fetch_raw_data(self, stock_code: str, start_date: str, end_date: str):
        return None

    def _normalize_data(self, df: pd.DataFrame, stock_code: str) -> pd.DataFrame:
        return df

    def get_cctv_news(self, date: str = "") -> pd.DataFrame:
        self.calls.append(date)
        return pd.DataFrame([{"date": date or "20260331", "title": "新闻联播"}])


class DummyUnsupportedFetcher(BaseFetcher):
    def __init__(self, name: str, priority: int):
        super().__init__()
        self.name = name
        self.priority = priority

    def _fetch_raw_data(self, stock_code: str, start_date: str, end_date: str):
        return None

    def _normalize_data(self, df: pd.DataFrame, stock_code: str) -> pd.DataFrame:
        return df


class DummyBaseDailyFetcher(BaseFetcher):
    def __init__(self, name: str, priority: int):
        super().__init__()
        self.name = name
        self.priority = priority
        self.calls: list[tuple[str, str, str]] = []

    def _fetch_raw_data(self, stock_code: str, start_date: str, end_date: str):
        self.calls.append((stock_code, start_date, end_date))
        return pd.DataFrame(
            [
                {
                    "date": "2026-01-02",
                    "open": 10.0,
                    "high": 11.0,
                    "low": 9.5,
                    "close": 10.5,
                    "volume": 1000,
                    "amount": 10000,
                }
            ]
        )

    def _normalize_data(self, df: pd.DataFrame, stock_code: str) -> pd.DataFrame:
        return df


def test_function_priority_filters_out_unconfigured_fetchers():
    manager = DataFetcherManager(auto_init=False)
    supported = DummyOnlyCctvFetcher("AkshareFetcher", 9)
    unsupported = DummyUnsupportedFetcher("TushareFetcher", 0)
    manager._fetchers = [unsupported, supported]

    ordered = manager._get_fetchers_for("get_cctv_news")

    assert [fetcher.name for fetcher in ordered] == ["AkshareFetcher"]

    result = manager.get_cctv_news("20260331")

    assert result is not None
    assert supported.calls == ["20260331"]


def test_get_board_cons_only_uses_supported_fetchers():
    manager = DataFetcherManager(auto_init=False)
    manager._fetchers = [
        DummyUnsupportedFetcher("PytdxFetcher", 0),
        DummyUnsupportedFetcher("BaostockFetcher", 1),
        DummyUnsupportedFetcher("AlphaVantage", 2),
        DummyUnsupportedFetcher("YfinanceFetcher", 3),
        DummyUnsupportedFetcher("EfinanceFetcher", 4),
        DummyUnsupportedFetcher("TushareFetcher", 5),
        DummyUnsupportedFetcher("AkshareFetcher", 6),
    ]

    ordered = manager._get_fetchers_for("get_board_cons")

    assert [fetcher.name for fetcher in ordered] == ["AkshareFetcher", "TushareFetcher"]


def test_failover_accepts_base_fetcher_daily_implementation():
    manager = DataFetcherManager(auto_init=False)
    fetcher = DummyBaseDailyFetcher("EfinanceFetcher", 0)
    manager._fetchers = [fetcher]

    result = manager.get_daily_data("600744", days=5)

    assert result is not None
    assert not result.empty
    assert fetcher.calls
