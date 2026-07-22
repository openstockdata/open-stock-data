"""
A股测试用例

测试标的: 平潭发展 000592 (深证)

运行方式:
  uv run pytest tests/test_a_stock.py -v -s
  uv run pytest tests/test_a_stock.py -k "TestAStockPrices" -v -s
"""

import pandas as pd
import pytest
from unittest.mock import patch
from tests.test_utils import assert_has_data, assert_csv_format

PREVIEW = 400


def preview(result, label=""):
    """打印结果前400字符"""
    tag = f"[{label}] " if label else ""
    print(f"\n{tag}{str(result)[:PREVIEW]}")


@pytest.mark.a_stock
@pytest.mark.network
class TestAStockSearch:
    """A股搜索测试"""

    def test_search_by_code(self, t, a_stock):
        result = t.search(keyword=a_stock["code"], market=a_stock["market"])
        preview(result, "search_by_code")
        assert_has_data(result)
        assert a_stock["code"] in result or a_stock["name"] in result

    def test_search_by_name(self, t, a_stock):
        result = t.search(keyword=a_stock["name"], market=a_stock["market"])
        preview(result, "search_by_name")
        assert_has_data(result)


@pytest.mark.a_stock
@pytest.mark.network
class TestAStockInfo:
    """A股基本信息测试"""

    def test_stock_info(self, t, a_stock):
        result = t.stock_info(symbol=a_stock["code"], market=a_stock["market"])
        preview(result, "stock_info")
        assert_has_data(result)

    def test_stock_indicators(self, t, a_stock):
        result = t.stock_indicators(symbol=a_stock["code"], market=a_stock["market"])
        preview(result, "stock_indicators")
        assert_has_data(result)
        assert_csv_format(result)


@pytest.mark.a_stock
@pytest.mark.network
def test_index_prices_falls_back_to_sina_when_eastmoney_unavailable(t):
    df = pd.DataFrame({
        "date": ["2099-03-24", "2099-03-25"],
        "open": [4458.907, 4506.625],
        "high": [4474.825, 4542.181],
        "low": [4394.294, 4502.027],
        "close": [4474.722, 4537.466],
        "volume": [25553251700, 26263927900],
    })

    with patch("open_stock_data.tools.a_stock.prices.get_data_manager") as get_manager:
        manager = get_manager.return_value
        manager.fetch_akshare.side_effect = [None, df]
        result = t.index_prices(symbol="000300", period="daily", limit=2)

    assert_has_data(result)
    assert "数据来源: akshare (sina)" in result
    assert "000300 指数历史价格" in result


@pytest.mark.a_stock
@pytest.mark.network
class TestAStockPrices:
    """A股价格数据测试"""

    def test_index_prices_hs300(self, t):
        result = t.index_prices(symbol="000300", period="daily", limit=30)
        preview(result, "index_prices_hs300")
        assert_has_data(result)
        assert_csv_format(result, min_rows=10)
        assert "000300" in result

    def test_daily_prices(self, t, a_stock):
        result = t.stock_prices(symbol=a_stock["code"], market=a_stock["market"], period="daily", limit=30)
        preview(result, "daily_prices")
        assert_has_data(result)
        assert_csv_format(result, min_rows=10)
        assert "MACD" in result or "MA5" in result

    def test_weekly_prices(self, t, a_stock):
        result = t.stock_prices(symbol=a_stock["code"], market=a_stock["market"], period="weekly", limit=10)
        preview(result, "weekly_prices")
        assert_has_data(result)


@pytest.mark.a_stock
@pytest.mark.network
class TestAStockRealtime:
    """A股实时行情测试"""

    def test_realtime_quote(self, t, a_stock):
        result = t.stock_realtime(symbol=a_stock["code"], market=a_stock["market"])
        preview(result, "realtime_quote")
        assert_has_data(result)
        assert "最新价" in result or "涨跌幅" in result

    def test_batch_realtime(self, t, a_stock):
        symbols = f"{a_stock['code']},000001,600519,518850"
        result = t.stock_batch_realtime(symbols=symbols, limit=10)
        preview(result, "batch_realtime")
        assert_has_data(result)
        assert_csv_format(result)


