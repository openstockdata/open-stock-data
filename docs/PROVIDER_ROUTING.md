# Provider 动态路由配置文档

## 架构概述

项目采用**动态插件路由**架构，核心组件：

- **`ProviderPlugin`** — 每个数据源实现的插件协议
- **`ProviderContext`** — 共享上下文，管理所有 provider 的注册、健康指标
- **`DynamicRouter`** — 自适应路由器，按健康分动态计算 fallback 顺序
- **`RouteRegistry`** — 静态路由定义，指定每个 API 使用哪些 provider
- **`OpenStockDataClient`** — 唯一取数入口；`tools` 文本工具（MCP 展示层）内部同样经 client 取数

**综合分公式（唯一一套评价体系）：**
```
score = priority × 1.0 + health_adjustment            # health_adjustment ≤ 0
health_adjustment = -(1 - success_rate) × 10          # 每次失败 -0.05 → 最多扣 0.5 分
                    - min(avg_latency_ms, 10000) / 10000 × 0.5   # 延迟最多扣 0.5 分
```

`priority` 是主排序键（整数档位）；成功率与延迟合计不足一个档位，只在同优先级之间打破平局。
连续失败达阈值（默认 3 次，`PROVIDER_HEALTH_FAILURE_THRESHOLD`）→ **OPEN，路由直接跳过**；
冷却（默认 300s，`PROVIDER_HEALTH_COOLDOWN_SECONDS`）结束 → HALF_OPEN 允许探测一次，
成功即恢复 CLOSED，失败则重新 OPEN。空结果（EMPTY）不计入健康失败。
配额类强制冷却（`block_for`，如 Tushare 日配额）与连续失败计数相互独立，期间一律 OPEN。

---

Tickflow免费无Key服务只支持历史日 K
使用 tushare 库获取 A 股数据，需要配置 TUSHARE_TOKEN 环境变量
使用 efinance 库获取东方财富 A 股数据
使用 akshare 库获取股票数据，支持多数据源（东财、新浪、腾讯）及多市场
使用 baostock 库获取 A 股历史数据，免费，无需 token
使用 yfinance 库获取国际股票数据

---
## 所有联网 API 及其 Provider 优先级

以下列表按**综合分降序**排列（每次执行时动态排序）。
`providers` 元组只是**候选名单**（该路由允许用哪些源）；实际回退顺序由
`ProviderContext` 按 `priority`（主键）+ 健康分实时排序，**与元组书写顺序无关**。

### A 股日线
| API | method | providers（候选名单，实际顺序按综合分动态排序） | cache_ttl |
|-----|--------|----------------------|-----------|-----------------|
| daily_prices(A_STOCK) | get_daily_data | Tickflow → Tushare→ Efinance → Akshare | 86400s |
| daily_prices(ETF) | get_daily_data | Tickflow→ Tushare → Efinance → Akshare → Yfinance | 86400s |
| daily_prices(HK) | get_daily_data | Yfinance → Akshare | 86400s |
| daily_prices(US) | get_daily_data | LocalStore → AlphaVantage → Yfinance | 86400s |

### 实时行情
| API | method | providers | cache_ttl |
|-----|--------|-----------|-----------|-----------------|
| realtime_quote(A_STOCK) | get_realtime_quote | Tickflow → Tushare → Efinance → Akshare | 10s |
| realtime_quote(ETF) | get_realtime_quote | Tickflow → Akshare → Yfinance | 10s |
| realtime_quote(HK) | get_realtime_quote | Tickflow → Akshare → Yfinance | 10s |
| realtime_quote(US) | get_realtime_quote | Tickflow → Yfinance | 30000s |

> Efinance 的单股/批量行情走 `push2 ulist` 按代码点查（每批 ≤100 只、一次请求），
> 不再为一只股票下载全市场 `clist` 分页；全市场分页只用于 `a_stock_snapshot`。

### A 股快照
| API | method | providers | cache_ttl |
|-----|--------|-----------|-----------|-----------------|
| a_stock_snapshot | get_a_stock_spot | Efinance → Akshare | 3000s |

