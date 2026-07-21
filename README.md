# open-stock-data

`open-stock-data` 是一个面向股票、指数、加密货币和财经新闻的数据工具库。项目以普通 Python 函数形式提供 43 个工具，覆盖 A 股、港股、美股、ETF、A 股指数、OKX、Binance 和财经新闻，并在多类行情/财务数据上内置多数据源故障转移。

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

除文本工具外，库还提供类型化的 `OpenStockDataClient`。它以声明式静态路由 + 单一执行器实现多数据源故障转移，返回结构化的 `FetchResult`（含 `data` / `source` / `from_cache` / `attempts`），失败时抛 `AllSourcesFailed`（不返回 None 或错误字符串）：

```python
from open_stock_data import get_default_client

client = get_default_client()

bars = client.daily_prices("600519", market="sh", days=30)   # FetchResult[DataFrame]，英文标准列
print(bars.source, bars.from_cache, len(bars.data))

quote = client.realtime_quote("600519", "sh")                # FetchResult[UnifiedRealtimeQuote]
batch = client.batch_realtime_quotes(["600519", "000001"])   # BatchFetchResult，支持部分成功
flow = client.fund_flow("600519")                            # 分析类：源生列
```

价格/快照返回英文标准列（date/open/close…）；资金流、板块、估值等分析类数据返回数据源原生列（多为中文）。文本工具即是这层 API 之上的展示适配器。

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
| `ENABLE_EASTMONEY_PATCH` | 设为 `true` 后启用东方财富限流缓解补丁。 |

示例：

```bash
export TICKFLOW_API_KEY="your-api-key"
export TUSHARE_TOKEN="your-token"
export ALPHA_VANTAGE_API_KEY="your-api-key"
```

## 数据源与故障转移

项目内置 8 个数据源，并根据市场和函数类型自动选择可用来源：

- **TickFlow**: 全球市场，免费日线K线，配置 API key 后支持实时行情
- **A 股**: Efinance、Akshare、Tushare、Pytdx、Baostock
- **港股**: TickFlow、Akshare、YFinance
- **美股**: TickFlow、YFinance、Alpha Vantage
- **加密货币**: OKX、Binance
- **新闻**: 东方财富、新浪、NewsNow

### 故障转移优先级

部分工具会按优先级自动故障转移（**已优化 2026-07-07**）：

- **K线数据**: `Efinance → Akshare → Tushare → TickFlow → Pytdx → Baostock`
- **全市场快照**: `Efinance → Akshare`
- **A股实时**: `TickFlow → Efinance → Tushare → Akshare`
- **港股实时**: `TickFlow → Akshare → YFinance`
- **美股实时**: `TickFlow → YFinance → AlphaVantage`

**优先级调整说明**:
- **TickFlow 降低优先级**: 限流严格（10次/分钟），批量场景易触发熔断，现调整到 Tushare 之后
- **Efinance 提升优先级**: 稳定性好，无明确限流，适合批量数据获取和全市场扫描
- **预期收益**: 优先使用更稳定的 Efinance 可减少批量场景下的限流熔断；实际收益随网络与时段而定，暂无可复现基准数据

**注意**:
- TickFlow 未配置 API key 时仅用于免费日线，实时行情自动回退到其他源
- TickFlow 适合少量股票查询、实时行情、美股/港股数据，不适合批量获取
- Tushare 需配置 token，配额限制 50次/分钟
- AlphaVantage 需配置 API key，配额限制 5次/分钟、500次/天

完整的数据源能力对照和故障转移配置详见 [docs/FETCHER_CAPABILITIES.md](docs/FETCHER_CAPABILITIES.md)。

数据源状态可通过 `data_source_status()` 查看。

## 东方财富限流补丁

当东方财富相关接口频繁出现 `RemoteDisconnected`、连接被关闭、请求被重置等问题时，可以开启内置补丁：

```bash
export ENABLE_EASTMONEY_PATCH=true
```

开启后会：

- 为东方财富请求注入随机 `User-Agent`
- 从匿名 web-report 接口获取并缓存 `nid18` token
- 将 `nid18` 合并到现有 Cookie
- **增加请求间隔** (0.5-1.2s) 和最小间隔 (0.8s)，降低触发限流的概率
- **降低并发数** (2) 和增加重试次数 (3)，提升稳定性

**性能调整** (2026-07-07):
- 请求间隔从 0-0.2s 增加到 0.5-1.2s
- 最小间隔从 0.35s 增加到 0.8s
- 并发数从 3 降低到 2
- 重试次数从 2 增加到 3
- **预期效果**: 降低东方财富连接中断频率、提升全市场快照稳定性；暂无可复现基准数据支撑具体百分比

补丁作用于共享的 `requests.Session.request` 层，因此同时覆盖项目自身请求和依赖库中触发的东方财富请求。

### 环境变量微调

可通过以下环境变量自定义补丁参数：

```bash
export EASTMONEY_PUSH2_MAX_CONCURRENCY=2          # 并发数
export EASTMONEY_PUSH2_MIN_INTERVAL_SECONDS=0.8   # 最小请求间隔（秒）
export EASTMONEY_PUSH2_MAX_RETRIES=3              # 重试次数
export EASTMONEY_PUSH2_RETRY_BACKOFF_SECONDS=2.0  # 重试间隔（秒）
```

## 可用工具

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
