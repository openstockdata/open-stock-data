"""健康态降级/恢复与评分排序（P0 修复回归测试）。

覆盖：
- 连续失败达阈值后进入降级，冷却后半开、成功即恢复（不再永久剔除）
- 空结果（EMPTY）不计入健康失败
- priority 是主排序键：延迟不能压倒优先级，从未调用过的 provider 不会插队
- 降级 provider 排到最后但仍在候选列表中（兜底）
- AllSourcesFailed 在候选为空/被跳过时说明原因
"""

import pandas as pd
import pytest

from open_stock_data.data_provider.base import BaseFetcher
from open_stock_data.data_provider.context import ProviderContext
from open_stock_data.data_provider.contracts import (
    AttemptOutcome,
    FetchAttempt,
    Operation,
    RouteRequest,
    RouteSpec,
)
from open_stock_data.data_provider.dynamic_router import DynamicRouter
from open_stock_data.data_provider.plugin import ProviderHealth, ProviderHealthEvent
from open_stock_data.data_provider.routing import RouteExecutor, RouteRegistry
from open_stock_data.data_provider.stock_code import StockType
from open_stock_data.exceptions import AllSourcesFailed


class Dummy(BaseFetcher):
    def __init__(self, name, *, priority=50, result=None, error=None):
        super().__init__()
        self.name = name
        self.priority = priority
        self.result = result
        self.error = error
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


def _route(providers):
    return RouteSpec(Operation.DAILY_PRICES, StockType.A_STOCK, providers, "load")


def _executor(route, *fetchers):
    ctx = ProviderContext()
    for f in fetchers:
        ctx.register(f)
    router = DynamicRouter(ctx)
    return RouteExecutor(router, RouteRegistry([route])), ctx, router


def _request():
    return RouteRequest(Operation.DAILY_PRICES, StockType.A_STOCK, args=("600519",))


def _emit(ctx, name, success, latency_ms):
    ctx.emit(ProviderHealthEvent(source=name, success=success, latency_ms=latency_ms))


# 每个用例都用全新的 ProviderContext，无需全局重置。


# ---------------------------------------------------------------------------
# ProviderHealth 状态机
# ---------------------------------------------------------------------------


def test_health_opens_after_threshold_then_half_opens_after_cooldown():
    h = ProviderHealth(failure_threshold=3, cooldown_seconds=300)
    t0 = 1_000.0
    for _ in range(3):
        h.update_failure(100, now=t0)

    assert h.state_at(t0) == "OPEN"
    assert h.state_at(t0 + 299) == "OPEN"
    assert h.state_at(t0 + 300) == "HALF_OPEN"


def test_success_closes_health_regardless_of_previous_failures():
    h = ProviderHealth(failure_threshold=3, cooldown_seconds=300)
    for _ in range(5):
        h.update_failure(100, now=1_000.0)
    assert h.state_at(1_000.0) == "OPEN"

    h.update_success(50)

    assert h.failure_count == 0
    assert h.state_at(1_000.0) == "CLOSED"
    assert h.state_at(10_000.0) == "CLOSED"


def test_failure_during_half_open_reopens_for_another_cooldown():
    h = ProviderHealth(failure_threshold=3, cooldown_seconds=300)
    for _ in range(3):
        h.update_failure(100, now=1_000.0)
    assert h.state_at(1_400.0) == "HALF_OPEN"

    h.update_failure(100, now=1_400.0)

    assert h.state_at(1_400.0) == "OPEN"
    assert h.state_at(1_700.0) == "HALF_OPEN"


def test_health_score_is_bounded_and_never_positive():
    fresh = ProviderHealth()
    slow = ProviderHealth()
    slow.update_success(60_000)  # 60 s，超过封顶
    failing = ProviderHealth()
    failing.update_failure(0, now=1.0)

    assert fresh.score == 0.0
    assert -1.0 < slow.score < 0.0          # 延迟最多扣 LATENCY_WEIGHT，不足一个优先级档位
    assert -1.0 < failing.score < 0.0       # 单次失败只扣 0.5


def test_to_dict_exposes_derived_circuit_state():
    h = ProviderHealth(failure_threshold=1, cooldown_seconds=300)
    assert h.to_dict()["circuit_state"] == "CLOSED"
    h.update_failure(10)
    assert h.to_dict()["circuit_state"] == "OPEN"


# ---------------------------------------------------------------------------
# 排序：priority 主导
# ---------------------------------------------------------------------------


def test_priority_dominates_latency_and_untried_provider_does_not_jump_ahead():
    """复现日志场景：Tickflow 成功但慢、Efinance 失败一次、Pytdx 从未调用。"""
    tickflow = Dummy("tickflow", priority=10)
    efinance = Dummy("efinance", priority=5)
    pytdx = Dummy("pytdx", priority=1)
    ctx = ProviderContext()
    for f in (pytdx, efinance, tickflow):  # 故意乱序注册
        ctx.register(f)

    _emit(ctx, "tickflow", True, 1234)
    _emit(ctx, "efinance", False, 17_000)

    order = [p.metadata.name for p in ctx.get_by_priority(Operation.REALTIME_QUOTE)]
    assert order == ["tickflow", "efinance", "pytdx"]


def test_slow_success_does_not_drop_below_lower_priority_provider():
    akshare = Dummy("akshare", priority=4)
    baostock = Dummy("baostock", priority=3)
    ctx = ProviderContext()
    ctx.register(baostock)
    ctx.register(akshare)

    _emit(ctx, "akshare", True, 32_000)  # 32 s 的成功

    order = [p.metadata.name for p in ctx.get_by_priority(Operation.DAILY_PRICES)]
    assert order == ["akshare", "baostock"]


