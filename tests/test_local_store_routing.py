"""本地优先 provider + persist 回写的端到端测试（RouteExecutor 级）。"""
import pandas as pd
import pytest

from open_stock_data.data_provider.circuit_breaker import get_circuit_breaker
from open_stock_data.data_provider.contracts import (
    AttemptOutcome,
    Operation,
    RouteRequest,
    RouteSpec,
)
from open_stock_data.data_provider.local_store import LocalStore, LocalStoreFetcher
from open_stock_data.data_provider.routing import RouteExecutor, RouteRegistry


@pytest.fixture(autouse=True)
def reset_breaker():
    get_circuit_breaker("dividend").reset()
    yield
    get_circuit_breaker("dividend").reset()


class NetFetcher:
    name = "TushareFetcher"
    priority = 0
    backend_group = ""

    def __init__(self, frame):
        self._frame = frame
        self.calls = 0

    @property
    def is_available(self):
        return True

    def get_backend_failure_scope(self, method_name, *a, **k):
        return None

    def get_dividend_history(self, symbol):
        self.calls += 1
        return self._frame


def _executor(store):
    def persist(data, request, source):
        if source == "LocalStoreFetcher" or request.cache_key is None:
            return
        store.save_fact(request.operation.value, request.cache_key, data, source)

    route = RouteSpec(
        Operation.DIVIDEND_HISTORY,
        None,
        ("LocalStoreFetcher", "TushareFetcher"),
        "get_dividend_history",
        "dividend",
        persist=persist,
    )
    net = NetFetcher(pd.DataFrame([{"公告日期": "2026-01-01", "派息": 3.0}]))
    providers = {"LocalStoreFetcher": LocalStoreFetcher(store), "TushareFetcher": net}
    return RouteExecutor(providers, RouteRegistry([route]), cache=None), net


def _req():
    return RouteRequest(Operation.DIVIDEND_HISTORY, None, args=("600519",), cache_key="600519")


def test_first_call_misses_local_hits_network_and_persists():
    store = LocalStore(path=":memory:")
    executor, net = _executor(store)

    result = executor.execute(_req())

    assert result.source == "TushareFetcher"
    assert net.calls == 1
    assert result.attempts[0].outcome == AttemptOutcome.SKIPPED or result.attempts[0].source == "LocalStoreFetcher"
    # 已回写本地
    assert store.load_fact("dividend_history", "600519") is not None
    store.close()


def test_second_call_served_from_local_without_network():
    store = LocalStore(path=":memory:")
    executor, net = _executor(store)

    executor.execute(_req())   # 首次：网络 + 回写
    net.calls = 0
    result = executor.execute(_req())  # 二次：本地命中

    assert result.source == "LocalStoreFetcher"
    assert net.calls == 0
    assert list(result.data.columns) == ["公告日期", "派息"]
    store.close()


def test_local_miss_records_empty_not_failure_then_falls_through():
    store = LocalStore(path=":memory:")
    executor, net = _executor(store)

    result = executor.execute(_req())

    first = result.attempts[0]
    assert first.source == "LocalStoreFetcher"
    assert first.outcome == AttemptOutcome.EMPTY  # 未命中记 EMPTY，不熔断
    assert result.source == "TushareFetcher"
    store.close()
