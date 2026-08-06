"""Default fixed provider order for the public market-data API."""

from __future__ import annotations

from ..cache import CACHE_TTLS
from .contracts import CachePolicy, Operation, RouteSpec
from .market_config import MarketHoursConfig, MarketType
from .routing import RouteRegistry
from .stock_code import StockType


def _realtime_ttl(market: MarketType) -> float:
    if MarketHoursConfig.is_trading_time(market):
        return CACHE_TTLS["realtime_trading"]
    return CACHE_TTLS["realtime_closed"]


def _snapshot_ttl() -> float:
    if MarketHoursConfig.is_trading_time(MarketType.A_STOCK):
        return CACHE_TTLS["spot_trading"]
    return CACHE_TTLS["spot_closed"]


def _fund_flow_ttl() -> float:
    if MarketHoursConfig.is_trading_time(MarketType.A_STOCK):
        return CACHE_TTLS["fund_flow_trading"]
    return CACHE_TTLS["fund_flow_closed"]


def _daily_policy() -> CachePolicy:
    return CachePolicy(
        ttl_seconds=CACHE_TTLS["daily"],
        allow_stale_on_error=True,
        max_stale_seconds=86400 * 7,
    )


def _quote_is_valid(quote) -> bool:
    return quote is not None and quote.has_basic_data()


def _snapshot_is_complete(df) -> bool:
    """全市场快照必须是完整的：部分页失败的结果不接受、不缓存，回退到下一数据源。"""
    if df is None or df.empty:
        return False
    return bool(df.attrs.get("spot_complete", True))


def _persist_fact(data, request, source) -> None:
    """低频事实路由的本地长期存储回写：按 (operation, cache_key) 整帧存放。

    来自本地 provider 的命中不回写；无 cache_key 的不存。异常由 RouteExecutor 吞掉。
    """
    if source == "LocalStoreFetcher" or request.cache_key is None:
        return
    from .local_store import get_local_store
    get_local_store().save_fact(request.operation.value, request.cache_key, data, source)