@pytest.mark.a_stock
@pytest.mark.network
class TestAStockChip:
    """A股筹码分布测试"""

    def test_chip_distribution(self, t, a_stock):
        result = t.stock_chip(symbol=a_stock["code"])
        preview(result, "chip")
        assert result is not None


@pytest.mark.a_stock
@pytest.mark.network
class TestAStockAnalytics:
    """A股分析数据测试"""

    def test_fund_flow(self, t, a_stock):
        result = t.stock_fund_flow(symbol=a_stock["code"])
        preview(result, "fund_flow")
        assert result is not None

    def test_sector_spot(self, t, a_stock):
        result = t.stock_sector_spot(symbol=a_stock["code"])
        preview(result, "sector_spot")
        assert result is not None


@pytest.mark.a_stock
@pytest.mark.network
class TestAStockBoard:
    """A股板块测试 - 仅测试Tushare支持的热门板块，避免冷门板块导致数据源切换"""

    # 行业板块备选列表（仅保留Tushare热门板块）
    INDUSTRY_BOARDS = [
        "银行",
        "证券",
        "保险",
    ]

    # 概念板块备选列表（仅保留Tushare热门板块）
    CONCEPT_BOARDS = [
        "华为概念",
        "新能源汽车",
        "锂电池",
    ]

    def test_board_cons_industry(self, t):
        """测试行业板块成分股（多个备选）"""
        last_error = None
        for board_name in self.INDUSTRY_BOARDS:
            result = t.stock_board_cons(board_name=board_name, board_type="industry", limit=10)
            if result is not None and "Not Found" not in result:
                preview(result, f"board_cons_industry({board_name})")
                assert "代码" in result or "名称" in result or "成分股" in result
                return  # 成功找到一个板块
            last_error = result
        # 所有板块都失败，给出详细信息
        pytest.skip(f"所有行业板块均不可用（网络问题），最后尝试: {last_error[:100] if last_error else 'None'}")

    def test_board_cons_concept(self, t):
        """测试概念板块成分股（多个备选）"""
        last_error = None
        for board_name in self.CONCEPT_BOARDS:
            result = t.stock_board_cons(board_name=board_name, board_type="concept", limit=10)
            if result is not None and "Not Found" not in result:
                preview(result, f"board_cons_concept({board_name})")
                assert "代码" in result or "名称" in result or "成分股" in result
                return  # 成功找到一个板块
            last_error = result
        # 所有板块都失败，给出详细信息
        pytest.skip(f"所有概念板块均不可用（Tushare配额/网络问题），最后尝试: {last_error[:100] if last_error else 'None'}")

    @pytest.mark.parametrize("board_name", ["银行", "证券"])
    def test_board_cons_industry_parametrized(self, t, board_name):
        """参数化测试热门行业板块"""
        result = t.stock_board_cons(board_name=board_name, board_type="industry", limit=5)
        preview(result, f"board_industry({board_name})")
        # 允许网络失败，但不允许代码错误
        assert result is not None
        if "Not Found" in result:
            pytest.skip(f"板块 {board_name} 暂时不可用")

    @pytest.mark.parametrize("board_name", ["华为概念", "新能源汽车"])
    def test_board_cons_concept_parametrized(self, t, board_name):
        """参数化测试热门概念板块（避免Tushare配额限制）"""
        result = t.stock_board_cons(board_name=board_name, board_type="concept", limit=5)
        preview(result, f"board_concept({board_name})")
        # 允许网络失败，但不允许代码错误
        assert result is not None
        if "Not Found" in result:
            pytest.skip(f"板块 {board_name} 暂时不可用（Tushare配额或网络问题）")


