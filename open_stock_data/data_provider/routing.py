"""Static route registry and the single synchronous failover executor."""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable, Mapping, Optional

from ..cache import CacheStore
from ..exceptions import AllSourcesFailed, BatchIncomplete, RateLimitError, RouteNotFoundError
from .base import BaseFetcher, _is_network_error
from .circuit_breaker import get_circuit_breaker
from .contracts import (
    AttemptOutcome,
    FetchAttempt,
    BatchFetchResult,
    BatchItemFailure,
    FetchResult,
    Operation,
    RouteRequest,
    RouteSpec,
    utc_now,
)


@dataclass(frozen=True)
class _CacheEntry:
    data: Any
    source: str
    fetched_at: datetime
    stored_at: float


class RouteRegistry:
    """Immutable lookup table for operation/market routes."""

    def __init__(self, routes: Iterable[RouteSpec]):
        route_map: dict[tuple, RouteSpec] = {}
        for route in routes:
            if route.key in route_map:
                raise ValueError(f"duplicate route: {route.key}")
            route_map[route.key] = route
        self._routes = route_map

    def resolve(self, request: RouteRequest) -> RouteSpec:
        route = self._routes.get(request.key)
        if route is None:
            route = self._routes.get((request.operation, None))
        if route is None:
            raise RouteNotFoundError(
                f"未配置路由: operation={request.operation.value}, market={request.market}"
            )
        return route

    def all(self) -> tuple[RouteSpec, ...]:
        return tuple(self._routes.values())


