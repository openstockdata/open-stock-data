"""
测试能力声明模型

验证各个 Fetcher 的能力声明是否正确。
"""

import os
import pytest
from unittest.mock import patch

from open_stock_data.data_provider.capability import (
    Market,
    DataType,
    CostModel,
    FetcherCapability,
)
from open_stock_data.data_provider.capability_definitions import (
    create_tickflow_capability,
    create_tushare_capability,
    create_efinance_capability,
    create_akshare_capability,
    create_baostock_capability,
    create_pytdx_capability,
    create_yfinance_capability,
    create_alphavantage_capability,
)


class TestTickflowCapability:
    """TickFlow 能力声明测试"""

    def test_without_api_key(self):
        """未配置 API key 时的能力"""
        with patch.dict(os.environ, {}, clear=True):
            capability = create_tickflow_capability()

            # 应该支持免费的日线数据
            assert capability.supports_data_type(DataType.DAILY_KLINE)
            assert capability.supports_data_type(DataType.WEEKLY_KLINE)
            assert capability.supports_data_type(DataType.MONTHLY_KLINE)

            # 不应该支持实时行情
            assert not capability.supports_data_type(DataType.REALTIME_QUOTE)
            assert not capability.supports_data_type(DataType.INTRADAY_KLINE)

            # 成本模型
            assert capability.cost_model == CostModel.FREE
            assert capability.is_free()
            assert not capability.requires_auth

            # 市场支持
            assert capability.supports_market(Market.A_STOCK)
            assert capability.supports_market(Market.HK_STOCK)
            assert capability.supports_market(Market.US_STOCK)

            # 总是可用
            assert capability.is_available()

    def test_with_api_key(self):
        """配置 API key 后的能力"""
        with patch.dict(os.environ, {"TICKFLOW_API_KEY": "test-key"}):
            capability = create_tickflow_capability()

            # 应该支持所有数据类型
            assert capability.supports_data_type(DataType.DAILY_KLINE)
            assert capability.supports_data_type(DataType.REALTIME_QUOTE)
            assert capability.supports_data_type(DataType.INTRADAY_KLINE)

            # 成本模型
            assert capability.cost_model == CostModel.FREE_WITH_REGISTER
            assert capability.is_free()
            assert capability.requires_auth
            assert capability.auth_env_var == "TICKFLOW_API_KEY"

            # 配额限制
            assert capability.has_quota_limit()
            assert capability.quota_limit is not None
            assert capability.quota_limit.requests_per_minute == 10

            # 批量查询支持
            assert capability.supports_batch_for(DataType.REALTIME_QUOTE)


class TestTushareCapability:
    """Tushare 能力声明测试"""

    def test_without_token(self):
        """未配置 token 时不可用"""
        with patch.dict(os.environ, {}, clear=True):
            capability = create_tushare_capability()

            assert not capability.is_available()
            assert capability.requires_auth
            assert capability.auth_env_var == "TUSHARE_TOKEN"

    def test_with_token(self):
        """配置 token 后的能力"""
        with patch.dict(os.environ, {"TUSHARE_TOKEN": "test-token"}):
            capability = create_tushare_capability()

            assert capability.is_available()

            # 仅支持 A 股市场
            assert capability.supports_market(Market.A_STOCK)
            assert capability.supports_market(Market.ETF)
            assert capability.supports_market(Market.INDEX)
            assert not capability.supports_market(Market.US_STOCK)
            assert not capability.supports_market(Market.HK_STOCK)

            # 支持多种数据类型
            assert capability.supports_data_type(DataType.DAILY_KLINE)
            assert capability.supports_data_type(DataType.REALTIME_QUOTE)
            assert capability.supports_data_type(DataType.FUND_FLOW)
            assert capability.supports_data_type(DataType.DIVIDEND_HISTORY)

            # 配额限制
            assert capability.cost_model == CostModel.QUOTA_LIMITED
            assert capability.has_quota_limit()
            assert capability.quota_limit.requests_per_minute == 50


class TestEfinanceCapability:
    """Efinance 能力声明测试"""

    def test_basic_capability(self):
        """基本能力测试"""
        capability = create_efinance_capability()

        # 完全免费，无需认证
        assert capability.cost_model == CostModel.FREE
        assert capability.is_free()
        assert not capability.requires_auth
        assert capability.is_available()

        # 仅支持 A 股市场
        assert capability.supports_market(Market.A_STOCK)
        assert capability.supports_market(Market.ETF)
        assert not capability.supports_market(Market.US_STOCK)

        # 支持批量查询
        assert capability.supports_batch_for(DataType.REALTIME_QUOTE)
        assert capability.supports_batch_for(DataType.MARKET_SNAPSHOT)


class TestAkshareCapability:
    """Akshare 能力声明测试"""

    def test_multi_market_support(self):
        """多市场支持"""
        capability = create_akshare_capability()

        # 支持所有主要市场
        assert capability.supports_market(Market.A_STOCK)
        assert capability.supports_market(Market.HK_STOCK)
        assert capability.supports_market(Market.US_STOCK)
        assert capability.supports_market(Market.ETF)

        # 数据类型最全
        assert capability.supports_data_type(DataType.DAILY_KLINE)
        assert capability.supports_data_type(DataType.REALTIME_QUOTE)
        assert capability.supports_data_type(DataType.CHIP_DISTRIBUTION)
        assert capability.supports_data_type(DataType.MARGIN_DETAIL)
        assert capability.supports_data_type(DataType.INDUSTRY_PE)

        # 完全免费
        assert capability.is_free()
        assert not capability.requires_auth


