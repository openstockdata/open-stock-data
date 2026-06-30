import atexit
import logging
import os
import pathlib
import sys
import threading
from typing import Any, Optional

import diskcache

_LOGGER = logging.getLogger(__name__)


def _env_ttl(key: str, default: float) -> float:
    return float(os.getenv(key, str(default)))


CACHE_TTLS = {
    # DataFetcherManager — 静态 TTL
    "daily": _env_ttl("CACHE_TTL_DAILY", 86400),
    "belong_board": _env_ttl("CACHE_TTL_BELONG_BOARD", 86400 * 7),
    "us_report": _env_ttl("CACHE_TTL_US_REPORT", 86400 * 7),
    "us_overview": _env_ttl("CACHE_TTL_US_OVERVIEW", 86400),
    "us_earnings": _env_ttl("CACHE_TTL_US_EARNINGS", 86400),
    "us_insider": _env_ttl("CACHE_TTL_US_INSIDER", 43200),
    # DataFetcherManager — 动态 TTL（交易时段 / 非交易时段）
    "realtime_trading": _env_ttl("CACHE_TTL_REALTIME_TRADING", 10),
    "realtime_pre_post": _env_ttl("CACHE_TTL_REALTIME_PRE_POST", 60),
    "realtime_closed": _env_ttl("CACHE_TTL_REALTIME_CLOSED", 30000),
    "spot_trading": _env_ttl("CACHE_TTL_SPOT_TRADING", 3000),
    "spot_closed": _env_ttl("CACHE_TTL_SPOT_CLOSED", 86400),
    "fund_flow_trading": _env_ttl("CACHE_TTL_FUND_FLOW_TRADING", 600),
    "fund_flow_closed": _env_ttl("CACHE_TTL_FUND_FLOW_CLOSED", 3600),
    # Tushare 板块缓存
    "tushare_board": _env_ttl("CACHE_TTL_TUSHARE_BOARD", 86400 * 7),
    "tushare_board_snapshot": _env_ttl("CACHE_TTL_TUSHARE_SNAPSHOT", 86400 * 180),
    # fetch_akshare 默认
    "akshare_default": _env_ttl("CACHE_TTL_AKSHARE_DEFAULT", 86400),
}


class _NullDiskCache:
    def get(self, _key, default=None):
        return default

    def set(self, _key, _val, **_kwargs):
        return True

    def delete(self, _key):
        return True

    def close(self):
        return None


class CacheStore:
    """纯磁盘缓存后端。"""

    _stores: dict[str, "CacheStore"] = {}
    _stores_lock = threading.Lock()

    def __init__(self, namespace: str, disk_subdir: Optional[str] = None):
        self.namespace = namespace
        self._disk_cache = self._init_disk_cache(disk_subdir or namespace)

    @classmethod
    def get_store(cls, namespace: str = "default", disk_subdir: Optional[str] = None) -> "CacheStore":
        with cls._stores_lock:
            if namespace in cls._stores:
                return cls._stores[namespace]
            store = cls(namespace=namespace, disk_subdir=disk_subdir)
            cls._stores[namespace] = store
            return store

    @staticmethod
    def get_cache_dir() -> pathlib.Path:
        home = pathlib.Path.home()
        name = __package__
        if sys.platform == "win32":
            return home / "AppData" / "Local" / "Cache" / name
        return home / ".cache" / name

    def _init_disk_cache(self, subdir: str):
        cache_dirs = [
            self.get_cache_dir() / subdir,
            pathlib.Path("/tmp") / "open_stock_data" / subdir,
        ]
        for cache_dir in cache_dirs:
            try:
                cache_dir.mkdir(parents=True, exist_ok=True)
                cache = diskcache.Cache(cache_dir)
                atexit.register(cache.close)
                _LOGGER.debug("[cache] init ok: namespace=%s dir=%s", self.namespace, cache_dir)
                return cache
            except Exception as exc:
                _LOGGER.debug("[cache] init fail: namespace=%s dir=%s err=%s", self.namespace, cache_dir, exc)
        _LOGGER.warning("[cache] all init failed, fallback to null: namespace=%s", self.namespace)
        return _NullDiskCache()

    def get(self, key: str, default=None):
        try:
            return self._disk_cache.get(key, default=default)
        except Exception as exc:
            _LOGGER.debug("[cache] get fail: namespace=%s key=%s err=%s", self.namespace, key, exc)
            return default

    def set(self, key: str, value: Any, expire: Optional[float] = None):
        try:
            self._disk_cache.set(key, value, expire=expire)
        except Exception as exc:
            _LOGGER.debug("[cache] set fail: namespace=%s key=%s err=%s", self.namespace, key, exc)

    def delete(self, key: str):
        try:
            self._disk_cache.delete(key)
        except Exception as exc:
            _LOGGER.debug("[cache] delete fail: namespace=%s key=%s err=%s", self.namespace, key, exc)

    def close(self):
        try:
            self._disk_cache.close()
        except Exception as exc:
            _LOGGER.debug("[cache] close fail: namespace=%s err=%s", self.namespace, exc)

    @classmethod
    def clear_all(cls):
        """清除所有 store 的磁盘缓存（用于测试）。"""
        with cls._stores_lock:
            for store in cls._stores.values():
                try:
                    store._disk_cache.clear()
                except Exception:
                    pass
