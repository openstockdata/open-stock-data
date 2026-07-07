"""
A股市场资金流测试

测试标的: 平潭发展 000592 (深证)

覆盖工具:
- stock_north_flow - 沪深港通北向资金流向
- stock_margin_trading - 融资融券数据
- stock_block_trade - 大宗交易数据
- stock_holder_num - 股东户数变化

运行方式:
  uv run pytest tests/test_a_stock_market_sentiment.py -v -s
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
class TestNorthFlow:
    """北向资金流向测试"""

    def test_north_flow_latest(self, t):
        """测试最新北向资金流向"""
        result = t.stock_north_flow()
        preview(result, "north_flow")
        assert_has_data(result)
        # 验证包含北向资金信息
        assert any(keyword in result for keyword in ["北向", "沪股通", "深股通", "资金"])


@pytest.mark.a_stock
@pytest.mark.network
class TestMarginTrading:
    """融资融券测试"""

    def test_margin_trading_summary(self, t):
        """测试市场整体融资融券汇总"""
        result = t.stock_margin_trading()
        preview(result, "margin_trading_summary")
        assert_has_data(result)
        # 验证包含融资融券信息
        assert any(keyword in result for keyword in ["融资", "融券", "余额"])

    def test_margin_trading_individual_stock(self, t, a_stock):
        """测试个股融资融券明细"""
        result = t.stock_margin_trading(symbol=a_stock["code"])
        preview(result, "margin_trading_individual")
        # 个股可能无融资融券数据，不强制要求
        if result and "失败" not in result:
            assert a_stock["code"] in result or "融资" in result


@pytest.mark.a_stock
@pytest.mark.network
class TestBlockTrade:
    """大宗交易测试"""

    def test_block_trade_summary(self, t):
        """测试市场大宗交易统计"""
        result = t.stock_block_trade()
        preview(result, "block_trade_summary")
        # 可能无数据（当天可能无大宗交易），不强制要求
        if result and "失败" not in result:
            assert "大宗" in result or "交易" in result or "成交" in result

    def test_block_trade_with_symbol(self, t, a_stock):
        """测试个股大宗交易"""
        result = t.stock_block_trade(symbol=a_stock["code"])
        preview(result, "block_trade_individual")
        # 可能无数据，不强制要求
        if result and "失败" not in result:
            assert a_stock["code"] in result or "成交" in result


@pytest.mark.a_stock
@pytest.mark.network
class TestHolderNum:
    """股东户数测试"""

    def test_holder_num(self, t, a_stock):
        """测试股东户数变化"""
        result = t.stock_holder_num(symbol=a_stock["code"])
        preview(result, "holder_num")
        # 可能无数据，不强制要求
        if result and "失败" not in result:
            assert a_stock["code"] in result or "股东" in result or "户数" in result

    def test_holder_num_popular_stock(self, t):
        """测试热门股票股东户数（贵州茅台 600519）"""
        result = t.stock_holder_num(symbol="600519")
        preview(result, "holder_num_600519")
        # 贵州茅台通常有股东户数数据
        if result and "失败" not in result:
            assert_has_data(result)