def create_default_routes() -> RouteRegistry:
    daily_a = (
        "EfinanceFetcher",
        "AkshareFetcher",
        "TushareFetcher",
        "TickflowFetcher",
        "PytdxFetcher",
        "BaostockFetcher",
    )
    routes = [
        RouteSpec(
            Operation.DAILY_PRICES,
            StockType.A_STOCK,
            daily_a,
            "get_daily_data",
            "daily",
            _daily_policy(),
        ),
        RouteSpec(
            Operation.DAILY_PRICES,
            StockType.ETF,
            ("EfinanceFetcher", "AkshareFetcher", "TickflowFetcher", "YfinanceFetcher", "BaostockFetcher"),
            "get_daily_data",
            "daily",
            _daily_policy(),
        ),
        RouteSpec(
            Operation.DAILY_PRICES,
            StockType.HK,
            ("TickflowFetcher", "YfinanceFetcher", "AkshareFetcher"),
            "get_daily_data",
            "daily",
            _daily_policy(),
        ),
        RouteSpec(
            Operation.DAILY_PRICES,
            StockType.US,
            ("TickflowFetcher", "YfinanceFetcher", "AlphaVantage"),
            "get_daily_data",
            "daily",
            _daily_policy(),
        ),
        RouteSpec(
            Operation.REALTIME_QUOTE,
            StockType.A_STOCK,
            ("TickflowFetcher", "EfinanceFetcher", "TushareFetcher", "AkshareFetcher"),
            "get_realtime_quote",
            "realtime",
            CachePolicy(lambda: _realtime_ttl(MarketType.A_STOCK)),
            validator=_quote_is_valid,
            batch_method="get_batch_realtime_quotes",
        ),
        RouteSpec(
            Operation.REALTIME_QUOTE,
            StockType.ETF,
            ("TickflowFetcher", "AkshareFetcher", "YfinanceFetcher"),
            "get_realtime_quote",
            "realtime",
            CachePolicy(lambda: _realtime_ttl(MarketType.A_STOCK)),
            validator=_quote_is_valid,
            batch_method="get_batch_realtime_quotes",
        ),
        RouteSpec(
            Operation.REALTIME_QUOTE,
            StockType.HK,
            ("TickflowFetcher", "AkshareFetcher", "YfinanceFetcher"),
            "get_realtime_quote",
            "realtime",
            CachePolicy(lambda: _realtime_ttl(MarketType.HK_STOCK)),
            validator=_quote_is_valid,
            batch_method="get_batch_realtime_quotes",
        ),
        RouteSpec(
            Operation.REALTIME_QUOTE,
            StockType.US,
            ("TickflowFetcher", "YfinanceFetcher", "AlphaVantage"),
            "get_realtime_quote",
            "realtime",
            CachePolicy(lambda: _realtime_ttl(MarketType.US_STOCK)),
            validator=_quote_is_valid,
            batch_method="get_batch_realtime_quotes",
        ),
        RouteSpec(
            Operation.A_STOCK_SNAPSHOT,
            StockType.A_STOCK,
            ("EfinanceFetcher", "AkshareFetcher"),
            "get_a_stock_spot",
            "spot",
            CachePolicy(_snapshot_ttl),
            validator=_snapshot_is_complete,
            skip_shared_backend_after_network_error=False,
        ),
        # ---- 个股分析 / 资金流 / 板块（A 股，市场无关路由）----
        RouteSpec(
            Operation.FUND_FLOW,
            None,
            ("EfinanceFetcher", "TushareFetcher", "AkshareFetcher"),
            "get_fund_flow",
            "fund_flow",
            CachePolicy(_fund_flow_ttl),
        ),
        RouteSpec(
            Operation.CHIP_DISTRIBUTION,
            None,
            ("AkshareFetcher",),
            "get_chip_distribution",
            "chip",
        ),
        RouteSpec(
            Operation.BELONG_BOARD,
            None,
            ("LocalStoreFetcher", "EfinanceFetcher", "TushareFetcher", "AkshareFetcher"),
            "get_belong_board",
            "board",
            CachePolicy(CACHE_TTLS["belong_board"]),
            persist=_persist_fact,
        ),
        RouteSpec(
            Operation.BOARD_CONS,
            None,
            ("LocalStoreFetcher", "AkshareFetcher", "TushareFetcher"),
            "get_board_cons",
            "board",
            persist=_persist_fact,
        ),
        RouteSpec(
            Operation.BILLBOARD,
            None,
            ("EfinanceFetcher", "AkshareFetcher", "TushareFetcher"),
            "get_billboard",
            "billboard",
        ),
        # ---- 估值 / 财务 ----
        RouteSpec(
            Operation.INDUSTRY_PE,
            None,
            ("LocalStoreFetcher", "AkshareFetcher"),
            "get_industry_pe",
            "industry_pe",
            persist=_persist_fact,
        ),
        RouteSpec(
            Operation.DIVIDEND_HISTORY,
            None,
            ("LocalStoreFetcher", "TushareFetcher", "AkshareFetcher"),
            "get_dividend_history",
            "dividend",
            persist=_persist_fact,
        ),
        RouteSpec(
            Operation.FUND_HOLDER,
            None,
            ("LocalStoreFetcher", "TushareFetcher", "AkshareFetcher"),
            "get_fund_holder",
            "fund_holder",
            persist=_persist_fact,
        ),
        # ---- 股东 ----
        RouteSpec(
            Operation.TOP10_HOLDERS,
            None,
            ("LocalStoreFetcher", "TushareFetcher", "AkshareFetcher"),
            "get_top10_holders",
            "top10_holders",
            persist=_persist_fact,
        ),
        # ---- 融资融券（个股明细，比例作末轮兜底）----
        RouteSpec(
            Operation.MARGIN_DETAIL,
            None,
            ("AkshareFetcher",),
            "get_margin_detail",
            "margin",
        ),
        RouteSpec(
            Operation.MARGIN_RATIO,
            None,
            ("AkshareFetcher",),
            "get_margin_ratio",
            "margin",
        ),
        # ---- 美股基本面（AlphaVantage > YFinance；news/tech 仅 AlphaVantage）----
        RouteSpec(
            Operation.US_OVERVIEW,
            StockType.US,
            ("LocalStoreFetcher", "AlphaVantage", "YfinanceFetcher"),
            "get_company_overview",
            "us_financials",
            CachePolicy(CACHE_TTLS["us_overview"]),
            persist=_persist_fact,
        ),
        RouteSpec(
            Operation.US_BALANCE_SHEET,
            StockType.US,
            ("LocalStoreFetcher", "AlphaVantage", "YfinanceFetcher"),
            "get_balance_sheet",
            "us_financials",
            CachePolicy(CACHE_TTLS["us_report"]),
            persist=_persist_fact,
        ),
        RouteSpec(
            Operation.US_INCOME_STATEMENT,
            StockType.US,
            ("LocalStoreFetcher", "AlphaVantage", "YfinanceFetcher"),
            "get_income_statement",
            "us_financials",
            CachePolicy(CACHE_TTLS["us_report"]),
            persist=_persist_fact,
        ),
        RouteSpec(
            Operation.US_CASH_FLOW,
            StockType.US,
            ("LocalStoreFetcher", "AlphaVantage", "YfinanceFetcher"),
            "get_cash_flow",
            "us_financials",
            CachePolicy(CACHE_TTLS["us_report"]),
            persist=_persist_fact,
        ),
        RouteSpec(
            Operation.US_EARNINGS,
            StockType.US,
            ("LocalStoreFetcher", "AlphaVantage", "YfinanceFetcher"),
            "get_earnings",
            "us_financials",
            CachePolicy(CACHE_TTLS["us_earnings"]),
            persist=_persist_fact,
        ),
        RouteSpec(
            Operation.US_NEWS_SENTIMENT,
            StockType.US,
            ("AlphaVantage",),
            "get_news_sentiment",
            "us_financials",
        ),
        RouteSpec(
            Operation.US_INSIDER,
            StockType.US,
            ("LocalStoreFetcher", "AlphaVantage", "YfinanceFetcher"),
            "get_insider_transactions",
            "us_financials",
            CachePolicy(CACHE_TTLS["us_insider"]),
            persist=_persist_fact,
        ),
        RouteSpec(
            Operation.US_TECH_INDICATOR,
            StockType.US,
            ("AlphaVantage",),
            "get_technical_indicator",
            "us_financials",
        ),
    ]
    return RouteRegistry(routes)
