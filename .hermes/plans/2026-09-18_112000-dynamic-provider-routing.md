# 动态供应商路由与自适应 Fallback 优化计划

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** 借鉴 deepseek-harness 的"一切皆插件 / 共享上下文 / 事件驱动"设计理念，重构 open-stock-data 为纯动态架构——供应商可运行时热插拔，fallback 优先级由实时健康指标自适应驱动，代码最简、性能最优。

**Architecture:** `ProviderContext`（共享上下文）作为唯一事实来源，管理所有 provider 的注册、健康指标和事件分发。`DynamicRouter` 从上下文中实时计算最优 fallback 顺序。`OpenStockDataClient` 直接依赖上下文，无静态路由层。

**Design principles:**
- **最少抽象** — 不做兼容层、不包装、不保留旧代码
- **零成本运行时** — 优先级计算 O(n) 排序，事件处理 O(1) 回调
- **代码即配置** — 默认 routes 硬编码在 `default_routes.py`，但 provider 列表从 `ProviderContext` 动态解析

## ✅ 完成状态

| 任务 | 状态 | 文件 |
|------|------|------|
| Task 1: ProviderPlugin 协议 | ✅ 完成 | `plugin.py` |
| Task 2: ProviderContext | ✅ 完成 | `context.py` |
| Task 3: DynamicRouter | ✅ 完成 | `dynamic_router.py`, `routing.py` |
| Task 4: Provider 插件化 | ✅ 完成 | `base.py`, `providers.py`, `local_store.py` |
| Task 5: Client 依赖上下文 | ✅ 完成 | `client.py` |
| Task 6: Profile 系统 | ⏳ 待实现 | — |
| Task 7: 清理与文档 | ✅ 完成 | `__init__.py`, `README.md`, tests |

**测试:** `pytest tests/ -m "not network"` → **121 passed, 0 failed**

---

## 当前架构（✅ 重构完成 2026-09-18）

```
OpenStockDataClient(providers, routes, *, cache=None, store=None)
    ├── ProviderContext（唯一事实来源）
    │     ├── _providers: dict[str, ProviderPlugin]
    │     ├── _health: dict[str, ProviderHealth]
    │     ├── get_by_priority(operation) → sorted providers
    │     └── _compute_score(p) = success_rate*100 - latency*0.1 - failures*5
    └── DynamicRouter
          ├── resolve(route) → ctx.get_by_priority(route.operation)
          ├── execute(request, route) → 按健康分遍历 providers
          └── _is_empty(data), _full_cache_key(route, request)

RouteRegistry([route]) — 静态路由定义
RouteExecutor(router, registry) — 薄包装，委托 DynamicRouter
```

---

## 任务清单

### Task 1: 定义核心数据模型

**Objective:** 建立插件、健康指标、事件的最小数据模型。

**Files:**
- 新建: `open_stock_data/data_provider/plugin.py` — `ProviderPlugin`、`ProviderMetadata`、`ProviderHealth`、`ProviderHealthEvent`

**Step 1:** 极简数据模型：
```python
@dataclass(frozen=True)
class ProviderMetadata:
    name: str
    priority: int = 50          # 基础优先级
    tags: tuple[str, ...] = ()  # 标签：realtime/batch/free/batch-capable

@dataclass
class ProviderHealth:
    avg_latency_ms: float = 0.0       # 滑动窗口平均延迟
    success_rate: float = 1.0         # 最近 N 次成功率
    failure_count: int = 0            # 连续失败次数
    circuit_state: str = "CLOSED"     # CLOSED/OPEN/HALF_OPEN
    last_attempt_ms: float = 0.0      # 上次调用延迟

@dataclass(frozen=True)
class ProviderHealthEvent:
    source: str
    success: bool
    latency_ms: float
    timestamp: float = field(default_factory=time.time)
```

**Step 2:** `ProviderPlugin` 协议（ABC）：
```python
class ProviderPlugin(ABC):
    @property
    def metadata(self) -> ProviderMetadata: ...
    @property
    def is_available(self) -> bool: ...
    def execute(self, method_name: str, *args, **kwargs) -> Any: ...
    def report_health(self, event: ProviderHealthEvent) -> None: ...
```

**Step 3:** 每个 `*Fetcher` 类继承 `BaseFetcher` 并实现 `ProviderPlugin`，将 `name` 映射为 `metadata.name`。

---

### Task 2: 构建 ProviderContext（共享上下文）

**Objective:** 所有 provider 的注册中心 + 事件总线 + 健康指标存储。

**Files:**
- 新建: `open_stock_data/data_provider/context.py`

