"""Route registry and thin executor wrapper.

RouteRegistry provides static route definitions (operation → method/validator/cache_policy).
RouteExecutor is a thin wrapper: uses RouteRegistry for metadata lookup
and DynamicRouter for provider selection and failover logic.
"""

from __future__ import annotations

import logging
from typing import Any, Mapping

from ..exceptions import AllSourcesFailed, BatchIncomplete, RateLimitError, RouteNotFoundError
from .contracts import (
    FetchResult,
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
        """Batch execution: each request goes through DynamicRouter."""
        data: dict[str, FetchResult[Any]] = {}
        failures: dict[str, BatchItemFailure] = {}

        for key, request in requests.items():
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
