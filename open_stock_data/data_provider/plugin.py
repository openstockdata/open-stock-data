"""Provider 插件协议、健康指标与事件模型。

设计参考 deepseek-harness 的 Cordis 插件体系：
每个数据源是一个插件，有注册/注销生命周期，向共享上下文发射健康事件。
"""

from __future__ import annotations

import os
import time
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional

_LOGGER = logging.getLogger(__name__)


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


# 健康态参数（唯一一套，可用环境变量覆盖）：
#   连续失败达阈值 → OPEN（路由跳过该源），冷却结束 → HALF_OPEN 允许探测；
#   探测成功 → CLOSED，探测失败 → 重新 OPEN。配额类强制冷却用 block_for() 叠加。
DEFAULT_FAILURE_THRESHOLD = max(1, int(_env_float("PROVIDER_HEALTH_FAILURE_THRESHOLD", 3)))
DEFAULT_COOLDOWN_SECONDS = max(0.0, _env_float("PROVIDER_HEALTH_COOLDOWN_SECONDS", 300.0))

# 评分权重。priority（整数档位）是主排序键；健康项只在同优先级之间做微调：
#   - 成功率：从 1.0 跌到 0.0 最多扣 SUCCESS_RATE_WEIGHT 分（每次失败 -0.05 → -0.5 分）
#   - 延迟：封顶 LATENCY_CAP_MS 后最多扣 LATENCY_WEIGHT 分，不足一个档位，只能打破平局
# 已 OPEN 的 provider 由路由直接跳过，不再参与排序，因此不需要额外的降级惩罚分。
SUCCESS_RATE_WEIGHT = 10.0
LATENCY_WEIGHT = 0.5
LATENCY_CAP_MS = 10_000.0


@dataclass(frozen=True)
class ProviderMetadata:
    """数据源的元数据描述。"""
    name: str
    priority: int = 50
    tags: tuple[str, ...] = ()


@dataclass
class ProviderHealth:
    """单个数据源的健康状态（唯一事实来源，取代旧的按路由组熔断器）。

    `failure_count` 是**连续**失败次数，任一成功即清零。连续失败达到 `failure_threshold`
    后进入 OPEN（路由跳过该源），持续 `cooldown_seconds`；冷却结束变为 HALF_OPEN，
    下一次尝试即探测：成功 → CLOSED，失败 → 重新 OPEN。

    `blocked_until`（monotonic）是配额类**强制冷却**（RateLimitError 携带的
    retry_after / 日配额到次日 0 点等），期间无条件 OPEN，与连续失败计数相互独立。
    """

    avg_latency_ms: float = 0.0
    success_rate: float = 1.0
    failure_count: int = 0
    last_attempt_ms: float = 0.0
    last_failure_at: float = 0.0
    blocked_until: float = 0.0
    block_reason: str = ""
    failure_threshold: int = DEFAULT_FAILURE_THRESHOLD
    cooldown_seconds: float = DEFAULT_COOLDOWN_SECONDS

    def update_success(self, latency_ms: float) -> None:
        self.avg_latency_ms = self.avg_latency_ms * 0.7 + latency_ms * 0.3
        self.success_rate = min(1.0, self.success_rate + 0.01)
        self.failure_count = 0
        self.last_attempt_ms = latency_ms
        self.blocked_until = 0.0
        self.block_reason = ""

    def update_failure(self, latency_ms: float, now: Optional[float] = None) -> None:
        self.failure_count += 1
        self.success_rate = max(0.0, self.success_rate - 0.05)
        self.last_attempt_ms = latency_ms
        self.last_failure_at = time.monotonic() if now is None else now

    def block_for(self, seconds: float, reason: str = "") -> None:
        """强制冷却：seconds 内一律视为 OPEN（用于配额/限流的 retry_after）。"""
        self.blocked_until = time.monotonic() + max(0.0, float(seconds))
        self.block_reason = reason

    # -- 状态 --

    def state_at(self, now: Optional[float] = None) -> str:
        """给定时刻的熔断状态：CLOSED / OPEN / HALF_OPEN（时间基准为 monotonic）。"""
        if now is None:
            now = time.monotonic()
        if self.blocked_until and now < self.blocked_until:
            return "OPEN"
        if self.failure_count < self.failure_threshold:
            return "CLOSED"
        if now - self.last_failure_at < self.cooldown_seconds:
            return "OPEN"
        return "HALF_OPEN"

    @property
    def circuit_state(self) -> str:
        return self.state_at()

    @property
    def is_degraded(self) -> bool:
        """是否处于 OPEN（路由会跳过该源）。"""
        return self.circuit_state == "OPEN"

    # -- 评分 --

    @property
    def score(self) -> float:
        """健康调整分，恒 ≤ 0；由 ProviderContext 叠加到 priority 上得到综合分。"""
        penalty = (1.0 - self.success_rate) * SUCCESS_RATE_WEIGHT
        penalty += min(self.avg_latency_ms, LATENCY_CAP_MS) / LATENCY_CAP_MS * LATENCY_WEIGHT
        return -penalty

    def to_dict(self) -> dict[str, Any]:
        """状态快照（含推导出的 circuit_state），供 data_source_status 等只读展示。"""
        return {
            "avg_latency_ms": round(self.avg_latency_ms, 1),
            "success_rate": round(self.success_rate, 3),
            "failure_count": self.failure_count,
            "circuit_state": self.circuit_state,
            "block_reason": self.block_reason,
            "last_attempt_ms": round(self.last_attempt_ms, 1),
            "last_failure_at": self.last_failure_at,
            "score": round(self.score, 3),
        }


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