def test_health_breaks_ties_within_same_priority():
    a = Dummy("a", priority=9)
    b = Dummy("b", priority=9)
    ctx = ProviderContext()
    ctx.register(a)
    ctx.register(b)

    _emit(ctx, "a", True, 5_000)
    _emit(ctx, "b", True, 100)
    assert [p.metadata.name for p in ctx.get_by_priority(Operation.DAILY_PRICES)] == ["b", "a"]

    _emit(ctx, "b", False, 100)  # b 失败一次 → 掉到 a 之后
    assert [p.metadata.name for p in ctx.get_by_priority(Operation.DAILY_PRICES)] == ["a", "b"]


def test_open_provider_stays_listed_but_router_skips_it():
    """统一模型：OPEN 的 provider 仍在候选列表（排序只看分数），但路由执行时跳过。"""
    high = Dummy("high", priority=10)
    low = Dummy("low", priority=1)
    ctx = ProviderContext()
    ctx.register(high)
    ctx.register(low)

    for _ in range(3):
        _emit(ctx, "high", False, 100)

    # 排序只是分数：priority 仍主导，high 仍排前面…
    order = [p.metadata.name for p in ctx.get_by_priority(Operation.DAILY_PRICES)]
    assert order == ["high", "low"]
    assert ctx.health_snapshot["high"].circuit_state == "OPEN"
    # …但路由执行时 OPEN 被跳过（SKIPPED circuit_open）
    assert ctx.circuit_state("high") == "OPEN"


def test_set_priority_override_still_wins():
    a = Dummy("a", priority=1)
    b = Dummy("b", priority=10)
    ctx = ProviderContext()
    ctx.register(a)
    ctx.register(b)

    ctx.set_priority("a", 20)

    assert [p.metadata.name for p in ctx.get_by_priority(Operation.DAILY_PRICES)] == ["a", "b"]


# ---------------------------------------------------------------------------
# 路由层：空结果不算失败、降级可恢复、候选为空有原因
# ---------------------------------------------------------------------------


def test_empty_result_does_not_count_as_health_failure():
    first = Dummy("first", priority=10, result=None)
    second = Dummy("second", priority=5, result=pd.DataFrame([{"close": 1.0}]))
    executor, ctx, _ = _executor(_route(("first", "second")), first, second)

    for _ in range(5):
        result = executor.execute(_request())
        assert result.source == "second"
        assert result.attempts[0].outcome == AttemptOutcome.EMPTY

    health = ctx.health_snapshot["first"]
    assert health.failure_count == 0
    assert health.circuit_state == "CLOSED"
    assert first.calls == 5  # 一直排在首位被尝试，没有被剔除


def test_errors_degrade_provider_and_success_restores_it():
    first = Dummy("first", priority=10, error=RuntimeError("boom"))
    second = Dummy("second", priority=5, result=pd.DataFrame([{"close": 1.0}]))
    route = _route(("first", "second"))
    executor, ctx, router = _executor(route, first, second)

    for _ in range(3):
        assert executor.execute(_request()).source == "second"

    assert ctx.health_snapshot["first"].circuit_state == "OPEN"
    # 排序只是分数（priority 仍主导），跳过发生在执行时
    assert [p.metadata.name for p in router.resolve(route)] == ["first", "second"]

    # 冷却结束（模拟）→ 半开 → 允许探测
    ctx.health_snapshot["first"].cooldown_seconds = 0
    assert ctx.health_snapshot["first"].circuit_state == "HALF_OPEN"
    assert [p.metadata.name for p in router.resolve(route)] == ["first", "second"]

    # 探测成功 → CLOSED
    first.error = None
    first.result = pd.DataFrame([{"close": 2.0}])
    assert executor.execute(_request()).source == "first"
    assert ctx.health_snapshot["first"].circuit_state == "CLOSED"
    assert ctx.health_snapshot["first"].failure_count == 0


def test_open_provider_is_skipped_and_all_fail_reports_skips():
    """OPEN 的源在执行时被跳过；其余源也失败时 AllSourcesFailed 带 SKIPPED 记录。"""
    first = Dummy("first", priority=10, error=RuntimeError("boom"))
    second = Dummy("second", priority=5, result=pd.DataFrame([{"close": 1.0}]))
    route = _route(("first", "second"))
    executor, ctx, _ = _executor(route, first, second)

    for _ in range(3):
        executor.execute(_request())
    assert ctx.health_snapshot["first"].circuit_state == "OPEN"

    # 其余源也失败：first 被跳过、second 报错
    second.error = RuntimeError("also down")
    second.result = None

    with pytest.raises(AllSourcesFailed) as caught:
        executor.execute(_request())

    assert [(a.source, a.outcome) for a in caught.value.attempts] == [
        ("first", AttemptOutcome.SKIPPED),
        ("second", AttemptOutcome.ERROR),
    ]


def test_route_without_registered_candidates_explains_itself():
    only = Dummy("first", priority=10, result=pd.DataFrame([{"close": 1.0}]))
    executor, _, _ = _executor(_route(("ghost",)), only)

    with pytest.raises(AllSourcesFailed) as caught:
        executor.execute(_request())

    assert caught.value.attempts == ()
    assert "ghost" in caught.value.request["reason"]
    assert "未发起任何尝试" in str(caught.value)
    assert "ghost" in str(caught.value)


def test_all_sources_failed_message_includes_outcome_and_skip_reason():
    exc = AllSourcesFailed(
        "realtime_quote",
        [
            FetchAttempt("EfinanceFetcher", AttemptOutcome.SKIPPED, reason="circuit_open"),
            FetchAttempt("AkshareFetcher", AttemptOutcome.ERROR, reason="down"),
        ],
    )
    text = str(exc)
    assert "EfinanceFetcher(skipped:circuit_open)" in text
    assert "AkshareFetcher(error)" in text
