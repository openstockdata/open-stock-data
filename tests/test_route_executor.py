import pandas as pd
import pytest

from open_stock_data.cache import CacheStore
from open_stock_data.data_provider.base import BaseFetcher
from open_stock_data.data_provider.circuit_breaker import get_circuit_breaker
from open_stock_data.data_provider.contracts import (
    AttemptOutcome,
    CachePolicy,
    Operation,
    RouteRequest,
    RouteSpec,
)
from open_stock_data.data_provider.routing import RouteExecutor, RouteRegistry
from open_stock_data.data_provider.stock_code import StockType
from open_stock_data.exceptions import AllSourcesFailed, RouteNotFoundError


class DummyFetcher(BaseFetcher):
    def __init__(self, name, *, result=None, error=None, available=True, backend_group=""):
        super().__init__()
        self.name = name
        self.result = result
        self.error = error
        self._available = available
        self.backend_group = backend_group
        self.calls = 0

    def _fetch_raw_data(self, stock_code, start_date, end_date):
        return None

    def _normalize_data(self, df, stock_code):
        return df

    def load(self, symbol):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.result


class MemoryCache:
    def __init__(self):
        self.values = {}

    def get(self, key, default=None):
        return self.values.get(key, default)

    def set(self, key, value, expire=None):
        self.values[key] = value


def make_route(**overrides):
    values = {
        "operation": Operation.DAILY_PRICES,
        "market": StockType.A_STOCK,
        "providers": ("first", "second"),
        "method_name": "load",
        "circuit_breaker": "daily",
    }
    values.update(overrides)
    return RouteSpec(**values)


@pytest.fixture(autouse=True)
def reset_breakers():
    for name in ("daily", "realtime", "spot"):
        get_circuit_breaker(name).reset()
    yield
    for name in ("daily", "realtime", "spot"):
        get_circuit_breaker(name).reset()


def execute(route, providers, *, cache=None, cache_key=None):
    request = RouteRequest(
        operation=route.operation,
        market=route.market,
        args=("600519",),
        cache_key=cache_key,
    )
    return RouteExecutor(providers, RouteRegistry([route]), cache=cache).execute(request)


