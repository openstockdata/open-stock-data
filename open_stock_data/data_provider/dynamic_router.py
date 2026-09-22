"""DynamicRouter：基于 ProviderContext 的自适应路由器。

替代静态 RouteRegistry 的 provider 列表，每次执行时从 ProviderContext
实时获取按健康分排序的最优 fallback 顺序。
"""

from __future__ import annotations

import time
import logging
from typing import Any, Optional

from .context import ProviderContext
from .contracts import RouteSpec, RouteRequest, FetchResult, FetchAttempt, AttemptOutcome, CachePolicy, ResultValidator, PersistHook, utc_now
from .plugin import ProviderHealthEvent
from ..cache import CacheStore, _CacheEntry
from ..exceptions import AllSourcesFailed, RateLimitError

_LOGGER = logging.getLogger(__name__)


class DynamicRouter:
    """自适应路由器：每次执行从 ProviderContext 动态计算最优 provider 顺序。

    核心逻辑：
    1. 从 ProviderContext.get_by_priority(operation) 获取排序后的 provider 列表
    2. 按顺序尝试；已熔断（OPEN）的跳过，HALF_OPEN 允许探测
    3. 每次尝试后发射 ProviderHealthEvent（唯一健康事实来源）
    4. 失败触发 context.emit()，连续失败达阈值自动熔断
    """

    # 路由缓存键 schema：belong_board 统一 schema 上线后旧格式帧（股票代码/行业列，
    # 无 板块名称）仍在 7 天 TTL 内，bump 到 v2 使其整体失效。本地长期存储的旧事实
    # 由 client.belong_board 读取时归一化兜底。
    _CACHE_SCHEMA = "v2"
    _RATE_LIMIT_DEFAULT_COOLDOWN = 300.0

    def __init__(self, context: ProviderContext):
        self._context = context

    def resolve(self, route: RouteSpec) -> list:
        """返回按健康分排序的 provider 列表。

        如果 RouteSpec 定义了 providers，则仅从这些 provider 中选择。
        """
        all_providers = self._context.get_by_priority(route.operation)
        if route.providers:
            provider_names = set(route.providers)
            all_providers = [p for p in all_providers if p.metadata.name in provider_names]
        return all_providers

    def execute(self, request: RouteRequest, route: RouteSpec) -> FetchResult:
        """执行路由：使用动态 provider 顺序。"""
        providers = self.resolve(route)
        fresh = self.read_fresh_cache(request, route)
        if fresh is not None:
            return fresh
        # 仅供失败时的 stale-on-error 降级（新鲜命中已在上面返回）
        cached, cache_age = self._read_cache(self._full_cache_key(route, request))

        attempts: list[FetchAttempt] = []

        for provider in providers:
            provider_name = provider.metadata.name
            if not provider.is_available:
                attempts.append(FetchAttempt(provider_name, AttemptOutcome.SKIPPED, reason="unavailable"))
                continue
            if self._context.circuit_state(provider_name) == "OPEN":
                attempts.append(FetchAttempt(provider_name, AttemptOutcome.SKIPPED, reason="circuit_open"))
                continue

            started = time.monotonic()
            try:
                data = provider.execute(route.method_name, *request.args, **dict(request.kwargs))
                latency_ms = (time.monotonic() - started) * 1000

                if self._is_empty(data) and route.empty_is_failure:
                    # 空结果只说明该源没有这份数据（本地未命中、板块不存在、配额冷却…），
                    # 不是故障：记 EMPTY 并回退到下一源，不计入健康分，也不触发熔断。
                    # 配额冷却的原因经 attrs / fetcher 实例透出，记入 reason 供 AllSourcesFailed 排查。
                    attempts.append(FetchAttempt(provider_name, AttemptOutcome.EMPTY, latency_ms, self._empty_reason(data, provider)))
                    continue
                if route.validator is not None and not route.validator(data):
                    attempts.append(FetchAttempt(provider_name, AttemptOutcome.INVALID, latency_ms, "invalid_result"))
                    self._emit_health(provider_name, False, latency_ms)
                    continue

                attempts.append(FetchAttempt(provider_name, AttemptOutcome.SUCCESS, latency_ms))
                self._emit_health(provider_name, True, latency_ms)

                result = FetchResult(
                    data=data,
                    source=provider_name,
                    fetched_at=utc_now(),
                    attempts=tuple(attempts),
                )
                self.store_result(request, route, result)
                self._run_persist(route, data, request, provider_name)
                return result

            except RateLimitError as exc:
                latency_ms = (time.monotonic() - started) * 1000
                cooldown = exc.retry_after or self._RATE_LIMIT_DEFAULT_COOLDOWN
                self._context.block_provider(provider_name, cooldown, str(exc))
                attempts.append(FetchAttempt(provider_name, AttemptOutcome.RATE_LIMITED, latency_ms, str(exc), type(exc).__name__))
                self._emit_health(provider_name, False, latency_ms)

            except Exception as exc:
                latency_ms = (time.monotonic() - started) * 1000
                attempts.append(FetchAttempt(provider_name, AttemptOutcome.ERROR, latency_ms, str(exc), type(exc).__name__))
                self._emit_health(provider_name, False, latency_ms)

        if (
            cached is not None
            and route.cache_policy is not None
            and route.cache_policy.allow_stale_on_error
            and cache_age <= cache_ttl + route.cache_policy.max_stale_seconds
        ):
            return FetchResult(
                data=cached.data,
                source=cached.source,
                fetched_at=cached.fetched_at,
                from_cache=True,
                is_stale=True,
                attempts=tuple(attempts),
            )

        request_info: dict[str, Any] = {"market": request.market.value if request.market else None}
        if not providers:
            # 候选为空通常是路由里的 provider 名称与注册名不一致，或 provider 初始化失败；
            # 写明原因，避免只看到一条空的 "sources=" 无从排查。
            request_info["reason"] = (
                f"无可用候选数据源: route.providers={list(route.providers)}, "
                f"registered={self._context.provider_names}"
            )
            _LOGGER.warning("[router] %s %s", route.operation.value, request_info["reason"])
        raise AllSourcesFailed(
            operation=request.operation.value,
            attempts=attempts,
            request=request_info,
        )

    def _emit_health(self, source: str, success: bool, latency_ms: float) -> None:
        """发射健康事件。"""
        self.report_health(source, success, latency_ms)

    def circuit_state(self, name: str) -> str:
        """provider 当前熔断状态（供 RouteExecutor 批量路径复用同一判定）。"""
        return self._context.circuit_state(name)

    def report_health(self, source: str, success: bool, latency_ms: float) -> None:
        """发射健康事件（供 RouteExecutor 批量路径上报批量调用的成败）。"""
        self._context.emit(ProviderHealthEvent(source=source, success=success, latency_ms=latency_ms))

    def read_fresh_cache(self, request: RouteRequest, route: RouteSpec) -> Optional[FetchResult]:
        """TTL 内的路由缓存命中（供单查与批量前置过滤复用）。"""
        if route.cache_policy is None or request.cache_key is None:
            return None
        cached, cache_age = self._read_cache(self._full_cache_key(route, request))
        if cached is not None and cache_age <= route.cache_policy.current_ttl():
            return FetchResult(
                data=cached.data,
                source=cached.source,
                fetched_at=cached.fetched_at,
                from_cache=True,
            )
        return None

    def store_result(self, request: RouteRequest, route: RouteSpec, result: FetchResult) -> None:
        """写入路由缓存（供批量覆盖项回填，与单查成功路径一致）。"""
        self._write_cache(self._full_cache_key(route, request), route, result)

    @staticmethod
    def _empty_reason(data, provider=None) -> str:
        """从空结果上提取可排查原因（如 tushare 配额冷却），无则用默认值。

        双通道：DataFrame 的 attrs["empty_reason"]（主）；
        标量返回（实时行情等无法挂 attrs）读 fetcher 的 _last_empty_reason（备）。
        """
        attrs = getattr(data, "attrs", None)
        if isinstance(attrs, dict):
            reason = attrs.get("empty_reason") or attrs.get("quota_reason")
            if reason:
                return str(reason)
        if provider is not None:
            fallback = getattr(provider, "_last_empty_reason", "") or ""
            if fallback:
                return str(fallback)
        return "empty_result"

    @staticmethod
    def _is_empty(value) -> bool:
        if value is None:
            return True
        if hasattr(value, "empty"):
            return bool(value.empty)
        if isinstance(value, (list, tuple, dict, set)):
            return not value
        return False

    def _full_cache_key(self, route: RouteSpec, request: RouteRequest) -> Optional[str]:
        if route.cache_policy is None or request.cache_key is None:
            return None
        market = request.market.value if request.market else "any"
        return f"route:{self._CACHE_SCHEMA}:{route.operation.value}:{market}:{request.cache_key}"

    def _read_cache(self, key: Optional[str]) -> tuple[Optional[_CacheEntry], float]:
        if key is None:
            return None, 0.0
        entry = CacheStore.get_store("routed_data").get(key)
        if not isinstance(entry, _CacheEntry):
            return None, 0.0
        import time as _time
        return entry, max(0.0, _time.time() - entry.stored_at)

    def _write_cache(self, key: Optional[str], route: RouteSpec, result: FetchResult) -> None:
        if key is None or route.cache_policy is None:
            return
        policy = route.cache_policy
        physical_ttl = policy.current_ttl() + policy.max_stale_seconds
        entry = _CacheEntry(result.data, result.source, result.fetched_at, time.time())
        CacheStore.get_store("routed_data").set(key, entry, expire=max(physical_ttl, 1.0))

    @staticmethod
    def _run_persist(route: RouteSpec, data: Any, request: RouteRequest, source: str) -> None:
        if route.persist is None:
            return
        try:
            route.persist(data, request, source)
        except Exception:
            _LOGGER.warning("[persist] %s 回写失败", route.operation.value)
