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
    """全市场快照优先级：Akshare → Efinance（运行时实际顺序由 priority 健康分决定）。"""
    assert _providers(Operation.A_STOCK_SNAPSHOT, StockType.A_STOCK) == (
        "AkshareFetcher",
        "EfinanceFetcher",
    )


def test_spot_route_has_no_shared_backend_skip():
    """同后端跳过机制已删除：RouteSpec 不再有 skip_shared_backend 字段，
    快照路由的 Efinance/Akshare 按统一健康体系逐个尝试。"""
    route = create_default_routes().resolve(RouteRequest(Operation.A_STOCK_SNAPSHOT, StockType.A_STOCK))
    assert not hasattr(route, "skip_shared_backend_after_network_error")


def test_daily_route_priority_tickflow_first():
    """A股日线：Tickflow 优先级最高（priority=10），其次 Efinance(5)，再次 Akshare(4)。
    providers 元组是候选名单，实际回退顺序由 priority + 健康分动态决定。"""
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
