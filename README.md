# open-stock-data

`open-stock-data` 是一个面向股票、指数、加密货币和财经新闻的数据工具库。项目以普通 Python 函数形式提供 43 个工具，覆盖 A 股、港股、美股、ETF、A 股指数、OKX、Binance 和财经新闻，并在多类行情/财务数据上内置多数据源故障转移。工具面向 MCP/展示场景（返回格式化文本），内部统一经 `OpenStockDataClient` 取数；程序化取数请直接使用 client。

## 安装

```bash
pip install open-stock-data
```

## 快速使用

```python
from open_stock_data.tools import get_current_time, stock_prices, stock_realtime

print(get_current_time())
print(stock_prices(symbol="600519", market="sh", limit=5))
print(stock_realtime(symbol="01810.HK", market="hk"))
```

工具函数返回文本结果，表格类数据通常以 CSV 形式输出，便于直接展示、写入文件或交给上层应用继续处理。

### 类型化数据 API（OpenStockDataClient）

除文本工具外，库还提供类型化的 `OpenStockDataClient`。构造签名为 `OpenStockDataClient(providers=None, routes=None, *, cache=None, store=None)`——三者都可省略，缺省使用默认 provider、默认路由和内置缓存。它以声明式静态路由 + 单一执行器实现多数据源故障转移，返回结构化的 `FetchResult`（含 `data` / `source` / `fetched_at` / `from_cache` / `is_stale` / `attempts`），失败时抛 `AllSourcesFailed`（不返回 None 或错误字符串）：

```python
from open_stock_data import get_default_client

client = get_default_client()

bars = client.daily_prices("600519", market="sh", days=30)   # FetchResult[DataFrame]，英文标准列
print(bars.source, bars.from_cache, len(bars.data))

quote = client.realtime_quote("600519", "sh")                # FetchResult[UnifiedRealtimeQuote]
batch = client.batch_realtime_quotes(["600519", "000001"])   # BatchFetchResult，支持部分成功
flow = client.fund_flow("600519")                            # 分析类：源生列
```

价格/快照返回英文标准列（date/open/close…）；资金流、板块、估值等分析类数据返回数据源原生列（多为中文）。

**分层约定：**

- `OpenStockDataClient` 是**唯一取数入口**（路由 + 故障转移 + 缓存），程序化数据需求一律直接调用它；
- `tools`（43 个文本工具）定位为 **MCP/展示层**：只负责清洗与格式化，取数内部统一走 client，与应用侧共享同一套路由和缓存；
- 应用项目（如 `stock-data-analyst`）通过自己的适配层直接封装 client，不依赖 `tools`。

client 常用方法分组（完整签名见 `open_stock_data/client.py`，均返回 `FetchResult`）：

| 分组 | 方法 |
| --- | --- |
| 价格/行情 | `daily_prices` `index_daily` `realtime_quote` `batch_realtime_quotes` `a_stock_snapshot` `bid_ask` |
| 个股信息/筹码/资金 | `stock_info` `stock_indicators` `financial_compare` `belong_board` `board_cons` `chip_distribution` `fund_flow` |
| 市场资金/风险 | `zt_pool` `north_flow` `sector_fund_flow_rank` `margin_trading` `margin_detail` `block_trade` `holder_num` `locked_shares` `pledge_ratio` `billboard` |
| 市场概览/股东 | `market_pe_percentile` `industry_pe` `earnings_calendar` `current_time` `fund_holder` `top10_holders` `dividend_history` |
| 业绩/分红 | `earnings_forecast` `earnings_report` `earnings_express` `dividend_plan` `dividend_cninfo` |
| 新闻 | `news` `news_global` `cctv_news` |
| 美股 | `us_overview` `us_balance_sheet` `us_income_statement` `us_cash_flow` `us_earnings` `us_news_sentiment` `us_insider` `us_tech_indicator` |

各路由的 provider 候选名单与缓存 TTL 见 [docs/PROVIDER_ROUTING.md](docs/PROVIDER_ROUTING.md)。

## 支持的代码格式

工具会在内部对常见股票代码格式做标准化，主要影响 `stock_prices`、`stock_realtime`、`stock_info`、`stock_indicators` 等函数。

| 市场 | 支持示例 |
| --- | --- |
| A 股个股 | `600519`, `000001`, `sh600519`, `sz000001`, `600519.SH`, `000001.SZ` |
| ETF | `510300`, `159001`, `sh510300`, `sz159001`, `510300.SH`, `159001.SZ` |
| 港股 | `01810`, `1810`, `HK01810`, `01810.HK`, `1810.hk` |
| 美股 | `AAPL`, `MSFT`, `BRK.B` |
| 加密货币 | `BTC`, `ETH`, `BTC-USDT` |

说明：

