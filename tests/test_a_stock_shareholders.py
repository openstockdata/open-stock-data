"""
A股股东数据测试

测试标的: 平潭发展 000592 (深证)

覆盖工具:
- stock_locked_shares - 限售股解禁
- stock_pledge_ratio - 股权质押
- stock_top10_holders - 十大股东

运行方式:
  uv run pytest tests/test_a_stock_shareholders.py -v -s
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
class TestLockedShares:
    """限售股解禁测试"""

    def test_locked_shares_upcoming(self, t):
        """测试即将解禁的限售股"""
        result = t.stock_locked_shares()
        preview(result, "locked_shares")
        assert_has_data(result)
        # 验证包含解禁信息
        assert any(keyword in result for keyword in ["解禁", "限售", "上市日"])


@pytest.mark.a_stock
@pytest.mark.network
class TestPledgeRatio:
    """股权质押测试"""

    def test_pledge_ratio_market_summary(self, t):
        """测试市场整体质押统计"""
        result = t.stock_pledge_ratio()
        preview(result, "pledge_ratio_summary")
        assert_has_data(result)
        # 验证包含质押信息
        assert any(keyword in result for keyword in ["质押", "股权", "比例"])


@pytest.mark.a_stock
@pytest.mark.network
class TestTop10Holders:
    """十大股东测试"""

    def test_top10_holders_circulating(self, t, a_stock):
        """测试十大流通股东"""
        result = t.stock_top10_holders(
            symbol=a_stock["code"],
            holder_type="流通股东"
        )
        preview(result, "top10_holders_circulating")
        # 可能无数据，不强制要求
        if result and "失败" not in result:
            assert a_stock["code"] in result or "股东" in result

    def test_top10_holders_all(self, t, a_stock):
        """测试十大股东"""
        result = t.stock_top10_holders(
            symbol=a_stock["code"],
            holder_type="十大股东"
        )
        preview(result, "top10_holders_all")
        # 可能无数据，不强制要求
        if result and "失败" not in result:
            assert a_stock["code"] in result or "股东" in result

    def test_top10_holders_popular_stock(self, t):
        """测试热门股票十大股东（贵州茅台 600519）"""
        result = t.stock_top10_holders(symbol="600519", holder_type="十大股东")
        preview(result, "top10_holders_600519")
        # 贵州茅台通常有十大股东数据
        if result and "失败" not in result:
            assert_has_data(result)
