"""
股票数据异常类定义

提供细粒度的异常分类，便于针对性处理和重试。
"""

from typing import Optional


class StockDataError(Exception):
    """股票数据错误基类"""

    def __init__(self, message: str, code: Optional[str] = None, source: Optional[str] = None):
        self.message = message
        self.code = code
        self.source = source
        super().__init__(self._format_message())

    def _format_message(self) -> str:
        parts = [self.message]
        if self.code:
            parts.append(f"[代码: {self.code}]")
        if self.source:
            parts.append(f"[数据源: {self.source}]")
        return " ".join(parts)


# ==================== 数据获取错误 ====================

class DataFetchError(StockDataError):
    """数据获取错误基类"""
    pass


class NetworkError(DataFetchError):
    """网络连接错误（可重试）"""
    pass


class RequestTimeoutError(DataFetchError):
    """请求超时错误（可重试）"""
    pass


class RateLimitError(DataFetchError):
    """API 限流错误（需等待后重试）"""

    def __init__(
        self,
        message: str,
        code: Optional[str] = None,
        source: Optional[str] = None,
        retry_after: Optional[int] = None,
        limit_type: str = "unknown"
    ):
        self.retry_after = retry_after
        self.limit_type = limit_type
        super().__init__(message, code, source)


class AuthenticationError(DataFetchError):
    """认证错误（API Key 无效等）"""
    pass


# ==================== 数据验证错误 ====================

class DataValidationError(StockDataError):
    """数据验证错误基类"""
    pass


class EmptyDataError(DataValidationError):
    """返回数据为空"""
    pass


class DataParseError(DataValidationError):
    """数据解析错误"""
    pass


# ==================== 异常分类 ====================

def classify_exception(error: Exception, source: str = None, code: str = None) -> StockDataError:
    """将通用异常转换为特定的 StockDataError 子类"""
    import requests

    error_msg = str(error)

    if isinstance(error, StockDataError):
        return error

    if isinstance(error, (ConnectionError, requests.exceptions.ConnectionError)):
        return NetworkError(f"网络连接失败: {error_msg}", code=code, source=source)

    if isinstance(error, (RequestTimeoutError, requests.exceptions.Timeout)):
        return RequestTimeoutError(f"请求超时: {error_msg}", code=code, source=source)

    if isinstance(error, requests.exceptions.RequestException):
        return NetworkError(f"请求失败: {error_msg}", code=code, source=source)

    if isinstance(error, (ValueError, KeyError, TypeError)):
        return DataParseError(f"数据解析失败: {error_msg}", code=code, source=source)

    if isinstance(error, IndexError):
        return EmptyDataError(f"数据为空或索引越界: {error_msg}", code=code, source=source)

    if "rate limit" in error_msg.lower() or "too many requests" in error_msg.lower():
        return RateLimitError(f"API限流: {error_msg}", code=code, source=source)

    if "unauthorized" in error_msg.lower() or "invalid api" in error_msg.lower():
        return AuthenticationError(f"认证失败: {error_msg}", code=code, source=source)

    return DataFetchError(error_msg, code=code, source=source)


def get_error_category(error: Exception) -> str:
    """获取异常的分类标签，用于日志和监控"""
    if isinstance(error, NetworkError):
        return "NETWORK"
    if isinstance(error, RateLimitError):
        return "RATE_LIMIT"
    if isinstance(error, AuthenticationError):
        return "AUTH"
    if isinstance(error, DataParseError):
        return "PARSE"
    if isinstance(error, EmptyDataError):
        return "EMPTY"
    if isinstance(error, StockDataError):
        return "DATA_ERROR"
    return "UNKNOWN"
