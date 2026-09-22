"""Typed public API for stock market data.

OpenStockDataClient uses ProviderContext + DynamicRouter for adaptive
provider selection and fallback.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta
import logging
import threading
from typing import Mapping, Optional

import pandas as pd

from .exceptions import AllSourcesFailed, BatchIncomplete
from .data_provider.base import BaseFetcher
from .data_provider.boards import normalize_belong_board
from .data_provider.columns import to_english_columns
from .data_provider.context import ProviderContext
from .data_provider.contracts import (
    BatchFetchResult,
    FetchResult,
    Operation,
    RouteRequest,
    utc_now,
)
from .data_provider.dynamic_router import DynamicRouter
from .data_provider.default_routes import create_default_routes
from .data_provider.local_store import get_local_store, last_expected_trade_date
from .data_provider.providers import create_default_providers
from .data_provider.routing import RouteExecutor, RouteRegistry
from .data_provider.stock_code import (
    StockType,
    detect_stock_type,
    normalize_stock_code,
    validate_stock_type,
)
from .data_provider.types import UnifiedRealtimeQuote

_LOGGER = logging.getLogger(__name__)

_DAILY_TARGET_ROWS = 500


class OpenStockDataClient:
    """Synchronous typed data API backed by dynamic provider routing.

    Uses ProviderContext for provider registry and DynamicRouter for
    adaptive fallback ordering based on real-time health metrics.
    """

    def __init__(
        self,
        providers: Optional[dict] = None,
        routes=None,
        *,
        cache=None,
        store=None,
    ):
        self._store = store
        if providers is not None:
            self._context = ProviderContext()
            for fetcher in providers.values():
                self._context.register(fetcher)
        else:
            self._context = ProviderContext.default()
        if not self._context.provider_names:
            create_default_providers(self._context)
        self._router = DynamicRouter(self._context)
        if cache is not None:
            self._router.cache = cache
        registry = routes if routes is not None else create_default_routes()
        self._executor = RouteExecutor(self._router, registry)

    @property
    def provider_context(self) -> ProviderContext:
        return self._context

    def _daily_store(self):
        return self._store if self._store is not None else get_local_store()

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

        if start_date is None and end_date is None:
            result = self._daily_incremental(normalized, stock_type, requested_days)
        else:
            request = RouteRequest(
                Operation.DAILY_PRICES,
                stock_type,
                args=(normalized,),
                kwargs={"start_date": start_date, "end_date": end_date, "days": requested_days},
                cache_key=f"{normalized}:{start_date or ''}:{end_date or ''}:{requested_days}",
            )
            result = self._executor.execute(request)
            result = replace(result, data=to_english_columns(result.data.copy()))

        data = result.data
        if period != "daily":
            data = self._resample_prices(data, period)
        data = data.tail(days).reset_index(drop=True)
        return replace(result, data=data)

    def _daily_incremental(
        self, symbol: str, stock_type: StockType, requested_days: int
    ) -> FetchResult[pd.DataFrame]:
        store = self._daily_store()
        target = max(requested_days, _DAILY_TARGET_ROWS)
        coverage = store.daily_coverage(symbol)
        last_expected = last_expected_trade_date()

        if coverage and coverage[1] >= last_expected and coverage[2] >= requested_days:
            local = store.load_daily(symbol, requested_days)
            if local is not None:
                return FetchResult(data=local, source="LocalStoreFetcher", fetched_at=utc_now(), from_cache=True)

        if coverage:
            start = (datetime.strptime(coverage[1], "%Y-%m-%d") - timedelta(days=10)).strftime("%Y%m%d")
            fetch_days = requested_days
        else:
            start = None
            fetch_days = target

        try:
            result = self._executor.execute(RouteRequest(
                Operation.DAILY_PRICES, stock_type, args=(symbol,),
                kwargs={"start_date": start, "end_date": None, "days": fetch_days}, cache_key=None,
            ))
        except AllSourcesFailed:
            local = store.load_daily(symbol, requested_days)
            if local is not None and not local.empty:
                _LOGGER.warning("[daily] %s 网络全失败，降级使用本地历史 (%d 行)", symbol, len(local))
                return FetchResult(data=local, source="LocalStoreFetcher", fetched_at=utc_now(),
                                   from_cache=True, is_stale=True)
            raise

        fetched = to_english_columns(result.data.copy())
        status = store.upsert_daily(symbol, fetched, source=result.source)
        if status == "conflict":
            full = self._executor.execute(RouteRequest(
                Operation.DAILY_PRICES, stock_type, args=(symbol,),
                kwargs={"start_date": None, "end_date": None, "days": target}, cache_key=None,
            ))
            store.upsert_daily(symbol, to_english_columns(full.data.copy()), source=full.source)
            result = full

        merged = store.load_daily(symbol, requested_days)
        if merged is None or merged.empty:
            merged = fetched.tail(requested_days).reset_index(drop=True)
        return FetchResult(data=merged, source=result.source, fetched_at=utc_now(), attempts=result.attempts)

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
        """所属板块，统一 schema：板块名称 / 板块代码 / 板块类型 / 股票代码 / 股票名称 + 各源附加列。

        各 provider 已在源头归一；这里再兜底一次，覆盖修复前写入本地存储/缓存的旧格式帧。
        """
        result = self._executor.execute(
            RouteRequest(Operation.BELONG_BOARD, None, args=(symbol,), cache_key=symbol)
        )
        normalized = normalize_belong_board(result.data)
        if normalized is not result.data:
            result = replace(result, data=normalized)
        return result

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

    # ==================== 美股基本面 ====================

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

    # ==================== 市场概览（Akshare 独有）=======

    def market_pe_percentile(self) -> FetchResult[pd.DataFrame]:
        return self._executor.execute(
            RouteRequest(Operation.MARKET_PE_PERCENTILE, None, cache_key="market_pe")
        )

    def earnings_calendar(self, period: str = "") -> FetchResult[pd.DataFrame]:
        return self._executor.execute(
            RouteRequest(Operation.EARNINGS_CALENDAR, None, args=(period,), cache_key=f"earnings_cal:{period}")
        )

    def financial_compare(self, symbol: str) -> FetchResult[pd.DataFrame]:
        return self._executor.execute(
            RouteRequest(Operation.FINANCIAL_COMPARE, None, args=(symbol,), cache_key=symbol)
        )

    def stock_info(self, symbol: str) -> FetchResult[pd.DataFrame]:
        return self._executor.execute(
            RouteRequest(Operation.STOCK_INFO, None, args=(symbol,), cache_key=symbol)
        )

    def stock_indicators(self, symbol: str) -> FetchResult[pd.DataFrame]:
        return self._executor.execute(
            RouteRequest(Operation.STOCK_INDICATORS, None, args=(symbol,), cache_key=symbol)
        )

    def current_time(self) -> FetchResult[pd.DataFrame]:
        return self._executor.execute(
            RouteRequest(Operation.CURRENT_TIME, None, cache_key="current_time")
        )

    def zt_pool(self, pool_type: str = "涨停", date: str = "") -> FetchResult[pd.DataFrame]:
        return self._executor.execute(
            RouteRequest(
                Operation.ZT_POOL,
                None,
                args=(pool_type, date),
                cache_key=f"zt_pool:{pool_type}:{date or 'latest'}",
            )
        )

    def north_flow(self, indicator: str = "北向资金") -> FetchResult[pd.DataFrame]:
        return self._executor.execute(
            RouteRequest(Operation.NORTH_FLOW, None, args=(indicator,), cache_key="north_flow")
        )

    def sector_fund_flow_rank(self, days: str = "今日", cate: str = "行业资金流") -> FetchResult[pd.DataFrame]:
        return self._executor.execute(
            RouteRequest(Operation.SECTOR_FUND_FLOW_RANK, None, args=(days, cate), cache_key=f"sector_ff:{days}:{cate}")
        )

    def block_trade(self, symbol: str = "", limit: int = 10) -> FetchResult[pd.DataFrame]:
        return self._executor.execute(
            RouteRequest(Operation.BLOCK_TRADE, None, args=(symbol, limit), cache_key=f"block_trade:{symbol}")
        )

    def holder_num(self, symbol: str = "") -> FetchResult[pd.DataFrame]:
        return self._executor.execute(
            RouteRequest(Operation.HOLDER_NUM, None, args=(symbol,), cache_key=f"holder_num:{symbol}")
        )

    def locked_shares(self, mode: str = "detail", limit: int = 20) -> FetchResult[pd.DataFrame]:
        return self._executor.execute(
            RouteRequest(Operation.LOCKED_SHARES, None, args=(mode, limit), cache_key=f"locked_shares:{mode}")
        )

    def pledge_ratio(self, mode: str = "industry", limit: int = 20) -> FetchResult[pd.DataFrame]:
        return self._executor.execute(
            RouteRequest(Operation.PLEDGE_RATIO, None, args=(mode, limit), cache_key=f"pledge_ratio:{mode}")
        )

    def news(self, symbol: str = "", limit: int = 15) -> FetchResult[pd.DataFrame]:
        return self._executor.execute(
            RouteRequest(Operation.NEWS, None, args=(symbol, limit), cache_key=f"news:{symbol}:{limit}")
        )

    def news_global(self) -> FetchResult[pd.DataFrame]:
        return self._executor.execute(
            RouteRequest(Operation.NEWS_GLOBAL, None, cache_key="news_global")
        )

    def margin_trading(self, symbol: str = "", market: str = "sh") -> FetchResult[pd.DataFrame]:
        return self._executor.execute(
            RouteRequest(
                Operation.MARGIN_TRADING,
                None,
                args=(symbol, market),
                cache_key=f"margin:{symbol or 'market'}:{market}",
            )
        )

    def index_daily(
        self, symbol: str, period: str = "daily", days: int = 30
    ) -> FetchResult[pd.DataFrame]:
        return self._executor.execute(
            RouteRequest(
                Operation.INDEX_DAILY,
                None,
                args=(symbol, period, days),
                cache_key=f"{symbol}:{period}:{days}",
            )
        )

    # ==================== 盘口 / 业绩 / 分红 / 新闻联播 ====================

    def bid_ask(self, symbol: str) -> FetchResult[pd.DataFrame]:
        return self._executor.execute(
            RouteRequest(Operation.BID_ASK, None, args=(symbol,), cache_key=symbol)
        )

    def cctv_news(self, date: str = "") -> FetchResult[pd.DataFrame]:
        return self._executor.execute(
            RouteRequest(Operation.CCTV_NEWS, None, args=(date,), cache_key=f"cctv:{date or 'latest'}")
        )

    def earnings_forecast(self, symbol: str) -> FetchResult[pd.DataFrame]:
        return self._executor.execute(
            RouteRequest(Operation.EARNINGS_FORECAST, None, args=(symbol,), cache_key=symbol)
        )

    def earnings_report(self, symbol: str) -> FetchResult[pd.DataFrame]:
        return self._executor.execute(
            RouteRequest(Operation.EARNINGS_REPORT, None, args=(symbol,), cache_key=symbol)
        )

    def earnings_express(self, symbol: str) -> FetchResult[pd.DataFrame]:
        return self._executor.execute(
            RouteRequest(Operation.EARNINGS_EXPRESS, None, args=(symbol,), cache_key=symbol)
        )

    def dividend_plan(self, symbol: str) -> FetchResult[pd.DataFrame]:
        return self._executor.execute(
            RouteRequest(Operation.DIVIDEND_PLAN, None, args=(symbol,), cache_key=symbol)
        )

    def dividend_cninfo(self, symbol: str) -> FetchResult[pd.DataFrame]:
        return self._executor.execute(
            RouteRequest(Operation.DIVIDEND_CNINFO, None, args=(symbol,), cache_key=symbol)
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
