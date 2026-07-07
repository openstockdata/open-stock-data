"""
港股测试用例

测试标的: 阿里巴巴 09988

运行方式:
  uv run pytest tests/test_hk_stock.py -v
"""

import pytest
from tests.test_utils import assert_has_data, assert_csv_format


@pytest.mark.hk_stock
@pytest.mark.network
class TestHKStockSearch:
    """港股搜索测试"""

    def test_search_by_code(self, t, hk_stock):
        result = t.search(keyword=hk_stock["code"], market=hk_stock["market"])
        assert_has_data(result)

    def test_search_by_name(self, t, hk_stock):
        result = t.search(keyword=hk_stock["name"], market=hk_stock["market"])
        assert_has_data(result)


@pytest.mark.hk_stock
@pytest.mark.network
class TestHKStockInfo:
    """港股基本信息测试"""

    def test_stock_info(self, t, hk_stock):
        result = t.stock_info(symbol=hk_stock["code"], market=hk_stock["market"])
        assert_has_data(result)

    def test_stock_indicators(self, t, hk_stock):
        result = t.stock_indicators(symbol=hk_stock["code"], market=hk_stock["market"])
        assert_has_data(result)
        assert_csv_format(result)


@pytest.mark.hk_stock
@pytest.mark.network
class TestHKStockPrices:
    """港股价格数据测试"""

    def test_daily_prices(self, t, hk_stock):
        result = t.stock_prices(symbol=hk_stock["code"], market=hk_stock["market"], period="daily", limit=30)
        assert_has_data(result)
        assert_csv_format(result, min_rows=10)
        assert "MACD" in result or "MA5" in result

    def test_weekly_prices(self, t, hk_stock):
        result = t.stock_prices(symbol=hk_stock["code"], market=hk_stock["market"], period="weekly", limit=10)
        assert_has_data(result)


@pytest.mark.hk_stock
@pytest.mark.network
class TestHKStockRealtime:
    """港股实时行情测试"""

    def test_realtime_quote(self, t, hk_stock):
        result = t.stock_realtime(symbol=hk_stock["code"], market=hk_stock["market"])
        assert result is not None


@pytest.mark.hk_stock
@pytest.mark.network
class TestHKStockNews:
    """港股新闻测试"""

    def test_stock_news(self, t, hk_stock):
        result = t.stock_news(symbol=hk_stock["code"], limit=10)
        assert result is not None
