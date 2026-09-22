"""LocalStoreFetcher 对未实现的操作按未命中处理（不抛异常、不被路由降级）。"""
import pandas as pd

from open_stock_data.data_provider.context import ProviderContext
from open_stock_data.data_provider.contracts import AttemptOutcome, Operation, RouteRequest, RouteSpec
from open_stock_data.data_provider.dynamic_router import DynamicRouter
from open_stock_data.data_provider.local_store import LocalStore, LocalStoreFetcher
from open_stock_data.data_provider.routing import RouteExecutor, RouteRegistry
from open_stock_data.data_provider.stock_code import StockType


class _Net:
    name = "YfinanceFetcher"
    priority = 9

    @property
    def is_available(self):
        return True

    @property
    def metadata(self):
        from open_stock_data.data_provider.plugin import ProviderMetadata
        return ProviderMetadata(name=self.name, priority=self.priority, tags=())

    def execute(self, method_name, *args, **kwargs):
        return getattr(self, method_name)(*args, **kwargs)

    def get_daily_data(self, symbol, **kwargs):
        return pd.DataFrame([{"date": "2026-01-01", "close": 1.0}])


def test_execute_unknown_method_returns_none():
    store = LocalStore(path=":memory:")
    assert LocalStoreFetcher(store).execute("get_daily_data", "AAPL") is None
    store.close()


def test_daily_route_with_local_store_first_records_empty_not_error():
    store = LocalStore(path=":memory:")
    ctx = ProviderContext()
    ctx.register(LocalStoreFetcher(store))
    ctx.register(_Net())
    route = RouteSpec(Operation.DAILY_PRICES, StockType.US, ("LocalStoreFetcher", "YfinanceFetcher"), "get_daily_data")
    executor = RouteExecutor(DynamicRouter(ctx), RouteRegistry([route]))

    for _ in range(4):
        result = executor.execute(RouteRequest(Operation.DAILY_PRICES, StockType.US, args=("AAPL",)))
        assert result.source == "YfinanceFetcher"
        assert result.attempts[0].source == "LocalStoreFetcher"
        assert result.attempts[0].outcome == AttemptOutcome.EMPTY

    health = ctx.health_snapshot["LocalStoreFetcher"]
    assert health.failure_count == 0
    assert health.circuit_state == "CLOSED"
    store.close()
