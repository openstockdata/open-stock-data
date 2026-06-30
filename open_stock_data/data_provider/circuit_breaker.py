"""熔断器实现与全局注册表。"""

import time
import logging
import threading
from enum import Enum
from dataclasses import dataclass
from typing import Optional, Dict, Any

_LOGGER = logging.getLogger(__name__)


class CircuitBreakerState(Enum):
    """熔断器状态"""
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class SourceState:
    """数据源状态"""
    state: CircuitBreakerState = CircuitBreakerState.CLOSED
    failure_count: int = 0
    last_failure_time: float = 0.0
    half_open_calls: int = 0
    last_error: Optional[str] = None
    custom_cooldown: Optional[float] = None  # 动态冷却时间（秒），覆盖默认值


class CircuitBreaker:
    """熔断器实现"""

    def __init__(
        self,
        failure_threshold: int = 3,
        cooldown_seconds: float = 300.0,
        half_open_max_calls: int = 1,
    ):
        self.failure_threshold = failure_threshold
        self.cooldown_seconds = cooldown_seconds
        self.half_open_max_calls = half_open_max_calls
        self._states: Dict[str, SourceState] = {}
        self._lock = threading.Lock()

    def _get_state(self, source: str) -> SourceState:
        if source not in self._states:
            self._states[source] = SourceState()
        return self._states[source]

    def is_available(self, source: str) -> bool:
        with self._lock:
            state = self._get_state(source)

            if state.state == CircuitBreakerState.CLOSED:
                return True

            if state.state == CircuitBreakerState.OPEN:
                cooldown = state.custom_cooldown or self.cooldown_seconds
                if time.time() - state.last_failure_time >= cooldown:
                    state.state = CircuitBreakerState.HALF_OPEN
                    state.half_open_calls = 1
                    state.custom_cooldown = None
                    _LOGGER.info(f"数据源 {source} 进入恢复状态")
                    return True
                return False

            if state.state == CircuitBreakerState.HALF_OPEN:
                if state.half_open_calls < self.half_open_max_calls:
                    state.half_open_calls += 1
                    return True
                return False

            return False

    def record_success(self, source: str):
        with self._lock:
            state = self._get_state(source)

            if state.state == CircuitBreakerState.HALF_OPEN:
                state.state = CircuitBreakerState.CLOSED
                state.failure_count = 0
                state.last_error = None
                _LOGGER.info(f"数据源 {source} 恢复正常")
            elif state.state == CircuitBreakerState.CLOSED:
                state.failure_count = 0

    def record_failure(self, source: str, error: Optional[str] = None):
        with self._lock:
            state = self._get_state(source)
            state.failure_count += 1
            state.last_failure_time = time.time()
            state.last_error = error

            if state.state == CircuitBreakerState.HALF_OPEN:
                state.state = CircuitBreakerState.OPEN
                _LOGGER.warning(f"数据源 {source} 恢复失败，重新熔断")
            elif state.state == CircuitBreakerState.CLOSED:
                if state.failure_count >= self.failure_threshold:
                    state.state = CircuitBreakerState.OPEN
                    _LOGGER.warning(f"数据源 {source} 触发熔断，错误: {error}")

    def force_open(self, source: str, cooldown_seconds: float, error: Optional[str] = None):
        with self._lock:
            state = self._get_state(source)
            state.state = CircuitBreakerState.OPEN
            state.last_failure_time = time.time()
            state.last_error = error
            state.custom_cooldown = cooldown_seconds

    def get_status(self) -> Dict[str, Dict[str, Any]]:
        with self._lock:
            return {
                source: {
                    'state': state.state.value,
                    'failure_count': state.failure_count,
                    'last_error': state.last_error,
                }
                for source, state in self._states.items()
            }

    def reset(self, source: Optional[str] = None):
        with self._lock:
            if source:
                if source in self._states:
                    self._states[source] = SourceState()
            else:
                self._states.clear()


class CircuitBreakerRegistry:
    """熔断器集中管理和存储"""

    def __init__(self):
        self._breakers: Dict[str, CircuitBreaker] = {}
        self._lock = threading.Lock()

    def get_or_create(
        self,
        name: str,
        failure_threshold: int = 3,
        cooldown_seconds: float = 300.0,
    ) -> CircuitBreaker:
        with self._lock:
            if name in self._breakers:
                return self._breakers[name]
            breaker = CircuitBreaker(
                failure_threshold=failure_threshold,
                cooldown_seconds=cooldown_seconds,
            )
            self._breakers[name] = breaker
            return breaker

    def get(self, name: str) -> Optional[CircuitBreaker]:
        return self._breakers.get(name)

    def reset(self, name: Optional[str] = None):
        with self._lock:
            if name:
                if name in self._breakers:
                    self._breakers[name].reset()
            else:
                for breaker in self._breakers.values():
                    breaker.reset()

    def get_all_status(self) -> Dict[str, Dict[str, Any]]:
        return {name: breaker.get_status() for name, breaker in self._breakers.items()}


# 表驱动配置：name → (failure_threshold, cooldown_seconds)
_BREAKER_CFG: Dict[str, tuple] = {
    "realtime":      (3, 300.0),   # 实时行情，5分钟
    "chip":          (2, 600.0),   # 筹码分布，10分钟
    "daily":         (3, 300.0),   # 日K线，5分钟
    "fund_flow":     (3, 300.0),   # 资金流向，5分钟
    "board":         (3, 600.0),   # 板块数据，10分钟
    "billboard":     (3, 300.0),   # 龙虎榜，5分钟
    "us_financials": (3, 600.0),   # 美股基本面，10分钟
    "margin":        (3, 300.0),   # 融资融券，5分钟
    "industry_pe":   (3, 600.0),   # 行业PE，10分钟
    "spot":          (3, 300.0),   # 全市场行情，5分钟
    "dividend":      (3, 600.0),   # 分红历史，10分钟
    "fund_holder":   (3, 600.0),   # 基金持仓，10分钟
    "top10_holders": (3, 600.0),   # 十大股东，10分钟
    "financial_ext": (3, 600.0),   # 业绩预告/报表/快报、分红送配，10分钟
}


_registry = CircuitBreakerRegistry()


def get_circuit_breaker(name: str) -> CircuitBreaker:
    """根据名称获取熔断器实例（线程安全），配置见 _BREAKER_CFG。"""
    if name not in _BREAKER_CFG:
        raise KeyError(f"未配置的熔断器: {name}（可选: {list(_BREAKER_CFG)}）")
    threshold, cooldown = _BREAKER_CFG[name]
    return _registry.get_or_create(name, threshold, cooldown)


def all_circuit_breaker_names() -> tuple:
    """返回所有已配置的熔断器名称。"""
    return tuple(_BREAKER_CFG.keys())
