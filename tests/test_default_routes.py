"""default_routes.py 的 TTL 助手与校验器单测（覆盖交易时段两分支与 validator）。"""
import pandas as pd

from open_stock_data.data_provider import default_routes as dr
from open_stock_data.data_provider.market_config import MarketType
from open_stock_data.data_provider.types import RealtimeSource, UnifiedRealtimeQuote


def test_ttl_helpers_cover_trading_and_closed(monkeypatch):
    monkeypatch.setattr(dr.MarketHoursConfig, "is_trading_time", staticmethod(lambda market: True))
    assert dr._realtime_ttl(MarketType.A_STOCK) == dr.CACHE_TTLS["realtime_trading"]
    assert dr._snapshot_ttl() == dr.CACHE_TTLS["spot_trading"]
    assert dr._fund_flow_ttl() == dr.CACHE_TTLS["fund_flow_trading"]

    monkeypatch.setattr(dr.MarketHoursConfig, "is_trading_time", staticmethod(lambda market: False))
    assert dr._realtime_ttl(MarketType.A_STOCK) == dr.CACHE_TTLS["realtime_closed"]
    assert dr._snapshot_ttl() == dr.CACHE_TTLS["spot_closed"]
    assert dr._fund_flow_ttl() == dr.CACHE_TTLS["fund_flow_closed"]


def test_daily_policy_allows_stale_on_error():
    policy = dr._daily_policy()
    assert policy.allow_stale_on_error is True
    assert policy.max_stale_seconds > 0
    assert policy.current_ttl() == dr.CACHE_TTLS["daily"]


def test_quote_validator():
    good = UnifiedRealtimeQuote(code="600519", source=RealtimeSource.FALLBACK, price=1.0, change_pct=1.0)
    assert dr._quote_is_valid(good) is True
    assert dr._quote_is_valid(None) is False


def test_snapshot_completeness_validator():
    assert dr._snapshot_is_complete(None) is False
    assert dr._snapshot_is_complete(pd.DataFrame()) is False

    complete = pd.DataFrame([{"a": 1}])
    complete.attrs["spot_complete"] = True
    assert dr._snapshot_is_complete(complete) is True

    partial = pd.DataFrame([{"a": 1}])
    partial.attrs["spot_complete"] = False
    assert dr._snapshot_is_complete(partial) is False
