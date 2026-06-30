# open-stock-data

Core library for stock/crypto data with multi-source failover.

Provides 43 tool functions for A-stock, HK, US stock, and crypto data with automatic failover across 6 data sources.

## Install

```bash
pip install open-stock-data
```

## Usage

```python
from open_stock_data.tools import stock_prices, get_current_time

# Get stock prices
print(stock_prices(symbol="600519", market="sh", limit=5))

# Get current time
print(get_current_time())
```

## Supported Symbol Formats

The tools normalize common stock-code inputs before routing. This is especially important for `stock_prices`, `stock_realtime`, `stock_info`, and `stock_indicators`.

- A股个股: `600519`, `000001`, `sh600519`, `sz000001`, `600519.SH`, `000001.SZ`
- ETF: `510300`, `159001`, `sh510300`, `sz159001`, `510300.SH`, `159001.SZ`
- 港股: `01810`, `1810`, `HK01810`, `01810.HK`, `1810.hk`
- 美股: `AAPL`, `MSFT`, `BRK.B`

Notes:

- 港股工具会将以上输入统一标准化为内部 5 位纯数字代码，例如 `01810.HK` -> `01810`
- A股/ETF 工具会将带市场前缀或后缀的代码统一标准化为 6 位纯数字代码，例如 `sh600519` -> `600519`
- 美股代码会统一转为大写，例如 `brk.b` -> `BRK.B`
- 尚未专门支持 `SHSE.600519`、`SZSE.159001` 这类交易所前缀格式

Example:

```python
from open_stock_data.tools import stock_prices, stock_realtime, stock_indicators

print(stock_realtime(symbol="01810.HK", market="hk"))
print(stock_prices(symbol="sh600519", market="sh", limit=5))
print(stock_indicators(symbol="BRK.B", market="us"))
```

## Environment Variables

| Variable | Purpose |
|----------|---------|
| `TUSHARE_TOKEN` | Tushare API token (enables Priority 0 A-share source) |
| `ALPHA_VANTAGE_API_KEY` | Alpha Vantage API key (enables Priority 4 US stock data) |
| `OKX_BASE_URL` | Custom OKX API proxy endpoint |
| `BINANCE_BASE_URL` | Custom Binance API proxy endpoint |
| `NEWSNOW_CHANNELS` | Comma-separated news source channels |
| `ENABLE_EASTMONEY_PATCH` | Set to `true` to inject randomized User-Agent and `nid18` token for Eastmoney requests when Eastmoney endpoints are being rate-limited |

## Eastmoney Rate-Limit Patch

If Eastmoney endpoints fail frequently with errors such as `RemoteDisconnected`, connection closed, or abrupt resets, you can enable a built-in patch:

```bash
export ENABLE_EASTMONEY_PATCH=true
```

When enabled, the library will:

- inject a randomized `User-Agent` for Eastmoney requests
- fetch and cache an Eastmoney `nid18` token from the anonymous web-report endpoint
- merge the `nid18` cookie into existing request cookies instead of overwriting them
- add a small randomized delay before Eastmoney requests to reduce rate-limit pressure

The patch is applied at the shared `requests.Session.request` layer, so it covers both this project's own `_http_session` requests and Eastmoney requests triggered inside dependencies such as `akshare`.

## Available Tools

### A-Stock (价格行情)
- `index_prices` — A股指数K线数据
- `stock_prices` — 个股K线数据
- `stock_realtime` — 个股实时行情
- `stock_batch_realtime` — 批量实时行情

### A-Stock (信息查询)
- `search` — 股票搜索
- `stock_info` — 个股基本信息
- `stock_indicators` — 财务指标摘要
- `get_current_time` — 当前时间与交易日历

### A-Stock (市场资金)
- `stock_lhb_ggtj_sina` — 龙虎榜
- `stock_sector_fund_flow_rank` — 板块资金流排名
- `stock_margin_trading` — 融资融券
- `stock_zt_pool` — 涨停池
- `stock_north_flow` — 北向资金
- `stock_block_trade` — 大宗交易
- `stock_holder_num` — 股东人数

### A-Stock (个股分析)
- `stock_chip` — 筹码分布
- `stock_fund_flow` — 个股资金流向
- `stock_sector_spot` — 板块行情
- `stock_board_cons` — 板块成分股

### A-Stock (估值财务)
- `stock_market_pe_percentile` — 市场PE分位
- `stock_industry_pe` — 行业PE
- `stock_dividend_history` — 分红历史
- `stock_institutional_holdings` — 基金持仓
- `stock_earnings_calendar` — 业绩披露日历
- `stock_financial_compare` — 财务对比

### A-Stock (股东)
- `stock_locked_shares` — 限售解禁
- `stock_pledge_ratio` — 质押比例
- `stock_top10_holders` — 十大股东

### A-Stock (量化)
- `backtest_strategy` — 回测策略

### US Stock
- `stock_prices_us` — 美股/港股K线
- `stock_overview_us` — 美股概览
- `stock_financials_us` — 美股财报
- `stock_earnings_us` — 美股业绩
- `stock_insider_us` — 内部交易
- `stock_news_us` — 美股新闻
- `stock_tech_indicators_us` — 美股技术指标

### Crypto
- `okx_prices` — OKX行情
- `okx_loan_ratios` — OKX借贷比
- `okx_taker_volume` — OKX主动买卖量
- `binance_ai_report` — Binance AI报告

### Market & News
- `stock_news` — 个股新闻
- `stock_news_global` — 全球财经新闻
- `data_source_status` — 数据源状态
