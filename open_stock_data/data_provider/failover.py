"""
通用故障转移装饰器和上下文管理

提供统一的故障转移逻辑，消除重复代码。
"""

import logging
import time
from dataclasses import dataclass, field
from typing import TypeVar, Callable, Optional, List, Any, Dict, Set
from functools import wraps

from .capability import Market, DataType
from .circuit_breaker import CircuitBreaker

_LOGGER = logging.getLogger(__name__)

T = TypeVar('T')


@dataclass
class FetchContext:
    """
    数据获取上下文

    封装请求的元信息，用于策略选择和日志记录。
    """
    # 方法名（如 "get_daily_data"）
    method_name: str

    # 请求参数
    args: tuple = field(default_factory=tuple)
    kwargs: dict = field(default_factory=dict)

    # 市场类型
    market: Optional[Market] = None

    # 数据类型
    data_type: Optional[DataType] = None

    # 是否批量查询
    is_batch: bool = False

    # 股票代码列表（批量查询时）
    stock_codes: List[str] = field(default_factory=list)

    # 是否交易时段
    is_trading_time: bool = False

    # 开始时间（用于性能监控）
    start_time: float = field(default_factory=time.time)

    def elapsed_ms(self) -> float:
        """返回已耗时（毫秒）"""
        return (time.time() - self.start_time) * 1000


@dataclass
class FetchResult:
    """
    获取结果封装

    包含结果数据和元信息。
    """
    # 结果数据
    data: Any

    # 使用的数据源名称
    source_name: str

    # 耗时（毫秒）
    latency_ms: float

    # 是否来自缓存
    from_cache: bool = False

    # 尝试的数据源数量
    attempts: int = 1

    # 失败的数据源列表
    failed_sources: List[str] = field(default_factory=list)


class FailoverCoordinator:
    """
    故障转移协调器

    管理故障转移流程的核心逻辑。
    """

    def __init__(self):
        self.failed_backend_scopes: Set[str] = set()

    def should_skip_fetcher(
        self,
        fetcher: 'BaseFetcher',
        circuit_breaker: CircuitBreaker,
        context: FetchContext,
        manager: 'DataFetcherManager',
    ) -> tuple[bool, Optional[str]]:
        """
        判断是否应该跳过某个 fetcher

        Returns:
            (should_skip, reason)
        """
        # 1. 检查熔断器
        if not circuit_breaker.is_available(fetcher.name):
            return True, f"circuit_breaker_open"

        # 2. 检查后端分组故障
        backend_scope = manager._get_backend_failure_scope(
            fetcher, context.method_name, *context.args, **context.kwargs
        )
        if backend_scope and backend_scope in self.failed_backend_scopes:
            return True, f"backend_scope_failed:{backend_scope}"

        # 3. 检查能力声明（如果 fetcher 声明了 capability）
        if hasattr(fetcher, 'capability'):
            capability = fetcher.capability

            # 检查是否可用（运行时条件）
            if not capability.is_available():
                return True, "capability_unavailable"

            # 检查市场支持
            if context.market and not capability.supports_market(context.market):
                return True, f"market_not_supported:{context.market.value}"

            # 检查数据类型支持
            if context.data_type and not capability.supports_data_type(context.data_type):
                return True, f"data_type_not_supported:{context.data_type.value}"

        return False, None

    def mark_backend_failed(
        self,
        fetcher: 'BaseFetcher',
        context: FetchContext,
        manager: 'DataFetcherManager',
    ) -> Optional[str]:
        """标记后端失败"""
        backend_scope = manager._get_backend_failure_scope(
            fetcher, context.method_name, *context.args, **context.kwargs
        )
        if backend_scope:
            self.failed_backend_scopes.add(backend_scope)
        return backend_scope

    def handle_fetch_error(
        self,
        fetcher: 'BaseFetcher',
        error: Exception,
        context: FetchContext,
        circuit_breaker: CircuitBreaker,
        manager: 'DataFetcherManager',
    ) -> Dict[str, Any]:
        """
        统一的错误处理

        Returns:
            错误信息字典（用于日志和统计）
        """
        from .base import _is_network_error, RateLimitError

        source_name = fetcher.name
        error_type = type(error).__name__
        error_msg = str(error)

        # 记录熔断器失败
        if isinstance(error, RateLimitError):
            # 动态冷却时间
            cooldown = getattr(error, 'retry_after', None)
            if cooldown:
                circuit_breaker.force_open(source_name, cooldown)
                _LOGGER.warning(
                    f"[{source_name}] 触发限流，熔断 {cooldown}s: {error_msg[:100]}"
                )
            else:
                circuit_breaker.record_failure(source_name, error_msg)
        else:
            circuit_breaker.record_failure(source_name, error_msg)

        # 网络错误标记后端失败
        backend_scope = None
        if _is_network_error(error):
            backend_scope = self.mark_backend_failed(fetcher, context, manager)
            if backend_scope:
                _LOGGER.debug(
                    f"[{source_name}] 网络错误，标记后端作用域失败: {backend_scope}"
                )

        # 记录警告日志
        log_prefix = f"[{source_name}] {context.method_name}"
        if context.args:
            log_prefix += f"({context.args[0][:20] if context.args else ''})"

        _LOGGER.warning(
            f"{log_prefix} 失败: {error_type}: {error_msg[:150]}"
        )

        return {
            'source': source_name,
            'error_type': error_type,
            'error_msg': error_msg,
            'backend_scope': backend_scope,
            'is_network_error': _is_network_error(error),
            'is_rate_limit': isinstance(error, RateLimitError),
        }


