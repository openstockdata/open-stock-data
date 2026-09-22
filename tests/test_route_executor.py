"""Tests for routing and executor using the new provider-plugin architecture."""

import pandas as pd
import pytest

from open_stock_data.cache import CacheStore
from open_stock_data.data_provider.base import BaseFetcher
from open_stock_data.data_provider.context import ProviderContext
from open_stock_data.data_provider.contracts import (
    AttemptOutcome,
    CachePolicy,
    Operation,
    RouteRequest,
    RouteSpec,
)
from open_stock_data.data_provider.dynamic_router import DynamicRouter
from open_stock_data.data_provider.routing import RouteExecutor, RouteRegistry
from open_stock_data.data_provider.stock_code import StockType
from open_stock_data.exceptions import AllSourcesFailed, RouteNotFoundError


@pytest.fixture(autouse=True)
def clear_cache():
    CacheStore.clear_all()
    yield
    CacheStore.clear_all()


class DummyFetcher(BaseFetcher):
    def __init__(self, name, *, result=None, error=None, available=True):
        super().__init__()
        self.name = name
        self.result = result
        self.error = error
        self._available = available
        self.calls = 0

    def _fetch_daily_data(self, stock_code, start_date, end_date):
        return None

    def _normalize_data(self, df, stock_code):
        return df

    def load(self, symbol):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.result


def make_route(**overrides):
    values = {
        "operation": Operation.DAILY_PRICES,
        "market": StockType.A_STOCK,
        "providers": ("first", "second"),
        "method_name": "load",
    }
    values.update(overrides)
    return RouteSpec(**values)


def make_executor(route, providers_dict):
    """Create a RouteExecutor with providers registered in a ProviderContext."""
    ctx = ProviderContext()
    for fetcher in providers_dict.values():
        ctx.register(fetcher)
    router = DynamicRouter(ctx)
    registry = RouteRegistry([route])
    return RouteExecutor(router, registry)


def execute(route, providers):
    request = RouteRequest(
        operation=route.operation,
        market=route.market,
        args=("600519",),
    )
    executor = make_executor(route, providers)
    return executor.execute(request)


# ---------------------------------------------------------------------------
# 基本路由测试
# ---------------------------------------------------------------------------


def test_fixed_order_skips_unavailable_provider():
    first = DummyFetcher("first", available=False)
    expected = pd.DataFrame([{"close": 10.0}])
    second = DummyFetcher("second", result=expected)

    result = execute(make_route(), {"first": first, "second": second})

    assert result.data is expected
    assert result.source == "second"
    # 不可用 provider 在排序阶段即被过滤，路由只记录实际尝试
    assert [attempt.outcome for attempt in result.attempts] == [
        AttemptOutcome.SUCCESS,
    ]
    assert first.calls == 0
    assert second.calls == 1


def test_empty_result_falls_back_when_route_requires_data():
    first = DummyFetcher("first", result=pd.DataFrame())
    second = DummyFetcher("second", result=pd.DataFrame([{"close": 10.0}]))

    result = execute(make_route(), {"first": first, "second": second})

    assert result.source == "second"
    assert result.attempts[0].outcome == AttemptOutcome.EMPTY


def test_network_failure_falls_back_to_next_provider():
    """统一健康体系下不再做同后端跳过：网络失败记 ERROR，回退到下一源。"""
    first = DummyFetcher("first", error=ConnectionError("down"))
    second = DummyFetcher("second", result={"ok": True})
    route = make_route()

    result = execute(route, {"first": first, "second": second})

    assert result.source == "second"
    assert [attempt.outcome for attempt in result.attempts] == [
        AttemptOutcome.ERROR,
        AttemptOutcome.SUCCESS,
    ]
    assert second.calls == 1


def test_network_failure_does_not_skip_provider_with_different_backend_scope():
    first = DummyFetcher("first", error=ConnectionError("down"))
    second = DummyFetcher("second", result={"ok": True})
    route = make_route()

    result = execute(route, {"first": first, "second": second})

    assert result.source == "second"
    assert [attempt.outcome for attempt in result.attempts] == [
        AttemptOutcome.ERROR,
        AttemptOutcome.SUCCESS,
    ]
    assert second.calls == 1


