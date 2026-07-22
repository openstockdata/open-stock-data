"""Fetcher 后端作用域 + fetch_akshare 参数过滤（不依赖旧 failover 引擎）。

同 backend 网络失败后的"同源跳过"、跨作用域"不跳过"行为已迁移到
RouteExecutor，由 tests/test_route_executor.py 覆盖：
  - test_network_failure_skips_provider_with_same_backend_scope
  - test_network_failure_does_not_skip_provider_with_different_backend_scope
"""
from open_stock_data.data_provider.base import DataFetcherManager
from open_stock_data.data_provider.akshare_fetcher import AkshareFetcher
from open_stock_data.data_provider.efinance_fetcher import EfinanceFetcher


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