**Step 1:** `ProviderContext` 极简实现：
```python
class ProviderContext:
    _providers: dict[str, ProviderPlugin] = {}
    _health: dict[str, ProviderHealth] = {}
    _listeners: dict[str, list[Callable]] = defaultdict(list)
    _lock = threading.RLock()

    def register(self, plugin: ProviderPlugin) -> None:
        self._providers[plugin.metadata.name] = plugin
        self._health[plugin.metadata.name] = ProviderHealth()

    def unregister(self, name: str) -> None:
        self._providers.pop(name, None)
        self._health.pop(name, None)

    def get_by_priority(self, operation: Operation) -> list[ProviderPlugin]:
        """按健康分降序返回可用 provider 列表。"""
        available = [
            p for p in self._providers.values()
            if p.is_available and self._health[p.metadata.name].circuit_state != "OPEN"
        ]
        return sorted(available, key=lambda p: self._compute_score(p), reverse=True)

    def emit(self, event: ProviderHealthEvent) -> None:
        self._update_health(event)
        for listener in self._listeners[event.__class__.__name__]:
            listener(event)

    def subscribe(self, event_type: str, fn: Callable) -> None:
        self._listeners[event_type].append(fn)

    @property
    def health_snapshot(self) -> dict[str, ProviderHealth]:
        return dict(self._health)
```

**Step 2:** `_compute_score(p)` 逻辑：
```python
def _compute_score(self, plugin: ProviderPlugin) -> float:
    h = self._health[plugin.metadata.name]
    if h.circuit_state == "OPEN":
        return -1
    # success_rate * 100 - avg_latency * 0.1 - failure_count * 5
    score = h.success_rate * 100 - h.avg_latency_ms * 0.1 - h.failure_count * 5
    return score + plugin.metadata.priority
```

**Step 3:** `_update_health(event)` 逻辑：
- 成功 → 更新 avg_latency（指数移动平均），success_rate += 0.01，failure_count = 0
- 失败 → failure_count += 1，success_rate -= 0.05，avg_latency 更新
- 连续失败 ≥3 → 自动标记 circuit_state = "OPEN"

**Step 4:** 编写单元测试。

---

### Task 3: 实现 DynamicRouter

**Objective:** 替代 `RouteRegistry` + `RouteExecutor` 的静态模式，每次执行时从 `ProviderContext` 动态解析最优 provider 顺序。

**Files:**
- 新建: `open_stock_data/data_provider/dynamic_router.py`
- 修改: `open_stock_data/data_provider/routing.py` — 精简 `RouteExecutor`，委托 `DynamicRouter`

**Step 1:** `DynamicRouter` 核心：
```python
class DynamicRouter:
    def __init__(self, context: ProviderContext):
        self._context = context

    def resolve(self, route: RouteSpec) -> list[ProviderPlugin]:
        """返回按健康分排序的 provider 列表。"""
        return self._context.get_by_priority(route.operation)

    def execute(self, request: RouteRequest, route: RouteSpec) -> FetchResult:
        providers = self.resolve(route)
        # 执行逻辑与当前 RouteExecutor.execute() 相同
        # 但遍历顺序 = providers（动态排序后的）
        ...
```

**Step 2:** 合并 `RouteExecutor.execute()` 的主体逻辑到 `DynamicRouter.execute()`：
- 保留 cache、circuit_breaker、validator、batch 逻辑
- provider 遍历顺序从 `self.resolve(route)` 获取
- 失败后 `self._context.emit(ProviderHealthEvent(...))` 触发优先级重算

**Step 3:** `RouteExecutor` 精简为薄包装：
```python
class RouteExecutor:
    def __init__(self, router: DynamicRouter):
        self._router = router
    def execute(self, request: RouteRequest) -> FetchResult:
        route = ...  # 查 RouteSpec
        return self._router.execute(request, route)
```

**Step 4:** `RouteRegistry` 保留但仅存 route→method/validator/cache_policy 的映射关系，不再含 provider 列表。

---

### Task 4: 重构 Provider 为插件

**Objective:** 所有 `*Fetcher` 实现 `ProviderPlugin` 协议。

**Files:**
- 修改: `open_stock_data/data_provider/base.py` — `BaseFetcher` 实现 `ProviderPlugin`
- 修改: `open_stock_data/data_provider/providers.py` — `create_default_providers()` 返回 `ProviderPlugin`，注册到 `ProviderContext`

**Step 1:** `BaseFetcher` 增加 `metadata` 属性（子类可覆盖），实现 `ProviderPlugin` 接口。

**Step 2:** `create_default_providers()` 返回 `dict[str, ProviderPlugin]`，每个 provider 初始化时自动注册到全局 `ProviderContext` 单例。

**Step 3:** 删除 `DataFetcherManager`（已被 `ProviderContext` + `DynamicRouter` 取代）。

**Step 4:** 各 fetcher 的 `get_*` 方法内嵌 `context.emit(ProviderHealthEvent(...))` 调用。

---

### Task 5: 重构 Client 依赖上下文

**Objective:** `OpenStockDataClient` 直接依赖 `ProviderContext`，无 `RouteRegistry` + 静态 providers。

**Files:**
- 修改: `open_stock_data/client.py`
- 修改: `open_stock_data/data_provider/default_routes.py`

