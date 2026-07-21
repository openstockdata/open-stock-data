"""Public contracts for routed market-data requests."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Generic, Mapping, Optional, TypeVar, Union

from .stock_code import StockType


T = TypeVar("T")


class Operation(str, Enum):
    DAILY_PRICES = "daily_prices"
    REALTIME_QUOTE = "realtime_quote"
    BATCH_REALTIME_QUOTES = "batch_realtime_quotes"
    A_STOCK_SNAPSHOT = "a_stock_snapshot"
    # 个股分析 / 资金流 / 板块
    FUND_FLOW = "fund_flow"
    CHIP_DISTRIBUTION = "chip_distribution"
    BELONG_BOARD = "belong_board"
    BOARD_CONS = "board_cons"
    BILLBOARD = "billboard"
    MARGIN_DETAIL = "margin_detail"
    MARGIN_RATIO = "margin_ratio"
    # 估值 / 财务
    INDUSTRY_PE = "industry_pe"
    DIVIDEND_HISTORY = "dividend_history"
    FUND_HOLDER = "fund_holder"
    # 股东
    TOP10_HOLDERS = "top10_holders"
    # 美股基本面（AlphaVantage > YFinance；news/tech 仅 AlphaVantage）
    US_OVERVIEW = "us_overview"
    US_BALANCE_SHEET = "us_balance_sheet"
    US_INCOME_STATEMENT = "us_income_statement"
    US_CASH_FLOW = "us_cash_flow"
    US_EARNINGS = "us_earnings"
    US_NEWS_SENTIMENT = "us_news_sentiment"
    US_INSIDER = "us_insider"
    US_TECH_INDICATOR = "us_tech_indicator"


class AttemptOutcome(str, Enum):
    SUCCESS = "success"
    SKIPPED = "skipped"
    EMPTY = "empty"
    INVALID = "invalid"
    ERROR = "error"
    RATE_LIMITED = "rate_limited"


@dataclass(frozen=True)
class FetchAttempt:
    source: str
    outcome: AttemptOutcome
    latency_ms: float = 0.0
    reason: Optional[str] = None
    error_type: Optional[str] = None


@dataclass(frozen=True)
class FetchResult(Generic[T]):
    data: T
    source: str
    fetched_at: datetime
    from_cache: bool = False
    is_stale: bool = False
    attempts: tuple[FetchAttempt, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class BatchItemFailure:
    key: str
    error_type: str
    message: str
    attempts: tuple[FetchAttempt, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class BatchFetchResult(Generic[T]):
    data: Mapping[str, FetchResult[T]]
    failures: Mapping[str, BatchItemFailure]
    fetched_at: datetime

    @property
    def is_partial(self) -> bool:
        return bool(self.data) and bool(self.failures)


@dataclass(frozen=True)
class CachePolicy:
    ttl_seconds: Union[float, Callable[[], float]]
    allow_stale_on_error: bool = False
    max_stale_seconds: float = 0.0

    def __post_init__(self) -> None:
        if not callable(self.ttl_seconds) and self.ttl_seconds < 0:
            raise ValueError("ttl_seconds must be non-negative")
        if self.max_stale_seconds < 0:
            raise ValueError("max_stale_seconds must be non-negative")
        if self.allow_stale_on_error and self.max_stale_seconds <= 0:
            raise ValueError("stale-on-error requires max_stale_seconds > 0")

    def current_ttl(self) -> float:
        value = self.ttl_seconds() if callable(self.ttl_seconds) else self.ttl_seconds
        value = float(value)
        if value < 0:
            raise ValueError("ttl_seconds must be non-negative")
        return value


ResultValidator = Callable[[Any], bool]


@dataclass(frozen=True)
class RouteSpec:
    operation: Operation
    market: Optional[StockType]
    providers: tuple[str, ...]
    method_name: str
    circuit_breaker: str
    cache_policy: Optional[CachePolicy] = None
    empty_is_failure: bool = True
    validator: Optional[ResultValidator] = None
    skip_shared_backend_after_network_error: bool = True
    batch_method: Optional[str] = None

    @property
    def key(self) -> tuple[Operation, Optional[StockType]]:
        return self.operation, self.market


@dataclass(frozen=True)
class RouteRequest:
    operation: Operation
    market: Optional[StockType]
    args: tuple[Any, ...] = field(default_factory=tuple)
    kwargs: Mapping[str, Any] = field(default_factory=dict)
    cache_key: Optional[str] = None

    @property
    def key(self) -> tuple[Operation, Optional[StockType]]:
        return self.operation, self.market


def utc_now() -> datetime:
    return datetime.now(timezone.utc)
