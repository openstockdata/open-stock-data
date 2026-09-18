# Fetcher 方法 vs RouteSpec 差异分析

## 各 Fetcher 支持的 API 方法

### TickflowFetcher
- `get_realtime_quote`
- `get_batch_realtime_quotes`
- **不支持**：daily_data（无 get_daily_data  override，但有基类实现）

### TushareFetcher  
- `get_realtime_quote`, `get_batch_realtime_quotes`
- `get_fund_flow`, `get_billboard`, `get_belong_board`, `get_board_cons`
- `get_dividend_history`, `get_fund_holder`, `get_top10_holders`
- **不支持**：`get_daily_data`（无 override，基类也没有——见下）

### EfinanceFetcher
- `get_realtime_quote`, `get_batch_realtime_quotes`
- `get_base_info`, `get_belong_board`, `get_fund_flow`, `get_billboard`
- `get_a_stock_spot`
- **不支持**：`get_daily_data`

### AkshareFetcher
- `get_realtime_quote`, `get_batch_realtime_quotes`
- `get_chip_distribution`, `get_fund_flow`, `get_belong_board`, `get_board_cons`
- `get_billboard`, `get_margin_detail`, `get_margin_ratio`
- `get_industry_pe`, `get_a_stock_spot`
- `get_dividend_history`, `get_fund_holder`, `get_top10_holders`
- `get_bid_ask`, `get_earnings_forecast/report/express`, `get_dividend_plan/cninfo`, `get_cctv_news`

### YfinanceFetcher
- `get_company_overview`, `get_balance_sheet`, `get_income_statement`
- `get_cash_flow`, `get_earnings`, `get_insider_transactions`
- `get_realtime_quote`

### AlphaVantageFetcher
- `get_company_overview`, `get_balance_sheet`, `get_income_statement`
- `get_cash_flow`, `get_earnings`
- `get_news_sentiment`, `get_insider_transactions`, `get_technical_indicator`
- **不支持**：`get_realtime_quote`, `get_daily_data`

### BaostockFetcher
- `get_belong_board`
- **不支持**：`get_daily_data`（基类也没定义）

### PytdxFetcher
- `get_realtime_quote`
- `get_stock_name`
- **不支持**：`get_daily_data`

---

## 关键发现：get_daily_data 来源

`base.py` 中 `BaseFetcher` 定义了 `get_daily_data`（继承自 BasicDataFetcher）。所有继承 BaseFetcher 的 fetcher 都有此方法。daily_prices 路由 method="get_daily_data" 对所有 provider 合法。但实际调用结果取决于每个 fetcher 内部的数据获取实现。

---

## 当前路由配置 vs Fetcher 能力差异

### ✅ 正确配置（fetcher 确实支持对应方法）

| 路由 | 配置的 Provider | Fetcher 是否均支持该 method |
|------|----------------|----------------------------|
| DAILY_PRICES(a) | Tickflow → Tushare → Efinance → Akshare → Yfinance | ✅ 全部继承基类 get_daily_data |
| DAILY_PRICES(etf) | Tickflow → Tushare → Efinance → Akshare → Yfinance | ✅ 同上 |
| DAILY_PRICES(hk) | Yfinance → Akshare | ✅ 同上 |
| DAILY_PRICES(us) | AlphaVantage → Yfinance | ✅ 同上 |
| REALTIME_QUOTE(a) | Tickflow → Tushare → Efinance → Akshare | ✅ 都有 get_realtime_quote |
| REALTIME_QUOTE(etf) | Tickflow → Akshare → Yfinance | ✅ |
| REALTIME_QUOTE(hk) | Tickflow → Akshare → Yfinance | ✅ |
| REALTIME_QUOTE(us) | **AlphaVantage, Yfinance** | ⚠️ AlphaVantage 无 get_realtime_quote |
| A_STOCK_SNAPSHOT(a) | Efinance, Akshare | ✅ 都有 get_a_stock_spot |
| FUND_FLOW | Efinance, Tushare, Akshare | ✅ 都有 get_fund_flow |
| CHIP_DISTRIBUTION | Akshare | ✅ 有 get_chip_distribution |
| BELONG_BOARD | LocalStore, Tushare, Efinance, Akshare | ✅ + Baostock 也有 |
| BOARD_CONS | LocalStore, Tushare, Akshare | ✅ |
| BILLBOARD | Tushare, Efinance, Akshare | ✅ 都有 get_billboard |
| INDUSTRY_PE | LocalStore, Akshare | ✅ 有 get_industry_pe |
| DIVIDEND_HISTORY | LocalStore, Tushare, Akshare | ✅ 都有 |
| FUND_HOLDER | LocalStore, Tushare, Akshare | ✅ 都有 |
| TOP10_HOLDERS | LocalStore, Tushare, Akshare | ✅ 都有 |
| MARGIN_DETAIL | Akshare | ✅ 有 get_margin_detail |
| MARGIN_RATIO | Akshare | ✅ 有 get_margin_ratio |
| US_* | LocalStore, AlphaVantage, Yfinance | ✅ 各有分工（news/tech 仅 AV） |

### ❌ 需修正的差异

1. **REALTIME_QUOTE(us)** 配置了 `AlphaVantage`，但 AlphaVantageFetcher 无 `get_realtime_quote`
   - 修改：从 REALTIME_QUOTE(us) providers 元组中移除 AlphaVantage
   - 推荐：`("TickflowFetcher", "YfinanceFetcher")`

2. **BELONG_BOARD** 配置了 LocalStore/Tushare/Efinance/Akshare，但 BaostockFetcher 也有 `get_belong_board` 却没被使用
   - 是否补充 Baostock 可选。視需求。

3. **Akshare 的独有方法未被任何路由使用**（可选 API 若需要可新增路由）：
   - `get_bid_ask`
   - `get_earnings_forecast/report/express`
   - `get_dividend_plan/cninfo`
   - `get_cctv_news`

4. **Tushare 支持但未被使用的**：无遗漏（fund_flow/billboard/belong_board/board_cons/realtime_quote/dividend_history/fund_holder/top10_holders 都有路由）。

5. **PytdxFetcher 支持 get_realtime_quote 但路由中完全未使用**
   - 可选补充。视 Pytdx 通达信协议稳定性决定。

---

## 修正建议

仅修正确认有问题的：REALTIME_QUOTE(us) 中的 AlphaVantage 需要移除。其他均为低优先级的"扩展支持"，非错误配置。
