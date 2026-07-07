"""
为现有 Fetcher 添加能力声明

演示如何为每个数据源定义能力。
"""

import os
from .capability import (
    FetcherCapability,
    Market,
    DataType,
    CostModel,
    QuotaLimit,
    CAPABILITY_A_STOCK_FULL,
    CAPABILITY_GLOBAL_MARKETS,
    CAPABILITY_KLINE_BASIC,
    CAPABILITY_REALTIME_BASIC,
    CAPABILITY_FINANCIAL_BASIC,
    CAPABILITY_FINANCIAL_EXTENDED,
)


# ==================== TickflowFetcher ====================

def create_tickflow_capability() -> FetcherCapability:
    """
    TickFlow 能力声明

    特点：
    - 免费日线服务（无需认证）
    - 实时行情需要 API key
    - 配额: 10次/分钟（单标的查询）
    - 支持全球市场
    """
    has_api_key = bool(os.getenv("TICKFLOW_API_KEY"))

    data_types = {
        DataType.DAILY_KLINE,  # 免费
        DataType.WEEKLY_KLINE,
        DataType.MONTHLY_KLINE,
    }

    if has_api_key:
        data_types.update({
            DataType.REALTIME_QUOTE,
            DataType.INTRADAY_KLINE,
        })

    return FetcherCapability(
        markets=CAPABILITY_GLOBAL_MARKETS,
        data_types=data_types,
        cost_model=CostModel.FREE_WITH_REGISTER if has_api_key else CostModel.FREE,
        quota_limit=QuotaLimit(
            requests_per_minute=10,
            description="按标的查询: 10次/分钟, 1标的/次"
        ) if has_api_key else None,
        requires_auth=has_api_key,
        auth_env_var="TICKFLOW_API_KEY",
        availability_check=lambda: True,  # 免费服务总是可用
        supports_batch={DataType.REALTIME_QUOTE} if has_api_key else set(),
        preferred_for={"全球市场", "日线K线", "免费服务"},
        limitations={"仅支持1d/1w/1M/1Q/1Y周期", "实时行情需要API key"},
    )


# ==================== TushareFetcher ====================

def create_tushare_capability() -> FetcherCapability:
    """
    Tushare 能力声明

    特点：
    - 需要 token（免费注册）
    - A股数据最全
    - 配额: 50次/分钟（免费版）
    - 仅支持 A 股和 ETF
    """
    has_token = bool(os.getenv("TUSHARE_TOKEN"))

    return FetcherCapability(
        markets=CAPABILITY_A_STOCK_FULL,
        data_types=CAPABILITY_KLINE_BASIC | CAPABILITY_REALTIME_BASIC | {
            DataType.FUND_FLOW,
            DataType.BOARD_INFO,
            DataType.BOARD_CONSTITUENTS,
            DataType.BILLBOARD,
            DataType.DIVIDEND_HISTORY,
            DataType.FUND_HOLDER,
            DataType.TOP10_HOLDERS,
        },
        cost_model=CostModel.QUOTA_LIMITED,
        quota_limit=QuotaLimit(
            requests_per_minute=50,
            description="免费版: 50次/分钟; 不同接口单独计费"
        ),
        requires_auth=True,
        auth_env_var="TUSHARE_TOKEN",
        availability_check=lambda: has_token,
        supports_batch=set(),  # Tushare 不支持批量查询
        preferred_for={"A股基本面", "财务数据", "板块数据"},
        limitations={"仅A股市场", "有配额限制", "部分高级接口需积分"},
    )


# ==================== EfinanceFetcher ====================

def create_efinance_capability() -> FetcherCapability:
    """
    Efinance 能力声明

    特点：
    - 完全免费
    - 东方财富数据
    - 实时行情支持批量（全市场缓存）
    - 仅 A 股
    """
    return FetcherCapability(
        markets=CAPABILITY_A_STOCK_FULL,
        data_types=CAPABILITY_KLINE_BASIC | CAPABILITY_REALTIME_BASIC | {
            DataType.MARKET_SNAPSHOT,
            DataType.FUND_FLOW,
            DataType.BOARD_INFO,
            DataType.BILLBOARD,
        },
        cost_model=CostModel.FREE,
        quota_limit=None,
        requires_auth=False,
        availability_check=lambda: True,
        supports_batch={
            DataType.REALTIME_QUOTE,
            DataType.MARKET_SNAPSHOT,
        },
        preferred_for={"A股实时行情", "批量查询", "资金流向"},
        limitations={"仅A股市场", "可能触发反爬限流"},
    )


