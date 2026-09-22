"""Route registry and thin executor wrapper.

RouteRegistry provides static route definitions (operation → method/validator/cache_policy).
RouteExecutor is a thin wrapper: uses RouteRegistry for metadata lookup
and DynamicRouter for provider selection and failover logic.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Mapping

from ..exceptions import AllSourcesFailed, BatchIncomplete, RateLimitError, RouteNotFoundError
from .contracts import (
    FetchResult,
    FetchAttempt,
    AttemptOutcome,
    Operation,
    RouteRequest,
    RouteSpec,
    BatchFetchResult,
    BatchItemFailure,
    utc_now,
)
from .context import ProviderContext
from .dynamic_router import DynamicRouter
from .providers import create_default_providers

_LOGGER = logging.getLogger(__name__)


class RouteRegistry:
    """Static route definition table: operation/market → RouteSpec."""

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
    """Thin wrapper: RouteRegistry for metadata, DynamicRouter for execution."""

    def __init__(self, router: DynamicRouter, registry: RouteRegistry):
        self._router = router
        self._registry = registry

    def execute(self, request: RouteRequest) -> FetchResult[Any]:
        route = self._registry.resolve(request)
        return self._router.execute(request, route)

    def execute_batch(
        self,
        requests: Mapping[str, RouteRequest],
        *,
        strict: bool = False,
    ) -> BatchFetchResult[Any]:
        """Batch execution: try batch_method per provider first, then single-query fallback."""
        # All requests must share the same route (operation/market); resolve once.
        any_request = next(iter(requests.values()))
        route = self._registry.resolve(any_request)

        # 1) Filter out symbols already in fresh cache (per-symbol cache_key).
        remaining: dict[str, RouteRequest] = {}
        cached_results: dict[str, FetchResult[Any]] = {}
        for key, req in requests.items():
            fresh = self._router.read_fresh_cache(req, route)
            if fresh is not None:
                cached_results[key] = fresh
            else:
                remaining[key] = req

        if not remaining:
            return BatchFetchResult(data=cached_results, failures={}, fetched_at=utc_now())

        # 2) Provider order for this operation.
        providers = self._router.resolve(route)
        batch_method = route.batch_method

        # 3) Try batch_method on each provider (if available) for the remaining symbols.
        covered: set[str] = set()
        attempts_per_symbol: dict[str, list] = {k: [] for k in remaining}

        for provider in providers:
            provider_name = provider.metadata.name
            if not provider.is_available:
                for k in remaining:
                    attempts_per_symbol[k].append(
                        FetchAttempt(provider_name, AttemptOutcome.SKIPPED, reason="unavailable")
                    )
                continue
            if self._router.circuit_state(provider_name) == "OPEN":
                for k in remaining:
                    attempts_per_symbol[k].append(
                        FetchAttempt(provider_name, AttemptOutcome.SKIPPED, reason="circuit_open")
                    )
                continue

            # Collect symbols not yet covered and not from cache
            pending = {k: remaining[k] for k in remaining if k not in covered}
            if not pending:
                break

            if batch_method and hasattr(provider, batch_method):
                try:
                    batch_fn = getattr(provider, batch_method)
                    started = time.monotonic()
                    batch_result = batch_fn(list(pending.keys()))
                    latency_ms = (time.monotonic() - started) * 1000

                    if batch_result:
                        # Record SUCCESS for each code the batch returned.
                        for code, result_data in batch_result.items():
                            if code in pending:
                                # Run validator on batch item (same as single-query path)
                                if route.validator is not None and not route.validator(result_data):
                                    attempts_per_symbol[code].append(
                                        FetchAttempt(provider_name, AttemptOutcome.INVALID, latency_ms, "invalid_result")
                                    )
                                    self._router.report_health(provider_name, False, latency_ms)
                                    continue
                                covered.add(code)
                                attempts_per_symbol[code].append(
                                    FetchAttempt(provider_name, AttemptOutcome.SUCCESS, latency_ms)
                                )
                                # Wrap batch item into FetchResult and cache it.
                                fr = FetchResult(
                                    data=result_data,
                                    source=provider_name,
                                    fetched_at=utc_now(),
                                    attempts=tuple(attempts_per_symbol[code]),
                                )
                                # Cache per-symbol for future single queries
                                self._router.store_result(pending[code], route, fr)
                                # Run persist hook (same as single-query path)
                                self._run_persist(route, result_data, pending[code], provider_name)
                                self._router.report_health(provider_name, True, latency_ms)
                    # Codes not in batch_result stay in pending → will fall back to single-query
                except RateLimitError as exc:
                    latency_ms = (time.monotonic() - started) * 1000 if 'started' in locals() else 0
                    cooldown = exc.retry_after or self._router._RATE_LIMIT_DEFAULT_COOLDOWN
                    self._router._context.block_provider(provider_name, cooldown, str(exc))
                    for code in pending:
                        attempts_per_symbol[code].append(
                            FetchAttempt(provider_name, AttemptOutcome.RATE_LIMITED, latency_ms, str(exc), type(exc).__name__)
                        )
                    self._router.report_health(provider_name, False, latency_ms)
                except Exception as exc:
                    latency_ms = (time.monotonic() - started) * 1000 if 'started' in locals() else 0
                    for code in pending:
                        attempts_per_symbol[code].append(
                            FetchAttempt(provider_name, AttemptOutcome.ERROR, latency_ms, str(exc), type(exc).__name__)
                        )
                    self._router.report_health(provider_name, False, latency_ms)
            # If no batch_method or batch failed, continue to next provider (single-query fallback later)

        # 4) Single-query fallback for any still-uncovered symbols (same loop as old execute, but per-symbol).
        for code in remaining:
            if code in covered:
                continue
            req = remaining[code]
            for provider in providers:
                provider_name = provider.metadata.name
                if not provider.is_available:
                    attempts_per_symbol[code].append(
                        FetchAttempt(provider_name, AttemptOutcome.SKIPPED, reason="unavailable")
                    )
                    continue
                if self._router.circuit_state(provider_name) == "OPEN":
                    attempts_per_symbol[code].append(
                        FetchAttempt(provider_name, AttemptOutcome.SKIPPED, reason="circuit_open")
                    )
                    continue

                started = time.monotonic()
                try:
                    data = provider.execute(route.method_name, *req.args, **dict(req.kwargs))
                    latency_ms = (time.monotonic() - started) * 1000

                    if self._router._is_empty(data) and route.empty_is_failure:
                        attempts_per_symbol[code].append(
                            FetchAttempt(provider_name, AttemptOutcome.EMPTY, latency_ms, self._router._empty_reason(data, provider))
                        )
                        continue
                    if route.validator is not None and not route.validator(data):
                        attempts_per_symbol[code].append(
                            FetchAttempt(provider_name, AttemptOutcome.INVALID, latency_ms, "invalid_result")
                        )
                        self._router.report_health(provider_name, False, latency_ms)
                        continue

                    attempts_per_symbol[code].append(FetchAttempt(provider_name, AttemptOutcome.SUCCESS, latency_ms))
                    self._router.report_health(provider_name, True, latency_ms)

                    result = FetchResult(
                        data=data,
                        source=provider_name,
                        fetched_at=utc_now(),
                        attempts=tuple(attempts_per_symbol[code]),
                    )
                    self._router.store_result(req, route, result)
                    self._run_persist(route, data, req, provider_name)
                    covered.add(code)
                    break

                except RateLimitError as exc:
                    latency_ms = (time.monotonic() - started) * 1000
                    cooldown = exc.retry_after or self._router._RATE_LIMIT_DEFAULT_COOLDOWN
                    self._router._context.block_provider(provider_name, cooldown, str(exc))
                    attempts_per_symbol[code].append(
                        FetchAttempt(provider_name, AttemptOutcome.RATE_LIMITED, latency_ms, str(exc), type(exc).__name__)
                    )
                    self._router.report_health(provider_name, False, latency_ms)

                except Exception as exc:
                    latency_ms = (time.monotonic() - started) * 1000
                    attempts_per_symbol[code].append(
                        FetchAttempt(provider_name, AttemptOutcome.ERROR, latency_ms, str(exc), type(exc).__name__)
                    )
                    self._router.report_health(provider_name, False, latency_ms)

        # 5) Assemble results + failures.
        data: dict[str, FetchResult[Any]] = {}
        failures: dict[str, BatchItemFailure] = {}

        for code in remaining:
            if code in cached_results:
                data[code] = cached_results[code]
            elif code in covered:
                # Reconstruct from stored attempts (already wrapped in single-query path above)
                # The single-query path already built the FetchResult and cached it; re-read from cache.
                fresh = self._router.read_fresh_cache(remaining[code], route)
                if fresh:
                    data[code] = fresh
                else:
                    # Should not happen, but guard: construct from attempts
                    fr = FetchResult(
                        data=None,
                        source="unknown",
                        fetched_at=utc_now(),
                        attempts=tuple(attempts_per_symbol[code]),
                    )
                    data[code] = fr
            else:
                failures[code] = BatchItemFailure(
                    key=code,
                    error_type="AllSourcesFailed",
                    message=f"所有 provider 均失败: {code}",
                    attempts=tuple(attempts_per_symbol[code]),
                )

        # Merge cached results
        data.update(cached_results)

        result = BatchFetchResult(data=data, failures=failures, fetched_at=utc_now())
        if not data and failures:
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

    def _run_persist(self, route: RouteSpec, data: Any, request: RouteRequest, source: str) -> None:
        if route.persist is None:
            return
        try:
            route.persist(data, request, source)
        except Exception:
            _LOGGER.warning("[persist] %s 回写失败", route.operation.value)
