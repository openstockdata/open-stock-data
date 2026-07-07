"""
open-stock-data 测试配置

测试标的:
- A股: 平潭发展 000592 (深证)
- ETF: 黄金ETF华夏 518850 (上证)
- 港股: 阿里巴巴 09988
- 美股: 拼多多 PDD
- 加密货币: BTC-USDT

环境变量配置:
  1. 复制 tests/.env.example 为 tests/.env
  2. 填入你的 API keys（可选）
  3. 运行测试: uv run pytest tests/ -v

注意:
  - 大部分测试不需要 API keys 也能运行
  - TUSHARE_TOKEN: A股高级数据（可选）
  - ALPHA_VANTAGE_API_KEY: 美股新闻和技术指标（可选）
  - TICKFLOW_API_KEY: 全球市场实时行情（可选）
"""

import os
import pytest
from pathlib import Path

# 尝试从 .env 文件加载环境变量
env_file = Path(__file__).parent / ".env"
if env_file.exists():
    with open(env_file) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip()
                if value:  # 只设置非空值
                    os.environ.setdefault(key, value)

# 设置默认值（如果未配置）
os.environ.setdefault("LOG_LEVEL", "INFO")


# ==================== 导入工具函数 ====================
# open-stock-data 的工具函数是普通 Python 函数，可直接调用

from open_stock_data.tools import (
    # 搜索/信息
    search, stock_info, index_prices, stock_prices, stock_indicators, get_current_time,
    # A股市场数据
    stock_zt_pool, stock_lhb_ggtj_sina, stock_sector_fund_flow_rank,
    # A股资金流/市场情绪
    stock_north_flow, stock_margin_trading, stock_block_trade, stock_holder_num,
    # 实时/分析
    stock_realtime, stock_batch_realtime, stock_chip,
    stock_fund_flow, stock_sector_spot, stock_board_cons,
    # A股估值分析
    stock_market_pe_percentile, stock_industry_pe,
    # 新增分析工具
    stock_dividend_history, stock_institutional_holdings, stock_earnings_calendar,
    stock_financial_compare,
    # 限售解禁/质押/股东
    stock_locked_shares, stock_pledge_ratio, stock_top10_holders,
    # 加密货币
    okx_prices, okx_loan_ratios, okx_taker_volume, binance_ai_report,
    # 新闻/全球
    stock_news, stock_news_global, data_source_status,
    # 美股
    stock_overview_us, stock_financials_us, stock_news_us,
    stock_earnings_us, stock_insider_us, stock_tech_indicators_us,
)


class Tools:
    """工具函数集合（普通函数，无需解包）"""

    # 搜索/信息
    search = staticmethod(search)
    stock_info = staticmethod(stock_info)
    index_prices = staticmethod(index_prices)
    stock_prices = staticmethod(stock_prices)
    stock_news = staticmethod(stock_news)
    stock_indicators = staticmethod(stock_indicators)

    # A股市场数据
    get_current_time = staticmethod(get_current_time)
    stock_zt_pool = staticmethod(stock_zt_pool)
    stock_lhb_ggtj_sina = staticmethod(stock_lhb_ggtj_sina)
    stock_sector_fund_flow_rank = staticmethod(stock_sector_fund_flow_rank)

    # A股资金流/市场情绪
    stock_north_flow = staticmethod(stock_north_flow)
    stock_margin_trading = staticmethod(stock_margin_trading)
    stock_block_trade = staticmethod(stock_block_trade)
    stock_holder_num = staticmethod(stock_holder_num)

    # 实时/分析
    stock_realtime = staticmethod(stock_realtime)
    stock_chip = staticmethod(stock_chip)
    stock_batch_realtime = staticmethod(stock_batch_realtime)
    stock_fund_flow = staticmethod(stock_fund_flow)
    stock_sector_spot = staticmethod(stock_sector_spot)
    stock_board_cons = staticmethod(stock_board_cons)

    # A股估值分析
    stock_market_pe_percentile = staticmethod(stock_market_pe_percentile)
    stock_industry_pe = staticmethod(stock_industry_pe)

    # 新增分析工具
    stock_dividend_history = staticmethod(stock_dividend_history)
    stock_institutional_holdings = staticmethod(stock_institutional_holdings)
    stock_earnings_calendar = staticmethod(stock_earnings_calendar)
    stock_financial_compare = staticmethod(stock_financial_compare)

    # 限售解禁/质押/股东
    stock_locked_shares = staticmethod(stock_locked_shares)
    stock_pledge_ratio = staticmethod(stock_pledge_ratio)
    stock_top10_holders = staticmethod(stock_top10_holders)

    # 加密货币
    okx_prices = staticmethod(okx_prices)
    okx_loan_ratios = staticmethod(okx_loan_ratios)
    okx_taker_volume = staticmethod(okx_taker_volume)
    binance_ai_report = staticmethod(binance_ai_report)

    # 新闻/全球
    stock_news_global = staticmethod(stock_news_global)
    data_source_status = staticmethod(data_source_status)

    # 美股
    stock_overview_us = staticmethod(stock_overview_us)
    stock_financials_us = staticmethod(stock_financials_us)
    stock_news_us = staticmethod(stock_news_us)
    stock_earnings_us = staticmethod(stock_earnings_us)
    stock_insider_us = staticmethod(stock_insider_us)
    stock_tech_indicators_us = staticmethod(stock_tech_indicators_us)


@pytest.fixture
def t():
    """返回工具集合"""
    return Tools


# ==================== 测试标的配置 ====================

class TestSymbols:
    A_STOCK_CODE = "000592"
    A_STOCK_NAME = "平潭发展"
    A_STOCK_MARKET = "sz"

    ETF_CODE = "518850"
    ETF_NAME = "黄金ETF华夏"
    ETF_MARKET = "sh"

    HK_STOCK_CODE = "09988"
    HK_STOCK_NAME = "阿里巴巴"
    HK_STOCK_MARKET = "hk"

    US_STOCK_CODE = "PDD"
    US_STOCK_NAME = "拼多多"
    US_STOCK_MARKET = "us"

    CRYPTO_SYMBOL = "BTC"
    CRYPTO_INST_ID = "BTC-USDT"


@pytest.fixture
def a_stock():
    return {"code": TestSymbols.A_STOCK_CODE, "name": TestSymbols.A_STOCK_NAME, "market": TestSymbols.A_STOCK_MARKET}


@pytest.fixture
def etf():
    return {"code": TestSymbols.ETF_CODE, "name": TestSymbols.ETF_NAME, "market": TestSymbols.ETF_MARKET}


@pytest.fixture
def hk_stock():
    return {"code": TestSymbols.HK_STOCK_CODE, "name": TestSymbols.HK_STOCK_NAME, "market": TestSymbols.HK_STOCK_MARKET}


@pytest.fixture
def us_stock():
    return {"code": TestSymbols.US_STOCK_CODE, "name": TestSymbols.US_STOCK_NAME, "market": TestSymbols.US_STOCK_MARKET}


@pytest.fixture
def crypto():
    return {"symbol": TestSymbols.CRYPTO_SYMBOL, "inst_id": TestSymbols.CRYPTO_INST_ID}


@pytest.fixture
def has_tushare_token():
    return bool(os.getenv("TUSHARE_TOKEN"))


@pytest.fixture
def has_alpha_vantage_key():
    return bool(os.getenv("ALPHA_VANTAGE_API_KEY"))