### 市场数据
| API | method | providers | cache_ttl |
|-----|--------|-----------|-----------|-----------------|
| fund_flow | get_fund_flow | Efinance → Tushare → Akshare | 600s |
| chip_distribution | get_chip_distribution | Akshare | 0s |
| belong_board | get_belong_board | LocalStore → Tushare → Efinance → Akshare | 604800s |
| board_cons | get_board_cons | LocalStore → Tushare → Akshare | 0s |
| billboard | get_billboard | Tushare → Efinance → Akshare | 0s |
| industry_pe | get_industry_pe | LocalStore → Akshare | 0s |
| dividend_history | get_dividend_history | LocalStore → Tushare → Akshare | 0s |
| fund_holder | get_fund_holder | LocalStore → Tushare → Akshare | 0s |
| top10_holders | get_top10_holders | LocalStore → Tushare → Akshare | 0s |
| margin_detail | get_margin_detail | Akshare | 3600s / 21600s |
| margin_ratio | get_margin_ratio | Akshare | 3600s / 21600s |

> `belong_board` 各源输出已统一为 `板块名称 / 板块代码 / 板块类型 / 股票代码 / 股票名称 + 各源附加列`，
> `板块类型 ∈ {industry, region, concept, unknown}`；用 `industry_board_name(df)` 取行业板块名，
> 不要再按列位置猜。`margin_detail` 的交易所全表在 AkshareFetcher 内按交易所缓存 6 小时，
> 多只股票只下载一次。

### 市场概览 / 个股信息 / 业绩分红（Akshare 独有）
| API | method | providers | cache_ttl |
|-----|--------|-----------|-----------|
| market_pe_percentile | get_market_pe_percentile | Akshare | 43200s |
| earnings_calendar | get_earnings_calendar | Akshare | 43200s |
| financial_compare | get_financial_compare | Akshare | 604800s |
| stock_info | get_stock_info | Akshare | 604800s |
| stock_indicators | get_stock_indicators | Akshare | 604800s |
| current_time | get_current_time | Akshare | 604800s |
| index_daily | get_index_daily | Akshare | 86400s（daily 策略，允许过期兜底） |
| bid_ask | get_bid_ask | Akshare | 不缓存 |
| cctv_news | get_cctv_news | Akshare | 不缓存 |
| earnings_forecast | get_earnings_forecast | Akshare | 不缓存 |
| earnings_report | get_earnings_report | Akshare | 不缓存 |
| earnings_express | get_earnings_express | Akshare | 不缓存 |
| dividend_plan | get_dividend_plan | Akshare | 不缓存 |
| dividend_cninfo | get_dividend_cninfo | Akshare | 不缓存 |

> `stock_info` / `stock_indicators` 按代码形态分派 A 股 / 港股 / 美股接口；
> `index_daily` 与日线同策略（TTL 86400s、允许过期数据兜底）。

### 资金流向 / 龙虎 / 股东风险 / 资讯（Akshare 独有）
| API | method | providers | cache_ttl |
|-----|--------|-----------|-----------|
| zt_pool | get_zt_pool | Akshare | 1200s |
| north_flow | get_north_flow | Akshare | 600s |
| sector_fund_flow_rank | get_sector_fund_flow_rank | Akshare | 600s |
| block_trade | get_block_trade | Akshare | 1800s |
| holder_num | get_holder_num | Akshare | 86400s |
| locked_shares | get_locked_shares | Akshare | 43200s |
| pledge_ratio | get_pledge_ratio | Akshare | 86400s |
| news | get_news | Akshare | 3600s |
| news_global | get_news_global | Akshare | 600s |
| margin_trading | get_margin_trading | Akshare | 盘中 3600s / 收盘 21600s |

> 参数语义：`zt_pool(pool_type, date)` 支持 涨停/强势/跌停/昨日涨停 四类；`block_trade` 传 `symbol` 查该股近 30 天大宗明细、空 `symbol` 查全市场统计；`news` 按 `(symbol, limit)` 维度缓存；`margin_trading` 为交易所融资融券汇总（按 `market` 分缓存）；个股按日明细走 `margin_detail(symbol, market)`。

### 美股
| API | method | providers | cache_ttl |
|-----|--------|-----------|-----------|-----------------|
| us_overview | get_company_overview | LocalStore → AlphaVantage → Yfinance | 86400s |
| us_balance_sheet | get_balance_sheet | LocalStore → AlphaVantage → Yfinance | 604800s |
| us_income_statement | get_income_statement | LocalStore → AlphaVantage → Yfinance | 604800s |
| us_cash_flow | get_cash_flow | LocalStore → AlphaVantage → Yfinance | 604800s |
| us_earnings | get_earnings | LocalStore → AlphaVantage → Yfinance | 86400s |
| us_news_sentiment | get_news_sentiment | AlphaVantage | 0s |
| us_insider | get_insider_transactions | LocalStore → AlphaVantage → Yfinance | 43200s |
| us_tech_indicator | get_technical_indicator | AlphaVantage | 0s |

