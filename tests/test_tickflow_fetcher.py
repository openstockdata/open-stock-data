import os

import pandas as pd
import pytest
import requests

from open_stock_data.data_provider.tickflow_fetcher import TickflowFetcher
from open_stock_data.data_provider.types import RealtimeSource
from open_stock_data.exceptions import AuthenticationError, DataFetchError, RateLimitError


class DummyResponse:
    def __init__(self, status_code=200, payload=None, text="{}", headers=None, json_error=False):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.text = text
        self.headers = headers or {}
        self._json_error = json_error

    def json(self):
        if self._json_error:
            raise ValueError("bad json")
        return self._payload


@pytest.fixture(autouse=True)
def clear_tickflow_env(monkeypatch):
    monkeypatch.delenv("TICKFLOW_API_KEY", raising=False)
    monkeypatch.delenv("TICKFLOW_API_URL", raising=False)


def test_init_uses_free_api_without_key():
    fetcher = TickflowFetcher()

    assert fetcher.priority == 0
    assert fetcher._base_url == "https://free-api.tickflow.org"
    assert fetcher._headers() == {"Accept": "application/json"}


def test_init_uses_full_api_with_key(monkeypatch):
    monkeypatch.setenv("TICKFLOW_API_KEY", "test-key")

    fetcher = TickflowFetcher()

    assert fetcher._base_url == "https://api.tickflow.org"
    assert fetcher._headers()["x-api-key"] == "test-key"


def test_init_honors_custom_api_url(monkeypatch):
    monkeypatch.setenv("TICKFLOW_API_URL", "https://example.test/")

    fetcher = TickflowFetcher()

    assert fetcher._base_url == "https://example.test"


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("600000", "600000.SH"),
        ("000001", "000001.SZ"),
        ("430047", "430047.BJ"),
        ("09988", "09988.HK"),
        ("988", "00988.HK"),
        ("AAPL", "AAPL.US"),
        ("600000.SH", "600000.SH"),
    ],
)
def test_convert_stock_code(source, expected):
    assert TickflowFetcher()._convert_stock_code(source) == expected


def test_compact_to_frame():
    df = TickflowFetcher._compact_to_frame(
        {
            "timestamp": [1767139200000, 1767225600000],
            "open": [10.0, 10.5],
            "high": [11.0, 11.5],
            "low": [9.5, 10.0],
            "close": [10.8, 11.2],
            "volume": [1000, 2000],
            "amount": [10000.0, 22000.0],
        }
    )

    assert list(df.columns) == ["date", "open", "high", "low", "close", "volume", "amount"]
    assert df["date"].tolist() == ["2025-12-31", "2026-01-01"]
    assert df["close"].tolist() == [10.8, 11.2]


def test_fetch_klines_builds_daily_request(monkeypatch):
    captured = {}

    def fake_request(method, url, params=None, json=None, headers=None, timeout=None):
        captured.update(
            method=method,
            url=url,
            params=params,
            json=json,
            headers=headers,
            timeout=timeout,
        )
        return DummyResponse(
            payload={
                "data": {
                    "timestamp": [1767225600000],
                    "open": [10.0],
                    "high": [11.0],
                    "low": [9.0],
                    "close": [10.5],
                    "volume": [100],
                    "amount": [1000.0],
                }
            }
        )

    monkeypatch.setattr(requests, "request", fake_request)

    df = TickflowFetcher()._fetch_raw_daily_data("600000", "20260101", "20260102")

    assert isinstance(df, pd.DataFrame)
    assert captured["method"] == "GET"
    assert captured["url"] == "https://free-api.tickflow.org/v1/klines"
    assert captured["params"]["symbol"] == "600000.SH"
    assert captured["params"]["period"] == "1d"
    assert captured["params"]["adjust"] == "none"


def test_realtime_quote_skips_without_key(monkeypatch):
    def fail_request(*args, **kwargs):
        raise AssertionError("request should not be called")

    monkeypatch.setattr(requests, "request", fail_request)

    assert TickflowFetcher().get_realtime_quote("600000") is None


def test_batch_realtime_quote(monkeypatch):
    monkeypatch.setenv("TICKFLOW_API_KEY", "test-key")
    captured = {}

    def fake_request(method, url, params=None, json=None, headers=None, timeout=None):
        captured.update(method=method, url=url, json=json, headers=headers)
        return DummyResponse(
            payload={
                "data": [
                    {
                        "symbol": "600000.SH",
                        "last_price": 10.5,
                        "prev_close": 10.0,
                        "open": 10.1,
                        "high": 10.8,
                        "low": 9.9,
                        "volume": 1000,
                        "amount": 10500.0,
                        "ext": {"name": "浦发银行", "change_pct": 0.05, "turnover_rate": 0.01},
                    }
                ]
            }
        )

    monkeypatch.setattr(requests, "request", fake_request)

    result = TickflowFetcher().get_batch_realtime_quotes(["600000"])

    assert captured["method"] == "POST"
    assert captured["url"] == "https://api.tickflow.org/v1/quotes"
    assert captured["json"] == {"symbols": ["600000.SH"]}
    assert captured["headers"]["x-api-key"] == "test-key"
    assert result["600000"].source is RealtimeSource.TICKFLOW
    assert result["600000"].change_pct == 5.0
    assert result["600000"].turnover_rate == 1.0


def test_request_error_mapping(monkeypatch):
    fetcher = TickflowFetcher()

    monkeypatch.setattr(requests, "request", lambda *args, **kwargs: DummyResponse(429, text="limited", headers={"Retry-After": "12"}))
    with pytest.raises(RateLimitError) as rate_limit:
        fetcher._request("GET", "/v1/quotes")
    assert rate_limit.value.retry_after == 12

    monkeypatch.setattr(requests, "request", lambda *args, **kwargs: DummyResponse(401, text="unauthorized"))
    with pytest.raises(AuthenticationError):
        fetcher._request("GET", "/v1/quotes")

    monkeypatch.setattr(requests, "request", lambda *args, **kwargs: DummyResponse(500, text="server error"))
    with pytest.raises(DataFetchError):
        fetcher._request("GET", "/v1/quotes")

    monkeypatch.setattr(requests, "request", lambda *args, **kwargs: DummyResponse(200, text="not json", json_error=True))
    with pytest.raises(DataFetchError):
        fetcher._request("GET", "/v1/quotes")
