"""Construction of the default provider registry.

Providers register themselves into ProviderContext on creation.
"""

from __future__ import annotations

import logging

from .plugin import ProviderPlugin
from .context import ProviderContext

_LOGGER = logging.getLogger(__name__)


def create_default_providers(context: Optional[ProviderContext] = None) -> dict[str, ProviderPlugin]:
    """Initialize default providers and register them into ProviderContext.

    Returns the dict of provider_name → ProviderPlugin.
    """
    ctx = context or ProviderContext.default()

    from .tickflow_fetcher import TickflowFetcher
    from .efinance_fetcher import EfinanceFetcher
    from .akshare_fetcher import AkshareFetcher
    from .tushare_fetcher import TushareFetcher
    from .baostock_fetcher import BaostockFetcher
    from .pytdx_fetcher import PytdxFetcher
    from .yfinance_fetcher import YfinanceFetcher
    from .alphavantage_fetcher import AlphaVantageFetcher

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
    providers: dict[str, ProviderPlugin] = {}
    for provider_type in provider_types:
        try:
            provider = provider_type()
            if provider.is_available:
                ctx.register(provider)
                providers[provider.metadata.name] = provider
        except Exception as exc:
            _LOGGER.warning("%s 初始化失败: %s", provider_type.__name__, exc)

    # LocalStoreFetcher
    try:
        from .local_store import LocalStoreFetcher
        local = LocalStoreFetcher()
        if local.is_available:
            ctx.register(local)
            providers[local.metadata.name] = local
    except Exception as exc:
        _LOGGER.warning("LocalStoreFetcher 初始化失败: %s", exc)

    _LOGGER.info("已注册 %d 个数据源: %s", len(providers), list(providers.keys()))
    return providers