# ==================== AkshareFetcher ====================

def create_akshare_capability() -> FetcherCapability:
    """
    Akshare 能力声明

    特点：
    - 完全免费
    - 支持多市场（A股、港股、美股）
    - 数据类型最全
    - 聚合多个数据源
    """
    return FetcherCapability(
        markets=CAPABILITY_GLOBAL_MARKETS,
        data_types=CAPABILITY_KLINE_BASIC | CAPABILITY_REALTIME_BASIC | CAPABILITY_FINANCIAL_EXTENDED,
        cost_model=CostModel.FREE,
        quota_limit=None,
        requires_auth=False,
        availability_check=lambda: True,
        supports_batch={DataType.MARKET_SNAPSHOT},
        preferred_for={"多市场支持", "特色数据", "筹码分布", "融资融券"},
        limitations={"数据来源混杂", "稳定性一般", "部分接口共享东财后端"},
    )


# ==================== BaostockFetcher ====================

def create_baostock_capability() -> FetcherCapability:
    """
    Baostock 能力声明

    特点：
    - 完全免费
    - 仅 K 线数据
    - 历史数据稳定
    - 仅 A 股
    """
    return FetcherCapability(
        markets={Market.A_STOCK, Market.ETF},
        data_types=CAPABILITY_KLINE_BASIC,
        cost_model=CostModel.FREE,
        quota_limit=None,
        requires_auth=False,
        availability_check=lambda: True,
        supports_batch=set(),
        preferred_for={"历史K线", "数据回测"},
        limitations={"仅K线数据", "无实时行情", "仅A股"},
    )


# ==================== PytdxFetcher ====================

def create_pytdx_capability() -> FetcherCapability:
    """
    Pytdx 能力声明

    特点：
    - 完全免费
    - 通达信行情服务器
    - 免登录
    - 仅 A 股 K 线
    """
    return FetcherCapability(
        markets={Market.A_STOCK},
        data_types={DataType.DAILY_KLINE},
        cost_model=CostModel.FREE,
        quota_limit=None,
        requires_auth=False,
        availability_check=lambda: True,
        supports_batch=set(),
        preferred_for={"历史K线", "免登录"},
        limitations={"仅日K线", "仅A股", "网络不稳定"},
    )


# ==================== YfinanceFetcher ====================

def create_yfinance_capability() -> FetcherCapability:
    """
    YFinance 能力声明

    特点：
    - 完全免费
    - 全球市场
    - 美股/港股首选
    - 稳定性好
    """
    return FetcherCapability(
        markets=CAPABILITY_GLOBAL_MARKETS,
        data_types=CAPABILITY_KLINE_BASIC | CAPABILITY_REALTIME_BASIC | {
            DataType.US_OVERVIEW,
            DataType.FINANCIAL,
        },
        cost_model=CostModel.FREE,
        quota_limit=None,
        requires_auth=False,
        availability_check=lambda: True,
        supports_batch=set(),
        preferred_for={"美股", "港股", "全球市场", "财务数据"},
        limitations={"A股数据不全", "无资金流向"},
    )


# ==================== AlphaVantageFetcher ====================

def create_alphavantage_capability() -> FetcherCapability:
    """
    AlphaVantage 能力声明

    特点：
    - 需要 API key（免费版有限额）
    - 美股专用
    - 基本面数据全
    - 配额: 5次/分钟, 500次/天（免费版）
    """
    has_api_key = bool(os.getenv("ALPHA_VANTAGE_API_KEY"))

    return FetcherCapability(
        markets={Market.US_STOCK},
        data_types={
            DataType.DAILY_KLINE,
            DataType.REALTIME_QUOTE,
            DataType.US_OVERVIEW,
            DataType.FINANCIAL,
            DataType.US_NEWS,
            DataType.US_INSIDER,
            DataType.US_TECH_INDICATORS,
        },
        cost_model=CostModel.QUOTA_LIMITED,
        quota_limit=QuotaLimit(
            requests_per_minute=5,
            requests_per_day=500,
            description="免费版: 5次/分钟, 500次/天"
        ),
        requires_auth=True,
        auth_env_var="ALPHA_VANTAGE_API_KEY",
        availability_check=lambda: has_api_key,
        supports_batch=set(),
        preferred_for={"美股基本面", "财务报表", "新闻分析", "技术指标"},
        limitations={"仅美股", "免费版配额小", "响应较慢"},
    )
