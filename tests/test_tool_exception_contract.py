"""迁移后的工具适配器不得把 AllSourcesFailed 吞成错误字符串（契约：不吞异常）。"""
import pytest

from open_stock_data.exceptions import AllSourcesFailed
import open_stock_data.tools.a_stock.quant as quant_mod
import open_stock_data.tools.a_stock.market_flow as market_flow_mod


class _RaisingClient:
    def daily_prices(self, *args, **kwargs):
        raise AllSourcesFailed("daily_prices", [])

    def margin_detail(self, *args, **kwargs):
        raise AllSourcesFailed("margin_detail", [])


def test_backtest_strategy_propagates_all_sources_failed(monkeypatch):
    monkeypatch.setattr(quant_mod, "get_default_client", lambda: _RaisingClient())

    with pytest.raises(AllSourcesFailed):
        quant_mod.backtest_strategy(symbol="600519", strategy="ma_cross")


def test_backtest_strategy_still_returns_message_on_compute_error(monkeypatch):
    """非数据源异常（计算类）仍返回友好字符串，不外抛。"""
    class _BadDataClient:
        def daily_prices(self, *args, **kwargs):
            raise ValueError("boom in compute path")

    monkeypatch.setattr(quant_mod, "get_default_client", lambda: _BadDataClient())
    result = quant_mod.backtest_strategy(symbol="600519", strategy="ma_cross")
    assert isinstance(result, str) and "失败" in result


def test_stock_margin_trading_propagates_all_sources_failed(monkeypatch):
    monkeypatch.setattr(market_flow_mod, "get_default_client", lambda: _RaisingClient())

    with pytest.raises(AllSourcesFailed):
        market_flow_mod.stock_margin_trading(symbol="600519", market="sh")
