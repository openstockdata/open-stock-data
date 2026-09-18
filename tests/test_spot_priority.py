"""全市场快照 / 日线的数据源优先级（迁移到静态路由后）。

优先级不再来自 DataFetcherManager._function_priorities，而是 default_routes.py
中的 RouteSpec.providers 顺序。这里直接断言路由配置。
"""
import pytest

from open_stock_data.data_provider.default_routes import create_default_routes
from open_stock_data.data_provider.contracts import Operation, RouteRequest
from open_stock_data.data_provider.stock_code import StockType


def _providers(operation, market):
    return create_default_routes().resolve(RouteRequest(operation, market)).providers


def test_spot_route_priority_order():
    """全市场快照优先级：Efinance → Akshare。"""
    assert _providers(Operation.A_STOCK_SNAPSHOT, StockType.A_STOCK) == (
        "EfinanceFetcher",
        "AkshareFetcher",
    )


def test_spot_route_disables_shared_backend_skip():
    """快照路由允许同 backend 的 Efinance/Akshare 都尝试（不做同源跳过）。"""
    route = create_default_routes().resolve(RouteRequest(Operation.A_STOCK_SNAPSHOT, StockType.A_STOCK))
    assert route.skip_shared_backend_after_network_error is False


def test_daily_route_priority_tickflow_first():
    """A股日线：Tickflow 优先级最高（priority=10），其次 Efinance(5)，再次 Akshare(1)。"""
    providers = _providers(Operation.DAILY_PRICES, StockType.A_STOCK)
    assert "TickflowFetcher" in providers and "EfinanceFetcher" in providers and "AkshareFetcher" in providers
    assert providers.index("TickflowFetcher") < providers.index("EfinanceFetcher")
    assert providers.index("EfinanceFetcher") < providers.index("AkshareFetcher")


@pytest.mark.network
def test_spot_integration_uses_efinance():
    """集成测试：实际获取时应使用 Efinance 或 Akshare（需要网络）。"""
    import os
    os.environ["ENABLE_EASTMONEY_PATCH"] = "true"

    from open_stock_data.client import OpenStockDataClient

    result = OpenStockDataClient().a_stock_snapshot()
    assert result.data is not None and not result.data.empty
    assert result.source in ("EfinanceFetcher", "AkshareFetcher")
    assert len(result.data) > 5000
