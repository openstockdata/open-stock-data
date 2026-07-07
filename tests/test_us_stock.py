"""
美股测试用例

测试标的: 拼多多 PDD

运行方式:
  uv run pytest tests/test_us_stock.py -v
  uv run pytest tests/test_us_stock.py -m "not requires_alpha_vantage" -v  # 跳过需要AV key的测试
"""

import pytest
from tests.test_utils import assert_has_data, assert_csv_format


@pytest.mark.us_stock
@pytest.mark.network
class TestUSStockSearch:
    """美股搜索测试"""

    def test_search_by_code(self, t, us_stock):
        result = t.search(keyword=us_stock["code"], market=us_stock["market"])
        assert_has_data(result)


@pytest.mark.us_stock
@pytest.mark.network
class TestUSStockPrices:
    """美股价格数据测试"""

    def test_daily_prices(self, t, us_stock):
        result = t.stock_prices(symbol=us_stock["code"], market=us_stock["market"], period="daily", limit=30)
        assert_has_data(result)
        assert_csv_format(result, min_rows=10)


@pytest.mark.us_stock
@pytest.mark.network
class TestUSStockIndicators:
    """美股财务指标测试"""

    def test_stock_indicators(self, t, us_stock):
        result = t.stock_indicators(symbol=us_stock["code"], market=us_stock["market"])
        assert_has_data(result)
        assert_csv_format(result)


@pytest.mark.us_stock
@pytest.mark.network
class TestUSStockOverview:
    """美股公司概览测试"""

    def test_company_overview(self, t, us_stock):
        result = t.stock_overview_us(symbol=us_stock["code"])
        assert result is not None
        assert len(str(result)) > 10


@pytest.mark.us_stock
@pytest.mark.network
class TestUSStockFinancials:
    """美股财务报表测试"""

    def test_balance_sheet_quarterly(self, t, us_stock):
        result = t.stock_financials_us(symbol=us_stock["code"], report_type="balance_sheet", quarterly=True)
        assert result is not None

    def test_income_statement_quarterly(self, t, us_stock):
        result = t.stock_financials_us(symbol=us_stock["code"], report_type="income_statement", quarterly=True)
        assert result is not None

    def test_cash_flow_annual(self, t, us_stock):
        result = t.stock_financials_us(symbol=us_stock["code"], report_type="cash_flow", quarterly=False)
        assert result is not None


@pytest.mark.us_stock
@pytest.mark.network
class TestUSStockEarnings:
    """美股盈利数据测试"""

    def test_earnings(self, t, us_stock):
        result = t.stock_earnings_us(symbol=us_stock["code"])
        assert result is not None


@pytest.mark.us_stock
@pytest.mark.network
class TestUSStockInsider:
    """美股内部交易测试"""

    def test_insider_transactions(self, t, us_stock):
        result = t.stock_insider_us(symbol=us_stock["code"], limit=10)
        assert result is not None


@pytest.mark.us_stock
@pytest.mark.network
class TestUSStockNews:
    """美股新闻测试"""

    @pytest.mark.requires_alpha_vantage
    def test_news_sentiment(self, t, us_stock, has_alpha_vantage_key):
        if not has_alpha_vantage_key:
            pytest.skip("未配置 ALPHA_VANTAGE_API_KEY")
        result = t.stock_news_us(symbol=us_stock["code"], limit=10)
        assert_has_data(result)

    @pytest.mark.requires_alpha_vantage
    def test_market_news(self, t, has_alpha_vantage_key):
        if not has_alpha_vantage_key:
            pytest.skip("未配置 ALPHA_VANTAGE_API_KEY")
        result = t.stock_news_us(symbol="", topics="technology", limit=10)
        assert_has_data(result)


@pytest.mark.us_stock
@pytest.mark.network
class TestUSStockTechIndicators:
    """美股技术指标测试"""

    @pytest.mark.requires_alpha_vantage
    def test_rsi(self, t, us_stock, has_alpha_vantage_key):
        if not has_alpha_vantage_key:
            pytest.skip("未配置 ALPHA_VANTAGE_API_KEY")
        result = t.stock_tech_indicators_us(
            symbol=us_stock["code"], indicator="RSI", interval="daily", time_period=14, limit=20
        )
        assert_has_data(result)

    @pytest.mark.requires_alpha_vantage
    def test_macd(self, t, us_stock, has_alpha_vantage_key):
        if not has_alpha_vantage_key:
            pytest.skip("未配置 ALPHA_VANTAGE_API_KEY")
        result = t.stock_tech_indicators_us(
            symbol=us_stock["code"], indicator="MACD", interval="daily", time_period=14, limit=20
        )
        assert_has_data(result)

    @pytest.mark.requires_alpha_vantage
    def test_bbands(self, t, us_stock, has_alpha_vantage_key):
        if not has_alpha_vantage_key:
            pytest.skip("未配置 ALPHA_VANTAGE_API_KEY")
        result = t.stock_tech_indicators_us(
            symbol=us_stock["code"], indicator="BBANDS", interval="daily", time_period=20, limit=20
        )
        assert_has_data(result)
