import requests

from open_stock_data.data_provider import akshare_fetcher as module
from open_stock_data.data_provider.akshare_fetcher import AkshareFetcher


class DummyResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


def page_payload(page_no, total=6):
    rows = []
    for index in range(2):
        code = f"{page_no * 1000 + index:06d}"
        rows.append(
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
    return {"total": total, "diff": rows}


class DummySession:
    """替身 Session：验证 _fetch_em_spot_page 走 _get_push2_session() 的连接复用通道。"""

    def __init__(self, getter):
        self._getter = getter
        self.calls = []

    def get(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return self._getter(*args, **kwargs)


def test_fetch_spot_page_retries_network_error(monkeypatch):
    fetcher = AkshareFetcher()
    calls = 0

    def fake_get(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise requests.ConnectionError("remote closed")
        return DummyResponse({"data": page_payload(1)})

    session = DummySession(fake_get)
    # 替换类方法 _get_push2_session 返回替身 session
    monkeypatch.setattr(AkshareFetcher, "_get_push2_session", classmethod(lambda cls: session))
    monkeypatch.setattr(module.time, "sleep", lambda *_args: None)

    result = fetcher._fetch_em_spot_page(1, 100)

    assert result == page_payload(1)
    assert calls == 2
    # 不再逐页轮换 UA：请求头由共享 Session/补丁统一提供
    assert all("headers" not in kwargs for _args, kwargs in session.calls)


def test_fetch_spot_page_uses_shared_keepalive_session(monkeypatch):
    fetcher = AkshareFetcher()

    def fake_get(_url, params=None, **_kwargs):
        return DummyResponse({"data": page_payload(int(params["pn"]))})

    holder = DummySession(fake_get)
    monkeypatch.setattr(AkshareFetcher, "_get_push2_session", classmethod(lambda cls: holder))
    monkeypatch.setattr(module.time, "sleep", lambda *_args: None)

    assert fetcher._fetch_em_spot_page(1, 100) == page_payload(1)
    assert fetcher._fetch_em_spot_page(2, 100) == page_payload(2)
    assert len(holder.calls) == 2


def test_a_stock_snapshot_retries_missing_pages(monkeypatch):
    fetcher = AkshareFetcher()
    fetcher._EM_SPOT_PAGE_SIZE = 2
    calls = {2: 0}

    def fake_page(page, _page_size, **_kwargs):
        if page == 2:
            calls[2] += 1
            if calls[2] == 1:
                return None
        return page_payload(page)

    monkeypatch.setattr(fetcher, "_fetch_em_spot_page", fake_page)
    monkeypatch.setattr(fetcher, "random_sleep", lambda *_args: None)
    monkeypatch.setattr(module.time, "sleep", lambda *_args: None)

    result = fetcher.get_a_stock_spot()

    assert result is not None
    assert len(result) == 6
    assert result.attrs["spot_complete"] is True
    assert calls[2] == 2


def test_a_stock_snapshot_marks_partial_result(monkeypatch):
    fetcher = AkshareFetcher()
    fetcher._EM_SPOT_PAGE_SIZE = 2

    def fake_page(page, _page_size, **_kwargs):
        if page == 3:
            return None
        return page_payload(page)

    monkeypatch.setattr(fetcher, "_fetch_em_spot_page", fake_page)
    monkeypatch.setattr(fetcher, "random_sleep", lambda *_args: None)
    monkeypatch.setattr(module.time, "sleep", lambda *_args: None)

    result = fetcher.get_a_stock_spot()

    assert result is not None
    assert len(result) == 4
    assert result.attrs["spot_complete"] is False