### 本地存储优先
`LocalStoreFetcher`（priority=100）在所有涉及本地存储的 API 中排第一，命中即免网络。

---

## Provider 优先级配置

### 当前所有 Provider 的 priority 值

| Provider | priority | 说明 |
|----------|----------|------|
| LocalStoreFetcher | **100** | 本地缓存，最高优先级，命中即免网络 |
| AlphaVantageFetcher | **10** | 美股首选（需 API key） |
| TickflowFetcher | **10** | 网络请求第一位，全球市场 |
| TushareFetcher | **9** | A 股/ETF（需配置 token） |
| YfinanceFetcher | **9** | 港股/美股 |
| EfinanceFetcher | **5** | A 股/ETF |
| AkshareFetcher | **4** | A 股全品种 |
| BaostockFetcher | **3** | A 股（已不参与日常路由） |
| PytdxFetcher | **1** | A 股（通达信协议，未加入路由） |

**priority 越大，排名越靠前。** 综合分 = `priority × 1.0 + 健康调整分（≤ 0）`，priority 是主排序键，健康项只在同优先级之间微调。

> **动态调整优先级：** 运行时可通过 `client.provider_context.set_priority("TickflowFetcher", 20)` 动态修改，立即生效，无需重启。

---

## 如何配置、修改、新增、删除 Provider

### 1. 修改 priority（推荐方式）

**方式 A：修改类属性（永久生效）**

编辑对应 fetcher 类中的 `priority` 类属性：

```python
# open_stock_data/data_provider/akshare_fetcher.py
class AkshareFetcher(BaseFetcher):
    priority = 4  # 修改这里，值越大排名越靠前
```

**重启后生效。**

**方式 B：运行时动态调整（立即生效）**

```python
from open_stock_data.client import OpenStockDataClient

client = OpenStockDataClient()

# 动态调整优先级，立即生效（重启后恢复默认值）
client.provider_context.set_priority("TickflowFetcher", 20)
client.provider_context.set_priority("EfinanceFetcher", 10)

# 查看当前优先级
client.provider_context.get_priority("TickflowFetcher")  # 返回 20
```

**动态优先级立即影响 `DynamicRouter` 的排序结果，无需重启。**

### 2. 新增 Provider

**步骤 1：** 创建 fetcher 类，继承 `BaseFetcher`，实现 `ProviderPlugin` 协议：

```python
from open_stock_data.data_provider.base import BaseFetcher

class MyNewFetcher(BaseFetcher):
    name = "MyNewFetcher"
    priority = 3  # 优先级（越大越靠前）

    @property
    def metadata(self):
        from .plugin import ProviderMetadata
        return ProviderMetadata(name=self.name, priority=self.priority, tags=())

    def get_realtime_quote(self, code):
        # 实现获取逻辑
        return data

    # 其他必要方法...
```

**步骤 2：** 在 `open_stock_data/data_provider/providers.py` 的 `create_default_providers()` 中注册：

```python
def create_default_providers(context: ProviderContext) -> None:
    ...
    context.register(MyNewFetcher())
```

**步骤 3：** 在 `open_stock_data/data_provider/default_routes.py` 的 `RouteSpec` 中添加 provider 名称到需要使用的 API 列表。

**步骤 4：** 重启或 `pip install -e .` 后生效。

### 3. 删除 Provider

**步骤 1：** 从 `create_default_providers()` 中移除注册：

```python
def create_default_providers(context: ProviderContext) -> None:
    # 不再注册要删除的 provider
    # context.register(TickflowFetcher())  # 注释掉或删除
    ...
```

**步骤 2：** 从 `default_routes.py` 的所有 `RouteSpec.providers` 元组中移除该 provider 名称。

**步骤 3：** 删除 fetcher 类文件或标记为不可用。

### 4. 修改 API 的 Provider 列表

编辑 `open_stock_data/data_provider/default_routes.py`，修改对应 `RouteSpec` 的 `providers` 元组：

```python
# 例如：让 realtime_quote 只用 Efinance 和 Akshare
RouteSpec(
    Operation.REALTIME_QUOTE,
    StockType.A_STOCK,
    ("EfinanceFetcher", "AkshareFetcher"),  # 候选名单；实际顺序按综合分动态排序
    "get_realtime_quote",
    ...
)
```

### 5. 运行时动态调整（无需重启）