- A 股和 ETF 会标准化为 6 位代码，例如 `sh600519` -> `600519`。
- 港股会标准化为 5 位代码，例如 `1810.hk` -> `01810`。
- 美股代码会转为大写，例如 `brk.b` -> `BRK.B`。
- 暂未专门支持 `SHSE.600519`、`SZSE.159001` 这类交易所前缀格式。

## 环境变量

大多数基础行情可直接使用。部分增强数据源或自定义代理需要通过环境变量配置。

| 变量 | 说明 |
| --- | --- |
| `TICKFLOW_API_KEY` | TickFlow API key。配置后启用 TickFlow 实时行情；未配置时仍使用官方免费服务获取历史日 K。 |
| `TICKFLOW_API_URL` | 自定义 TickFlow API 基础地址；有 key 默认 `https://api.tickflow.org`，无 key 默认 `https://free-api.tickflow.org`。 |
| `TUSHARE_TOKEN` | Tushare Pro token。配置后启用 Tushare A 股数据源。 |
| `ALPHA_VANTAGE_API_KEY` | Alpha Vantage API key。配置后启用部分美股新闻、技术指标和财务数据增强能力。 |
| `OKX_BASE_URL` | 自定义 OKX API 基础地址，默认 `https://www.okx.com`。 |
| `BINANCE_BASE_URL` | 自定义 Binance API 基础地址，默认 `https://www.binance.com`。 |
| `NEWSNOW_CHANNELS` | NewsNow 新闻频道列表，多个频道用逗号分隔。 |
| `LOCAL_STORE_PATH` | 本地 SQLite 长期存储路径，默认 `<缓存目录>/local_store.db`。 |
| `PYTDX_SERVERS` | Pytdx 服务器列表（分号分隔的 `host:port`）；或用 `PYTDX_HOST` + `PYTDX_PORT` 指定单个服务器。 |

示例：

```bash
export TICKFLOW_API_KEY="your-api-key"
export TUSHARE_TOKEN="your-token"
export ALPHA_VANTAGE_API_KEY="your-api-key"
```

## 数据源与故障转移

### 架构：动态插件体系

项目采用**动态插件架构**（借鉴 deepseek-harness 设计理念），核心组件：

- **`ProviderPlugin`** — 每个数据源实现的插件协议（`metadata`、`is_available`、`execute`、`report_health`）
- **`ProviderContext`** — 共享上下文，管理所有 provider 的注册、健康指标和事件分发
- **`DynamicRouter`** — 自适应路由器，运行时根据健康分（成功率、延迟、熔断状态）动态计算最优 fallback 顺序
- **`OpenStockDataClient`** — 依赖 `ProviderContext`（缺省 `ProviderContext.default()`，也可构造时传入 `providers` dict 自定义），无硬编码 fallback 链

```
OpenStockDataClient
    ├── ProviderContext（唯一事实来源）
    │     ├── ProviderRegistry（运行时可插拔）
    │     ├── HealthMetrics（综合分 = priority + 健康调整分，健康项 ≤ 0）
    │     └── EventBus（健康事件通知）
    └── DynamicRouter（按健康分排序，动态 fallback）
          └── RouteRegistry（静态路由定义：operation/method/validator/cache_policy）
```

priority 高的 provider 优先被调用，成功率/延迟只在同优先级之间微调；连续失败的 provider 降为最低优先级但仍保留为兜底，冷却后自动恢复；本地存储 `LocalStoreFetcher` 优先级最高（`priority=100`），命中即免网络。

**动态调整优先级：** 运行时可通过 `client.provider_context.set_priority("TickflowFetcher", 20)` 动态修改任意 provider 的优先级，立即生效，无需重启。详见 [PROVIDER_ROUTING.md](docs/PROVIDER_ROUTING.md)。

### 内置数据源

项目内置 8+ 个数据源，根据市场和函数类型自动选择可用来源：

- **TickFlow**: 全球市场，免费日线K线，配置 API key 后支持实时行情
- **A 股**: Efinance、Akshare、Tushare、Pytdx、Baostock
- **港股**: TickFlow、Akshare、YFinance
- **美股**: TickFlow、YFinance、Alpha Vantage
- **加密货币**: OKX、Binance
- **新闻**: 东方财富、新浪、NewsNow

数据源状态可通过 `data_source_status()` 查看。

完整的数据源能力对照和 Provider 路由配置详见 [docs/PROVIDER_ROUTING.md](docs/PROVIDER_ROUTING.md)。

## 可用工具

以下工具均返回格式化文本（面向 MCP 调用/直接展示），取数统一经 `OpenStockDataClient`（见上方分层约定）；各数据的来源、回退与缓存行为见 [docs/PROVIDER_ROUTING.md](docs/PROVIDER_ROUTING.md)。

### A 股价格行情

| 工具 | 说明 |
| --- | --- |
| `index_prices` | 获取 A 股指数历史价格，例如沪深 300、上证指数。 |
| `stock_prices` | 获取 A 股、ETF、港股、美股历史价格及技术指标。 |
| `stock_realtime` | 获取 A 股、港股、ETF 实时行情。 |
| `stock_batch_realtime` | 批量获取多只 A 股实时行情。 |

