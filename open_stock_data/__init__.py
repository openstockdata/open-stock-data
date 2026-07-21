"""
open-stock-data: Core library for stock/crypto data with multi-source failover

提供股票、加密货币数据获取的核心库，支持多数据源自动故障转移。
"""

from importlib.metadata import version, PackageNotFoundError

from .logging_safety import install_log_redaction
from .client import OpenStockDataClient, get_default_client

install_log_redaction()

try:
    __version__ = version("open-stock-data")
except PackageNotFoundError:
    __version__ = "0.0.0-dev"

__all__ = ["OpenStockDataClient", "get_default_client", "__version__"]