class TestBaostockCapability:
    """Baostock 能力声明测试"""

    def test_kline_only(self):
        """仅支持 K 线数据"""
        capability = create_baostock_capability()

        # 仅支持 A 股
        assert capability.supports_market(Market.A_STOCK)
        assert capability.supports_market(Market.ETF)
        assert not capability.supports_market(Market.US_STOCK)

        # 仅支持 K 线
        assert capability.supports_data_type(DataType.DAILY_KLINE)
        assert capability.supports_data_type(DataType.WEEKLY_KLINE)
        assert capability.supports_data_type(DataType.MONTHLY_KLINE)

        # 不支持实时行情
        assert not capability.supports_data_type(DataType.REALTIME_QUOTE)
        assert not capability.supports_data_type(DataType.FUND_FLOW)

        # 完全免费
        assert capability.is_free()


class TestYfinanceCapability:
    """YFinance 能力声明测试"""

    def test_global_market_support(self):
        """全球市场支持"""
        capability = create_yfinance_capability()

        # 支持全球市场
        assert capability.supports_market(Market.A_STOCK)
        assert capability.supports_market(Market.HK_STOCK)
        assert capability.supports_market(Market.US_STOCK)
        assert capability.supports_market(Market.ETF)

        # 支持基础数据类型
        assert capability.supports_data_type(DataType.DAILY_KLINE)
        assert capability.supports_data_type(DataType.REALTIME_QUOTE)
        assert capability.supports_data_type(DataType.US_OVERVIEW)

        # 完全免费
        assert capability.is_free()
        assert not capability.requires_auth


class TestAlphaVantageCapability:
    """AlphaVantage 能力声明测试"""

    def test_without_api_key(self):
        """未配置 API key 时不可用"""
        with patch.dict(os.environ, {}, clear=True):
            capability = create_alphavantage_capability()

            assert not capability.is_available()
            assert capability.requires_auth

    def test_with_api_key(self):
        """配置 API key 后的能力"""
        with patch.dict(os.environ, {"ALPHA_VANTAGE_API_KEY": "test-key"}):
            capability = create_alphavantage_capability()

            assert capability.is_available()

            # 仅支持美股
            assert capability.supports_market(Market.US_STOCK)
            assert not capability.supports_market(Market.A_STOCK)

            # 支持美股专用数据
            assert capability.supports_data_type(DataType.US_OVERVIEW)
            assert capability.supports_data_type(DataType.US_NEWS)
            assert capability.supports_data_type(DataType.US_INSIDER)
            assert capability.supports_data_type(DataType.US_TECH_INDICATORS)

            # 配额限制严格
            assert capability.cost_model == CostModel.QUOTA_LIMITED
            assert capability.has_quota_limit()
            assert capability.quota_limit.requests_per_minute == 5
            assert capability.quota_limit.requests_per_day == 500


class TestCapabilityComparison:
    """能力对比测试"""

    def test_free_fetchers(self):
        """测试免费数据源"""
        free_fetchers = [
            ("Efinance", create_efinance_capability()),
            ("Akshare", create_akshare_capability()),
            ("Baostock", create_baostock_capability()),
            ("Pytdx", create_pytdx_capability()),
            ("YFinance", create_yfinance_capability()),
        ]

        for name, capability in free_fetchers:
            assert capability.is_free(), f"{name} 应该是免费的"
            assert not capability.requires_auth or capability.is_free(), \
                f"{name} 如果需要认证，应该是免费注册"

    def test_realtime_quote_support(self):
        """测试实时行情支持"""
        with patch.dict(os.environ, {
            "TICKFLOW_API_KEY": "test",
            "TUSHARE_TOKEN": "test",
            "ALPHA_VANTAGE_API_KEY": "test",
        }):
            capabilities = {
                "Tickflow": create_tickflow_capability(),
                "Tushare": create_tushare_capability(),
                "Efinance": create_efinance_capability(),
                "Akshare": create_akshare_capability(),
                "YFinance": create_yfinance_capability(),
            }

            for name, capability in capabilities.items():
                supports = capability.supports_data_type(DataType.REALTIME_QUOTE)
                print(f"{name}: 实时行情 = {supports}")
                assert supports, f"{name} 应该支持实时行情"

    def test_us_stock_support(self):
        """测试美股支持"""
        with patch.dict(os.environ, {
            "TICKFLOW_API_KEY": "test",
            "ALPHA_VANTAGE_API_KEY": "test",
        }):
            us_capable = []
            for name, factory in [
                ("Tickflow", create_tickflow_capability),
                ("Tushare", create_tushare_capability),
                ("Efinance", create_efinance_capability),
                ("Akshare", create_akshare_capability),
                ("YFinance", create_yfinance_capability),
                ("AlphaVantage", create_alphavantage_capability),
            ]:
                capability = factory()
                if capability.supports_market(Market.US_STOCK):
                    us_capable.append(name)

            print(f"支持美股的数据源: {us_capable}")
            assert "YFinance" in us_capable
            assert "AlphaVantage" in us_capable
            assert "Tickflow" in us_capable


class TestCapabilityEdgeCases:
    """边界情况测试"""

    def test_empty_capability(self):
        """测试空能力声明"""
        capability = FetcherCapability()

        assert not capability.supports_market(Market.A_STOCK)
        assert not capability.supports_data_type(DataType.DAILY_KLINE)
        assert capability.is_available()  # 默认可用
        assert capability.is_free()  # 默认免费

    def test_capability_with_custom_check(self):
        """测试自定义可用性检查"""
        check_called = []

        def custom_check():
            check_called.append(True)
            return False

        capability = FetcherCapability(
            availability_check=custom_check
        )

        result = capability.is_available()
        assert not result
        assert len(check_called) == 1


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
