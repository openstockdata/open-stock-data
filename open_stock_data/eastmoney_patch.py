"""东方财富限流补丁。"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import random
import secrets
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urlparse

import requests

_LOGGER = logging.getLogger(__name__)
_ORIGINAL_SESSION_REQUEST = requests.Session.request
_AUTH_URL = "https://anonflow2.eastmoney.com/backend/api/webreport"
_EASTMONEY_HOST_KEYWORD = "eastmoney.com"
_PUSH2_HOST = "push2.eastmoney.com"
_NID_COOKIE_KEY = "nid18"
_DEFAULT_TTL_SECONDS = 20
_DEFAULT_BACKOFF_SECONDS = 5 * 60
_DEFAULT_SLEEP_RANGE = (1.0, 4.0)
_DEFAULT_PUSH2_SLEEP_RANGE = (0.0, 0.2)
_DEFAULT_PUSH2_MAX_CONCURRENCY = 3
_DEFAULT_PUSH2_MIN_INTERVAL_SECONDS = 0.35
_DEFAULT_PUSH2_MAX_RETRIES = 2
_DEFAULT_PUSH2_RETRY_BACKOFF_SECONDS = 0.8
_SESSION_UA_ATTR = "_open_stock_data_eastmoney_user_agent"
_FALLBACK_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
]

try:
    from fake_useragent import UserAgent  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    UserAgent = None


@dataclass
class _AuthCache:
    data: Optional[str] = None
    expire_at: float = 0.0
    ttl: int = _DEFAULT_TTL_SECONDS
    lock: threading.Lock = field(default_factory=threading.Lock)


@dataclass
class _HostThrottle:
    max_concurrency: int
    min_interval_seconds: float
    semaphore: threading.BoundedSemaphore = field(init=False)
    lock: threading.Lock = field(default_factory=threading.Lock)
    next_allowed_at: float = 0.0

    def __post_init__(self) -> None:
        self.semaphore = threading.BoundedSemaphore(max(1, self.max_concurrency))

    def acquire(self) -> None:
        self.semaphore.acquire()
        if self.min_interval_seconds <= 0:
            return

        while True:
            with self.lock:
                now = time.monotonic()
                wait_seconds = self.next_allowed_at - now
                if wait_seconds <= 0:
                    self.next_allowed_at = now + self.min_interval_seconds
                    return
            time.sleep(wait_seconds)

    def release(self) -> None:
        self.semaphore.release()


@dataclass(frozen=True)
class _RequestPolicy:
    sleep_range: tuple[float, float]
    max_retries: int = 0
    retry_backoff_seconds: float = 0.0
    throttle: Optional[_HostThrottle] = None


_auth_cache = _AuthCache()
_patch_lock = threading.Lock()
_is_patched = False
_user_agent_provider = UserAgent() if UserAgent is not None else None
_push2_throttle = _HostThrottle(
    max_concurrency=max(
        1,
        int(os.getenv("EASTMONEY_PUSH2_MAX_CONCURRENCY", _DEFAULT_PUSH2_MAX_CONCURRENCY)),
    ),
    min_interval_seconds=max(
        0.0,
        float(
            os.getenv(
                "EASTMONEY_PUSH2_MIN_INTERVAL_SECONDS",
                _DEFAULT_PUSH2_MIN_INTERVAL_SECONDS,
            )
        ),
    ),
)


def _normalize_sleep_range(sleep_range: tuple[float, float]) -> tuple[float, float]:
    min_sleep, max_sleep = sleep_range
    min_sleep = max(0.0, min_sleep)
    max_sleep = max(0.0, max_sleep)
    if max_sleep < min_sleep:
        max_sleep = min_sleep
    return min_sleep, max_sleep


def _get_request_policy(url: str) -> _RequestPolicy:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if host == _PUSH2_HOST:
        return _RequestPolicy(
            sleep_range=_DEFAULT_PUSH2_SLEEP_RANGE,
            max_retries=max(
                0,
                int(os.getenv("EASTMONEY_PUSH2_MAX_RETRIES", _DEFAULT_PUSH2_MAX_RETRIES)),
            ),
            retry_backoff_seconds=max(
                0.0,
                float(
                    os.getenv(
                        "EASTMONEY_PUSH2_RETRY_BACKOFF_SECONDS",
                        _DEFAULT_PUSH2_RETRY_BACKOFF_SECONDS,
                    )
                ),
            ),
            throttle=_push2_throttle,
        )
    return _RequestPolicy(sleep_range=_DEFAULT_SLEEP_RANGE)


def _random_user_agent() -> str:
    if _user_agent_provider is not None:
        try:
            candidate = _user_agent_provider.random
            if candidate:
                return candidate
        except Exception as exc:  # pragma: no cover - optional dependency failure
            _LOGGER.debug("fake_useragent 获取失败，回退内置 UA: %s", exc)
    return random.choice(_FALLBACK_USER_AGENTS)


def _generate_uuid_md5() -> str:
    return hashlib.md5(str(uuid.uuid4()).encode("utf-8")).hexdigest()


def _generate_st_nvi() -> str:
    hash_length = 4
    charset = "useandom-26T198340PX75pxJACKVERYMINDBUSHWOLF_GQZbfghjklqvwyzrict"
    random_str = "".join(secrets.choice(charset) for _ in range(21))
    hash_prefix = hashlib.sha256(random_str.encode("utf-8")).hexdigest()[:hash_length]
    return random_str + hash_prefix


def _merge_cookie(existing_cookie: str, key: str, value: str) -> str:
    cookie_map: dict[str, str] = {}
    for chunk in existing_cookie.split(";") if existing_cookie else []:
        part = chunk.strip()
        if not part or "=" not in part:
            continue
        cookie_key, cookie_value = part.split("=", 1)
        cookie_map[cookie_key.strip()] = cookie_value.strip()
    cookie_map[key] = value
    return "; ".join(f"{cookie_key}={cookie_value}" for cookie_key, cookie_value in cookie_map.items())


def _is_target_url(url: str) -> bool:
    parsed = urlparse(url)
    host = (parsed.netloc or "").lower()
    if not host:
        return False
    if _EASTMONEY_HOST_KEYWORD not in host:
        return False
    return _AUTH_URL not in url


def _resolve_user_agent(session: requests.Session, headers: dict[str, str]) -> str:
    existing = headers.get("User-Agent")
    if existing:
        return existing

    session_user_agent = getattr(session, _SESSION_UA_ATTR, None)
    if session_user_agent:
        return session_user_agent

    session_user_agent = _random_user_agent()
    setattr(session, _SESSION_UA_ATTR, session_user_agent)
    return session_user_agent


def _sleep_for_request(policy: _RequestPolicy) -> None:
    min_sleep, max_sleep = _normalize_sleep_range(policy.sleep_range)
    if max_sleep <= 0:
        return
    time.sleep(random.uniform(min_sleep, max_sleep))


def _request_with_policy(
    session: requests.Session,
    method: str,
    url: str,
    kwargs: dict,
    policy: _RequestPolicy,
):
    attempts = 0
    method_upper = method.upper()

    while True:
        throttle = policy.throttle
        delay = 0.0
        if throttle is not None:
            throttle.acquire()
        try:
            _sleep_for_request(policy)
            return _ORIGINAL_SESSION_REQUEST(session, method, url, **kwargs)
        except requests.RequestException as exc:
            if method_upper not in {"GET", "HEAD", "OPTIONS"} or attempts >= policy.max_retries:
                raise

            attempts += 1
            delay = policy.retry_backoff_seconds * attempts
            _LOGGER.warning(
                "东方财富请求失败，准备重试: url=%s, attempt=%s/%s, error=%s",
                url,
                attempts,
                policy.max_retries,
                exc,
            )
        finally:
            if throttle is not None:
                throttle.release()

        if delay > 0:
            time.sleep(delay)


def _fetch_nid(user_agent: str) -> Optional[str]:
    now = time.time()
    if _auth_cache.data and now < _auth_cache.expire_at:
        return _auth_cache.data

    with _auth_cache.lock:
        now = time.time()
        if _auth_cache.data and now < _auth_cache.expire_at:
            return _auth_cache.data
        session = requests.Session()
        try:
            payload = {
                "osPlatform": "Windows",
                "sourceType": "WEB",
                "osversion": "Windows 10.0",
                "language": "zh-CN",
                "timezone": "Asia/Shanghai",
                "webDeviceInfo": {
                    "screenResolution": random.choice(["1920X1080", "2560X1440", "3840X2160"]),
                    "userAgent": user_agent,
                    "canvasKey": _generate_uuid_md5(),
                    "webglKey": _generate_uuid_md5(),
                    "fontKey": _generate_uuid_md5(),
                    "audioKey": _generate_uuid_md5(),
                },
            }
            headers = {
                "Cookie": f"st_nvi={_generate_st_nvi()}",
                "Content-Type": "application/json",
                "User-Agent": user_agent,
            }
            response = _ORIGINAL_SESSION_REQUEST(
                session,
                "POST",
                _AUTH_URL,
                headers=headers,
                data=json.dumps(payload),
                timeout=30,
            )
            response.raise_for_status()
            nid = (response.json() or {}).get("data", {}).get("nid")
            if nid:
                _auth_cache.data = nid
                _auth_cache.expire_at = now + _auth_cache.ttl
                return nid
            _LOGGER.warning("东方财富授权接口未返回 nid")
        except requests.RequestException as exc:
            _LOGGER.warning("请求东方财富授权接口失败: %s", exc)
        except (ValueError, KeyError, TypeError) as exc:
            _LOGGER.warning("解析东方财富授权接口响应失败: %s", exc)
        finally:
            session.close()

        _auth_cache.data = None
        _auth_cache.expire_at = now + _DEFAULT_BACKOFF_SECONDS
        return None


def enable_eastmoney_patch() -> bool:
    global _is_patched
    if _is_patched:
        return False

    with _patch_lock:
        if _is_patched:
            return False

        def patched_request(self, method, url, **kwargs):
            if not _is_target_url(url or ""):
                return _ORIGINAL_SESSION_REQUEST(self, method, url, **kwargs)

            headers = dict(kwargs.get("headers") or {})
            user_agent = _resolve_user_agent(self, headers)
            headers["User-Agent"] = user_agent

            nid = _fetch_nid(user_agent)
            if nid:
                headers["Cookie"] = _merge_cookie(headers.get("Cookie", ""), _NID_COOKIE_KEY, nid)

            kwargs["headers"] = headers
            policy = _get_request_policy(url)
            return _request_with_policy(self, method, url, kwargs, policy)

        requests.Session.request = patched_request
        _is_patched = True
        _LOGGER.info("ENABLE_EASTMONEY_PATCH 已启用")

        # 降低 efinance 全局 session 的 urllib3 retry（默认 5 次，叠加 patch 层重试会导致爆炸）
        try:
            from efinance.shared import session as ef_session
            low_retry_adapter = requests.adapters.HTTPAdapter(
                pool_connections=50, pool_maxsize=50, max_retries=1,
            )
            ef_session.mount("http://", low_retry_adapter)
            ef_session.mount("https://", low_retry_adapter)
            _LOGGER.debug("已降低 efinance session urllib3 retry 为 1")
        except Exception:
            pass

        return True


def disable_eastmoney_patch() -> bool:
    global _is_patched
    with _patch_lock:
        if not _is_patched:
            requests.Session.request = _ORIGINAL_SESSION_REQUEST
            return False
        requests.Session.request = _ORIGINAL_SESSION_REQUEST
        _is_patched = False
        return True


def eastmoney_patch_enabled() -> bool:
    return _is_patched
