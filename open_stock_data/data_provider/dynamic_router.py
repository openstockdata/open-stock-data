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
from .circuit_breaker import get_circuit_breaker
from ..cache import CacheStore, _CacheEntry
from ..exceptions import AllSourcesFailed, RateLimitError
from .base import _is_network_error

_LOGGER = logging.getLogger(__name__)


class DynamicRouter:
    """自适应路由器：每次执行从 ProviderContext 动态计算最优 provider 顺序。

    核心逻辑：
    1. 从 ProviderContext.get_by_priority(operation) 获取排序后的 provider 列表
    2. 按顺序尝试，熔断的跳过
    3. 每次尝试后发射 ProviderHealthEvent
    4. 失败触发 context.emit()，自动重算优先级
    """

    _CACHE_SCHEMA = "v1"

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
        cache_ttl = route.cache_policy.current_ttl() if route.cache_policy else 0.0
        cache_key = self._full_cache_key(route, request)
        cached, cache_age = self._read_cache(cache_key)

        if cached is not None and cache_age <= cache_ttl:
            return FetchResult(
                data=cached.data,
                source=cached.source,
                fetched_at=cached.fetched_at,
                from_cache=True,
            )

        attempts: list[FetchAttempt] = []
        failed_backend_scopes: set[str] = set()
        circuit_breaker = get_circuit_breaker(route.circuit_breaker)

        for provider in providers:
            provider_name = provider.metadata.name
            if not provider.is_available:
                attempts.append(FetchAttempt(provider_name, AttemptOutcome.SKIPPED, reason="unavailable"))
                continue
            if not circuit_breaker.is_available(provider_name):
                attempts.append(FetchAttempt(provider_name, AttemptOutcome.SKIPPED, reason="circuit_open"))
                continue

            backend_scope = self._get_backend_scope(provider, route.method_name, *request.args, **dict(request.kwargs))
            if (
                route.skip_shared_backend_after_network_error
                and backend_scope
                and backend_scope in failed_backend_scopes
            ):
                attempts.append(FetchAttempt(provider_name, AttemptOutcome.SKIPPED, reason=f"backend_failed:{backend_scope}"))
                continue

            started = time.monotonic()
            try:
                data = provider.execute(route.method_name, *request.args, **dict(request.kwargs))
                latency_ms = (time.monotonic() - started) * 1000

                if self._is_empty(data) and route.empty_is_failure:
                    attempts.append(FetchAttempt(provider_name, AttemptOutcome.EMPTY, latency_ms, "empty_result"))
                    self._emit_health(provider_name, False, latency_ms)
                    continue
                if route.validator is not None and not route.validator(data):
                    circuit_breaker.record_failure(provider_name, "invalid_result")
                    attempts.append(FetchAttempt(provider_name, AttemptOutcome.INVALID, latency_ms, "invalid_result"))
                    self._emit_health(provider_name, False, latency_ms)
                    continue

                circuit_breaker.record_success(provider_name)
                attempts.append(FetchAttempt(provider_name, AttemptOutcome.SUCCESS, latency_ms))
                self._emit_health(provider_name, True, latency_ms)

                result = FetchResult(
                    data=data,
                    source=provider_name,
                    fetched_at=utc_now(),
                    attempts=tuple(attempts),
                )
                self._write_cache(cache_key, route, result)
                self._run_persist(route, data, request, provider_name)
                return result

            except RateLimitError as exc:
                latency_ms = (time.monotonic() - started) * 1000
                circuit_breaker.force_open(provider_name, exc.retry_after or 300, str(exc))
                attempts.append(FetchAttempt(provider_name, AttemptOutcome.RATE_LIMITED, latency_ms, str(exc), type(exc).__name__))
                self._emit_health(provider_name, False, latency_ms)

            except Exception as exc:
                latency_ms = (time.monotonic() - started) * 1000
                circuit_breaker.record_failure(provider_name, str(exc))
                if _is_network_error(exc) and backend_scope:
                    failed_backend_scopes.add(backend_scope)
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

        raise AllSourcesFailed(
            operation=request.operation.value,
            attempts=attempts,
            request={"market": request.market.value if request.market else None},
        )

    def _emit_health(self, source: str, success: bool, latency_ms: float) -> None:
        """发射健康事件。"""
        self._context.emit(ProviderHealthEvent(source=source, success=success, latency_ms=latency_ms))

    def _get_backend_scope(self, provider, method_name: str, *args, **kwargs) -> Optional[str]:
        if hasattr(provider, 'backend_group') and provider.backend_group:
            return f"{provider.backend_group}:{method_name}"
        return None

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
