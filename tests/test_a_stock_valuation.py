"""
A股估值与财务测试

测试标的: 平潭发展 000592 (深证)

覆盖工具:
- stock_market_pe_percentile - 市场PE/PB分位
- stock_industry_pe - 行业PE对比
- stock_dividend_history - 分红历史
- stock_institutional_holdings - 基金持仓
- stock_earnings_calendar - 财报日历
- stock_financial_compare - 财务指标对比

运行方式:
  uv run pytest tests/test_a_stock_valuation.py -v -s
"""

import pytest
from tests.test_utils import assert_has_data

PREVIEW = 400


def preview(result, label=""):
    """打印结果前400字符"""
    tag = f"[{label}] " if label else ""
    print(f"\n{tag}{str(result)[:PREVIEW]}")


@pytest.mark.a_stock
@pytest.mark.network
class TestMarketPEPercentile:
    """市场PE/PB分位测试"""

    def test_market_pe_percentile(self, t):
        """测试市场整体PE/PB历史分位"""
        result = t.stock_market_pe_percentile()
        preview(result, "market_pe_percentile")
        assert_has_data(result)
        # 验证包含关键字段
        assert "PE" in result or "PB" in result or "百分位" in result


@pytest.mark.a_stock
@pytest.mark.network
class TestIndustryPE:
    """行业PE对比测试"""

    def test_industry_pe(self, t):
        """测试行业PE对比数据"""
        result = t.stock_industry_pe()
        preview(result, "industry_pe")
        # 可能因数据源问题失败，不强制要求
        if result and "失败" not in result:
            assert_has_data(result)
            # 验证包含行业信息
            assert "行业" in result or "PE" in result

    def test_industry_pe_with_date(self, t):
        """测试指定日期的行业PE"""
        result = t.stock_industry_pe(date="20241231")
        preview(result, "industry_pe_with_date")
        # 可能无数据，不强制要求
        if result and "失败" not in result:
            assert "行业" in result or "PE" in result


@pytest.mark.a_stock
@pytest.mark.network
class TestDividendHistory:
    """分红历史测试"""

    def test_dividend_history(self, t, a_stock):
        """测试个股分红历史"""
        result = t.stock_dividend_history(symbol=a_stock["code"])
        preview(result, "dividend_history")
        # 可能无分红记录，不强制要求
        if result and "失败" not in result:
            assert a_stock["code"] in result or "分红" in result or "送转" in result

    def test_dividend_history_high_dividend_stock(self, t):
        """测试高分红股票（中国神华 601088）"""
        result = t.stock_dividend_history(symbol="601088")
        preview(result, "dividend_history_601088")
        # 中国神华通常有分红记录
        if result and "失败" not in result:
            assert_has_data(result)


@pytest.mark.a_stock
@pytest.mark.network
class TestInstitutionalHoldings:
    """基金持仓测试"""

    def test_institutional_holdings_latest(self, t):
        """测试最新季度基金持仓（返回重仓股榜单）"""
        result = t.stock_institutional_holdings()
        preview(result, "institutional_holdings")
        assert_has_data(result)
        # 验证包含基金持仓信息
        assert "基金" in result or "持仓" in result or "股票" in result

    def test_institutional_holdings_with_date(self, t):
        """测试指定季度基金持仓"""
        result = t.stock_institutional_holdings(date="20240930")
        preview(result, "institutional_holdings_20240930")
        # 可能无数据或失败，不强制要求
        if result and "失败" not in result:
            assert_has_data(result)


@pytest.mark.a_stock
@pytest.mark.network
class TestEarningsCalendar:
    """财报日历测试"""

    def test_earnings_calendar_latest(self, t):
        """测试最新财报日历"""
        result = t.stock_earnings_calendar()
        preview(result, "earnings_calendar")
        assert_has_data(result)
        # 验证包含财报相关信息
        assert "披露" in result or "报告期" in result

    def test_earnings_calendar_annual_report(self, t):
        """测试年报披露日历"""
        import datetime
        current_year = datetime.datetime.now().year
        result = t.stock_earnings_calendar(period=f"{current_year}年报")
        preview(result, "earnings_calendar_annual")
        # 可能无数据（当前时间点可能还未到年报季）
        if result and "失败" not in result:
            assert_has_data(result)


@pytest.mark.a_stock
@pytest.mark.network
class TestFinancialCompare:
    """财务指标对比测试"""

    def test_financial_compare(self, t, a_stock):
        """测试财务指标对比"""
        result = t.stock_financial_compare(symbol=a_stock["code"])
        preview(result, "financial_compare")
        assert_has_data(result)
        # 验证包含财务指标
        assert any(keyword in result for keyword in [
            "营业收入", "净利润", "资产", "负债", "ROE", "毛利率"
        ])

    def test_financial_compare_bank_stock(self, t):
        """测试银行股财务指标（招商银行 600036）"""
        result = t.stock_financial_compare(symbol="600036")
        preview(result, "financial_compare_600036")
        assert_has_data(result)
        # 验证包含财务数据
        assert any(keyword in result for keyword in [
            "营业收入", "净利润", "资产"
        ])
