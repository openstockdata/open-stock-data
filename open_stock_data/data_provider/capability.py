"""
数据源能力声明模型

提供声明式的 Fetcher 能力定义，替代反射检查。
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Set, Optional, Callable


class Market(Enum):
    """支持的市场类型"""
    A_STOCK = "a_stock"
    HK_STOCK = "hk_stock"
    US_STOCK = "us_stock"
    ETF = "etf"
    INDEX = "index"
    CRYPTO = "crypto"


class DataType(Enum):
    """数据类型"""
    # K线数据
    DAILY_KLINE = "daily_kline"
    WEEKLY_KLINE = "weekly_kline"
    MONTHLY_KLINE = "monthly_kline"
    INTRADAY_KLINE = "intraday_kline"

    # 实时行情
    REALTIME_QUOTE = "realtime_quote"
    REALTIME_TICK = "realtime_tick"
    BID_ASK = "bid_ask"

    # 市场数据
    MARKET_SNAPSHOT = "market_snapshot"  # 全市场快照

    # 资金流向
    FUND_FLOW = "fund_flow"

    # 板块数据
    BOARD_INFO = "board_info"
    BOARD_CONSTITUENTS = "board_cons"

    # 龙虎榜
    BILLBOARD = "billboard"

    # 财务数据
    FINANCIAL = "financial"
    CHIP_DISTRIBUTION = "chip_distribution"
    MARGIN_DETAIL = "margin_detail"
    DIVIDEND_HISTORY = "dividend_history"
    FUND_HOLDER = "fund_holder"
    TOP10_HOLDERS = "top10_holders"
    INDUSTRY_PE = "industry_pe"
    EARNINGS_FORECAST = "earnings_forecast"
    EARNINGS_REPORT = "earnings_report"

    # 美股特有
    US_OVERVIEW = "us_overview"
    US_NEWS = "us_news"
    US_INSIDER = "us_insider"
    US_TECH_INDICATORS = "us_tech_indicators"


class CostModel(Enum):
    """成本模型"""
    FREE = "free"  # 完全免费
    FREE_WITH_REGISTER = "free_with_register"  # 免费但需注册
    QUOTA_LIMITED = "quota_limited"  # 有配额限制
    PAID = "paid"  # 付费


@dataclass
class QuotaLimit:
    """配额限制"""
    requests_per_minute: Optional[int] = None
    requests_per_day: Optional[int] = None
    description: str = ""


@dataclass
class FetcherCapability:
    """
    Fetcher 能力声明

    声明式定义数据源的能力边界，避免运行时反射检查。
    """

    # 支持的市场
    markets: Set[Market] = field(default_factory=set)

    # 支持的数据类型（method_name -> 是否支持）
    data_types: Set[DataType] = field(default_factory=set)

    # 成本模型
    cost_model: CostModel = CostModel.FREE

    # 配额限制
    quota_limit: Optional[QuotaLimit] = None

    # 是否需要认证
    requires_auth: bool = False

    # 认证密钥环境变量名
    auth_env_var: Optional[str] = None

    # 运行时可用性检查（用于需要网络/认证的动态判断）
    availability_check: Optional[Callable[[], bool]] = None

    # 批量查询支持的数据类型
    supports_batch: Set[DataType] = field(default_factory=set)

    # 优先使用场景（描述性，用于策略选择）
    preferred_for: Set[str] = field(default_factory=set)

    # 局限性说明（如"仅支持前复权"、"不支持盘中数据"）
    limitations: Set[str] = field(default_factory=set)

    def supports_market(self, market: Market) -> bool:
        """检查是否支持指定市场"""
        return market in self.markets

    def supports_data_type(self, data_type: DataType) -> bool:
        """检查是否支持指定数据类型"""
        return data_type in self.data_types

    def supports_batch_for(self, data_type: DataType) -> bool:
        """检查是否支持批量查询"""
        return data_type in self.supports_batch

    def is_available(self) -> bool:
        """运行时可用性检查"""
        if self.availability_check is None:
            return True
        return self.availability_check()

    def is_free(self) -> bool:
        """是否免费"""
        return self.cost_model in (CostModel.FREE, CostModel.FREE_WITH_REGISTER)

    def has_quota_limit(self) -> bool:
        """是否有配额限制"""
        return self.quota_limit is not None or self.cost_model == CostModel.QUOTA_LIMITED


# 预定义常用能力集合

CAPABILITY_A_STOCK_FULL = {
    Market.A_STOCK,
    Market.ETF,
    Market.INDEX,
}

CAPABILITY_GLOBAL_MARKETS = {
    Market.A_STOCK,
    Market.HK_STOCK,
    Market.US_STOCK,
    Market.ETF,
}

CAPABILITY_KLINE_BASIC = {
    DataType.DAILY_KLINE,
    DataType.WEEKLY_KLINE,
    DataType.MONTHLY_KLINE,
}

CAPABILITY_KLINE_FULL = CAPABILITY_KLINE_BASIC | {
    DataType.INTRADAY_KLINE,
}

CAPABILITY_REALTIME_BASIC = {
    DataType.REALTIME_QUOTE,
}

CAPABILITY_REALTIME_FULL = CAPABILITY_REALTIME_BASIC | {
    DataType.REALTIME_TICK,
    DataType.BID_ASK,
    DataType.MARKET_SNAPSHOT,
}

CAPABILITY_FINANCIAL_BASIC = {
    DataType.FUND_FLOW,
    DataType.BOARD_INFO,
    DataType.BOARD_CONSTITUENTS,
    DataType.BILLBOARD,
}

CAPABILITY_FINANCIAL_EXTENDED = CAPABILITY_FINANCIAL_BASIC | {
    DataType.CHIP_DISTRIBUTION,
    DataType.MARGIN_DETAIL,
    DataType.DIVIDEND_HISTORY,
    DataType.FUND_HOLDER,
    DataType.TOP10_HOLDERS,
    DataType.INDUSTRY_PE,
    DataType.EARNINGS_FORECAST,
    DataType.EARNINGS_REPORT,
}
