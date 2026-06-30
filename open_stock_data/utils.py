"""
公共工具模块

包含全局数据管理器、数据源辅助函数、HTTP 重试、缓存包装器等。
从 stock_data_mcp/core.py 提取，去除 MCP 相关代码。
"""

import os
import time
import atexit
import random
import logging
import threading
from datetime import date, datetime, timedelta
from typing import Any, Callable, Optional

import requests
import pandas as pd
from pydantic import Field

try:
    import akshare as ak
except ImportError:
    ak = None

from .data_provider import DataFetcherManager, NETWORK_EXCEPTIONS
from .logging_safety import sanitize_for_logging
from .eastmoney_patch import enable_eastmoney_patch

_LOGGER = logging.getLogger(__name__)

ENABLE_EASTMONEY_PATCH = os.getenv("ENABLE_EASTMONEY_PATCH", "false").strip().lower() in {"1", "true", "yes", "on"}
if ENABLE_EASTMONEY_PATCH:
    enable_eastmoney_patch()

# 全局 HTTP Session（连接池复用 TCP 连接）
_http_session = requests.Session()
atexit.register(_http_session.close)

# 全局数据获取管理器（支持多数据源自动故障转移）
_data_manager = None
_data_manager_lock = threading.Lock()
def get_data_manager() -> DataFetcherManager:
    """获取全局数据管理器（延迟初始化，线程安全）"""
    global _data_manager
    if _data_manager is None:
        with _data_manager_lock:
            if _data_manager is None:
                _data_manager = DataFetcherManager()
    return _data_manager


# 数据源名称映射：将 Fetcher 类名转换为友好显示名称
_SOURCE_NAME_MAP = {
    # Fetcher 类名映射
    "EfinanceFetcher": "efinance",
    "AkshareFetcher": "akshare",
    "TushareFetcher": "tushare",
    "BaostockFetcher": "baostock",
    "YfinanceFetcher": "yfinance",
    "AlphaVantage": "alphavantage",
    "AlphaVantageFetcher": "alphavantage",
}

# akshare 函数后缀到数据源的映射
_AKSHARE_SUFFIX_MAP = {
    "_em": "东方财富",
    "_sina": "新浪",
    "_ths": "同花顺",
    "_cninfo": "巨潮资讯",
    "_qq": "腾讯",
    "_163": "网易",
    "_szse": "深交所",
    "_sse": "上交所",
}

# akshare 无后缀但数据源已知的函数（部分列表）
_AKSHARE_KNOWN_SOURCES = {
    "stock_sector_fund_flow_rank": "东方财富",
    "stock_board_industry_name": "东方财富",
    "stock_board_concept_name": "东方财富",
    "stock_zh_a_spot": "东方财富",
    "stock_zh_a_hist": "东方财富",
    "stock_individual_info": "东方财富",
    "stock_circulate_stock_holder": "东方财富",
    "stock_main_stock_holder": "东方财富",
    "stock_dzjy": "东方财富",
    "stock_hsgt_fund_flow": "东方财富",
    "stock_margin": "交易所",
    "stock_report_fund_hold": "东方财富",
    "stock_report_disclosure": "巨潮资讯",
    "stock_financial_abstract": "同花顺",
    "stock_financial_analysis_indicator": "同花顺",
    "stock_history_dividend": "东方财富",
    "stock_a_ttm_lyr": "乐咕乐股",
    "stock_a_all_pb": "乐咕乐股",
    "stock_industry_pe_ratio": "巨潮资讯",
    "stock_zh_a_gdhs": "东方财富",
}


def format_source_name(source: str) -> str:
    """格式化数据源名称为友好显示格式"""
    if not source:
        return "-"
    base_source = source.split("_")[0]
    friendly_name = _SOURCE_NAME_MAP.get(base_source, source)
    if "_" in source:
        suffix = source.split("_", 1)[1]
        friendly_name = f"{friendly_name} ({suffix})"
    return friendly_name


def get_akshare_source(func) -> str:
    """
    从 akshare 函数判断数据来源

    Args:
        func: akshare 函数对象或函数名字符串

    Returns:
        格式化的数据来源字符串，如 "akshare (东方财富)"
    """
    if func is None:
        return "akshare"

    # 获取函数名
    func_name = func.__name__ if callable(func) else str(func)

    # 1. 先检查已知函数映射（精确匹配）
    for known_func, source_name in _AKSHARE_KNOWN_SOURCES.items():
        if func_name.startswith(known_func):
            return f"akshare ({source_name})"

    # 2. 再检查后缀映射
    for suffix, source_name in _AKSHARE_SUFFIX_MAP.items():
        if suffix in func_name:
            return f"akshare ({source_name})"

    return "akshare"


