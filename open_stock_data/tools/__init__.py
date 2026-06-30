"""
工具模块

导出所有原子工具函数，以及 ALL_TOOLS 和 TOOL_REGISTRY 供外部使用。
"""

# A-Stock: 价格与行情
from .a_stock.prices import (
    index_prices,
    stock_prices,
    stock_realtime,
    stock_batch_realtime,
)

# A-Stock: 基本信息与搜索
from .a_stock.info import (
    search,
    stock_info,
    stock_indicators,
    get_current_time,
)

# A-Stock: 市场资金流
from .a_stock.market_flow import (
    stock_zt_pool,
    stock_lhb_ggtj_sina,
    stock_sector_fund_flow_rank,
    stock_north_flow,
    stock_margin_trading,
    stock_block_trade,
    stock_holder_num,
)

# A-Stock: 个股分析
from .a_stock.analysis import (
    stock_chip,
    stock_fund_flow,
    stock_sector_spot,
    stock_board_cons,
)

# A-Stock: 估值与财务
from .a_stock.valuation import (
    stock_market_pe_percentile,
    stock_industry_pe,
    stock_dividend_history,
    stock_institutional_holdings,
    stock_earnings_calendar,
    stock_financial_compare,
)

# A-Stock: 股东
from .a_stock.shareholders import (
    stock_locked_shares,
    stock_pledge_ratio,
    stock_top10_holders,
)

# A-Stock: 量化分析
from .a_stock.quant import backtest_strategy

# 美股
from .us_stock import (
    stock_prices_us,
    stock_overview_us,
    stock_financials_us,
    stock_news_us,
    stock_earnings_us,
    stock_insider_us,
    stock_tech_indicators_us,
)

# 加密货币
from .crypto import (
    okx_prices,
    okx_loan_ratios,
    okx_taker_volume,
    binance_ai_report,
)

# 市场与系统
from .market import (
    stock_news,
    stock_news_global,
    data_source_status,
)

# ==================== ALL_TOOLS ====================
# 纯函数字典，供 CLI 等直接调用

ALL_TOOLS = {
    # A-Stock: 价格
    "index_prices": index_prices,
    "stock_prices": stock_prices,
    "stock_realtime": stock_realtime,
    "stock_batch_realtime": stock_batch_realtime,
    # A-Stock: 信息
    "search": search,
    "stock_info": stock_info,
    "stock_indicators": stock_indicators,
    "get_current_time": get_current_time,
    # A-Stock: 市场资金流
    "stock_zt_pool": stock_zt_pool,
    "stock_lhb_ggtj_sina": stock_lhb_ggtj_sina,
    "stock_sector_fund_flow_rank": stock_sector_fund_flow_rank,
    "stock_north_flow": stock_north_flow,
    "stock_margin_trading": stock_margin_trading,
    "stock_block_trade": stock_block_trade,
    "stock_holder_num": stock_holder_num,
    # A-Stock: 分析
    "stock_chip": stock_chip,
    "stock_fund_flow": stock_fund_flow,
    "stock_sector_spot": stock_sector_spot,
    "stock_board_cons": stock_board_cons,
    # A-Stock: 估值
    "stock_market_pe_percentile": stock_market_pe_percentile,
    "stock_industry_pe": stock_industry_pe,
    "stock_dividend_history": stock_dividend_history,
    "stock_institutional_holdings": stock_institutional_holdings,
    "stock_earnings_calendar": stock_earnings_calendar,
    "stock_financial_compare": stock_financial_compare,
    # A-Stock: 股东
    "stock_locked_shares": stock_locked_shares,
    "stock_pledge_ratio": stock_pledge_ratio,
    "stock_top10_holders": stock_top10_holders,
    # A-Stock: 量化
    "backtest_strategy": backtest_strategy,
    # 美股
    "stock_prices_us": stock_prices_us,
    "stock_overview_us": stock_overview_us,
    "stock_financials_us": stock_financials_us,
    "stock_news_us": stock_news_us,
    "stock_earnings_us": stock_earnings_us,
    "stock_insider_us": stock_insider_us,
    "stock_tech_indicators_us": stock_tech_indicators_us,
    # 加密货币
    "okx_prices": okx_prices,
    "okx_loan_ratios": okx_loan_ratios,
    "okx_taker_volume": okx_taker_volume,
    "binance_ai_report": binance_ai_report,
    # 市场与系统
    "stock_news": stock_news,
    "stock_news_global": stock_news_global,
    "data_source_status": data_source_status,
}