def test_fixed_order_skips_unavailable_provider():
    first = DummyFetcher("first", available=False)
    expected = pd.DataFrame([{"close": 10.0}])
    second = DummyFetcher("second", result=expected)

    result = execute(make_route(), {"first": first, "second": second})

    assert result.data is expected
    assert result.source == "second"
    assert [attempt.outcome for attempt in result.attempts] == [
        AttemptOutcome.SKIPPED,
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


def test_network_failure_skips_provider_with_same_backend_scope():
    first = DummyFetcher("first", error=ConnectionError("down"), backend_group="shared")
    second = DummyFetcher("second", result={"ok": True}, backend_group="shared")
    route = make_route()

    with pytest.raises(AllSourcesFailed) as caught:
        execute(route, {"first": first, "second": second})

    assert [attempt.outcome for attempt in caught.value.attempts] == [
        AttemptOutcome.ERROR,
        AttemptOutcome.SKIPPED,
    ]
    assert second.calls == 0


def test_network_failure_does_not_skip_provider_with_different_backend_scope():
    first = DummyFetcher("first", error=ConnectionError("down"), backend_group="scope_a")
    second = DummyFetcher("second", result={"ok": True}, backend_group="scope_b")
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


def test_fresh_cache_bypasses_providers():
    cache = MemoryCache()
    provider = DummyFetcher("first", result={"value": 1})
    route = make_route(
        providers=("first",),
        cache_policy=CachePolicy(ttl_seconds=60),
    )

    first_result = execute(route, {"first": provider}, cache=cache, cache_key="600519")
    second_result = execute(route, {"first": provider}, cache=cache, cache_key="600519")

    assert first_result.from_cache is False
    assert second_result.from_cache is True
    assert second_result.is_stale is False
    assert provider.calls == 1


def test_low_frequency_route_can_return_stale_data_after_failure():
    cache = MemoryCache()
    provider = DummyFetcher("first", result={"value": 1})
    route = make_route(
        providers=("first",),
        cache_policy=CachePolicy(
            ttl_seconds=0,
            allow_stale_on_error=True,
            max_stale_seconds=60,
        ),
    )
    execute(route, {"first": provider}, cache=cache, cache_key="600519")
    provider.error = ConnectionError("down")

    result = execute(route, {"first": provider}, cache=cache, cache_key="600519")

    assert result.from_cache is True
    assert result.is_stale is True
    assert result.data == {"value": 1}
    assert result.attempts[0].outcome == AttemptOutcome.ERROR


def test_route_without_stale_policy_raises_after_expiry():
    cache = MemoryCache()
    provider = DummyFetcher("first", result={"value": 1})
    route = make_route(
        providers=("first",),
        cache_policy=CachePolicy(ttl_seconds=0),
    )
    execute(route, {"first": provider}, cache=cache, cache_key="600519")
    provider.error = ConnectionError("down")

    with pytest.raises(AllSourcesFailed):
        execute(route, {"first": provider}, cache=cache, cache_key="600519")


def test_missing_route_is_explicit_error():
    request = RouteRequest(Operation.REALTIME_QUOTE, StockType.US)

    with pytest.raises(RouteNotFoundError):
        RouteRegistry([]).resolve(request)


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
    cache = MemoryCache()
    first = DummyFetcher("first", result=_partial_frame())
    second = DummyFetcher("second", result=_complete_frame())
    route = make_route(
        operation=Operation.A_STOCK_SNAPSHOT,
        providers=("first", "second"),
        validator=_spot_complete,
        cache_policy=CachePolicy(ttl_seconds=60),
    )

    result = execute(route, {"first": first, "second": second}, cache=cache, cache_key="all")

    assert result.source == "second"
    assert [attempt.outcome for attempt in result.attempts] == [
        AttemptOutcome.INVALID,
        AttemptOutcome.SUCCESS,
    ]
    cached_entries = list(cache.values.values())
    assert len(cached_entries) == 1
    assert bool(cached_entries[0].data.attrs.get("spot_complete")) is True


def test_snapshot_all_partial_fails_and_nothing_cached():
    cache = MemoryCache()
    first = DummyFetcher("first", result=_partial_frame())
    second = DummyFetcher("second", result=_partial_frame())
    route = make_route(
        operation=Operation.A_STOCK_SNAPSHOT,
        providers=("first", "second"),
        validator=_spot_complete,
        cache_policy=CachePolicy(ttl_seconds=60),
    )

    with pytest.raises(AllSourcesFailed) as caught:
        execute(route, {"first": first, "second": second}, cache=cache, cache_key="all")

    assert [attempt.outcome for attempt in caught.value.attempts] == [
        AttemptOutcome.INVALID,
        AttemptOutcome.INVALID,
    ]
    assert cache.values == {}


# ---------------------------------------------------------------------------
# 原生批量执行器
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

    def _fetch_raw_data(self, stock_code, start_date, end_date):
        return None

    def _normalize_data(self, df, stock_code):
        return df

    def get_realtime_quote(self, code):
        self.single_calls += 1
        value = self.singles.get(code)
        if isinstance(value, Exception):
            raise value
        return value

    def get_batch_realtime_quotes(self, codes):
        self.batch_calls += 1
        if self.batch_error is not None:
            raise self.batch_error
        return {code: self.batch[code] for code in codes if code in self.batch}


def make_batch_route(**overrides):
    values = {
        "operation": Operation.REALTIME_QUOTE,
        "market": StockType.A_STOCK,
        "providers": ("first", "second"),
        "method_name": "get_realtime_quote",
        "circuit_breaker": "realtime",
        "batch_method": "get_batch_realtime_quotes",
    }
    values.update(overrides)
    return RouteSpec(**values)


def run_batch(route, providers, codes, *, cache=None, strict=False):
    requests = {
        code: RouteRequest(route.operation, route.market, args=(code,), cache_key=code)
        for code in codes
    }
    executor = RouteExecutor(providers, RouteRegistry([route]), cache=cache)
    return executor.execute_batch(requests, strict=strict)


def test_native_batch_covers_all_codes_in_single_call():
    first = BatchDummy("first", batch={"600519": {"p": 1}, "000001": {"p": 2}})
    second = BatchDummy("second")

    result = run_batch(make_batch_route(), {"first": first, "second": second}, ["600519", "000001"])

    assert set(result.data) == {"600519", "000001"}
    assert result.data["600519"].source == "first"
    assert first.batch_calls == 1
    assert first.single_calls == 0
    assert second.batch_calls == 0


def test_native_batch_remaining_covered_by_next_provider():
    first = BatchDummy("first", batch={"600519": {"p": 1}})
    second = BatchDummy("second", batch={"000001": {"p": 2}})

    result = run_batch(make_batch_route(), {"first": first, "second": second}, ["600519", "000001"])

    assert result.data["600519"].source == "first"
    assert result.data["000001"].source == "second"
    assert first.batch_calls == 1 and second.batch_calls == 1
    assert first.single_calls == 0 and second.single_calls == 0


def test_native_batch_uncovered_code_falls_back_to_single_quote():
    first = BatchDummy("first", batch={"600519": {"p": 1}}, singles={"000001": {"p": 9}})

    result = run_batch(
        make_batch_route(providers=("first",)),
        {"first": first},
        ["600519", "000001"],
    )

    assert result.data["600519"].source == "first"
    assert result.data["000001"].source == "first"
    assert first.batch_calls == 1
    assert first.single_calls == 1


def test_native_batch_error_falls_back_to_single_quote():
    first = BatchDummy(
        "first",
        batch_error=ConnectionError("batch down"),
        singles={"600519": {"p": 1}},
    )

    result = run_batch(make_batch_route(providers=("first",)), {"first": first}, ["600519"])

    assert result.data["600519"].source == "first"
    assert first.batch_calls == 1
    assert first.single_calls == 1


def test_native_batch_invalid_quote_falls_back_to_single_quote():
    first = BatchDummy(
        "first",
        batch={"600519": {"ok": True}, "000001": {"ok": False}},
        singles={"000001": {"ok": True}},
    )
    route = make_batch_route(providers=("first",), validator=lambda quote: quote.get("ok", False))

    result = run_batch(route, {"first": first}, ["600519", "000001"])

    assert result.data["600519"].source == "first"
    assert result.data["000001"].data == {"ok": True}
    assert first.single_calls == 1


def test_native_batch_result_is_cached_per_code():
    cache = MemoryCache()
    first = BatchDummy("first", batch={"600519": {"p": 1}})
    route = make_batch_route(providers=("first",), cache_policy=CachePolicy(ttl_seconds=60))

    run_batch(route, {"first": first}, ["600519"], cache=cache)
    # 第二次批量：应命中缓存，不再触发原生批量调用
    result = run_batch(route, {"first": first}, ["600519"], cache=cache)

    assert result.data["600519"].from_cache is True
    assert first.batch_calls == 1