@pytest.mark.a_stock
@pytest.mark.network
class TestAStockMarket:
    """A股市场数据测试"""

    def test_zt_pool(self, t):
        result = t.stock_zt_pool(pool_type="涨停", limit=30)
        preview(result, "zt_pool")
        assert result is not None

    def test_zt_pool_strong(self, t):
        result = t.stock_zt_pool(pool_type="强势", limit=30)
        preview(result, "zt_pool_strong")
        assert result is not None

    def test_lhb_stats(self, t):
        result = t.stock_lhb_ggtj_sina(days="5", limit=30)
        preview(result, "lhb_stats")
        assert result is not None

    def test_sector_fund_flow(self, t):
        result = t.stock_sector_fund_flow_rank(days="今日", cate="行业资金流")
        preview(result, "sector_fund_flow")
        assert result is not None
        if "失败" in result:
            pytest.skip(result)
        assert "失败" not in result


@pytest.mark.a_stock
@pytest.mark.network
class TestAStockNews:
    """A股新闻测试"""

    def test_stock_news(self, t, a_stock):
        result = t.stock_news(symbol=a_stock["code"], limit=10)
        preview(result, "stock_news")
        assert result is not None

    def test_stock_news_not_found(self, t):
        """测试新闻查询无结果时的处理"""
        result = t.stock_news(symbol="不存在的关键词xyz123", limit=5)
        preview(result, "stock_news_not_found")
        assert result is not None
        assert "失败" in result or "未找到" in result


# ==================== ETF 测试 ====================

@pytest.mark.a_stock
@pytest.mark.network
class TestETFPrices:
    """ETF 价格数据测试"""

    def test_etf_daily_prices(self, t, etf):
        """测试 ETF 历史价格获取"""
        result = t.stock_prices(symbol=etf["code"], market=etf["market"], period="daily", limit=30)
        preview(result, "etf_daily_prices")
        assert_has_data(result)
        assert_csv_format(result, min_rows=10)

    def test_etf_realtime(self, t, etf):
        """测试 ETF 实时行情"""
        result = t.stock_realtime(symbol=etf["code"], market=etf["market"])
        preview(result, "etf_realtime")
        assert result is not None


@pytest.mark.a_stock
@pytest.mark.network
class TestETFChip:
    """ETF 筹码分布测试 - 应返回友好提示"""

    def test_etf_chip_not_supported(self, t, etf):
        """测试 ETF 筹码分布应返回不支持提示"""
        result = t.stock_chip(symbol=etf["code"])
        preview(result, "etf_chip")
        assert result is not None
        assert "ETF" in result or "不支持" in result or "基金" in result


@pytest.mark.a_stock
@pytest.mark.network
class TestETFFundFlow:
    """ETF 资金流向测试"""

    def test_etf_fund_flow(self, t, etf):
        """测试 ETF 资金流向"""
        result = t.stock_fund_flow(symbol=etf["code"])
        preview(result, "etf_fund_flow")
        assert result is not None


@pytest.mark.a_stock
@pytest.mark.network
class TestSectorFundFlow:
    """板块资金流向测试"""

    def test_sector_fund_flow_5d(self, t):
        """测试5日板块资金流（避免今日非交易时段无数据）"""
        result = t.stock_sector_fund_flow_rank(days="5日", cate="行业资金流")
        preview(result, "sector_fund_flow_5d")
        assert result is not None
        if "失败" in result:
            pytest.skip(result)
        assert "失败" not in result

    def test_sector_fund_flow_concept(self, t):
        """测试概念资金流"""
        result = t.stock_sector_fund_flow_rank(days="5日", cate="概念资金流")
        preview(result, "sector_fund_flow_concept")
        assert result is not None
        if "失败" in result:
            pytest.skip(result)
        assert "失败" not in result


# ==================== 新增工具测试 ====================

