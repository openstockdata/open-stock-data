import requests

from open_stock_data import eastmoney_patch as patch_module


class _DummyResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload or {"ok": True}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"status={self.status_code}")

    def json(self):
        return self._payload


def setup_function():
    patch_module.disable_eastmoney_patch()
    patch_module._auth_cache.data = None
    patch_module._auth_cache.expire_at = 0
    patch_module._push2_throttle.next_allowed_at = 0.0


def teardown_function():
    patch_module.disable_eastmoney_patch()
    patch_module._auth_cache.data = None
    patch_module._auth_cache.expire_at = 0
    patch_module._push2_throttle.next_allowed_at = 0.0


def test_patch_only_targets_eastmoney_domains(monkeypatch):
    calls = []

    def fake_original(self, method, url, **kwargs):
        calls.append((method, url, kwargs.get("headers", {})))
        return _DummyResponse()

    monkeypatch.setattr(patch_module, "_ORIGINAL_SESSION_REQUEST", fake_original)
    monkeypatch.setattr(patch_module, "_fetch_nid", lambda user_agent: "nid-token")
    monkeypatch.setattr(patch_module, "_random_user_agent", lambda: "UA-1")
    monkeypatch.setattr(patch_module.random, "uniform", lambda a, b: 0)
    monkeypatch.setattr(patch_module.time, "sleep", lambda *args, **kwargs: None)

    patch_module.enable_eastmoney_patch()
    session = requests.Session()
    session.get("https://api.github.com", headers={"User-Agent": "plain-ua"})
    session.get("https://push2.eastmoney.com/api/qt/stock/get", headers={"Cookie": "foo=bar"})

    assert calls[0][1] == "https://api.github.com"
    assert calls[0][2]["User-Agent"] == "plain-ua"
    assert calls[1][1] == "https://push2.eastmoney.com/api/qt/stock/get"
    assert calls[1][2]["User-Agent"] == "UA-1"
    assert calls[1][2]["Cookie"] == "foo=bar; nid18=nid-token"


def test_fetch_nid_uses_cache(monkeypatch):
    calls = []

    def fake_original(self, method, url, **kwargs):
        calls.append((method, url))
        return _DummyResponse(payload={"data": {"nid": "cached-nid"}})

    monkeypatch.setattr(patch_module, "_ORIGINAL_SESSION_REQUEST", fake_original)
    nid1 = patch_module._fetch_nid("UA-1")
    nid2 = patch_module._fetch_nid("UA-2")

    assert nid1 == "cached-nid"
    assert nid2 == "cached-nid"
    assert len(calls) == 1


def test_disable_restores_original_request(monkeypatch):
    original = requests.Session.request
    monkeypatch.setattr(patch_module, "_fetch_nid", lambda user_agent: None)
    monkeypatch.setattr(patch_module, "_random_user_agent", lambda: "UA-1")
    monkeypatch.setattr(patch_module.random, "uniform", lambda a, b: 0)
    monkeypatch.setattr(patch_module.time, "sleep", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        patch_module,
        "_ORIGINAL_SESSION_REQUEST",
        lambda self, method, url, **kwargs: _DummyResponse(),
    )

    patch_module.enable_eastmoney_patch()
    assert requests.Session.request is not original
    patch_module.disable_eastmoney_patch()
    assert requests.Session.request is patch_module._ORIGINAL_SESSION_REQUEST


def test_patch_preserves_existing_user_agent(monkeypatch):
    calls = []
    used_agents = []

    def fake_original(self, method, url, **kwargs):
        calls.append((method, url, kwargs.get("headers", {})))
        return _DummyResponse()

    monkeypatch.setattr(patch_module, "_ORIGINAL_SESSION_REQUEST", fake_original)
    monkeypatch.setattr(
        patch_module,
        "_fetch_nid",
        lambda user_agent: used_agents.append(user_agent) or "nid-token",
    )
    monkeypatch.setattr(patch_module.random, "uniform", lambda a, b: 0)
    monkeypatch.setattr(patch_module.time, "sleep", lambda *args, **kwargs: None)

    patch_module.enable_eastmoney_patch()
    session = requests.Session()
    session.get(
        "https://push2.eastmoney.com/api/qt/stock/get",
        headers={"User-Agent": "fixed-ua", "Cookie": "foo=bar"},
    )

    assert used_agents == ["fixed-ua"]
    assert calls[0][2]["User-Agent"] == "fixed-ua"
    assert calls[0][2]["Cookie"] == "foo=bar; nid18=nid-token"


def test_push2_request_retries_on_connection_error(monkeypatch):
    calls = []

    def flaky_original(self, method, url, **kwargs):
        calls.append((method, url, kwargs.get("headers", {})))
        if len(calls) == 1:
            raise requests.ConnectionError("remote closed")
        return _DummyResponse()

    monkeypatch.setattr(patch_module, "_ORIGINAL_SESSION_REQUEST", flaky_original)
    monkeypatch.setattr(patch_module, "_fetch_nid", lambda user_agent: None)
    monkeypatch.setattr(patch_module, "_random_user_agent", lambda: "UA-1")
    monkeypatch.setattr(
        patch_module,
        "_get_request_policy",
        lambda url: patch_module._RequestPolicy(
            sleep_range=(0.0, 0.0),
            max_retries=1,
            retry_backoff_seconds=0.0,
        ),
    )
    monkeypatch.setattr(patch_module.time, "sleep", lambda *args, **kwargs: None)

    patch_module.enable_eastmoney_patch()
    session = requests.Session()
    response = session.get("https://push2.eastmoney.com/api/qt/stock/get")

    assert response.status_code == 200
    assert len(calls) == 2


def test_push2_subdomain_uses_same_retry_policy(monkeypatch):
    calls = []

    def flaky_original(self, method, url, **kwargs):
        calls.append((method, url))
        if len(calls) == 1:
            raise requests.ConnectionError("remote closed")
        return _DummyResponse()

    monkeypatch.setattr(patch_module, "_ORIGINAL_SESSION_REQUEST", flaky_original)
    monkeypatch.setattr(patch_module, "_fetch_nid", lambda user_agent: None)
    monkeypatch.setattr(patch_module, "_random_user_agent", lambda: "UA-1")
    monkeypatch.setattr(
        patch_module,
        "_get_request_policy",
        lambda url: patch_module._RequestPolicy(
            sleep_range=(0.0, 0.0),
            max_retries=1,
            retry_backoff_seconds=0.0,
        ),
    )
    monkeypatch.setattr(patch_module.time, "sleep", lambda *args, **kwargs: None)

    patch_module.enable_eastmoney_patch()
    session = requests.Session()
    response = session.get("https://82.push2.eastmoney.com/api/qt/stock/get")

    assert response.status_code == 200
    assert len(calls) == 2

