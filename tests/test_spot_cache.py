import time
import pytest

import pandas as pd

from open_stock_data.cache import CacheStore
from open_stock_data.data_provider.base import DataFetcherManager


@pytest.fixture(autouse=True)
def clean_cache():
    CacheStore.clear_all()
    yield
    CacheStore.clear_all()


def _spot_df(complete=False, source=None):
    df = pd.DataFrame([
        {"代码": "600519", "名称": "贵州茅台", "最新价": 1500.0},
    ])
    if complete:
        df.attrs["spot_complete"] = True
    if source is not None:
        df.attrs["source"] = source
    return df


def test_get_a_stock_spot_cache_hit(monkeypatch):
    manager = DataFetcherManager(auto_init=False)
    cached_df = _spot_df(complete=True, source="AkshareFetcher")
    manager._set_dynamic_cached("spot:a_stock", cached_df)

    calls = {"count": 0}
    monkeypatch.setattr(manager, "_get_data_with_failover", lambda *a, **k: (calls.__setitem__("count", calls["count"] + 1), None)[1])

    result = manager.get_a_stock_spot()

    assert calls["count"] == 0
    assert result is not None
    assert result.equals(cached_df)
    assert result is not cached_df


def test_get_a_stock_spot_caches_complete_data(monkeypatch):
    manager = DataFetcherManager(auto_init=False)
    fetched_df = _spot_df(complete=True, source="AkshareFetcher")
    monkeypatch.setattr(manager, "_get_data_with_failover", lambda *a, **k: fetched_df)

    result = manager.get_a_stock_spot()

    assert result is fetched_df
    cached = manager._get_dynamic_cached("spot:a_stock", 86400)
    assert cached is not None


def test_get_a_stock_spot_does_not_cache_incomplete_data(monkeypatch):
    manager = DataFetcherManager(auto_init=False)
    fetched_df = _spot_df(complete=False, source="AkshareFetcher")
    monkeypatch.setattr(manager, "_get_data_with_failover", lambda *a, **k: fetched_df)

    result = manager.get_a_stock_spot()

    assert result is fetched_df
    cached = manager._get_dynamic_cached("spot:a_stock", 86400)
    assert cached is None


def test_get_a_stock_spot_returns_none_when_fetch_fails(monkeypatch):
    manager = DataFetcherManager(auto_init=False)
    monkeypatch.setattr(manager, "_get_data_with_failover", lambda *a, **k: None)

    result = manager.get_a_stock_spot()
    assert result is None


def test_get_belong_board_cache_hit(monkeypatch):
    manager = DataFetcherManager(auto_init=False)
    cached_df = pd.DataFrame([{"股票代码": "600410", "板块名称": "电气设备"}])
    manager._store.set("belong_board:600410", cached_df, expire=86400)

    calls = {"count": 0}
    monkeypatch.setattr(manager, "_get_data_with_failover", lambda *a, **k: (calls.__setitem__("count", calls["count"] + 1), None)[1])

    result = manager.get_belong_board("600410")

    assert calls["count"] == 0
    assert result is not None
    assert result.equals(cached_df)
    assert result is not cached_df


def test_get_belong_board_writes_cache(monkeypatch):
    manager = DataFetcherManager(auto_init=False)
    fetched_df = pd.DataFrame([{"股票代码": "600410", "板块名称": "电气设备"}])
    monkeypatch.setattr(manager, "_get_data_with_failover", lambda *a, **k: fetched_df)

    result = manager.get_belong_board("600410")

    assert result is not None
    cached = manager._store.get("belong_board:600410")
    assert cached is not None


def test_get_fund_flow_cache_hit(monkeypatch):
    manager = DataFetcherManager(auto_init=False)
    cached_df = pd.DataFrame([{"日期": "2026-04-01", "主力净流入": 1000}])
    manager._set_dynamic_cached("fund_flow:600410", cached_df)

    calls = {"count": 0}
    monkeypatch.setattr(manager, "_get_data_with_failover", lambda *a, **k: (calls.__setitem__("count", calls["count"] + 1), None)[1])

    result = manager.get_fund_flow("600410")

    assert calls["count"] == 0
    assert result is not None
    assert result.equals(cached_df)
    assert result is not cached_df


def test_get_fund_flow_writes_cache(monkeypatch):
    manager = DataFetcherManager(auto_init=False)
    fetched_df = pd.DataFrame([{"日期": "2026-04-01", "主力净流入": 1000}])
    monkeypatch.setattr(manager, "_get_data_with_failover", lambda *a, **k: fetched_df)

    result = manager.get_fund_flow("600410")

    assert result is not None
    cached = manager._get_dynamic_cached("fund_flow:600410", 86400)
    assert cached is not None