# ==================== TOOL_REGISTRY ====================
# 元数据注册表：{name: (fn, title, description)}，供 MCP 服务器批量注册

TOOL_REGISTRY = {
    # A-Stock: 价格
    "index_prices": (index_prices, "获取A股指数历史价格", "根据A股指数代码获取指数历史价格及技术指标，例如 000300(沪深300)。"),
    "stock_prices": (stock_prices, "获取股票历史价格", "根据股票代码和市场获取股票历史价格及技术指标, 不支持加密货币。支持多数据源自动故障转移。"),
    "stock_realtime": (stock_realtime, "获取股票实时行情", "获取A股/港股实时行情数据，包括最新价、涨跌幅、成交量、换手率、市盈率等。支持多数据源自动故障转移。"),
    "stock_batch_realtime": (stock_batch_realtime, "批量获取实时行情", "批量获取多只A股实时行情数据。支持多数据源自动故障转移。"),
    # A-Stock: 信息
    "search": (search, "查找股票代码", "根据股票名称、公司名称等关键词查找股票代码, 不支持加密货币。该工具比较耗时，当你知道股票代码或用户已指定股票代码时，建议直接通过股票代码使用其他工具"),
    "stock_info": (stock_info, "获取股票信息", "根据股票代码和市场获取股票基本信息, 不支持加密货币"),
    "stock_indicators": (stock_indicators, "股票财务指标", "获取股票财务报告关键指标，支持A股、港股、美股市场"),
    "get_current_time": (get_current_time, "获取当前时间及A股交易日信息", "获取当前系统时间及A股交易日信息，建议在调用其他需要日期参数的工具前使用该工具"),
    # A-Stock: 市场资金流
    "stock_zt_pool": (stock_zt_pool, "A股涨停/强势股池", "获取中国A股市场(上证、深证)的涨停股池或强势股池数据"),
    "stock_lhb_ggtj_sina": (stock_lhb_ggtj_sina, "A股龙虎榜统计", "获取中国A股市场(上证、深证)的龙虎榜个股上榜统计数据。支持多数据源。"),
    "stock_sector_fund_flow_rank": (stock_sector_fund_flow_rank, "A股板块资金流", "获取中国A股市场(上证、深证)的行业资金流向数据"),
    "stock_north_flow": (stock_north_flow, "沪深港通北向资金", "获取沪深港通北向资金(外资)流向数据，包括沪股通、深股通的资金净流入情况。北向资金是A股重要的风向标。"),
    "stock_margin_trading": (stock_margin_trading, "A股融资融券", "获取A股市场融资融券数据，包括融资余额、融券余额等。融资融券是衡量市场杠杆资金的重要指标。"),
    "stock_block_trade": (stock_block_trade, "A股大宗交易", "获取A股大宗交易数据，包括成交价、成交量、溢价率等。大宗交易反映机构大额交易动向。"),
    "stock_holder_num": (stock_holder_num, "A股股东人数", "获取A股股东户数变化数据，筹码集中度的重要指标。股东人数减少通常意味着筹码趋于集中。"),
    # A-Stock: 分析
    "stock_chip": (stock_chip, "获取筹码分布", "获取A股筹码分布数据，包括获利比例、平均成本、成本区间、筹码集中度等。"),
    "stock_fund_flow": (stock_fund_flow, "获取个股资金流向", "获取A股个股的资金流向数据，包括主力、超大单、大单、中单、小单的流入流出情况。支持多数据源自动故障转移。"),
    "stock_sector_spot": (stock_sector_spot, "获取个股所属板块", "获取A股个股所属的行业和概念板块信息"),
    "stock_board_cons": (stock_board_cons, "获取板块成分股", "获取行业或概念板块的成分股列表。支持多数据源自动故障转移。"),
    # A-Stock: 估值
    "stock_market_pe_percentile": (stock_market_pe_percentile, "A股市场PE分位", "获取A股市场整体PE/PB的历史分位数，用于判断市场整体估值水平。"),
    "stock_industry_pe": (stock_industry_pe, "A股行业PE对比", "获取A股各行业PE对比数据，用于行业估值比较和行业轮动分析。"),
    "stock_dividend_history": (stock_dividend_history, "A股分红历史", "获取A股个股历史分红送转数据，包括派息、送股、转增等。用于分析股息率和分红政策。"),
    "stock_institutional_holdings": (stock_institutional_holdings, "A股基金持仓", "获取A股基金重仓股数据，显示公募基金持仓最多的股票及持仓变化。用于跟踪机构动向。"),
    "stock_earnings_calendar": (stock_earnings_calendar, "A股财报日历", "获取A股财报披露时间表，查看即将披露财报的公司。用于跟踪财报季。"),
    "stock_financial_compare": (stock_financial_compare, "A股财务指标对比", "获取A股个股详细财务指标，包括盈利能力、偿债能力、运营能力等多维度分析。"),
    # A-Stock: 股东
    "stock_locked_shares": (stock_locked_shares, "A股限售解禁日历", "获取A股限售股解禁日历，查看即将解禁的股票及解禁规模。限售解禁是重要的市场供给压力指标。"),
    "stock_pledge_ratio": (stock_pledge_ratio, "A股股权质押", "获取A股股权质押数据，包括行业质押统计和市场整体质押比例。股权质押是衡量大股东杠杆风险的重要指标。"),
    "stock_top10_holders": (stock_top10_holders, "A股十大股东", "获取A股个股十大股东或十大流通股东信息，用于分析股权结构和机构持仓变化。"),
    # A-Stock: 量化
    "backtest_strategy": (backtest_strategy, "A股策略回测", "对股票进行简单策略回测，支持均线交叉、MACD、KDJ等策略，返回收益率、最大回撤、胜率等指标。"),
    # 美股
    "stock_prices_us": (stock_prices_us, "获取美股/港股历史价格", "获取美股或港股的历史价格数据及技术指标。支持多数据源自动故障转移。"),
    "stock_overview_us": (stock_overview_us, "美股公司概览", "获取美股公司基本面概览，包括市值、PE、EPS、股息率、52周高低点、分析师评级等。支持多数据源: Alpha Vantage (需API key) -> yfinance (免费)。"),
    "stock_financials_us": (stock_financials_us, "美股财务报表", "获取美股财务报表数据，包括资产负债表、利润表、现金流量表。支持多数据源: Alpha Vantage (需API key) -> yfinance (免费)。"),
    "stock_news_us": (stock_news_us, "美股新闻情绪", "获取美股相关新闻及情绪分析数据。需要配置 ALPHA_VANTAGE_API_KEY 环境变量。"),
    "stock_earnings_us": (stock_earnings_us, "美股盈利数据", "获取美股历史盈利数据和分析师预期。支持多数据源: Alpha Vantage (需API key) -> yfinance (免费)。"),
    "stock_insider_us": (stock_insider_us, "美股内部交易", "获取美股公司内部人交易记录。支持多数据源: Alpha Vantage (需API key) -> yfinance (免费)。"),
    "stock_tech_indicators_us": (stock_tech_indicators_us, "美股技术指标", "获取美股技术分析指标数据，如SMA、EMA、RSI、MACD、布林带等。需要配置 ALPHA_VANTAGE_API_KEY 环境变量。"),
    # 加密货币
    "okx_prices": (okx_prices, "获取加密货币历史价格", "获取OKX加密货币的历史K线数据，包括价格、交易量和技术指标。支持自动重试。"),
    "okx_loan_ratios": (okx_loan_ratios, "获取加密货币杠杆多空比", "获取OKX加密货币借入计价货币与借入交易货币的累计数额比值。支持自动重试。"),
    "okx_taker_volume": (okx_taker_volume, "获取加密货币主动买卖情况", "获取OKX加密货币主动买入和卖出的交易量。支持自动重试。"),
    "binance_ai_report": (binance_ai_report, "获取加密货币分析报告", "获取币安对加密货币的AI分析报告，此工具对分析加密货币非常有用。支持自动重试。"),
    # 市场与系统
    "stock_news": (stock_news, "获取股票/加密货币相关新闻", "根据股票代码或加密货币符号获取近期相关新闻"),
    "stock_news_global": (stock_news_global, "全球财经快讯", "获取最新的全球财经快讯"),
    "data_source_status": (data_source_status, "查看数据源状态", "查看多数据源的状态和熔断器信息"),
}