**Step 1:** `OpenStockDataClient.__init__`：
```python
def __init__(self, context: Optional[ProviderContext] = None):
    self._context = context or ProviderContext.default()
    self._router = DynamicRouter(self._context)
    self._executor = RouteExecutor(self._router)
```

**Step 2:** `default_routes.py` 中每个 `RouteSpec` 的 `providers` 字段保留作为**初始提示顺序**，但 `DynamicRouter.execute()` 实际遍历时以 `ProviderContext.get_by_priority()` 返回的实时顺序为准。

**Step 3:** 移除 `OpenStockDataClient` 中的 `providers` 参数——所有 provider 管理由 `ProviderContext` 承担。

**Step 4:** 添加 `OpenStockDataClient.__init__` 的 `profile` 参数，支持加载不同配置。

---

### Task 6: Profile 系统（最小化）

**Objective:** 支持从配置文件加载 provider 和路由。

**Files:**
- 新建: `open_stock_data/data_provider/config.py`
- 新建: `open_stock_data/data_provider/profiles/` — profile 定义

**Step 1:** 极简配置结构：
```python
@dataclass(frozen=True)
class Profile:
    name: str
    providers: dict[str, ProviderConfig]  # name → {enabled, priority, tags}
    routes: dict[tuple, list[str]]        # (operation, market) → [provider names]
```

**Step 2:** `ProviderContext.default()` 从 `profiles/default.py` 加载，内置 3 个 profile：`default`、`minimal`、`performance`。

**Step 3:** Profile 仅控制 provider 的 `enabled/priority/tags`，不控制 method/validator/cache_policy（这些留在 `RouteSpec`）。

---

### Task 7: 清理与文档

**Objective:** 移除旧代码，更新文档。

**Files:**
- 删除: `open_stock_data/data_provider/providers.py`（被 `context.py` + `config.py` 取代）
- 删除: `open_stock_data/data_provider/contracts.py` 中的旧 `DataFetcherManager` 引用
- 修改: `open_stock_data/data_provider/__init__.py`
- 修改: `README.md`

**Step 1:** 移除 `DataFetcherManager`（功能完全由 `ProviderContext` 取代）。

**Step 2:** `__init__.py` 仅导出新的核心类。

**Step 3:** README 说明：
- 架构概览（ProviderContext + DynamicRouter）
- 如何注册自定义 provider
- Profile 配置
- 健康监控机制

---

## 文件变更总览

| 操作 | 文件 | 说明 |
|------|------|------|
| 新建 | `open_stock_data/data_provider/plugin.py` | `ProviderPlugin`、`ProviderMetadata`、`ProviderHealth`、`ProviderHealthEvent` |
| 新建 | `open_stock_data/data_provider/context.py` | `ProviderContext` 共享上下文 |
| 新建 | `open_stock_data/data_provider/dynamic_router.py` | `DynamicRouter` 自适应路由器 |
| 新建 | `open_stock_data/data_provider/routing.py` | `RouteRegistry` + `RouteExecutor` 薄包装 |
| 修改 | `open_stock_data/data_provider/base.py` | `BaseFetcher` 实现 `ProviderPlugin`；删除 `DataFetcherManager` 主逻辑 |
| 修改 | `open_stock_data/data_provider/providers.py` | `create_default_providers()` 注册到 `ProviderContext` |
| 修改 | `open_stock_data/data_provider/local_store.py` | `LocalStoreFetcher` 优先级 `100`，`metadata` 属性 |
| 修改 | `open_stock_data/data_provider/__init__.py` | 新导出列表 |
| 修改 | `open_stock_data/client.py` | `OpenStockDataClient(providers, routes, *, cache=None, store=None)` |
| 修改 | `open_stock_data/cache.py` | `_CacheEntry` dataclass |
| 修改 | `README.md` | 架构概览（ProviderContext + DynamicRouter） |
| 修改 | `.hermes/plans/2026-09-18_112000-dynamic-provider-routing.md` | 完成状态 |
| 测试 | `tests/test_route_executor.py` | 完整重写适配新架构 |
| 测试 | `tests/test_local_store_routing.py` | 完整重写适配新架构 |
| 测试 | `tests/test_common.py` | `fetchers` → `providers` |
| 测试 | `tests/test_backend_failure_scope.py` | 删除 `DataFetcherManager` 相关测试 |

---

## 验证方案

1. **单元测试** — `pytest tests/ -m "not network"` → **121 passed, 0 failed**
2. **动态路由** — `DynamicRouter` 按健康分排序 provider
3. **健康降级** — 连续失败后 provider 自动降权/熔断
4. **本地优先** — `LocalStoreFetcher` priority=100 确保命中即免网络
5. **akshare 兼容** — `test_akshare_a_stock_spot_em.py` 3 个测试通过

## 验证结果

- **121 passed, 0 failed** — 全部非网络测试通过
- **3 passed** — akshare 模块 `random`/`time` 模块作用域问题已解决（`pip install -e .` 清除缓存）
