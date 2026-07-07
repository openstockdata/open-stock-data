"""
通用工具测试用例

测试不依赖特定标的的 open-stock-data 工具

运行方式:
  uv run pytest tests/test_common.py -v
"""

import pytest
from tests.test_utils import assert_has_data
from open_stock_data.data_provider import detect_stock_type, StockType, is_etf_code

PREVIEW = 400


def preview(result, label=""):
    """打印结果前400字符"""
    tag = f"[{label}] " if label else ""
    print(f"\n{tag}{str(result)[:PREVIEW]}")



@pytest.mark.network
class TestCurrentTime:
    """获取当前时间测试"""

    def test_get_current_time(self, t):
        result = t.get_current_time()
        preview(result, "get_current_time")
        assert_has_data(result)
        assert "当前时间" in result
        assert "交易日" in result or "星期" in result


@pytest.mark.network
class TestGlobalNews:
    """全球财经快讯测试"""

    def test_global_news(self, t):
        result = t.stock_news_global()
        preview(result, "global_news")
        assert result is not None


class TestDataSourceStatus:
    """数据源状态测试"""

    def test_status(self, t):
        result = t.data_source_status()
        preview(result, "data_source_status")
        assert_has_data(result)
        assert "数据源" in result
        assert "熔断" in result


class TestDataProvider:
    """数据提供者层测试"""

    def test_data_manager_init(self):
        from open_stock_data.utils import get_data_manager
        manager = get_data_manager()
        assert manager is not None

    def test_data_manager_status(self):
        from open_stock_data.utils import get_data_manager
        manager = get_data_manager()
        status = manager.get_status()
        assert isinstance(status, dict)
        assert "fetchers" in status
        assert len(status["fetchers"]) > 0

    def test_circuit_breaker_types(self):
        from open_stock_data.data_provider import get_circuit_breaker

        for name in ("daily", "realtime", "chip", "fund_flow", "board", "billboard"):
            assert get_circuit_breaker(name) is not None


@pytest.mark.network
class TestCrossMarketComparison:
    """跨市场对比测试 - 综合使用多个标的"""

    def test_all_markets_prices(self, t, a_stock, hk_stock, us_stock, crypto):
        a_result = t.stock_prices(symbol=a_stock["code"], market=a_stock["market"], limit=10)
        preview(a_result, "a_stock_prices")
        assert a_result is not None

        hk_result = t.stock_prices(symbol=hk_stock["code"], market=hk_stock["market"], limit=10)
        preview(hk_result, "hk_stock_prices")
        assert hk_result is not None

        us_result = t.stock_prices(symbol=us_stock["code"], market=us_stock["market"], limit=10)
        preview(us_result, "us_stock_prices")
        assert us_result is not None

        crypto_result = t.okx_prices(instId=crypto["inst_id"], bar="1D", limit=10)
        preview(crypto_result, "crypto_prices")
        assert crypto_result is not None

class TestStockTypeDetection:
    """股票代码类型检测测试"""

    def test_detects_mainland_codes_with_leading_zero(self):
        assert detect_stock_type("002402") == StockType.A_STOCK
        assert detect_stock_type("000001") == StockType.A_STOCK
        assert detect_stock_type("159001") == StockType.ETF
        assert detect_stock_type("01810.HK") == StockType.HK
        assert is_etf_code("159001") is True

