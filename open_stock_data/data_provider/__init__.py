"""
多数据源数据提供层

支持自动故障转移的多数据源股票数据获取：
- TushareFetcher (优先级 0): Tushare Pro A 股数据（需要 token）
- EfinanceFetcher (优先级 1): 东方财富 A 股数据
- AkshareFetcher (优先级 2): Akshare 多市场数据
- PytdxFetcher (优先级 2): 通达信行情服务器（A 股 K 线，免登录）
- BaostockFetcher (优先级 3): Baostock A 股免费数据
- AlphaVantageFetcher (优先级 4): Alpha Vantage 美股基本面和新闻（需要 API key）
- YfinanceFetcher (优先级 5): Yahoo Finance 全局后备

使用示例:
    from open_stock_data.data_provider import DataFetcherManager

    manager = DataFetcherManager()
    df = manager.get_daily_data("600519", days=30)
    quote = manager.get_realtime_quote("600519")
    chip = manager.get_chip_distribution("600519")
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
from .circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerState,
    get_circuit_breaker,
    all_circuit_breaker_names,
)

from .base import (
    BaseFetcher,
    DataFetcherManager,
    DataFetchError,
    RateLimitError,
    NetworkError,
    classify_exception,
    get_error_category,
    NETWORK_EXCEPTIONS,
)

from .efinance_fetcher import EfinanceFetcher
from .akshare_fetcher import AkshareFetcher
from .tushare_fetcher import TushareFetcher
from .baostock_fetcher import BaostockFetcher
from .yfinance_fetcher import YfinanceFetcher
from .alphavantage_fetcher import AlphaVantageFetcher, AlphaVantageRateLimitError
from .pytdx_fetcher import PytdxFetcher

__all__ = [
    # 管理器
    "DataFetcherManager",
    # 数据获取器
    "BaseFetcher",
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
    "CircuitBreaker",
    "CircuitBreakerState",
    "StockType",
    # 异常
    "DataFetchError",
    "RateLimitError",
    "NetworkError",
    "AlphaVantageRateLimitError",
    "classify_exception",
    "get_error_category",
    # 熔断器
    "get_circuit_breaker",
    "all_circuit_breaker_names",
    # 工具函数
    "safe_float",
    "safe_int",
    "to_chinese_columns",
    "to_english_columns",
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
    "NETWORK_EXCEPTIONS",
]
