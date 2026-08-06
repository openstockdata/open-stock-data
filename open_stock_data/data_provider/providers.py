"""Construction of the default provider registry."""

from __future__ import annotations

import logging

from .base import BaseFetcher


_LOGGER = logging.getLogger(__name__)


def create_default_providers() -> dict[str, BaseFetcher]:
    from .akshare_fetcher import AkshareFetcher
    from .alphavantage_fetcher import AlphaVantageFetcher
    from .baostock_fetcher import BaostockFetcher
    from .efinance_fetcher import EfinanceFetcher
    from .pytdx_fetcher import PytdxFetcher
    from .tickflow_fetcher import TickflowFetcher
    from .tushare_fetcher import TushareFetcher
    from .yfinance_fetcher import YfinanceFetcher

    provider_types = (
        TickflowFetcher,
        EfinanceFetcher,
        AkshareFetcher,
        TushareFetcher,
        PytdxFetcher,
        BaostockFetcher,
        YfinanceFetcher,
        AlphaVantageFetcher,
    )
    providers: dict[str, BaseFetcher] = {}
    for provider_type in provider_types:
        try:
            provider = provider_type()
        except Exception as exc:
            _LOGGER.warning("%s 初始化失败: %s", provider_type.__name__, exc)
            continue
        providers[provider.name] = provider

    # 本地长期存储读取器：排在各事实路由 providers 首位，命中即免网络
    try:
        from .local_store import LocalStoreFetcher
        local = LocalStoreFetcher()
        providers[local.name] = local
    except Exception as exc:
        _LOGGER.warning("LocalStoreFetcher 初始化失败: %s", exc)

    return providers
