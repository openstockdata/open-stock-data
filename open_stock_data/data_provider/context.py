"""ProviderContext：共享上下文，管理所有 provider 的注册、健康指标和事件分发。

类似 deepseek-harness 的 Cordis 共享上下文——所有数据源的状态集中于此，
DynamicRouter 从中获取实时最优 provider 排序。
"""

from __future__ import annotations

import time
import threading
import logging
from collections import defaultdict
from typing import Optional
from enum import Enum

from .plugin import ProviderPlugin, ProviderHealth, ProviderHealthEvent, ProviderMetadata
from .contracts import Operation

_LOGGER = logging.getLogger(__name__)

# priority 每档折算的分数；健康调整分（ProviderHealth.score）以此为尺度设计，见 plugin.py。
PRIORITY_WEIGHT = 1.0


class ProviderContext:
    """所有 provider 的共享注册中心 + 事件总线 + 健康指标存储。

    单例模式，全局唯一事实来源。
    """

    _instance: Optional["ProviderContext"] = None
    _lock = threading.Lock()

    def __init__(self):
        self._providers: dict[str, ProviderPlugin] = {}
        self._health: dict[str, ProviderHealth] = {}
        self._listeners: dict[str, list] = defaultdict(list)
        self._internal_lock = threading.RLock()
        self._priority_overrides: dict[str, int] = {}

    def set_priority(self, name: str, priority: int) -> None:
        """动态设置 provider 优先级，立即生效。"""
        with self._internal_lock:
            self._priority_overrides[name] = priority
            _LOGGER.info("[provider_context] priority updated: %s=%d", name, priority)

    def get_priority(self, name: str) -> Optional[int]:
        """获取 provider 当前优先级（含覆盖）。"""
        return self._priority_overrides.get(name)

    # -- 单例 --

    @classmethod
    def default(cls) -> "ProviderContext":
        """返回全局单例。"""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        """重置单例（测试用）。"""
        with cls._lock:
            cls._instance = None

    # -- 注册/注销 --

    def register(self, plugin: ProviderPlugin) -> None:
        """注册一个 provider 插件。"""
        with self._internal_lock:
            name = plugin.metadata.name
            self._providers[name] = plugin
            if name not in self._health:
                self._health[name] = ProviderHealth()
            _LOGGER.info("[provider_context] registered: %s", name)

    def unregister(self, name: str) -> None:
        """注销一个 provider 插件。"""
        with self._internal_lock:
            self._providers.pop(name, None)
            self._health.pop(name, None)
            _LOGGER.info("[provider_context] unregistered: %s", name)

    def get(self, name: str) -> Optional[ProviderPlugin]:
        return self._providers.get(name)

    # -- 动态排序 --

    def get_available_providers(self) -> list[ProviderPlugin]:
        """返回所有可用的 provider。"""
        with self._internal_lock:
            return [
                p for p in self._providers.values()
                if p.is_available
            ]

    def get_by_priority(self, operation: Operation) -> list[ProviderPlugin]:
        """按综合分降序返回可用 provider 列表（DynamicRouter 用）。

        综合分 = priority × PRIORITY_WEIGHT + 健康调整分（≤ 0，见 plugin.py）。
        priority 是主排序键；成功率/延迟只在同优先级之间微调。
        已熔断（OPEN）的 provider 不在这里过滤——是否跳过由路由执行时统一判定，
        本方法只负责排序。
        """
        with self._internal_lock:
            scored = [
                (self._compute_score(p), p)
                for p in self._providers.values()
                if p.is_available
            ]
            scored.sort(key=lambda x: x[0], reverse=True)
            return [p for _, p in scored]

    def _compute_score(self, plugin: ProviderPlugin) -> float:
        """计算 provider 的综合分（priority 主导 + 健康调整）。"""
        h = self._health.get(plugin.metadata.name, ProviderHealth())
        priority = self._priority_overrides.get(plugin.metadata.name, plugin.metadata.priority)
        return priority * PRIORITY_WEIGHT + h.score

    def block_provider(self, name: str, seconds: float, reason: str = "") -> None:
        """配额冷却/强制熔断：seconds 内该 provider 一律 OPEN（路由跳过）。"""
        with self._internal_lock:
            h = self._health.get(name)
            if h is not None:
                h.block_for(seconds, reason)
                _LOGGER.warning(
                    "[provider_context] %s 强制冷却 %.0fs: %s", name, seconds, reason or "-",
                )

    def circuit_state(self, name: str) -> str:
        """provider 当前熔断状态：CLOSED / OPEN / HALF_OPEN。"""
        with self._internal_lock:
            h = self._health.get(name)
        return h.circuit_state if h is not None else "CLOSED"

    # -- 事件系统 --

    def emit(self, event: ProviderHealthEvent) -> None:
        """发射健康事件，更新指标并通知所有监听者。"""
        with self._internal_lock:
            name = event.source
            h = self._health.get(name)
            if h is None:
                return
            before = h.circuit_state
            if event.success:
                h.update_success(event.latency_ms)
            else:
                h.update_failure(event.latency_ms)
            after = h.circuit_state
            failures, cooldown = h.failure_count, h.cooldown_seconds

        if after == "OPEN" and before != "OPEN":
            _LOGGER.warning(
                "[provider_context] %s 连续失败 %d 次，熔断跳过（%.0fs 后允许探测）",
                name, failures, cooldown,
            )
        elif after == "CLOSED" and before != "CLOSED":
            _LOGGER.info("[provider_context] %s 探测成功，恢复正常优先级", name)

        # 通知监听者（锁外执行，避免死锁）
        for listener in self._listeners.get(event.__class__.__name__, []):
            try:
                listener(event)
            except Exception:
                _LOGGER.warning("[provider_context] listener error: %s", exc_info=True)

    def subscribe(self, event_type: str, fn) -> None:
        """订阅事件类型。"""
        self._listeners[event_type].append(fn)

    # -- 状态快照 --

    @property
    def health_snapshot(self) -> dict[str, ProviderHealth]:
        """返回所有 provider 的健康指标快照。"""
        with self._internal_lock:
            return dict(self._health)

    @property
    def provider_names(self) -> list[str]:
        """返回已注册的 provider 名称列表。"""
        with self._internal_lock:
            return list(self._providers.keys())

    def __repr__(self) -> str:
        return f"ProviderContext(providers={len(self._providers)}, health={len(self._health)})"
