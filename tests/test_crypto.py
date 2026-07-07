"""
加密货币测试用例

测试标的: BTC-USDT (比特币)

运行方式:
  uv run pytest tests/test_crypto.py -v
"""

import pytest
from tests.test_utils import assert_has_data, assert_csv_format


@pytest.mark.crypto
@pytest.mark.network
class TestCryptoPrices:
    """加密货币价格测试"""

    def test_okx_daily_kline(self, t, crypto):
        result = t.okx_prices(instId=crypto["inst_id"], bar="1D", limit=30)
        assert_has_data(result)
        assert_csv_format(result, min_rows=10)
        assert "MACD" in result or "MA5" in result

    def test_okx_hourly_kline(self, t, crypto):
        result = t.okx_prices(instId=crypto["inst_id"], bar="1H", limit=50)
        assert_has_data(result)
        assert_csv_format(result, min_rows=20)

    def test_okx_4h_kline(self, t, crypto):
        result = t.okx_prices(instId=crypto["inst_id"], bar="4H", limit=30)
        assert_has_data(result)
        assert_csv_format(result, min_rows=10)

    def test_okx_weekly_kline(self, t, crypto):
        result = t.okx_prices(instId=crypto["inst_id"], bar="1W", limit=20)
        assert_has_data(result)

    def test_okx_other_crypto(self, t):
        result = t.okx_prices(instId="ETH-USDT", bar="1D", limit=30)
        assert_has_data(result)


@pytest.mark.crypto
@pytest.mark.network
class TestCryptoLoanRatios:
    """加密货币杠杆多空比测试"""

    def test_loan_ratio_1h(self, t, crypto):
        result = t.okx_loan_ratios(symbol=crypto["symbol"], period="1H")
        assert_has_data(result)
        assert "多空比" in result

    def test_loan_ratio_1d(self, t, crypto):
        result = t.okx_loan_ratios(symbol=crypto["symbol"], period="1D")
        assert_has_data(result)


@pytest.mark.crypto
@pytest.mark.network
class TestCryptoTakerVolume:
    """加密货币主动买卖测试"""

    def test_spot_taker_volume(self, t, crypto):
        result = t.okx_taker_volume(symbol=crypto["symbol"], period="1H", instType="SPOT")
        assert_has_data(result)
        assert "买入量" in result or "卖出量" in result

    def test_contracts_taker_volume(self, t, crypto):
        result = t.okx_taker_volume(symbol=crypto["symbol"], period="1H", instType="CONTRACTS")
        assert_has_data(result)


@pytest.mark.crypto
@pytest.mark.network
class TestCryptoReport:
    """加密货币分析报告测试"""

    def test_binance_ai_report_btc(self, t, crypto):
        result = t.binance_ai_report(symbol=crypto["symbol"])
        assert result is not None

    def test_binance_ai_report_eth(self, t):
        result = t.binance_ai_report(symbol="ETH")
        assert result is not None


@pytest.mark.crypto
@pytest.mark.network
class TestCryptoNews:
    """加密货币新闻测试"""

    def test_btc_news(self, t):
        result = t.stock_news(symbol="BTC", limit=10)
        assert result is not None
