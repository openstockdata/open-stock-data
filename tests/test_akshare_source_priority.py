import pandas as pd
import pytest

from open_stock_data.data_provider.akshare_fetcher import AkshareFetcher
from open_stock_data.data_provider.types import RealtimeSource, UnifiedRealtimeQuote


def _quote(code: str, source: RealtimeSource) -> UnifiedRealtimeQuote:
    return UnifiedRealtimeQuote(
        code=code,
        name="测试",
        source=source,
        price=10.0,
        change_pct=1.0,
    )


def test_realtime_fallback_prefers_tencent_over_sina(monkeypatch):
    fetcher = AkshareFetcher()
    calls: list[str] = []

    monkeypatch.setattr(
        fetcher,
        "_get_stock_realtime_quote_em",
        lambda code: calls.append("em") or None,
    )
    monkeypatch.setattr(
        fetcher,
        "_get_stock_realtime_quote_tencent",
        lambda code: calls.append("tencent") or _quote(code, RealtimeSource.TENCENT),
    )
    monkeypatch.setattr(
        fetcher,
        "_get_stock_realtime_quote_sina",
        lambda code: calls.append("sina") or _quote(code, RealtimeSource.AKSHARE_SINA),
    )

    quote = fetcher.get_realtime_quote("600519")

    assert quote is not None
    assert quote.source == RealtimeSource.TENCENT
    assert calls == ["em", "tencent"]


def _raise_conn(code):
    raise ConnectionError("push2 down")


def test_realtime_em_network_error_falls_through_to_tencent(monkeypatch):
    """东财网络不通时腾讯/新浪是独立后端，必须继续回退而不是整条链失败。"""
    fetcher = AkshareFetcher()
    monkeypatch.setattr(fetcher, "random_sleep", lambda *a, **k: None)
    calls: list[str] = []

    monkeypatch.setattr(fetcher, "_get_stock_realtime_quote_em", lambda code: calls.append("em") or _raise_conn(code))
    monkeypatch.setattr(
        fetcher,
        "_get_stock_realtime_quote_tencent",
        lambda code: calls.append("tencent") or _quote(code, RealtimeSource.TENCENT),
    )
    monkeypatch.setattr(fetcher, "_get_stock_realtime_quote_sina", lambda code: calls.append("sina") or None)

    quote = fetcher.get_realtime_quote("600519")

    assert quote is not None and quote.source == RealtimeSource.TENCENT
    assert calls == ["em", "tencent"]


def test_realtime_all_sources_network_error_is_raised(monkeypatch):
    fetcher = AkshareFetcher()
    monkeypatch.setattr(fetcher, "random_sleep", lambda *a, **k: None)
    for name in ("_get_stock_realtime_quote_em", "_get_stock_realtime_quote_tencent", "_get_stock_realtime_quote_sina"):
        monkeypatch.setattr(fetcher, name, _raise_conn)

    with pytest.raises(ConnectionError):
        fetcher.get_realtime_quote("600519")


def test_realtime_network_error_then_empty_returns_none(monkeypatch):
    """部分来源网络异常、其余来源正常但无数据：视为无数据（None），不当作后端不可达。"""
    fetcher = AkshareFetcher()
    monkeypatch.setattr(fetcher, "random_sleep", lambda *a, **k: None)
    monkeypatch.setattr(fetcher, "_get_stock_realtime_quote_em", _raise_conn)
    monkeypatch.setattr(fetcher, "_get_stock_realtime_quote_tencent", lambda code: None)
    monkeypatch.setattr(fetcher, "_get_stock_realtime_quote_sina", lambda code: None)

    assert fetcher.get_realtime_quote("600519") is None


def test_daily_fallback_prefers_tencent_over_sina_after_eastmoney(monkeypatch):
    fetcher = AkshareFetcher()
    calls: list[str] = []

    monkeypatch.setattr(
        fetcher,
        "_fetch_stock_data_em",
        lambda *args, **kwargs: calls.append("em") or pd.DataFrame(),
    )
    monkeypatch.setattr(
        fetcher,
        "_fetch_stock_data_tx",
        lambda *args, **kwargs: calls.append("tencent") or pd.DataFrame([{"日期": "2026-03-26", "收盘": 10.0}]),
    )
    monkeypatch.setattr(
        fetcher,
        "_fetch_stock_data_sina",
        lambda *args, **kwargs: calls.append("sina") or pd.DataFrame([{"日期": "2026-03-26", "收盘": 11.0}]),
    )

    df = fetcher._fetch_stock_data("600519", "2026-03-20", "2026-03-26")

    assert df is not None
    assert not df.empty
    assert calls == ["em", "tencent"]