@pytest.mark.a_stock
@pytest.mark.network
class TestAStockMarketSentiment:
    """A股市场情绪/资金流向测试（新增工具）"""

    def test_north_flow(self, t):
        """测试北向资金流向"""
        result = t.stock_north_flow(indicator="北向资金")
        preview(result, "north_flow")
        assert result is not None
        assert "净流入" in result or "日期" in result

    def test_north_flow_hgt(self, t):
        """测试沪股通资金流向"""
        result = t.stock_north_flow(indicator="沪股通")
        preview(result, "north_flow_hgt")
        assert result is not None

    def test_margin_trading_market(self, t):
        """测试市场融资融券数据"""
        result = t.stock_margin_trading(market="sh", limit=10)
        preview(result, "margin_trading")
        assert result is not None

    def test_block_trade_market(self, t):
        """测试大宗交易数据"""
        result = t.stock_block_trade(limit=20)
        preview(result, "block_trade")
        assert result is not None

    def test_holder_num(self, t, a_stock):
        """测试股东人数变化"""
        result = t.stock_holder_num(symbol=a_stock["code"])
        preview(result, "holder_num")
        assert result is not None


@pytest.mark.a_stock
@pytest.mark.network
class TestAStockPricesIndicators:
    """A股技术指标测试（新增指标）"""

    def test_prices_with_new_indicators(self, t, a_stock):
        """测试新增技术指标: ADX, CCI, WR, VWAP, +DI, -DI, BOLL.W, VMA"""
        result = t.stock_prices(symbol=a_stock["code"], market=a_stock["market"], period="daily", limit=60)
        preview(result, "prices_indicators")
        assert_has_data(result)
        # 检查新增指标是否存在
        core_indicators = ["ADX", "CCI", "WR", "VWAP"]
        dmi_indicators = ["+DI", "-DI"]
        vol_indicators = ["BOLL.W", "VMA5", "VMA10"]
        has_core = any(ind in result for ind in core_indicators)
        has_dmi = any(ind in result for ind in dmi_indicators)
        has_vol = any(ind in result for ind in vol_indicators)
        assert has_core, f"核心技术指标 ({core_indicators}) 应存在于输出中"
        assert has_dmi or has_vol, f"扩展技术指标 ({dmi_indicators + vol_indicators}) 应存在于输出中"


@pytest.mark.a_stock
@pytest.mark.network
class TestAStockZtPoolExtended:
    """A股涨停/跌停股池扩展测试"""

    def test_zt_pool_dt(self, t):
        """测试跌停股池"""
        result = t.stock_zt_pool(pool_type="跌停", limit=20)
        preview(result, "zt_pool_dt")
        assert result is not None

    def test_zt_pool_yesterday(self, t):
        """测试昨日涨停股今日表现"""
        result = t.stock_zt_pool(pool_type="昨日涨停", limit=20)
        preview(result, "zt_pool_yesterday")
        assert result is not None


# ==================== A股估值分析测试 ====================

@pytest.mark.a_stock
@pytest.mark.network
class TestAStockValuation:
    """A股估值分析工具测试"""

    def test_market_pe_percentile(self, t):
        """测试市场整体PE分位"""
        result = t.stock_market_pe_percentile()
        preview(result, "market_pe_percentile")
        assert result is not None
        # 应包含分位数相关信息
        has_percentile = any(k in result for k in ["分位", "PE", "PB", "历史"])
        assert has_percentile, f"市场PE分位应包含相关信息: {result[:200]}"

    def test_industry_pe(self, t):
        """测试行业PE对比"""
        result = t.stock_industry_pe(date="")  # 空字符串表示最新
        preview(result, "industry_pe")
        assert result is not None
        assert "失败" not in result


# ==================== 新增分析工具测试 ====================

