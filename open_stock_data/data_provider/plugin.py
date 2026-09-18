"""Provider 插件协议、健康指标与事件模型。

设计参考 deepseek-harness 的 Cordis 插件体系：
每个数据源是一个插件，有注册/注销生命周期，向共享上下文发射健康事件。
"""

from __future__ import annotations

import time
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class ProviderMetadata:
    """数据源的元数据描述。"""
    name: str
    priority: int = 50
    tags: tuple[str, ...] = ()


@dataclass
class ProviderHealth:
    """运行时健康指标。"""
    avg_latency_ms: float = 0.0
    success_rate: float = 1.0
    failure_count: int = 0
    circuit_state: str = "CLOSED"  # CLOSED / OPEN / HALF_OPEN
    last_attempt_ms: float = 0.0

    def update_success(self, latency_ms: float) -> None:
        self.avg_latency_ms = self.avg_latency_ms * 0.7 + latency_ms * 0.3
        self.success_rate = min(1.0, self.success_rate + 0.01)
        self.failure_count = 0
        self.last_attempt_ms = latency_ms

    def update_failure(self, latency_ms: float) -> None:
        self.failure_count += 1
        self.success_rate = max(0.0, self.success_rate - 0.05)
        self.last_attempt_ms = latency_ms
        if self.failure_count >= 3:
            self.circuit_state = "OPEN"

    @property
    def score(self) -> float:
        if self.circuit_state == "OPEN":
            return -1.0
        return self.success_rate * 100 - self.avg_latency_ms * 0.1 - self.failure_count * 5


@dataclass(frozen=True)
class ProviderHealthEvent:
    """Provider 健康事件，由 executor 发射，context 收集。"""
    source: str
    success: bool
    latency_ms: float
    timestamp: float = field(default_factory=time.time)


class ProviderPlugin(ABC):
    """每个数据源必须实现的插件协议。"""

    @property
    @abstractmethod
    def metadata(self) -> ProviderMetadata: ...

    @property
    @abstractmethod
    def is_available(self) -> bool: ...

    @abstractmethod
    def execute(self, method_name: str, *args, **kwargs) -> Any:
        """执行一次数据获取。"""
        ...

    def report_health(self, event: ProviderHealthEvent) -> None:
        """接收健康事件并更新内部状态。默认空实现，子类可覆盖。"""
        pass