class RouteExecutor:
    """Execute one fixed route with filtering, cache, and sequential failover."""

    _CACHE_SCHEMA = "v1"

    def __init__(
        self,
        providers: Mapping[str, BaseFetcher],
        routes: RouteRegistry,
        cache: Optional[CacheStore] = None,
    ):
        self._providers = dict(providers)
        self._routes = routes
        self._cache = cache

    def execute(self, request: RouteRequest) -> FetchResult[Any]:
        route = self._routes.resolve(request)
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

        for provider_name in route.providers:
            provider = self._providers.get(provider_name)
            if provider is None:
                attempts.append(self._skipped(provider_name, "not_registered"))
                continue
            if not provider.is_available:
                attempts.append(self._skipped(provider_name, "unavailable"))
                continue
            if not circuit_breaker.is_available(provider.name):
                attempts.append(self._skipped(provider.name, "circuit_open"))
                continue

            backend_scope = provider.get_backend_failure_scope(
                route.method_name, *request.args, **dict(request.kwargs)
            )
            if (
                route.skip_shared_backend_after_network_error
                and backend_scope
                and backend_scope in failed_backend_scopes
            ):
                attempts.append(self._skipped(provider.name, f"backend_failed:{backend_scope}"))
                continue

            started = time.monotonic()
            try:
                method = getattr(provider, route.method_name)
                data = method(*request.args, **dict(request.kwargs))
                latency_ms = (time.monotonic() - started) * 1000
                if self._is_empty(data) and route.empty_is_failure:
                    attempts.append(
                        FetchAttempt(provider.name, AttemptOutcome.EMPTY, latency_ms, "empty_result")
                    )
                    continue
                if route.validator is not None and not route.validator(data):
                    circuit_breaker.record_failure(provider.name, "invalid_result")
                    attempts.append(
                        FetchAttempt(provider.name, AttemptOutcome.INVALID, latency_ms, "invalid_result")
                    )
                    continue

                circuit_breaker.record_success(provider.name)
                attempts.append(FetchAttempt(provider.name, AttemptOutcome.SUCCESS, latency_ms))
                result = FetchResult(
                    data=data,
                    source=provider.name,
                    fetched_at=utc_now(),
                    attempts=tuple(attempts),
                )
                self._write_cache(cache_key, route, result)
                return result
            except RateLimitError as exc:
                latency_ms = (time.monotonic() - started) * 1000
                circuit_breaker.force_open(provider.name, exc.retry_after or 300, str(exc))
                attempts.append(
                    FetchAttempt(
                        provider.name,
                        AttemptOutcome.RATE_LIMITED,
                        latency_ms,
                        str(exc),
                        type(exc).__name__,
                    )
                )
            except Exception as exc:
                latency_ms = (time.monotonic() - started) * 1000
                circuit_breaker.record_failure(provider.name, str(exc))
                if _is_network_error(exc) and backend_scope:
                    failed_backend_scopes.add(backend_scope)
                attempts.append(
                    FetchAttempt(
                        provider.name,
                        AttemptOutcome.ERROR,
                        latency_ms,
                        str(exc),
                        type(exc).__name__,
                    )
                )

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

    def execute_batch(
        self,
        requests: Mapping[str, RouteRequest],
        *,
        strict: bool = False,
    ) -> BatchFetchResult[Any]:
        data: dict[str, FetchResult[Any]] = {}
        failures: dict[str, BatchItemFailure] = {}

        # 1. 先服务新鲜缓存命中，未命中的按路由分组
        grouped: dict[Any, tuple[RouteSpec, dict[str, RouteRequest]]] = {}
        for key, request in requests.items():
            route = self._routes.resolve(request)
            cached = self._fresh_cached_result(route, request)
            if cached is not None:
                data[key] = cached
                continue
            _, bucket = grouped.setdefault(route.key, (route, {}))
            bucket[key] = request

        # 2. 每个路由组：优先原生批量接口（remaining 追踪），未覆盖者逐票回退
        for route, bucket in grouped.values():
            remaining = dict(bucket)
            if route.batch_method:
                self._run_native_batch(route, remaining, data)
            for key, request in remaining.items():
                try:
                    data[key] = self.execute(request)
                except AllSourcesFailed as exc:
                    failures[key] = BatchItemFailure(
                        key=key,
                        error_type=type(exc).__name__,
                        message=str(exc),
                        attempts=exc.attempts,
                    )

        result = BatchFetchResult(data=data, failures=failures, fetched_at=utc_now())
        if not data:
            attempts = tuple(
                attempt
                for failure in failures.values()
                for attempt in failure.attempts
            )
            raise AllSourcesFailed(
                operation=Operation.BATCH_REALTIME_QUOTES.value,
                attempts=attempts,
                request={"items": tuple(requests)},
            )
        if strict and failures:
            raise BatchIncomplete(result)
        return result

    def _run_native_batch(
        self,
        route: RouteSpec,
        remaining: dict[str, RouteRequest],
        data: dict[str, FetchResult[Any]],
    ) -> None:
        """尽量用数据源原生批量接口一次覆盖多只代码，命中者移出 remaining。"""
        circuit_breaker = get_circuit_breaker(route.circuit_breaker)
        for provider_name in route.providers:
            if not remaining:
                return
            provider = self._providers.get(provider_name)
            if provider is None or not provider.is_available:
                continue
            if not circuit_breaker.is_available(provider.name):
                continue
            batch_fn = getattr(provider, route.batch_method, None)
            if not callable(batch_fn):
                continue

            codes = {key: request.args[0] for key, request in remaining.items()}
            try:
                quotes = batch_fn(list(dict.fromkeys(codes.values()))) or {}
            except RateLimitError as exc:
                circuit_breaker.force_open(provider.name, exc.retry_after or 300, str(exc))
                continue
            except Exception as exc:
                circuit_breaker.record_failure(provider.name, str(exc))
                continue

            covered = False
            for key, code in list(codes.items()):
                quote = quotes.get(code)
                if quote is None:
                    continue
                if route.validator is not None and not route.validator(quote):
                    continue
                covered = True
                request = remaining.pop(key)
                result = FetchResult(data=quote, source=provider.name, fetched_at=utc_now())
                data[key] = result
                self._write_cache(self._full_cache_key(route, request), route, result)
            if covered:
                circuit_breaker.record_success(provider.name)

    def _fresh_cached_result(
        self, route: RouteSpec, request: RouteRequest
    ) -> Optional[FetchResult[Any]]:
        cached, cache_age = self._read_cache(self._full_cache_key(route, request))
        if cached is None:
            return None
        cache_ttl = route.cache_policy.current_ttl() if route.cache_policy else 0.0
        if cache_age > cache_ttl:
            return None
        return FetchResult(
            data=cached.data,
            source=cached.source,
            fetched_at=cached.fetched_at,
            from_cache=True,
        )

    @staticmethod
    def _skipped(source: str, reason: str) -> FetchAttempt:
        return FetchAttempt(source, AttemptOutcome.SKIPPED, reason=reason)

    @staticmethod
    def _is_empty(value: Any) -> bool:
        if value is None:
            return True
        if hasattr(value, "empty"):
            return bool(value.empty)
        if isinstance(value, (list, tuple, dict, set)):
            return not value
        return False

    def _full_cache_key(self, route: RouteSpec, request: RouteRequest) -> Optional[str]:
        if self._cache is None or route.cache_policy is None or request.cache_key is None:
            return None
        market = request.market.value if request.market else "any"
        return f"route:{self._CACHE_SCHEMA}:{route.operation.value}:{market}:{request.cache_key}"

    def _read_cache(self, key: Optional[str]) -> tuple[Optional[_CacheEntry], float]:
        if key is None or self._cache is None:
            return None, 0.0
        entry = self._cache.get(key)
        if not isinstance(entry, _CacheEntry):
            return None, 0.0
        return entry, max(0.0, time.time() - entry.stored_at)

    def _write_cache(self, key: Optional[str], route: RouteSpec, result: FetchResult[Any]) -> None:
        if key is None or self._cache is None or route.cache_policy is None:
            return
        policy = route.cache_policy
        physical_ttl = policy.current_ttl() + policy.max_stale_seconds
        entry = _CacheEntry(result.data, result.source, result.fetched_at, time.time())
        self._cache.set(key, entry, expire=max(physical_ttl, 1.0))