### A 股信息查询

| 工具 | 说明 |
| --- | --- |
| `search` | 根据名称、公司名或关键词查找股票代码。 |
| `stock_info` | 获取股票基本信息。 |
| `stock_indicators` | 获取 A 股、港股、美股财务指标摘要。 |
| `get_current_time` | 获取当前时间和 A 股交易日信息。 |

### A 股市场资金

| 工具 | 说明 |
| --- | --- |
| `stock_zt_pool` | 获取涨停池、强势股池等数据。 |
| `stock_lhb_ggtj_sina` | 获取龙虎榜个股上榜统计。 |
| `stock_sector_fund_flow_rank` | 获取行业或概念板块资金流排名。 |
| `stock_north_flow` | 获取沪深港通北向资金流向。 |
| `stock_margin_trading` | 获取融资融券数据。 |
| `stock_block_trade` | 获取大宗交易数据。 |
| `stock_holder_num` | 获取股东户数变化数据。 |

### A 股个股分析

| 工具 | 说明 |
| --- | --- |
| `stock_chip` | 获取筹码分布、获利比例、平均成本和集中度。 |
| `stock_fund_flow` | 获取个股主力、超大单、大单、中单、小单资金流向。 |
| `stock_sector_spot` | 获取个股所属行业和概念板块。 |
| `stock_board_cons` | 获取行业或概念板块成分股。 |

说明：`stock_sector_spot` 的所属板块（belong_board）结果经 `boards.py` 统一 schema，前几列固定为 `板块名称 / 板块代码 / 板块类型 / 股票代码 / 股票名称`，`板块类型` ∈ {industry, region, concept, unknown}，各数据源的其它原始列保留在后面。

### A 股估值与财务

| 工具 | 说明 |
| --- | --- |
| `stock_market_pe_percentile` | 获取市场整体 PE/PB 历史分位。 |
| `stock_industry_pe` | 获取行业 PE 对比数据。 |
| `stock_dividend_history` | 获取个股历史分红送转数据。 |
| `stock_institutional_holdings` | 获取基金重仓股和机构持仓数据。 |
| `stock_earnings_calendar` | 获取财报披露日历。 |
| `stock_financial_compare` | 获取盈利、偿债、运营等多维财务指标。 |

### A 股股东数据

| 工具 | 说明 |
| --- | --- |
| `stock_locked_shares` | 获取限售股解禁日历和解禁规模。 |
| `stock_pledge_ratio` | 获取股权质押统计和质押比例。 |
| `stock_top10_holders` | 获取十大股东或十大流通股东信息。 |

### A 股量化

| 工具 | 说明 |
| --- | --- |
| `backtest_strategy` | 对均线交叉、MACD、KDJ 等简单策略进行回测。 |

### 美股与港股

| 工具 | 说明 |
| --- | --- |
| `stock_prices_us` | 获取美股或港股历史价格及技术指标。 |
| `stock_overview_us` | 获取美股公司概览，包括市值、PE、EPS、股息率、52 周高低点等。 |
| `stock_financials_us` | 获取美股资产负债表、利润表、现金流量表。 |
| `stock_news_us` | 获取美股新闻和情绪数据，需要 `ALPHA_VANTAGE_API_KEY`。 |
| `stock_earnings_us` | 获取美股历史盈利和分析师预期。 |
| `stock_insider_us` | 获取美股内部人交易记录。 |
| `stock_tech_indicators_us` | 获取美股 SMA、EMA、RSI、MACD、布林带等技术指标，需要 `ALPHA_VANTAGE_API_KEY`。 |

### 加密货币

| 工具 | 说明 |
| --- | --- |
| `okx_prices` | 获取 OKX 加密货币 K 线价格、成交量和技术指标。 |
| `okx_loan_ratios` | 获取 OKX 杠杆借币多空比。 |
| `okx_taker_volume` | 获取 OKX 主动买入和主动卖出数据。 |
| `binance_ai_report` | 获取 Binance 加密货币 AI 分析报告。 |

### 市场新闻与状态

| 工具 | 说明 |
| --- | --- |
| `stock_news` | 获取股票或加密货币相关新闻。 |
| `stock_news_global` | 获取全球财经快讯。 |
| `data_source_status` | 查看数据源状态和熔断器信息。 |

## 直接导入工具注册表

如果需要批量注册到外部系统，可以使用 `ALL_TOOLS` 或 `TOOL_REGISTRY`：

```python
from open_stock_data.tools import ALL_TOOLS, TOOL_REGISTRY

print(len(ALL_TOOLS))
print(TOOL_REGISTRY["stock_prices"][1])
```

## 许可证

本项目使用 MIT License，详见 [LICENSE](LICENSE)。
