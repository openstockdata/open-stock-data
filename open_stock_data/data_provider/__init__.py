"""
多数据源数据提供层

每个数据源是 ProviderPlugin 插件，注册到 ProviderContext 共享上下文。
DynamicRouter 根据实时健康指标自适应调整 fallback 顺序。
"""

from .types import (
    UnifiedRealtimeQuote,
    ChipDistribution,
    RealtimeSource,
    safe_float,
    safe_int,
)
from .columns import (
    to_chinese_columns,
    to_english_columns,
    STANDARD_COLUMNS,
    COLUMN_MAPPING_TO_CN,
    COLUMN_MAPPING_TO_EN,
)
from .boards import (
    normalize_belong_board,
    industry_board_name,
    BELONG_BOARD_LEADING_COLUMNS,
)
from .stock_code import (
    StockType,
    is_etf_code,
    is_hk_code,
    is_us_code,
    is_a_stock_code,
    detect_stock_type,
    normalize_hk_code,
    normalize_stock_code,
    validate_stock_type,
    market_to_stock_type,
    stock_type_to_market,
)
from .plugin import ProviderPlugin, ProviderMetadata, ProviderHealth, ProviderHealthEvent
from .context import ProviderContext
from .dynamic_router import DynamicRouter
from .base import BaseFetcher, DataFetcherManager, DataFetchError, RateLimitError, NetworkError, classify_exception, get_error_category, NETWORK_EXCEPTIONS

from .efinance_fetcher import EfinanceFetcher
from .akshare_fetcher import AkshareFetcher
from .tushare_fetcher import TushareFetcher
from .baostock_fetcher import BaostockFetcher
from .yfinance_fetcher import YfinanceFetcher
from .alphavantage_fetcher import AlphaVantageFetcher, AlphaVantageRateLimitError
from .pytdx_fetcher import PytdxFetcher
from .tickflow_fetcher import TickflowFetcher

__all__ = [
    # 插件体系
    "ProviderPlugin",
    "ProviderMetadata",
    "ProviderHealth",
    "ProviderHealthEvent",
    "ProviderContext",
    "DynamicRouter",
    "BaseFetcher",
    "DataFetcherManager",
    # 数据获取器
    "TickflowFetcher",
    "EfinanceFetcher",
    "AkshareFetcher",
    "TushareFetcher",
    "BaostockFetcher",
    "YfinanceFetcher",
    "AlphaVantageFetcher",
    "PytdxFetcher",
    # 数据类型
    "UnifiedRealtimeQuote",
    "ChipDistribution",
    "RealtimeSource",
    "StockType",
    # 异常
    "DataFetchError",
    "RateLimitError",
    "NetworkError",
    "AlphaVantageRateLimitError",
    "classify_exception",
    "get_error_category",
    "NETWORK_EXCEPTIONS",
    # 工具函数
    "safe_float",
    "safe_int",
    "to_chinese_columns",
    "to_english_columns",
    "normalize_belong_board",
    "industry_board_name",
    "BELONG_BOARD_LEADING_COLUMNS",
    "is_etf_code",
    "is_hk_code",
    "is_us_code",
    "is_a_stock_code",
    "detect_stock_type",
    "normalize_hk_code",
    "normalize_stock_code",
    "validate_stock_type",
    "market_to_stock_type",
    "stock_type_to_market",
    # 常量
    "STANDARD_COLUMNS",
    "COLUMN_MAPPING_TO_CN",
    "COLUMN_MAPPING_TO_EN",
]