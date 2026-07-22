import pandas as pd
from datetime import datetime, timezone

from open_stock_data.data_provider import (
    StockType,
    RealtimeSource,
    UnifiedRealtimeQuote,
    detect_stock_type,
    normalize_stock_code,
    validate_stock_type,
)
from open_stock_data.data_provider.contracts import FetchResult
from open_stock_data.data_provider import akshare_fetcher as akshare_fetcher_module
from open_stock_data.data_provider.akshare_fetcher import AkshareFetcher
from open_stock_data.tools.a_stock import info as info_module
from open_stock_data.tools.a_stock import prices as prices_module
from open_stock_data.tools import us_stock as us_stock_module


def test_detect_stock_type_and_normalize_stock_code_support_common_formats():
    assert detect_stock_type("600519") == StockType.A_STOCK
    assert detect_stock_type("sh600519") == StockType.A_STOCK
    assert detect_stock_type("600519.SH") == StockType.A_STOCK
    assert normalize_stock_code("sh600519", "sh") == "600519"
    assert normalize_stock_code("600519.SH", "sh") == "600519"

    assert detect_stock_type("159001") == StockType.ETF
    assert detect_stock_type("sz159001") == StockType.ETF
    assert normalize_stock_code("sz159001", "sz") == "159001"

    assert detect_stock_type("01810.HK") == StockType.HK
    assert detect_stock_type("1810.hk") == StockType.HK
    assert detect_stock_type("HK01810") == StockType.HK
    assert normalize_stock_code("01810.HK", "hk") == "01810"
    assert normalize_stock_code("HK01810", "hk") == "01810"
    assert validate_stock_type("01810.HK", "hk") == (StockType.HK, "hk")

    assert detect_stock_type("AAPL") == StockType.US
    assert detect_stock_type("brk.b") == StockType.US
    assert normalize_stock_code("brk.b", "us") == "BRK.B"


class _DummyClient:
    def __init__(self):
        self.calls = []

    def realtime_quote(self, symbol, market=None):
        normalized = normalize_stock_code(symbol, market)
        self.calls.append((normalized, market))
        return FetchResult(
            data=UnifiedRealtimeQuote(
                code=normalized,
                name="Test",
                source=RealtimeSource.AKSHARE_EM,
                price=10.0,
                change_pct=1.2,
            ),
            source="AkshareFetcher",
            fetched_at=datetime.now(timezone.utc),
        )


def test_stock_realtime_normalizes_hk_suffix_input(monkeypatch):
    client = _DummyClient()
    monkeypatch.setattr(prices_module, "get_default_client", lambda: client)

    result = prices_module.stock_realtime(symbol="01810.HK", market="hk")

    assert client.calls == [("01810", "hk")]
    assert "01810" in result
    assert "01810.HK.hk" not in result


def test_stock_prices_normalizes_hk_suffix_input(monkeypatch):
    captured = {}

    class DummyPriceClient:
        def daily_prices(self, symbol, market, *, days, period):
            normalized = normalize_stock_code(symbol, market)
            captured.update(symbol=normalized, market=market, period=period)
            return FetchResult(
                data=pd.DataFrame(
                    [
                        {"date": "2026-03-26", "close": 10.0, "low": 9.5, "high": 10.5},
                        {"date": "2026-03-27", "close": 10.2, "low": 9.7, "high": 10.6},
                    ]
                ),
                source="YfinanceFetcher",
                fetched_at=datetime.now(timezone.utc),
            )

    monkeypatch.setattr(prices_module, "get_default_client", lambda: DummyPriceClient())
    monkeypatch.setattr(prices_module, "add_technical_indicators", lambda *args, **kwargs: None)
    monkeypatch.setattr(prices_module, "STOCK_PRICE_COLUMNS", ["日期", "收盘", "最低", "最高"])

    result = prices_module.stock_prices(symbol="01810.HK", market="hk", period="daily", limit=1)

    assert captured["symbol"] == "01810"
    assert captured["market"] == "hk"
    assert "01810.HK.hk" not in result


def test_stock_indicators_normalizes_hk_suffix_input(monkeypatch):
    captured = {}

    def fake_ak_cache(func, *args, **kwargs):
        captured["symbol"] = kwargs["symbol"]
        return pd.DataFrame([{"报告期": "2025Q4", "ROE": 12.3}])

    monkeypatch.setattr(info_module, "get_data_manager", lambda: type("Manager", (), {"fetch_akshare": staticmethod(fake_ak_cache)})())

    result = info_module.stock_indicators(symbol="01810.HK", market="hk")

    assert captured["symbol"] == "01810"
    assert "获取港股指标失败" not in result


def test_akshare_fetcher_hk_realtime_accepts_hk_suffix(monkeypatch):
    monkeypatch.setattr(
        akshare_fetcher_module.ak,
        "stock_hk_spot_em",
        lambda: pd.DataFrame(
            [
                {
                    "代码": "1810",
                    "名称": "小米集团-W",
                    "最新价": 50.0,
                    "涨跌幅": 1.5,
                    "成交量": 1000,
                    "成交额": 50000,
                    "今开": 49.0,
                    "最高": 51.0,
                    "最低": 48.5,
                    "昨收": 49.3,
                }
            ]
        ),
    )

    fetcher = AkshareFetcher()
    quote = fetcher._get_hk_realtime_quote("01810.HK")

    assert quote is not None
    assert quote.code == "01810.HK"
    assert quote.name == "小米集团-W"


def test_stock_info_normalizes_hk_suffix_input(monkeypatch):
    captured = {}

    def fake_ak_cache(func, *args, **kwargs):
        captured["symbol"] = kwargs.get("symbol")
        return pd.DataFrame([{"item": "总市值", "value": "1000亿"}])

    monkeypatch.setattr(info_module, "get_data_manager", lambda: type("Manager", (), {"fetch_akshare": staticmethod(fake_ak_cache)})())

    result = info_module.stock_info(symbol="01810.HK", market="hk")

    assert captured["symbol"] == "01810"
    assert "Not Found" not in result


def test_akshare_realtime_fallback_prefers_tencent_over_sina(monkeypatch):
    fetcher = AkshareFetcher()
    calls = []

    monkeypatch.setattr(fetcher, "_get_stock_realtime_quote_em", lambda code: calls.append("em") or None)
    monkeypatch.setattr(
        fetcher,
        "_get_stock_realtime_quote_tencent",
        lambda code: calls.append("tencent") or UnifiedRealtimeQuote(
            code=code,
            name="Test",
            source=RealtimeSource.TENCENT,
            price=10.0,
            change_pct=1.2,
        ),
    )
    monkeypatch.setattr(
        fetcher,
        "_get_stock_realtime_quote_sina",
        lambda code: calls.append("sina") or UnifiedRealtimeQuote(
            code=code,
            name="Test",
            source=RealtimeSource.AKSHARE_SINA,
            price=10.0,
            change_pct=1.2,
        ),
    )

    quote = fetcher.get_realtime_quote("600519")

    assert quote is not None
    assert quote.source == RealtimeSource.TENCENT
    assert calls == ["em", "tencent"]


def test_akshare_daily_fallback_prefers_tencent_over_sina(monkeypatch):
    fetcher = AkshareFetcher()
    calls = []

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
