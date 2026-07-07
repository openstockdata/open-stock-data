import requests

from open_stock_data.data_provider.akshare_fetcher import AkshareFetcher


class _DummyResponse:
    def __init__(self, payload):
        self.status_code = 200
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def _page_payload(page_no, total=6):
    base = page_no * 1000
    diff = []
    for index in range(2):
        code = f"{base + index:06d}"
        diff.append(
            {
                "f2": 10 + index,
                "f3": 1.5 - index,
                "f4": 0.1,
                "f5": 1000,
                "f6": 10000,
                "f7": 2.5,
                "f8": 3.5,
                "f9": 12.0,
                "f10": 1.1,
                "f12": code,
                "f14": f"股票{code}",
                "f15": 11.0,
                "f16": 9.0,
                "f17": 10.0,
                "f18": 9.8,
                "f20": 1000000,
                "f21": 800000,
                "f23": 1.8,
                "f24": 5.2,
                "f25": 7.6,
                "f22": 0.2,
                "f11": 0.0,
                "f62": 5000,
                "f128": "",
                "f136": "",
                "f115": 20.0,
                "f152": "",
            }
        )
    return {"data": {"total": total, "diff": diff}}


def test_fetch_a_stock_spot_em_paginated_retries_missing_pages(monkeypatch):
    fetcher = AkshareFetcher()
    state = {"page2_calls": 0}

    def fake_get(self, url, params=None, timeout=None, **kwargs):
        page_no = int(params["pn"])
        if page_no == 2:
            state["page2_calls"] += 1
            if state["page2_calls"] == 1:
                raise requests.ConnectionError("remote closed")
        return _DummyResponse(_page_payload(page_no))

    monkeypatch.setattr(requests.Session, "get", fake_get)
    monkeypatch.setattr("open_stock_data.data_provider.akshare_fetcher.time.sleep", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(fetcher, "random_sleep", lambda *_args, **_kwargs: None)

    result = fetcher._fetch_a_stock_spot_em_paginated()

    assert result is not None
    assert result.attrs["spot_cache_source"] == "akshare_em"
    assert len(result) == 6
    assert state["page2_calls"] == 2


def test_fetch_a_stock_spot_em_paginated_returns_partial_when_pages_remain_missing(monkeypatch):
    fetcher = AkshareFetcher()

    def fake_get(self, url, params=None, timeout=None, **kwargs):
        page_no = int(params["pn"])
        if page_no == 3:
            raise requests.Timeout("timed out")
        return _DummyResponse(_page_payload(page_no))

    monkeypatch.setattr(requests.Session, "get", fake_get)
    monkeypatch.setattr("open_stock_data.data_provider.akshare_fetcher.time.sleep", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(fetcher, "random_sleep", lambda *_args, **_kwargs: None)

    result = fetcher._fetch_a_stock_spot_em_paginated()

    assert result is not None
    assert result.attrs["spot_cache_source"] == "akshare_em_partial"
    assert result.attrs["spot_partial_pages"] == [3]
    assert len(result) == 4


def test_a_stock_spot_page_schedule_slows_down_for_tail_pages():
    fetcher = AkshareFetcher()

    early = fetcher._get_a_stock_spot_page_schedule(page_no=10, total_page=59)
    middle = fetcher._get_a_stock_spot_page_schedule(page_no=25, total_page=59)
    late = fetcher._get_a_stock_spot_page_schedule(page_no=41, total_page=59)
    tail = fetcher._get_a_stock_spot_page_schedule(page_no=55, total_page=59)

    assert early.chunk_size == 10
    assert middle.chunk_size == 6
    assert middle.refresh_session is True
    assert late.chunk_size == 2
    assert tail.chunk_size == 1
    assert tail.refresh_session is True
    assert late.refresh_session is True
    assert tail.inter_page_sleep[0] > late.inter_page_sleep[0] > middle.inter_page_sleep[0] > early.inter_page_sleep[0]


def test_fetch_a_stock_spot_em_paginated_rotates_session_for_late_pages(monkeypatch):
    fetcher = AkshareFetcher()
    created_sessions = []

    class DummySession:
        def __init__(self):
            self.closed = False
            created_sessions.append(self)

        def get(self, url, params=None, timeout=None, **kwargs):
            return _DummyResponse(_page_payload(int(params["pn"]), total=320))

        def close(self):
            self.closed = True

    monkeypatch.setattr("open_stock_data.data_provider.akshare_fetcher.requests.Session", DummySession)
    monkeypatch.setattr("open_stock_data.data_provider.akshare_fetcher.time.sleep", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(fetcher, "random_sleep", lambda *_args, **_kwargs: None)

    result = fetcher._fetch_a_stock_spot_em_paginated()

    assert result is not None
    assert result.attrs["spot_cache_source"] == "akshare_em"
    assert len(created_sessions) >= 2
    assert any(session.closed for session in created_sessions[:-1])


def test_fetch_eastmoney_spot_page_rotates_host_on_retry(monkeypatch):
    fetcher = AkshareFetcher()
    calls = []

    class DummySession:
        def get(self, url, params=None, timeout=None, headers=None, **kwargs):
            calls.append((url, headers))
            if len(calls) == 1:
                raise requests.ConnectionError("remote closed")
            return _DummyResponse(_page_payload(41, total=5900))

    monkeypatch.setattr(fetcher, "_get_eastmoney_spot_hosts", lambda: ("82.push2.eastmoney.com", "push2.eastmoney.com"))
    monkeypatch.setattr("open_stock_data.data_provider.akshare_fetcher.time.sleep", lambda *_args, **_kwargs: None)

    page_df, total = fetcher._fetch_eastmoney_spot_page(DummySession(), 41)

    assert not page_df.empty
    assert total == 5900
    assert calls[0][0].startswith("https://82.push2.eastmoney.com/")
    assert calls[1][0].startswith("https://push2.eastmoney.com/")
    assert calls[0][1]["X-Open-Stock-No-Inner-Retry"] == "1"