```python
from open_stock_data.client import OpenStockDataClient

client = OpenStockDataClient()
ctx = client.provider_context

# 运行时注册新 provider
ctx.register(MyNewFetcher())

# 运行时注销 provider
ctx.unregister("TickflowFetcher")

# 查看当前健康状态
for name, health in ctx.health_snapshot.items():
    print(f"{name}: score={ctx._compute_score(ctx._providers[name]):.1f} circuit={health.circuit_state}")
```

`DynamicRouter` 下次执行时会自动使用新的 provider 列表和健康分排序。

---

## 健康监控与熔断

### 查看健康状态

```python
client = OpenStockDataClient()
ctx = client.provider_context

# 所有 provider 健康快照
for name, health in ctx.health_snapshot.items():
    print(f"{name}: success_rate={health.success_rate:.2f}, "
          f"latency={health.avg_latency_ms:.1f}ms, "
          f"failures={health.failure_count}, "
          f"circuit={health.circuit_state}")
```

### 熔断机制（统一单层：`ProviderHealth`）

只有一套评价体系，职责划分如下：

- **连续失败计数 → 跳过（OPEN）**：`failure_count` 是连续失败次数，任一成功即清零；
  达阈值（默认 3 次，`PROVIDER_HEALTH_FAILURE_THRESHOLD`）→ `circuit_state = "OPEN"`，
  路由执行时直接跳过（`SKIPPED circuit_open`），**不是**只降排序。
- **冷却 → 探测（HALF_OPEN）**：冷却（默认 300s，`PROVIDER_HEALTH_COOLDOWN_SECONDS`）结束 →
  `HALF_OPEN`，下一次尝试即探测：成功 → `CLOSED`，失败 → 重新 `OPEN`。
- **配额强制冷却（`block_for`）**：与连续失败计数相互独立，期间一律 OPEN；
  用于 `RateLimitError.retry_after`、Tushare 日配额（到北京时间次日 0 点）等。
- **排序微调**：未 OPEN 的 provider 按 `priority × 1.0 + 健康调整分（≤ 0）` 排序；
  成功率/延迟合计不足一个档位，只在同优先级之间打破平局。
- **EMPTY 不计入失败**：空结果、校验不通过记尝试但只影响排序微调（validator 失败计失败，
  EMPTY 不计）；异常、限流计入连续失败。
- 状态转换打日志：`[provider_context] X 连续失败 N 次，熔断跳过（Ms 后允许探测）` /
  `[provider_context] X 探测成功，恢复正常优先级`。

**Tushare 配额（按接口冷却，fetcher 内部处理）**
- Tushare 的配额按接口独立计量（如 `concept` 1 次/小时、大多数接口 50 次/分钟）。
  识别到配额文案后只冷却出错的那个接口（分钟级 120s、小时级 3600s、
  **日级到北京时间次日 0 点**、无权限 1h），冷却期内该接口返回带原因的空结果
  （`attrs["empty_reason"]`），路由记 `EMPTY` 并把原因写入 `FetchAttempt.reason`，
  `AllSourcesFailed` 里可直接排查；其它接口照常。不再向路由层抛 `RateLimitError`
  把 Tushare 整体熔断。
- `concept` 板块列表落盘缓存，命中即免请求。

### 手动触发熔断/恢复

```python
client = OpenStockDataClient()
ctx = client.provider_context

ctx.block_provider("EfinanceFetcher", 60, "manual")  # 手动熔断 60s
```

---

## 注意事项

1. **`us_news_sentiment` 和 `us_tech_indicator` 没有 provider** — 需要配置 Alpha Vantage API key 或其他数据源
2. **`LocalStoreFetcher` 不实现 `get_realtime_quote`** — 本地存储只覆盖历史数据
3. **`empty_is_failure=True`** — 返回空数据视为该源未命中，自动 fallback 到下一源；不计入健康分，也不触发熔断
4. **`cache_policy`** — `ttl_seconds=0` 表示不缓存，`None` 表示使用默认缓存
5. **回退顺序** — 由 `priority` + 实时健康分决定，与 `providers` 元组书写顺序无关
6. **批量执行与缓存优化** — `RouteExecutor.execute_batch()` 现会优先尝试每个 provider 的 `batch_method`（如 `get_batch_realtime_quotes`），一次性获取多只股票行情（efinance 的 ulist 接口一次可查 100 只）。批量结果逐项校验、缓存、持久化；未覆盖代码自动落回单查回退。单只实时行情在 TTL 内复用 `a_stock_snapshot` 全市场快照缓存，不再每次触发全市场爬取。