def with_failover(
    circuit_breaker_name: str,
    validate_result: Optional[Callable[[Any], bool]] = None,
    cache_result: bool = False,
    data_type: Optional[DataType] = None,
):
    """
    通用故障转移装饰器

    Usage:
        @with_failover(
            circuit_breaker_name="realtime",
            validate_result=lambda q: q is not None and q.has_basic_data(),
            cache_result=True,
            data_type=DataType.REALTIME_QUOTE
        )
        def get_realtime_quote(self, stock_code: str, stock_type: Optional[StockType] = None):
            pass

    Args:
        circuit_breaker_name: 熔断器名称
        validate_result: 结果验证函数（返回 True 表示有效结果）
        cache_result: 是否缓存结果
        data_type: 数据类型（用于能力检查）
    """

    def decorator(func: Callable[..., T]) -> Callable[..., Optional[T]]:
        method_name = func.__name__

        @wraps(func)
        def wrapper(self, *args, **kwargs) -> Optional[T]:
            # 导入放在这里避免循环依赖
            from .circuit_breaker import get_circuit_breaker
            from .stock_code import detect_stock_type

            # 构建上下文
            context = FetchContext(
                method_name=method_name,
                args=args,
                kwargs=kwargs,
                data_type=data_type,
            )

            # 推断市场类型
            if args and isinstance(args[0], str):
                stock_code = args[0]
                stock_type = kwargs.get('stock_type')
                if not stock_type and hasattr(detect_stock_type, '__call__'):
                    from .stock_code import StockType
                    stock_type = detect_stock_type(stock_code)
                    # 转换 StockType 到 Market
                    market_map = {
                        'a_stock': Market.A_STOCK,
                        'hk_stock': Market.HK_STOCK,
                        'us_stock': Market.US_STOCK,
                        'etf': Market.ETF,
                    }
                    if stock_type and hasattr(stock_type, 'value'):
                        context.market = market_map.get(stock_type.value)

            # 获取熔断器
            circuit_breaker = get_circuit_breaker(circuit_breaker_name)

            # 获取 fetcher 列表
            fetchers = self._get_fetchers_for(method_name, kwargs.get('stock_type'))

            # 故障转移协调器
            coordinator = FailoverCoordinator()

            # 尝试结果验证
            def is_valid(result):
                if validate_result:
                    return validate_result(result)
                return result is not None

            # 尝试每个 fetcher
            failed_sources = []
            for fetcher in fetchers:
                source_name = fetcher.name

                # 跳过检查
                should_skip, skip_reason = coordinator.should_skip_fetcher(
                    fetcher, circuit_breaker, context, self
                )
                if should_skip:
                    _LOGGER.debug(
                        f"[{source_name}] 跳过 {method_name}: {skip_reason}"
                    )
                    continue

                # 尝试获取数据
                try:
                    fetch_start = time.time()
                    result = getattr(fetcher, method_name)(*args, **kwargs)
                    latency_ms = (time.time() - fetch_start) * 1000

                    # 验证结果
                    if is_valid(result):
                        circuit_breaker.record_success(source_name)

                        _LOGGER.info(
                            f"[{source_name}] {method_name} 成功 "
                            f"(耗时: {latency_ms:.0f}ms, 尝试: {len(failed_sources) + 1})"
                        )

                        # 缓存结果（如果需要）
                        if cache_result and hasattr(result, '__dict__'):
                            # 标记数据源
                            if hasattr(result, 'attrs'):
                                result.attrs['source'] = source_name
                            elif isinstance(result, dict):
                                result['_source'] = source_name

                        return result

                except Exception as e:
                    # 统一错误处理
                    error_info = coordinator.handle_fetch_error(
                        fetcher, e, context, circuit_breaker, self
                    )
                    failed_sources.append(source_name)
                    continue

            # 所有数据源都失败
            _LOGGER.error(
                f"{method_name} 所有数据源失败 (尝试: {len(failed_sources)}): {failed_sources}"
            )
            return None

        return wrapper

    return decorator