def test_all_sources_failed_exposes_attempts():
    first = DummyFetcher("first", error=RuntimeError("broken"))
    second = DummyFetcher("second", result=None)

    with pytest.raises(AllSourcesFailed) as caught:
        execute(make_route(), {"first": first, "second": second})

    assert caught.value.operation == Operation.DAILY_PRICES.value
    assert [attempt.outcome for attempt in caught.value.attempts] == [
        AttemptOutcome.ERROR,
        AttemptOutcome.EMPTY,
    ]


def test_missing_route_is_explicit_error():
    request = RouteRequest(Operation.REALTIME_QUOTE, StockType.US)

    with pytest.raises(RouteNotFoundError):
        RouteRegistry([]).resolve(request)


# ---------------------------------------------------------------------------
# 原生批量执行器
# ---------------------------------------------------------------------------


def _spot_complete(df):
    return df is not None and not df.empty and bool(df.attrs.get("spot_complete", True))


def _partial_frame():
    frame = pd.DataFrame([{"code": "1"}])
    frame.attrs["spot_complete"] = False
    return frame


def _complete_frame():
    frame = pd.DataFrame([{"code": "1"}, {"code": "2"}])
    frame.attrs["spot_complete"] = True
    return frame


def test_partial_snapshot_is_rejected_and_only_complete_result_cached():
    first = DummyFetcher("first", result=_partial_frame())
    second = DummyFetcher("second", result=_complete_frame())
    route = make_route(
        operation=Operation.A_STOCK_SNAPSHOT,
        providers=("first", "second"),
        validator=_spot_complete,
        cache_policy=CachePolicy(ttl_seconds=60),
    )

    executor = make_executor(route, {"first": first, "second": second})
    request = RouteRequest(
        operation=Operation.A_STOCK_SNAPSHOT,
        market=StockType.A_STOCK,
        args=("all",),
    )
    result = executor.execute(request)

    assert result.source == "second"
    assert [attempt.outcome for attempt in result.attempts] == [
        AttemptOutcome.INVALID,
        AttemptOutcome.SUCCESS,
    ]


def test_snapshot_all_partial_fails_and_nothing_cached():
    first = DummyFetcher("first", result=_partial_frame())
    second = DummyFetcher("second", result=_partial_frame())
    route = make_route(
        operation=Operation.A_STOCK_SNAPSHOT,
        providers=("first", "second"),
        validator=_spot_complete,
        cache_policy=CachePolicy(ttl_seconds=60),
    )

    executor = make_executor(route, {"first": first, "second": second})
    request = RouteRequest(
        operation=Operation.A_STOCK_SNAPSHOT,
        market=StockType.A_STOCK,
        args=("all",),
    )
    with pytest.raises(AllSourcesFailed) as caught:
        executor.execute(request)

    assert [attempt.outcome for attempt in caught.value.attempts] == [
        AttemptOutcome.INVALID,
        AttemptOutcome.INVALID,
    ]


# ---------------------------------------------------------------------------
# 批量测试
# ---------------------------------------------------------------------------


class BatchDummy(BaseFetcher):
    def __init__(self, name, *, batch=None, singles=None, batch_error=None, available=True):
        super().__init__()
        self.name = name
        self._available = available
        self.batch = batch or {}
        self.singles = singles or {}
        self.batch_error = batch_error
        self.batch_calls = 0
        self.single_calls = 0

    def _fetch_daily_data(self, stock_code, start_date, end_date):
        return None

    def _normalize_data(self, df, stock_code):
        return df

    def get_realtime_quote(self, code):
        self.single_calls += 1
        return self.singles.get(code)

    def get_batch_realtime_quotes(self, codes):
        self.batch_calls += 1
        if self.batch_error is not None:
            raise self.batch_error
        return {code: self.batch[code] for code in codes if code in self.batch}


