"""Typed public API for stock market data."""

from __future__ import annotations

from dataclasses import replace
import threading
from typing import Mapping, Optional

import pandas as pd

from .cache import CacheStore
from .data_provider.base import BaseFetcher
from .data_provider.columns import to_english_columns
from .data_provider.contracts import (
    BatchFetchResult,
    FetchResult,
    Operation,
    RouteRequest,
)
from .exceptions import AllSourcesFailed
from .data_provider.default_routes import create_default_routes
from .data_provider.providers import create_default_providers
from .data_provider.routing import RouteExecutor, RouteRegistry
from .data_provider.stock_code import (
    StockType,
    detect_stock_type,
    normalize_stock_code,
    validate_stock_type,
)
from .data_provider.types import UnifiedRealtimeQuote


class OpenStockDataClient:
    """Synchronous typed data API backed by fixed provider routes.

    列名契约（两级，刻意区分）：
    - **价格/快照**（daily_prices、a_stock_snapshot）：返回标准英文列
      （date/open/close/high/low/volume），由 `to_english_columns` 归一，
      供跨市场统一消费与技术指标计算。
    - **分析类**（fund_flow、belong_board、industry_pe、dividend_history、
      top10_holders、margin_detail 等）：返回数据源原生列（多为中文，如
      主力净流入/板块名称），因这些字段无标准英文 schema，强转只会造成
      "半中半英"。工具层按需格式化后输出。
    """

    def __init__(
        self,
        providers: Optional[Mapping[str, BaseFetcher]] = None,
        routes: Optional[RouteRegistry] = None,
        cache: Optional[CacheStore] = None,
    ):
        provider_map = dict(providers) if providers is not None else create_default_providers()
        self._executor = RouteExecutor(
            provider_map,
            routes or create_default_routes(),
            cache=cache or CacheStore.get_store("routed_data"),
        )

    def daily_prices(
        self,
        symbol: str,
        market: str = "sh",
        *,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        days: int = 30,
        period: str = "daily",
    ) -> FetchResult[pd.DataFrame]:
        if period not in {"daily", "weekly", "monthly"}:
            raise ValueError(f"unsupported period: {period}")
        requested_days = days
        if period == "weekly":
            requested_days = days * 7 + 30
        elif period == "monthly":
            requested_days = days * 31 + 60
        normalized = normalize_stock_code(symbol, market)
        stock_type, _ = validate_stock_type(normalized, market)
        request = RouteRequest(
            Operation.DAILY_PRICES,
            stock_type,
            args=(normalized,),
            kwargs={"start_date": start_date, "end_date": end_date, "days": requested_days},
            cache_key=f"{normalized}:{start_date or ''}:{end_date or ''}:{requested_days}",
        )
        result = self._executor.execute(request)
        data = to_english_columns(result.data.copy())
        if period != "daily":
            data = self._resample_prices(data, period)
        data = data.tail(days).reset_index(drop=True)
        return replace(result, data=data)

    def realtime_quote(
        self,
        symbol: str,
        market: Optional[str] = None,
    ) -> FetchResult[UnifiedRealtimeQuote]:
        normalized = normalize_stock_code(symbol, market)
        stock_type = detect_stock_type(normalized)
        if market:
            stock_type, _ = validate_stock_type(normalized, market)
        request = RouteRequest(
            Operation.REALTIME_QUOTE,
            stock_type,
            args=(normalized,),
            cache_key=normalized,
        )
        return self._executor.execute(request)

    def batch_realtime_quotes(
        self,
        symbols: list[str],
        market: Optional[str] = None,
        *,
        strict: bool = False,
    ) -> BatchFetchResult[UnifiedRealtimeQuote]:
        requests = {}
        for symbol in dict.fromkeys(symbols):
            normalized = normalize_stock_code(symbol, market)
            stock_type = detect_stock_type(normalized)
            if market:
                stock_type, _ = validate_stock_type(normalized, market)
            requests[symbol] = RouteRequest(
                Operation.REALTIME_QUOTE,
                stock_type,
                args=(normalized,),
                cache_key=normalized,
            )
        return self._executor.execute_batch(requests, strict=strict)

    def a_stock_snapshot(self) -> FetchResult[pd.DataFrame]:
        request = RouteRequest(
            Operation.A_STOCK_SNAPSHOT,
            StockType.A_STOCK,
            cache_key="all",
        )
        result = self._executor.execute(request)
        data = to_english_columns(result.data.copy())
        return replace(result, data=data)

    # ==================== 个股分析 / 资金流 / 板块 ====================

    def fund_flow(self, symbol: str) -> FetchResult[pd.DataFrame]:
        return self._executor.execute(
            RouteRequest(Operation.FUND_FLOW, None, args=(symbol,), cache_key=symbol)
        )

    def chip_distribution(self, symbol: str) -> FetchResult:
        return self._executor.execute(
            RouteRequest(Operation.CHIP_DISTRIBUTION, None, args=(symbol,), cache_key=symbol)
        )

    def belong_board(self, symbol: str) -> FetchResult[pd.DataFrame]:
        return self._executor.execute(
            RouteRequest(Operation.BELONG_BOARD, None, args=(symbol,), cache_key=symbol)
        )

    def board_cons(self, board_name: str, board_type: str = "industry") -> FetchResult[pd.DataFrame]:
        return self._executor.execute(
            RouteRequest(
                Operation.BOARD_CONS,
                None,
                args=(board_name, board_type),
                cache_key=f"{board_name}:{board_type}",
            )
        )

    def billboard(self, days: str = "5") -> FetchResult[pd.DataFrame]:
        return self._executor.execute(
            RouteRequest(Operation.BILLBOARD, None, args=(days,), cache_key=days)
        )

    # ==================== 估值 / 财务 / 股东 ====================

    def industry_pe(self, date: str = "") -> FetchResult[pd.DataFrame]:
        return self._executor.execute(
            RouteRequest(Operation.INDUSTRY_PE, None, args=(date,), cache_key=date or "latest")
        )

    def dividend_history(self, symbol: str) -> FetchResult[pd.DataFrame]:
        return self._executor.execute(
            RouteRequest(Operation.DIVIDEND_HISTORY, None, args=(symbol,), cache_key=symbol)
        )

    def fund_holder(self, symbol: str = "", date: str = "") -> FetchResult[pd.DataFrame]:
        return self._executor.execute(
            RouteRequest(
                Operation.FUND_HOLDER,
                None,
                args=(symbol,),
                kwargs={"date": date},
                cache_key=f"{symbol}:{date}",
            )
        )

    def top10_holders(self, symbol: str, holder_type: str = "main") -> FetchResult[pd.DataFrame]:
        return self._executor.execute(
            RouteRequest(
                Operation.TOP10_HOLDERS,
                None,
                args=(symbol,),
                kwargs={"holder_type": holder_type},
                cache_key=f"{symbol}:{holder_type}",
            )
        )

    def margin_detail(self, symbol: str, market: str = "sh") -> FetchResult[pd.DataFrame]:
        """融资融券：先查指定市场明细，再查另一市场，最后以融资融券比例兜底。"""
        other_market = "sz" if market == "sh" else "sh"
        for exchange in (market, other_market):
            try:
                return self._executor.execute(
                    RouteRequest(
                        Operation.MARGIN_DETAIL,
                        None,
                        args=(symbol, exchange),
                        cache_key=f"{symbol}:{exchange}",
                    )
                )
            except AllSourcesFailed:
                continue
        result = self._executor.execute(
            RouteRequest(Operation.MARGIN_RATIO, None, args=(symbol,), cache_key=symbol)
        )
        result.data.attrs["is_ratio_data"] = True
        return result

    # ==================== 美股基本面（AlphaVantage > YFinance）====================

    def us_overview(self, symbol: str) -> FetchResult[dict]:
        return self._executor.execute(
            RouteRequest(Operation.US_OVERVIEW, StockType.US, args=(symbol,), cache_key=symbol.upper())
        )

    def us_balance_sheet(self, symbol: str, quarterly: bool = True) -> FetchResult[dict]:
        return self._us_financial(Operation.US_BALANCE_SHEET, symbol, quarterly)

    def us_income_statement(self, symbol: str, quarterly: bool = True) -> FetchResult[dict]:
        return self._us_financial(Operation.US_INCOME_STATEMENT, symbol, quarterly)

    def us_cash_flow(self, symbol: str, quarterly: bool = True) -> FetchResult[dict]:
        return self._us_financial(Operation.US_CASH_FLOW, symbol, quarterly)

    def _us_financial(self, operation: Operation, symbol: str, quarterly: bool) -> FetchResult[dict]:
        return self._executor.execute(
            RouteRequest(
                operation,
                StockType.US,
                args=(symbol,),
                kwargs={"quarterly": quarterly},
                cache_key=f"{symbol.upper()}:{'q' if quarterly else 'a'}",
            )
        )

    def us_earnings(self, symbol: str) -> FetchResult[dict]:
        return self._executor.execute(
            RouteRequest(Operation.US_EARNINGS, StockType.US, args=(symbol,), cache_key=symbol.upper())
        )

    def us_news_sentiment(
        self, symbol: Optional[str] = None, topics: Optional[str] = None, limit: int = 50
    ) -> FetchResult[dict]:
        return self._executor.execute(
            RouteRequest(
                Operation.US_NEWS_SENTIMENT,
                StockType.US,
                kwargs={"symbol": symbol, "topics": topics, "limit": limit},
            )
        )

    def us_insider(self, symbol: str) -> FetchResult[dict]:
        return self._executor.execute(
            RouteRequest(Operation.US_INSIDER, StockType.US, args=(symbol,), cache_key=symbol.upper())
        )

    def us_tech_indicator(
        self, symbol: str, indicator: str, interval: str = "daily", time_period: int = 14
    ) -> FetchResult[dict]:
        return self._executor.execute(
            RouteRequest(
                Operation.US_TECH_INDICATOR,
                StockType.US,
                args=(symbol, indicator, interval, time_period),
            )
        )

    @staticmethod
    def _resample_prices(data: pd.DataFrame, period: str) -> pd.DataFrame:
        if data.empty:
            return data
        required = {"date", "open", "high", "low", "close"}
        missing = required.difference(data.columns)
        if missing:
            raise ValueError(f"cannot resample prices; missing columns: {sorted(missing)}")
        work = data.copy()
        work["date"] = pd.to_datetime(work["date"], errors="coerce")
        work = work.dropna(subset=["date"]).sort_values("date")
        aggregations = {
            "open": "first",
            "high": "max",
            "low": "min",
            "close": "last",
        }
        for column in ("volume", "amount"):
            if column in work.columns:
                aggregations[column] = "sum"
        if "pct_chg" in work.columns:
            aggregations["pct_chg"] = "last"
        rule = "W" if period == "weekly" else "ME"
        result = work.set_index("date").resample(rule).agg(aggregations).dropna(subset=["close"])
        result = result.reset_index()
        result["date"] = result["date"].dt.strftime("%Y-%m-%d")
        return result


_default_client: Optional[OpenStockDataClient] = None
_default_client_lock = threading.Lock()


def get_default_client() -> OpenStockDataClient:
    global _default_client
    if _default_client is None:
        with _default_client_lock:
            if _default_client is None:
                _default_client = OpenStockDataClient()
    return _default_client