# 公共 Field 定义
field_symbol = Field(description="股票代码（纯数字或字母组合，如600519、AAPL、HK00700）")
field_market = Field("sh", description="股票市场，仅支持: sh(上证), sz(深证), hk(港股), us(美股), 不支持加密货币")


def resolve_field(value, default):
    """将 FieldInfo 对象解析为真实默认值，防止直接调用工具函数时参数未经 Pydantic 验证。"""
    from pydantic.fields import FieldInfo
    if isinstance(value, FieldInfo):
        from pydantic_core import PydanticUndefined
        return default if value.default is PydanticUndefined else value.default
    return value

# API 基础 URL
OKX_BASE_URL = os.getenv("OKX_BASE_URL") or "https://www.okx.com"
BINANCE_BASE_URL = os.getenv("BINANCE_BASE_URL") or "https://www.binance.com"
ALPHA_VANTAGE_API_KEY = os.getenv("ALPHA_VANTAGE_API_KEY")
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10) AppleWebKit/537.36 Chrome/139"


def _http_get_with_retry(url: str, params=None, headers=None, max_retries: int = 3, timeout: int = 20) -> Optional[requests.Response]:
    """带重试的 HTTP GET 请求"""
    return _http_request_with_retry("GET", url, params=params, headers=headers, max_retries=max_retries, timeout=timeout)


def _http_post_with_retry(url: str, json=None, headers=None, max_retries: int = 3, timeout: int = 20) -> Optional[requests.Response]:
    """带重试的 HTTP POST 请求"""
    return _http_request_with_retry("POST", url, json=json, headers=headers, max_retries=max_retries, timeout=timeout)


def _http_request_with_retry(method: str, url: str, params=None, json=None, headers=None, max_retries: int = 3, timeout: int = 20) -> Optional[requests.Response]:
    """带重试的 HTTP 请求（指数退避）"""
    if headers is None:
        headers = {"User-Agent": USER_AGENT}
    last_error = None
    for i in range(max_retries):
        try:
            res = _http_session.request(method, url, params=params, json=json, headers=headers, timeout=timeout)
            if res.status_code == 200:
                return res
        except Exception as e:
            last_error = e
            _LOGGER.warning(
                "HTTP %s 第%s次失败 [%s]: %s",
                method,
                i + 1,
                sanitize_for_logging(url),
                sanitize_for_logging(str(e)),
            )
        if i < max_retries - 1:
            time.sleep(min(2 ** i + random.uniform(0, 1), 30))
    if last_error:
        raise last_error
    return None


def recent_trade_date() -> date:
    """获取最近交易日"""
    now = datetime.now().date()
    if ak is None:
        return now
    dfs = get_data_manager().fetch_akshare(
        ak.tool_trade_date_hist_sina,
        ttl=86400 * 7,
    )
    if dfs is None:
        return now
    dfs.sort_values("trade_date", ascending=False, inplace=True)
    for d in dfs["trade_date"]:
        if d <= now:
            return d
    return now

def fetch_with_retry(func: Callable, max_retries: int = 3, delay: float = 1.0, initial_delay: float = 0.5, **kwargs) -> Any:
    """
    带重试的数据获取（指数退避 + 反爬虫延迟）

    Args:
        func: 获取函数
        max_retries: 最大重试次数
        delay: 重试间隔基数（秒）
        initial_delay: 首次请求前延迟（秒）
        **kwargs: 传递给函数的参数

    Returns:
        函数返回值或 None
    """
    last_error = None
    for i in range(max_retries):
        try:
            sleep_time = initial_delay + random.uniform(0.5, 1.5)
            time.sleep(sleep_time)
            result = func(**kwargs)
            if result is not None:
                return result
        except NETWORK_EXCEPTIONS as e:
            last_error = e
            _LOGGER.warning(f"[{func.__name__}] 第{i+1}次尝试失败(网络): {type(e).__name__}")
        except Exception as e:
            last_error = e
            _LOGGER.warning(f"[{func.__name__}] 第{i+1}次尝试失败: {e}")
        if i < max_retries - 1:
            time.sleep(min(delay * 2 ** i + random.uniform(0, 1), 30))
    return None


def _detect_stock_market(symbol: str) -> str:
    """根据股票代码判断市场"""
    if symbol.startswith(('6', '5')):
        return 'sh'
    elif symbol.startswith(('0', '3', '1', '2')):
        return 'sz'
    return 'sh'
