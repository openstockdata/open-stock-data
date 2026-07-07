import pandas as pd

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