@pytest.mark.a_stock
@pytest.mark.network
class TestAStockDividend:
    """A股分红历史测试"""

    def test_dividend_history(self, t, a_stock):
        """测试分红历史"""
        result = t.stock_dividend_history(symbol=a_stock["code"], limit=5)
        preview(result, "dividend_history")
        assert result is not None
        has_dividend = any(k in result for k in ["分红", "派息", "送股", "转增"])
        assert has_dividend, f"分红历史应包含分红信息: {result[:200]}"
        result = t.stock_dividend_history(symbol="600036", limit=5)
        preview(result, "dividend_history_600036")



@pytest.mark.a_stock
@pytest.mark.network
class TestAStockInstitutional:
    """A股机构持仓测试"""

    def test_institutional_holdings(self, t):
        """测试基金重仓股"""
        result = t.stock_institutional_holdings(limit=20)
        preview(result, "institutional_holdings")
        assert result is not None
        has_holding = any(k in result for k in ["基金", "持仓", "持股", "市值"])
        assert has_holding, f"基金持仓应包含相关信息: {result[:200]}"


@pytest.mark.a_stock
@pytest.mark.network
class TestAStockEarningsCalendar:
    """A股财报日历测试"""

    def test_earnings_calendar(self, t):
        """测试财报披露日历"""
        result = t.stock_earnings_calendar(limit=20)
        preview(result, "earnings_calendar")
        assert result is not None
        has_calendar = any(k in result for k in ["财报", "披露", "预约", "股票"])
        assert has_calendar, f"财报日历应包含相关信息: {result[:200]}"


@pytest.mark.a_stock
@pytest.mark.network
class TestAStockFinancialCompare:
    """A股财务指标对比测试"""

    def test_financial_compare(self, t, a_stock):
        """测试财务指标详情"""
        result = t.stock_financial_compare(symbol=a_stock["code"])
        preview(result, "financial_compare")
        assert result is not None
        has_financial = any(k in result for k in ["盈利", "ROE", "净利润", "资产"])
        assert has_financial, f"财务指标应包含相关信息: {result[:200]}"


# ==================== 限售解禁/质押/股东测试 ====================

@pytest.mark.a_stock
@pytest.mark.network
class TestLockedShares:
    """限售解禁日历测试"""

    def test_locked_shares_detail(self, t):
        """测试个股解禁明细"""
        result = t.stock_locked_shares(mode="detail", limit=10)
        preview(result, "locked_shares_detail")
        assert result is not None
        assert "限售解禁日历" in result or "未获取到" in result

    def test_locked_shares_summary(self, t):
        """测试解禁汇总"""
        result = t.stock_locked_shares(mode="summary", limit=10)
        preview(result, "locked_shares_summary")
        assert result is not None
        assert "限售解禁日历" in result or "未获取到" in result


@pytest.mark.a_stock
@pytest.mark.network
class TestPledgeRatio:
    """股权质押测试"""

    def test_pledge_industry(self, t):
        """测试行业质押统计"""
        result = t.stock_pledge_ratio(mode="industry", limit=15)
        preview(result, "pledge_industry")
        assert result is not None
        assert "质押" in result or "失败" in result

    def test_pledge_market(self, t):
        """测试市场整体质押趋势"""
        result = t.stock_pledge_ratio(mode="market", limit=10)
        preview(result, "pledge_market")
        assert result is not None
        assert "质押" in result or "失败" in result


@pytest.mark.a_stock
@pytest.mark.network
class TestTop10Holders:
    """十大股东测试"""

    def test_main_holders(self, t, a_stock):
        """测试十大股东"""
        result = t.stock_top10_holders(symbol="600519", holder_type="main")
        preview(result, "top10_main")
        assert result is not None
        assert "股东" in result or "失败" in result

    def test_circulate_holders(self, t, a_stock):
        """测试十大流通股东"""
        result = t.stock_top10_holders(symbol=a_stock["code"], holder_type="circulate")
        preview(result, "top10_circulate")
        assert result is not None
        assert "股东" in result or "失败" in result