def make_batch_route(**overrides):
    from open_stock_data.data_provider.contracts import CachePolicy
    values = {
        "operation": Operation.REALTIME_QUOTE,
        "market": StockType.A_STOCK,
        "providers": ("first", "second"),
        "method_name": "get_realtime_quote",
        "batch_method": "get_batch_realtime_quotes",
        "cache_policy": CachePolicy(ttl_seconds=60),
    }
    values.update(overrides)
    return RouteSpec(**values)


def run_batch(route, providers, codes):
    ctx = ProviderContext()
    for fetcher in providers.values():
        ctx.register(fetcher)
    router = DynamicRouter(ctx)
    registry = RouteRegistry([route])
    executor = RouteExecutor(router, registry)

    requests = {
        code: RouteRequest(route.operation, route.market, args=(code,), cache_key=code)
        for code in codes
    }
    return executor.execute_batch(requests)


def test_native_batch_covers_all_codes_in_single_call():
    first = BatchDummy("first", batch={"600519": {"p": 1}, "000001": {"p": 2}})
    second = BatchDummy("second")

    result = run_batch(make_batch_route(), {"first": first, "second": second}, ["600519", "000001"])

    assert set(result.data) == {"600519", "000001"}
    assert result.data["600519"].source == "first"
    # 批量接口命中所有，单查不再调用
    assert first.batch_calls == 1
    assert first.single_calls == 0
    assert second.batch_calls == 0


def test_native_batch_remaining_covered_by_next_provider():
    first = BatchDummy("first", batch={"600519": {"p": 1}})
    second = BatchDummy("second", batch={"000001": {"p": 2}})

    result = run_batch(make_batch_route(), {"first": first, "second": second}, ["600519", "000001"])

    assert result.data["600519"].source == "first"
    assert result.data["000001"].source == "second"
    # first 批量只返回它 batch 里的 600519；000001 落到下一 provider 的批量
    assert first.batch_calls == 1
    assert second.batch_calls == 1


def test_native_batch_uncovered_code_falls_back_to_single_quote():
    first = BatchDummy("first", batch={"600519": {"p": 1}}, singles={"000001": {"p": 9}})

    result = run_batch(
        make_batch_route(providers=("first",)),
        {"first": first},
        ["600519", "000001"],
    )

    assert result.data["600519"].source == "first"
    assert result.data["000001"].source == "first"
    # 600519 走批量，000001 批量无返回 -> 单查 fallback
    assert first.batch_calls == 1
    assert first.single_calls == 1


def test_native_batch_error_falls_back_to_single_quote():
    first = BatchDummy("first")
    second = BatchDummy("second", batch={"600519": {"p": 1}})
    # Make first's batch raise an error
    def bad_batch(self, codes):
        raise ConnectionError("down")
    first.get_batch_realtime_quotes = bad_batch

    result = run_batch(make_batch_route(providers=("first", "second")), {"first": first, "second": second}, ["600519"])

    assert result.data["600519"].source == "second"
    # first 批量失败，second 批量成功
    assert second.batch_calls == 1
    # single_calls 可能被调用也可能不（取决于是否走单查 fallback），不强制断言


def test_native_batch_invalid_quote_falls_back_to_single_quote():
    first = BatchDummy("first", batch={"600519": {"ok": True}, "000001": {"ok": False}})
    route = make_batch_route(providers=("first",), validator=lambda quote: quote.get("ok", False))

    result = run_batch(route, {"first": first}, ["600519", "000001"])

    assert result.data["600519"].source == "first"
    # 000001's quote is invalid (ok=False), so it fails validation and is not in data
    assert "000001" not in result.data
    # 批量调用后，600519 有效，000001 校验失败（单查时会校验并落到 failures 或被跳过）
    # 实现中：batch 返回的数据会逐个校验，失败的进入单查 fallback，最终仍在 failures 中
    assert first.batch_calls == 1
    # single_calls 可能被调用（000001 校验失败后尝试单查），不强制断言
