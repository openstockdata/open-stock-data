"""
数据获取基类和管理器
"""

import logging
import os
import random
import time
import threading
from abc import ABC, abstractmethod
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any, TYPE_CHECKING, Callable

import pandas as pd
import numpy as np
import requests.exceptions

from ..cache import CACHE_TTLS, CacheStore

# 网络类异常：后端服务器不可达，应向上传播以触发同源跳过
NETWORK_EXCEPTIONS = (
    requests.exceptions.ConnectionError,
    requests.exceptions.Timeout,
    ConnectionError,
    TimeoutError,
)

from .types import (
    UnifiedRealtimeQuote,
    ChipDistribution,
)
from .columns import STANDARD_COLUMNS
from .stock_code import StockType
from .circuit_breaker import get_circuit_breaker
from .market_config import MarketHoursConfig, MarketType

# 从统一异常模块导入
from ..exceptions import (
    DataFetchError,
    RateLimitError,
    NetworkError,
    classify_exception,
    get_error_category,
)

if TYPE_CHECKING:
    from .tickflow_fetcher import TickflowFetcher
    from .efinance_fetcher import EfinanceFetcher
    from .akshare_fetcher import AkshareFetcher
    from .tushare_fetcher import TushareFetcher
    from .baostock_fetcher import BaostockFetcher
    from .pytdx_fetcher import PytdxFetcher
    from .yfinance_fetcher import YfinanceFetcher

_LOGGER = logging.getLogger(__name__)


def _priority_order(*fetcher_names: str) -> Dict[str, int]:
    """按声明顺序生成函数级数据源优先级。"""
    return {name: priority for priority, name in enumerate(fetcher_names)}


def _is_network_error(e: Exception) -> bool:
    """判断是否为网络连接错误（后端不可达）"""
    return isinstance(e, (*NETWORK_EXCEPTIONS, NetworkError))


class BaseFetcher(ABC):
    """数据获取器基类"""

    name: str = "BaseFetcher"
    priority: int = 99
    backend_group: str = ""  # 后端服务器分组，同组共享连接状态

    # User-Agent 池用于反爬
    USER_AGENTS = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 Safari/605.1.15",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
    ]

    def __init__(self):
        self._available = True

    @property
    def is_available(self) -> bool:
        """数据源是否可用"""
        return self._available

    def random_sleep(self, min_seconds: float = 1.0, max_seconds: float = 3.0):
        """随机延迟，用于反爬"""
        time.sleep(random.uniform(min_seconds, max_seconds))

    def get_random_user_agent(self) -> str:
        """获取随机 User-Agent"""
        return random.choice(self.USER_AGENTS)

    def get_backend_failure_scope(self, method_name: str, *args, **kwargs) -> Optional[str]:
        """返回后端失败作用域，默认按“供应商+方法”粒度隔离。"""
        if not self.backend_group:
            return None
        return f"{self.backend_group}:{method_name}"

    _TRADING_DAY_TO_CALENDAR_RATIO = 1.8
    _TRADING_DAY_BUFFER = 30

    @classmethod
    def _estimate_calendar_days(cls, trading_days: int) -> int:
        trading_days = max(int(trading_days), 1)
        return int(trading_days * cls._TRADING_DAY_TO_CALENDAR_RATIO) + cls._TRADING_DAY_BUFFER

    @abstractmethod
    def _fetch_raw_data(
        self,
        stock_code: str,
        start_date: str,
        end_date: str
    ) -> Optional[pd.DataFrame]:
        """获取原始数据（子类实现）"""
        pass

    @abstractmethod
    def _normalize_data(
        self,
        df: pd.DataFrame,
        stock_code: str
    ) -> pd.DataFrame:
        """标准化数据（子类实现）"""
        pass

    def get_daily_data(
        self,
        stock_code: str,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        days: int = 30
    ) -> Optional[pd.DataFrame]:
        """
        获取日线数据

        Args:
            stock_code: 股票代码
            start_date: 开始日期 (YYYYMMDD)
            end_date: 结束日期 (YYYYMMDD)
            days: 获取天数（当 start_date 未指定时使用）

        Returns:
            标准化的 DataFrame，列名为 STANDARD_COLUMNS
        """
        stock_code = str(stock_code).strip()

        if not end_date:
            end_date = datetime.now().strftime("%Y%m%d")
        if not start_date:
            start_date = (datetime.now() - timedelta(days=self._estimate_calendar_days(days))).strftime("%Y%m%d")

        try:
            df = self._fetch_raw_data(stock_code, start_date, end_date)
            if df is None or df.empty:
                return None

            df = self._normalize_data(df, stock_code)
            df = self._clean_data(df)
            df = self._calculate_indicators(df)

            return df

        except Exception as e:
            classified = classify_exception(e, source=self.name, code=stock_code)
            category = get_error_category(classified)
            _LOGGER.warning(f"[{self.name}] [{category}] 获取 {stock_code} 数据失败: {e}")
            raise classified

    def get_raw_daily_data(
        self,
        stock_code: str,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        days: int = 30
    ) -> Optional[pd.DataFrame]:
        """获取未复权日线数据，不计算技术指标。"""
        stock_code = str(stock_code).strip()

        if not end_date:
            end_date = datetime.now().strftime("%Y%m%d")
        if not start_date:
            start_date = (datetime.now() - timedelta(days=self._estimate_calendar_days(days))).strftime("%Y%m%d")

        try:
            df = self._fetch_raw_daily_data(stock_code, start_date, end_date)
            if df is None or df.empty:
                return None

            df = self._normalize_data(df, stock_code)
            df = self._clean_data(df)
            return df

        except Exception as e:
            classified = classify_exception(e, source=self.name, code=stock_code)
            category = get_error_category(classified)
            _LOGGER.warning(f"[{self.name}] [{category}] 获取 {stock_code} 未复权数据失败: {e}")
            raise classified

    def _fetch_raw_daily_data(
        self,
        stock_code: str,
        start_date: str,
        end_date: str,
    ) -> Optional[pd.DataFrame]:
        """获取未复权日线原始数据，默认不支持。"""
        return None

    def _clean_data(self, df: pd.DataFrame) -> pd.DataFrame:
        """清洗数据"""
        if df is None or df.empty:
            return df

        # 确保有标准列
        for col in STANDARD_COLUMNS:
            if col not in df.columns:
                df[col] = np.nan

        # 日期格式化
        if 'date' in df.columns:
            df['date'] = pd.to_datetime(df['date'], errors='coerce')
            df = df.dropna(subset=['date'])
            df['date'] = df['date'].dt.strftime('%Y-%m-%d')

        # 数值类型转换
        numeric_cols = ['open', 'high', 'low', 'close', 'volume', 'amount', 'pct_chg']
        for col in numeric_cols:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors='coerce')

        # 删除收盘价为空的行
        df = df.dropna(subset=['close'])

        # 按日期排序
        if 'date' in df.columns:
            df = df.sort_values('date', ascending=True)

        return df.reset_index(drop=True)

    def _calculate_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """计算技术指标"""
        if df is None or df.empty or 'close' not in df.columns:
            return df

        close = df['close']

        # 计算移动平均线
        df['MA5'] = close.rolling(window=5, min_periods=1).mean()
        df['MA10'] = close.rolling(window=10, min_periods=1).mean()
        df['MA20'] = close.rolling(window=20, min_periods=1).mean()

        # 计算成交量比率
        if 'volume' in df.columns:
            vol = df['volume']
            df['volume_ratio'] = vol / vol.rolling(window=5, min_periods=1).mean()

        return df

    def get_realtime_quote(self, stock_code: str) -> Optional[UnifiedRealtimeQuote]:
        """获取实时行情（子类可覆盖）"""
        return None

    def get_bid_ask(self, stock_code: str) -> Optional[pd.DataFrame]:
        """获取五档盘口（子类可覆盖）"""
        return None

    def get_chip_distribution(self, stock_code: str) -> Optional[ChipDistribution]:
        """获取筹码分布（子类可覆盖）"""
        return None

    def get_fund_flow(self, stock_code: str) -> Optional[pd.DataFrame]:
        """获取资金流向（子类可覆盖）"""
        return None

    def get_belong_board(self, stock_code: str) -> Optional[pd.DataFrame]:
        """获取所属板块（子类可覆盖）"""
        return None

    def get_board_cons(self, board_name: str, board_type: str = "industry") -> Optional[pd.DataFrame]:
        """获取板块成分股（子类可覆盖）"""
        return None

    def get_billboard(self, days: str = "5") -> Optional[pd.DataFrame]:
        """获取龙虎榜统计（子类可覆盖）"""
        return None

    def get_margin_detail(self, stock_code: str, market: str = "sh") -> Optional[pd.DataFrame]:
        """获取融资融券明细（子类可覆盖）"""
        return None

    def get_margin_ratio(self, stock_code: str) -> Optional[pd.DataFrame]:
        """获取融资融券比例（子类可覆盖）"""
        return None

    def get_industry_pe(self, date: str = "") -> Optional[pd.DataFrame]:
        """获取行业PE数据（子类可覆盖）"""
        return None

    def get_a_stock_spot(self) -> Optional[pd.DataFrame]:
        """获取全市场A股行情快照（子类可覆盖）"""
        return None

    def get_dividend_history(self, symbol: str) -> Optional[pd.DataFrame]:
        """获取分红历史（子类可覆盖）"""
        return None

    def get_fund_holder(self, symbol: str, date: str = "") -> Optional[pd.DataFrame]:
        """获取基金持仓（子类可覆盖）"""
        return None

    def get_top10_holders(self, symbol: str, holder_type: str = "main") -> Optional[pd.DataFrame]:
        """获取十大股东（子类可覆盖）"""
        return None

    def get_earnings_forecast(self, symbol: str) -> Optional[pd.DataFrame]:
        """获取业绩预告（子类可覆盖）"""
        return None

    def get_earnings_report(self, symbol: str) -> Optional[pd.DataFrame]:
        """获取业绩报表（子类可覆盖）"""
        return None

    def get_earnings_express(self, symbol: str) -> Optional[pd.DataFrame]:
        """获取业绩快报（子类可覆盖）"""
        return None

    def get_dividend_plan(self, symbol: str) -> Optional[pd.DataFrame]:
        """获取分红送配方案（子类可覆盖）"""
        return None

    def get_dividend_cninfo(self, symbol: str) -> Optional[pd.DataFrame]:
        """获取巨潮分红明细（子类可覆盖）"""
        return None


class DataFetcherManager:
    """数据获取管理器，支持多数据源自动故障转移"""

    # 函数级数据源优先级（method_name → {FetcherName: priority}）
    # 支持 "method:stock_type" 复合键按股票类型细分优先级
    # 未配置的函数使用全局 self._fetchers 顺序
    _function_priorities: Dict[str, Dict[str, int]] = {
        # ---- 日线数据 ----
        # TickFlow 10次/分钟限流，Tushare 50次/分钟
        # 单只股票查询时：Efinance/Akshare 稳定性好，TickFlow 和 Tushare 适合少量查询
        "get_daily_data": _priority_order(
            "EfinanceFetcher",
            "AkshareFetcher",
            "TushareFetcher",
            "TickflowFetcher",  # 在 Tushare 后，避免过快触发限流
            "PytdxFetcher",
            "BaostockFetcher",
        ),
        "get_raw_daily_data": _priority_order(
            "EfinanceFetcher",
            "AkshareFetcher",
            "TushareFetcher",
            "TickflowFetcher",  # 在 Tushare 后
        ),
        # ---- 实时行情 ----
        # TickFlow 有 API Key 时优先提供实时行情；未配置时自动跳过。
        "get_realtime_quote": _priority_order(
            "TickflowFetcher",
            "EfinanceFetcher",
            "TushareFetcher",
            "AkshareFetcher",
        ),
        "get_bid_ask": _priority_order("AkshareFetcher"),
        # 全市场A股快照：Efinance 更稳定，优先使用；Akshare 作为备份
        "get_a_stock_spot": _priority_order("EfinanceFetcher", "AkshareFetcher"),
        # 港股：Akshare 数据全
        "get_realtime_quote:hk": _priority_order(
            "TickflowFetcher",
            "AkshareFetcher",
            "YfinanceFetcher",
        ),
        # ETF：Akshare 支持 fund_etf_spot_em
        "get_realtime_quote:etf": _priority_order(
            "TickflowFetcher",
            "AkshareFetcher",
            "YfinanceFetcher",
        ),
        # 美股：YFinance 免费实时报价
        "get_realtime_quote:us": _priority_order(
            "TickflowFetcher",
            "YfinanceFetcher",
            "AlphaVantage",
        ),
        # ---- 资金流向/所属板块/龙虎榜 ----
        # Efinance 响应快且支持批量，Tushare 有配额限制放后面
        "get_fund_flow": _priority_order("EfinanceFetcher", "TushareFetcher", "AkshareFetcher"),
        "get_belong_board": _priority_order("EfinanceFetcher", "TushareFetcher", "AkshareFetcher"),
        "get_board_cons": _priority_order("AkshareFetcher", "TushareFetcher"),
        "get_billboard": _priority_order("EfinanceFetcher", "AkshareFetcher", "TushareFetcher"),
        # ---- Akshare 财务扩展 ----
        "get_chip_distribution": _priority_order("AkshareFetcher"),
        "get_margin_detail": _priority_order("AkshareFetcher"),
        "get_dividend_history": _priority_order("TushareFetcher", "AkshareFetcher"),
        "get_fund_holder": _priority_order("TushareFetcher", "AkshareFetcher"),
        "get_top10_holders": _priority_order("TushareFetcher", "AkshareFetcher"),
        "get_industry_pe": _priority_order("AkshareFetcher"),
        "get_earnings_forecast": _priority_order("AkshareFetcher"),
        "get_earnings_report": _priority_order("AkshareFetcher"),
        "get_earnings_express": _priority_order("AkshareFetcher"),
        "get_dividend_plan": _priority_order("AkshareFetcher"),
        "get_dividend_cninfo": _priority_order("AkshareFetcher"),
    }

    def __init__(self, auto_init: bool = True):
        self._fetchers: List[BaseFetcher] = []
        self._daily_cache_target_days: int = max(int(os.getenv("DAILY_CACHE_TARGET_DAYS", "425")), 30)
        self._batch_realtime_min_size: int = max(
            int(os.getenv("BATCH_REALTIME_MIN_SIZE", "8")),
            1,
        )
        self._store = CacheStore.get_store("data_provider")

        if auto_init:
            self._init_default_fetchers()

    def _get_backend_failure_scope(
        self,
        fetcher: BaseFetcher,
        method_name: str,
        *args,
        **kwargs,
    ) -> Optional[str]:
        """解析 fetcher 在当前方法上的后端失败作用域。"""
        if not fetcher.backend_group:
            return None
        scope = fetcher.get_backend_failure_scope(method_name, *args, **kwargs)
        return scope or fetcher.backend_group

    def _is_backend_scope_failed(
        self,
        failed_backend_scopes: set,
        fetcher: BaseFetcher,
        method_name: str,
        *args,
        **kwargs,
    ) -> tuple[bool, Optional[str]]:
        scope = self._get_backend_failure_scope(fetcher, method_name, *args, **kwargs)
        return bool(scope and scope in failed_backend_scopes), scope

    def _mark_backend_scope_failed(
        self,
        failed_backend_scopes: set,
        fetcher: BaseFetcher,
        method_name: str,
        *args,
        **kwargs,
    ) -> Optional[str]:
        scope = self._get_backend_failure_scope(fetcher, method_name, *args, **kwargs)
        if scope:
            failed_backend_scopes.add(scope)
        return scope

    def _get_realtime_ttl(self) -> float:
        a = MarketHoursConfig.is_trading_time(MarketType.A_STOCK)
        hk = MarketHoursConfig.is_trading_time(MarketType.HK_STOCK)
        us = MarketHoursConfig.is_trading_time(MarketType.US_STOCK)
        if a or hk or us:
            return CACHE_TTLS["realtime_trading"]
        now = datetime.now()
        t = now.hour * 100 + now.minute
        if (800 <= t < 930) or (1500 < t <= 1800):
            return CACHE_TTLS["realtime_pre_post"]
        return CACHE_TTLS["realtime_closed"]

    def _get_spot_ttl(self) -> float:
        if MarketHoursConfig.is_trading_time(MarketType.A_STOCK):
            return CACHE_TTLS["spot_trading"]
        return CACHE_TTLS["spot_closed"]

    def _get_fund_flow_ttl(self) -> float:
        if MarketHoursConfig.is_trading_time(MarketType.A_STOCK):
            return CACHE_TTLS["fund_flow_trading"]
        return CACHE_TTLS["fund_flow_closed"]

    _DYNAMIC_CACHE_DISK_EXPIRE = 86400.0

    def _get_dynamic_cached(self, cache_key: str, ttl: float):
        cached = self._store.get(cache_key)
        if not isinstance(cached, tuple) or len(cached) != 2:
            return None
        value, ts = cached
        if time.time() - ts < ttl:
            return value
        return None

    def _set_dynamic_cached(self, cache_key: str, value):
        self._store.set(cache_key, (value, time.time()), expire=self._DYNAMIC_CACHE_DISK_EXPIRE)

    def _init_default_fetchers(self):
        """初始化默认数据源"""
        try:
            from .tickflow_fetcher import TickflowFetcher
            fetcher = TickflowFetcher()
            if fetcher.is_available:
                self.add_fetcher(fetcher)
        except Exception as e:
            _LOGGER.warning(f"TickflowFetcher 初始化失败: {e}")

        try:
            from .efinance_fetcher import EfinanceFetcher
            self.add_fetcher(EfinanceFetcher())
        except Exception as e:
            _LOGGER.warning(f"EfinanceFetcher 初始化失败: {e}")

        try:
            from .akshare_fetcher import AkshareFetcher
            self.add_fetcher(AkshareFetcher())
        except Exception as e:
            _LOGGER.warning(f"AkshareFetcher 初始化失败: {e}")

        try:
            from .tushare_fetcher import TushareFetcher
            fetcher = TushareFetcher()
            if fetcher.is_available:
                self.add_fetcher(fetcher)
        except Exception as e:
            _LOGGER.warning(f"TushareFetcher 初始化失败: {e}")

        try:
            from .baostock_fetcher import BaostockFetcher
            self.add_fetcher(BaostockFetcher())
        except Exception as e:
            _LOGGER.warning(f"BaostockFetcher 初始化失败: {e}")

        try:
            from .pytdx_fetcher import PytdxFetcher
            fetcher = PytdxFetcher()
            if fetcher.is_available:
                self.add_fetcher(fetcher)
        except Exception as e:
            _LOGGER.debug(f"PytdxFetcher 初始化失败: {e}")

        try:
            from .yfinance_fetcher import YfinanceFetcher
            self.add_fetcher(YfinanceFetcher())
        except Exception as e:
            _LOGGER.warning(f"YfinanceFetcher 初始化失败: {e}")

        try:
            from .alphavantage_fetcher import AlphaVantageFetcher
            fetcher = AlphaVantageFetcher()
            if fetcher.is_available:
                self.add_fetcher(fetcher)
        except Exception as e:
            _LOGGER.warning(f"AlphaVantageFetcher 初始化失败: {e}")

        _LOGGER.info(f"已初始化 {len(self._fetchers)} 个数据源: {[f.name for f in self._fetchers]}")

    def add_fetcher(self, fetcher: BaseFetcher):
        """添加数据源并按优先级排序"""
        self._fetchers.append(fetcher)
        self._fetchers.sort(key=lambda f: f.priority)

    def get_fetchers(self) -> List[BaseFetcher]:
        """获取所有数据源"""
        return self._fetchers.copy()

    def _get_fetchers_for(self, method_name: str, stock_type: Optional[StockType] = None) -> List[BaseFetcher]:
        """按函数级优先级返回 fetcher，支持按股票类型细分，未配置则返回全局顺序。
        配置了优先级的函数只返回配置中列出的 fetcher。"""
        base_method = getattr(BaseFetcher, method_name, None)

        def supports_method(fetcher: BaseFetcher) -> bool:
            impl = getattr(type(fetcher), method_name, None)
            return impl is not None and not (base_method is not None and impl is base_method)

        def configured_fetchers(priority_order: Dict[str, int]) -> List[BaseFetcher]:
            candidates = [f for f in self._fetchers if f.name in priority_order]
            if candidates:
                return sorted(candidates, key=lambda f: priority_order[f.name])
            return [f for f in self._fetchers if supports_method(f)]

        if stock_type:
            key = f"{method_name}:{stock_type.value}"
            priority_order = self._function_priorities.get(key)
            if priority_order:
                return configured_fetchers(priority_order)
        priority_order = self._function_priorities.get(method_name)
        if not priority_order:
            return [f for f in self._fetchers if supports_method(f)]
        return configured_fetchers(priority_order)

    def fetch_with_cache(
        self,
        loader,
        *args,
        ttl: float = 86400,
        key: Optional[str] = None,
        namespace: str = "general",
        cache_none: bool = False,
        **kwargs,
    ) -> Any:
        loader_name = getattr(loader, "__name__", str(loader))
        cache_key_suffix = key or f"{loader_name}-{args}-{kwargs}"
        cache_key = f"cache:{namespace}:{cache_key_suffix}"

        cached = self._store.get(cache_key)
        if cached is not None:
            _LOGGER.debug("[cache] hit: key=%s", cache_key)
            return cached

        _LOGGER.debug("[cache] miss: key=%s", cache_key)
        result = loader(*args, **kwargs)
        if result is not None or cache_none:
            self._store.set(cache_key, result, expire=float(ttl))
        return result

    def fetch_akshare(
        self,
        fun,
        *args,
        ttl: float = 86400,
        key: Optional[str] = None,
        **kwargs,
    ) -> Any:
        cache_kwargs = dict(kwargs)
        call_kwargs = dict(kwargs)
        call_kwargs.pop("ttl2", None)
        cache_kwargs.pop("ttl2", None)

        cache_key = key or f"{fun.__name__}-{args}-{cache_kwargs}"

        def _load():
            for attempt in range(2):
                try:
                    _LOGGER.debug("[cache] akshare request: key=%s", cache_key)
                    return fun(*args, **call_kwargs)
                except NETWORK_EXCEPTIONS as exc:
                    if attempt == 0:
                        _LOGGER.warning(
                            "[cache] akshare network error, retry: key=%s error=%s: %s",
                            cache_key, type(exc).__name__, exc,
                        )
                        time.sleep(2 + random.uniform(0, 1))
                    else:
                        _LOGGER.warning(
                            "[cache] akshare network error after retry: key=%s error=%s: %s",
                            cache_key, type(exc).__name__, exc,
                        )
                except Exception as exc:
                    _LOGGER.warning(
                        "[cache] akshare call failed: key=%s error=%s: %s",
                        cache_key, type(exc).__name__, exc,
                    )
                    break
            return None

        return self.fetch_with_cache(_load, ttl=ttl, key=cache_key, namespace="akshare")

    def get_daily_data(
        self,
        stock_code: str,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        days: int = 30
    ) -> Optional[pd.DataFrame]:
        """
        获取日线数据，自动故障转移，带智能缓存。

        缓存策略（仅 days 模式生效，指定 start_date/end_date 时跳过）：
        - 缓存行数 ≥ 请求天数 → 直接从缓存截取
        - 缓存行数 < 请求天数 → 重新获取并替换缓存
        - TTL 1天
        """
        stock_code = str(stock_code).strip()
        use_cache = start_date is None and end_date is None
        target_days = max(int(days), self._daily_cache_target_days) if use_cache else int(days)

        if use_cache:
            cache_key = f"daily:{stock_code}"
            cached_df = self._store.get(cache_key)
            if isinstance(cached_df, pd.DataFrame) and len(cached_df) >= days:
                result = cached_df.tail(days).copy()
                result.attrs = cached_df.attrs.copy()
                _LOGGER.debug(
                    "[cache] daily hit %s: cached=%d, return=%d",
                    stock_code, len(cached_df), len(result),
                )
                return result

        df = self._fetch_daily_data(stock_code, start_date, end_date, target_days)

        if use_cache and df is not None and not df.empty:
            existing = self._store.get(cache_key)
            if not isinstance(existing, pd.DataFrame) or len(df) >= len(existing):
                self._store.set(cache_key, df, expire=CACHE_TTLS["daily"])
                _LOGGER.debug("[cache] daily write %s: rows=%d", stock_code, len(df))

        if use_cache and df is not None and not df.empty:
            result = df.tail(days).copy()
            result.attrs = df.attrs.copy()
            return result

        return df

    def get_raw_daily_data(
        self,
        stock_code: str,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        days: int = 30,
    ) -> Optional[pd.DataFrame]:
        """获取未复权日线数据，自动故障转移，不复用技术指标缓存。"""
        stock_code = str(stock_code).strip()
        return self._fetch_raw_daily_data_via_manager(stock_code, start_date, end_date, days)

    def _fetch_raw_daily_data_via_manager(
        self,
        stock_code: str,
        start_date: Optional[str],
        end_date: Optional[str],
        days: int,
    ) -> Optional[pd.DataFrame]:
        """实际获取未复权日线数据，自动故障转移（启用同源后端隔离）。"""
        return self._get_data_with_failover(
            "get_raw_daily_data",
            get_circuit_breaker("daily"),
            f"raw {stock_code} 数据",
            stock_code,
            start_date,
            end_date,
            days,
        )

    def _fetch_daily_data(
        self,
        stock_code: str,
        start_date: Optional[str],
        end_date: Optional[str],
        days: int,
    ) -> Optional[pd.DataFrame]:
        """实际获取日线数据，自动故障转移（启用同源后端隔离）。"""
        return self._get_data_with_failover(
            "get_daily_data",
            get_circuit_breaker("daily"),
            f"{stock_code} 数据",
            stock_code,
            start_date,
            end_date,
            days,
        )

    def get_realtime_quote(
        self,
        stock_code: str,
        stock_type: Optional[StockType] = None
    ) -> Optional[UnifiedRealtimeQuote]:
        """
        获取实时行情，自动故障转移

        Args:
            stock_code: 股票代码
            stock_type: 股票类型（可选），用于优化数据源选择

        Returns:
            UnifiedRealtimeQuote 或 None
        """
        cache_key = f"realtime:{stock_code}"
        cached = self._get_dynamic_cached(cache_key, self._get_realtime_ttl())
        if isinstance(cached, UnifiedRealtimeQuote):
            _LOGGER.debug("[cache] realtime hit: %s", stock_code)
            return cached

        # 使用泛型故障转移方法
        quote = self._get_with_failover(
            "get_realtime_quote",
            get_circuit_breaker("realtime"),
            f"{stock_code} 实时行情",
            stock_code,
            fetchers=self._get_fetchers_for("get_realtime_quote", stock_type),
            accept_result=lambda q: q is not None and q.has_basic_data(),
        )

        if quote is not None:
            self._set_dynamic_cached(cache_key, quote)

        return quote

    def get_chip_distribution(self, stock_code: str) -> Optional[ChipDistribution]:
        """
        获取筹码分布，自动故障转移

        Args:
            stock_code: 股票代码

        Returns:
            ChipDistribution 或 None
        """
        return self._get_with_failover(
            "get_chip_distribution",
            get_circuit_breaker("chip"),
            f"{stock_code} 筹码分布",
            stock_code,
        )

    def prefetch_realtime_quotes(
        self,
        stock_codes: List[str],
        stock_type: Optional[StockType] = None
    ) -> Dict[str, UnifiedRealtimeQuote]:
        """
        批量预取实时行情

        Args:
            stock_codes: 股票代码列表
            stock_type: 股票类型（可选），如果为None则自动检测

        Returns:
            股票代码 -> UnifiedRealtimeQuote 的映射
        """
        from .stock_code import detect_stock_type

        result: Dict[str, UnifiedRealtimeQuote] = {}
        remaining = set(stock_codes)

        # 小批量场景下，直接逐票获取更稳妥。
        # 部分 fetcher 的“批量”实现实际会先拉全市场分页数据再过滤；
        # 当只请求少量代码时，这条路径会制造大量 push2 请求与重试日志。
        if len(remaining) < self._batch_realtime_min_size:
            for code in stock_codes:
                code_type = stock_type or detect_stock_type(code)
                quote = self.get_realtime_quote(code, stock_type=code_type)
                if quote:
                    result[code] = quote
            return result

        # 使用实时行情专用优先级，优先尝试支持批量获取的数据源
        circuit_breaker = get_circuit_breaker("realtime")
        failed_backend_scopes: set = set()
        for fetcher in self._get_fetchers_for("get_realtime_quote", stock_type):
            if not remaining:
                break
            if hasattr(fetcher, 'get_batch_realtime_quotes'):
                source_name = fetcher.name
                if not circuit_breaker.is_available(source_name):
                    _LOGGER.debug(f"[{source_name}] 熔断中，跳过批量行情")
                    continue

                should_skip, backend_scope = self._is_backend_scope_failed(
                    failed_backend_scopes,
                    fetcher,
                    "get_batch_realtime_quotes",
                    list(remaining),
                )
                if should_skip:
                    _LOGGER.debug(f"[{source_name}] 后端作用域 {backend_scope} 已故障，跳过批量行情")
                    continue
                try:
                    batch_result = fetcher.get_batch_realtime_quotes(list(remaining))
                    if batch_result:
                        circuit_breaker.record_success(source_name)
                        result.update(batch_result)
                        remaining -= batch_result.keys()
                        for code, quote in batch_result.items():
                            self._set_dynamic_cached(f"realtime:{code}", quote)
                except RateLimitError as e:
                    cooldown = e.retry_after or 300
                    circuit_breaker.force_open(source_name, cooldown, str(e))
                    self._mark_backend_scope_failed(
                        failed_backend_scopes,
                        fetcher,
                        "get_batch_realtime_quotes",
                        list(remaining),
                    )
                    _LOGGER.warning(f"[{source_name}] [RATE_LIMIT] 批量获取实时行情失败: {e}")
                    continue
                except Exception as e:
                    circuit_breaker.record_failure(source_name, str(e))
                    if _is_network_error(e):
                        self._mark_backend_scope_failed(
                            failed_backend_scopes,
                            fetcher,
                            "get_batch_realtime_quotes",
                            list(remaining),
                        )
                    _LOGGER.warning(f"[{source_name}] 批量获取实时行情失败: {e}")
                    continue

        # 回退到逐个获取仍未覆盖的代码
        for code in remaining:
            code_type = stock_type or detect_stock_type(code)
            quote = self.get_realtime_quote(code, stock_type=code_type)
            if quote:
                result[code] = quote

        return result

    def get_status(self) -> Dict[str, Any]:
        """获取数据源状态"""
        return {
            'fetchers': [
                {
                    'name': f.name,
                    'priority': f.priority,
                    'available': f.is_available,
                }
                for f in self._fetchers
            ],
            'daily_circuit_breaker': get_circuit_breaker("daily").get_status(),
            'realtime_circuit_breaker': get_circuit_breaker("realtime").get_status(),
            'chip_circuit_breaker': get_circuit_breaker("chip").get_status(),
            'fund_flow_circuit_breaker': get_circuit_breaker("fund_flow").get_status(),
            'board_circuit_breaker': get_circuit_breaker("board").get_status(),
            'billboard_circuit_breaker': get_circuit_breaker("billboard").get_status(),
            'us_financials_circuit_breaker': get_circuit_breaker("us_financials").get_status(),
            'margin_circuit_breaker': get_circuit_breaker("margin").get_status(),
            'industry_pe_circuit_breaker': get_circuit_breaker("industry_pe").get_status(),
            'spot_circuit_breaker': get_circuit_breaker("spot").get_status(),
            'dividend_circuit_breaker': get_circuit_breaker("dividend").get_status(),
            'fund_holder_circuit_breaker': get_circuit_breaker("fund_holder").get_status(),
            'top10_holders_circuit_breaker': get_circuit_breaker("top10_holders").get_status(),
            'financial_ext_circuit_breaker': get_circuit_breaker("financial_ext").get_status(),
        }

    # 禁用同源跳过的方法集合（这些方法的不同 fetcher 虽然 backend_group 相同，
    # 但实际走不同 HTTP 端点，一个失败不代表另一个也不可用）
    _skip_backend_group_methods: set = {"get_a_stock_spot"}

    def _get_with_failover(
        self,
        method_name: str,
        circuit_breaker,
        label: str,
        *args,
        fetchers: Optional[List[BaseFetcher]] = None,
        accept_result: Optional[Callable[[Any], bool]] = None,
        return_unaccepted_fallback: bool = False,
        unaccepted_reason: str = "unaccepted",
        **kwargs,
    ) -> Optional[Any]:
        """
        通用多数据源故障转移（泛型版本，支持任意返回类型）

        Args:
            method_name: BaseFetcher 上的方法名
            circuit_breaker: 对应的熔断器实例
            label: 日志标签（如 "资金流向"）
            *args, **kwargs: 传递给 fetcher 方法的参数
            fetchers: 可选的 fetcher 列表，默认按函数级优先级或全局优先级
            accept_result: 可选结果校验函数，返回 False 时继续回退
            return_unaccepted_fallback: 所有源无可接受结果时，是否返回首个未通过校验的非空结果
            unaccepted_reason: 未通过校验时写入日志和 attempts 的原因标签
        """
        failed_backend_scopes: set = set()
        check_backend = method_name not in self._skip_backend_group_methods
        ordered_fetchers = fetchers or self._get_fetchers_for(method_name)
        attempt_summaries: List[str] = []
        unaccepted_fallback: Optional[Any] = None

        _LOGGER.debug(
            "[failover] 开始获取%s: method=%s, fetchers=%s, check_backend=%s",
            label,
            method_name,
            [
                f"{f.name}(priority={f.priority}, backend={f.backend_group or '-'})"
                for f in ordered_fetchers
            ],
            check_backend,
        )

        for fetcher in ordered_fetchers:
            source_name = fetcher.name

            if not circuit_breaker.is_available(source_name):
                _LOGGER.debug(f"[{source_name}] 熔断中，跳过{label}")
                attempt_summaries.append(f"{source_name}:circuit_open")
                continue

            should_skip = False
            backend_scope = None
            if check_backend:
                should_skip, backend_scope = self._is_backend_scope_failed(
                    failed_backend_scopes,
                    fetcher,
                    method_name,
                    *args,
                    **kwargs,
                )
            if should_skip:
                _LOGGER.debug(f"[{source_name}] 后端作用域 {backend_scope} 已故障，跳过{label}")
                attempt_summaries.append(f"{source_name}:skip_backend={backend_scope}")
                continue

            started_at = time.monotonic()
            try:
                fn = getattr(fetcher, method_name, None)
                if fn is None:
                    _LOGGER.debug(f"[{source_name}] 缺少方法 {method_name}，跳过{label}")
                    attempt_summaries.append(f"{source_name}:missing_method")
                    continue
                _LOGGER.debug(
                    "[%s] 开始获取%s: method=%s, backend_group=%s",
                    source_name,
                    label,
                    method_name,
                    fetcher.backend_group or "-",
                )
                result = fn(*args, **kwargs)
                elapsed = time.monotonic() - started_at

                if result is None:
                    _LOGGER.debug(f"[{source_name}] 获取{label}返回 None，耗时 {elapsed:.2f}s")
                    attempt_summaries.append(f"{source_name}:none:{elapsed:.2f}s")
                    continue

                # 检查是否为空数据（DataFrame/list/dict等）
                is_empty = False
                if hasattr(result, 'empty'):
                    is_empty = result.empty
                elif hasattr(result, '__len__'):
                    is_empty = len(result) == 0

                if is_empty:
                    _LOGGER.debug(f"[{source_name}] 获取{label}返回空数据，耗时 {elapsed:.2f}s")
                    attempt_summaries.append(f"{source_name}:empty:{elapsed:.2f}s")
                    continue

                # 设置source属性（如果支持）
                if hasattr(result, 'attrs'):
                    result.attrs['source'] = source_name
                elif isinstance(result, dict) and '_source' not in result:
                    result['_source'] = source_name

                # 自定义结果校验
                if accept_result is not None and not accept_result(result):
                    if return_unaccepted_fallback and unaccepted_fallback is None:
                        unaccepted_fallback = result
                    circuit_breaker.record_failure(
                        source_name,
                        f"{unaccepted_reason}",
                    )
                    _LOGGER.info(
                        "[%s] 获取%s结果未通过校验，继续回退: reason=%s, 耗时 %.2fs",
                        source_name,
                        label,
                        unaccepted_reason,
                        elapsed,
                    )
                    attempt_summaries.append(
                        f"{source_name}:{unaccepted_reason}:{elapsed:.2f}s"
                    )
                    continue

                circuit_breaker.record_success(source_name)
                _LOGGER.debug(
                    "[%s] 成功获取%s, 耗时 %.2fs",
                    source_name,
                    label,
                    elapsed,
                )
                return result

            except RateLimitError as e:
                # 配额超限：立即熔断，使用动态冷却时间
                cooldown = e.retry_after or 300
                elapsed = time.monotonic() - started_at
                _LOGGER.warning(
                    f"[{source_name}] [RATE_LIMIT] 获取{label}失败，"
                    f"熔断 {cooldown}s，耗时 {elapsed:.2f}s: {type(e).__name__}: {e}"
                )
                circuit_breaker.force_open(source_name, cooldown, str(e))
                self._mark_backend_scope_failed(
                    failed_backend_scopes,
                    fetcher,
                    method_name,
                    *args,
                    **kwargs,
                )
                attempt_summaries.append(
                    f"{source_name}:rate_limit:{elapsed:.2f}s:{cooldown}s:{type(e).__name__}"
                )
                continue
            except Exception as e:
                elapsed = time.monotonic() - started_at
                circuit_breaker.record_failure(source_name, str(e))
                if _is_network_error(e):
                    self._mark_backend_scope_failed(
                        failed_backend_scopes,
                        fetcher,
                        method_name,
                        *args,
                        **kwargs,
                    )
                    _LOGGER.debug(
                        f"[{source_name}] 网络错误，{label}失败，耗时 {elapsed:.2f}s: "
                        f"{type(e).__name__}: {e}",
                        exc_info=True,
                    )
                    attempt_summaries.append(
                        f"{source_name}:network_error:{elapsed:.2f}s:{type(e).__name__}"
                    )
                else:
                    _LOGGER.warning(
                        f"[{source_name}] 获取{label}失败，耗时 {elapsed:.2f}s: "
                        f"{type(e).__name__}: {e}"
                    )
                    attempt_summaries.append(
                        f"{source_name}:error:{elapsed:.2f}s:{type(e).__name__}"
                    )
                continue

        if unaccepted_fallback is not None:
            _LOGGER.warning(
                "所有数据源均无法获取可接受的%s，使用未通过校验的兜底结果; attempts=%s",
                label,
                "; ".join(attempt_summaries) if attempt_summaries else "-",
            )
            return unaccepted_fallback

        if attempt_summaries:
            _LOGGER.error(
                "所有数据源均无法获取%s; attempts=%s",
                label,
                "; ".join(attempt_summaries),
            )
        else:
            _LOGGER.error(f"所有数据源均无法获取{label}")
        return None

    def _get_data_with_failover(
        self,
        method_name: str,
        circuit_breaker,
        label: str,
        *args,
        fetchers: Optional[List[BaseFetcher]] = None,
        accept_result: Optional[Callable[[pd.DataFrame], bool]] = None,
        return_unaccepted_fallback: bool = False,
        unaccepted_reason: str = "unaccepted",
        **kwargs,
    ) -> Optional[pd.DataFrame]:
        """
        DataFrame 专用的故障转移方法（向后兼容，内部调用泛型版本）
        """
        return self._get_with_failover(
            method_name,
            circuit_breaker,
            label,
            *args,
            fetchers=fetchers,
            accept_result=accept_result,
            return_unaccepted_fallback=return_unaccepted_fallback,
            unaccepted_reason=unaccepted_reason,
            **kwargs,
        )

    def get_bid_ask(self, stock_code: str) -> Optional[pd.DataFrame]:
        """获取五档盘口，自动故障转移"""
        return self._get_data_with_failover(
            "get_bid_ask", get_circuit_breaker("realtime"),
            f"{stock_code} 五档盘口", stock_code,
        )

    def get_fund_flow(self, stock_code: str) -> Optional[pd.DataFrame]:
        """获取资金流向，自动故障转移"""
        cache_key = f"fund_flow:{stock_code}"
        cached = self._get_dynamic_cached(cache_key, self._get_fund_flow_ttl())
        if isinstance(cached, pd.DataFrame):
            _LOGGER.debug("[cache] fund_flow hit %s", stock_code)
            return cached.copy()

        result = self._get_data_with_failover(
            "get_fund_flow", get_circuit_breaker("fund_flow"),
            f"{stock_code} 资金流向", stock_code,
        )
        if result is not None and not result.empty:
            self._set_dynamic_cached(cache_key, result)
            _LOGGER.debug("[cache] fund_flow write %s: rows=%d", stock_code, len(result))
        return result

    def get_belong_board(self, stock_code: str) -> Optional[pd.DataFrame]:
        """获取所属板块，自动故障转移"""
        cache_key = f"belong_board:{stock_code}"
        cached = self._store.get(cache_key)
        if isinstance(cached, pd.DataFrame):
            _LOGGER.debug("[cache] belong_board hit %s", stock_code)
            return cached.copy()

        result = self._get_data_with_failover(
            "get_belong_board", get_circuit_breaker("board"),
            f"{stock_code} 所属板块", stock_code,
        )
        if result is not None and not result.empty:
            self._store.set(cache_key, result, expire=CACHE_TTLS["belong_board"])
            _LOGGER.debug("[cache] belong_board write %s: rows=%d", stock_code, len(result))
        return result

    def get_board_cons(self, board_name: str, board_type: str = "industry") -> Optional[pd.DataFrame]:
        """获取板块成分股，自动故障转移"""
        return self._get_data_with_failover(
            "get_board_cons", get_circuit_breaker("board"),
            f"{board_name} 成分股", board_name, board_type,
        )

    def get_industry_pe(self, date: str = "") -> Optional[pd.DataFrame]:
        """获取行业PE数据，自动故障转移"""
        return self._get_data_with_failover(
            "get_industry_pe", get_circuit_breaker("industry_pe"),
            "行业PE数据", date,
        )

    def get_a_stock_spot(self) -> Optional[pd.DataFrame]:
        """获取全市场A股行情快照，自动故障转移，带缓存（仅缓存完整数据）"""
        cache_key = "spot:a_stock"
        cached = self._get_dynamic_cached(cache_key, self._get_spot_ttl())
        if isinstance(cached, pd.DataFrame):
            _LOGGER.debug("[cache] spot hit: rows=%d", len(cached))
            return cached.copy()

        df = self._get_data_with_failover(
            "get_a_stock_spot",
            get_circuit_breaker("spot"),
            "全市场A股行情",
            accept_result=lambda result: result.attrs.get("spot_complete", False),
            return_unaccepted_fallback=True,
            unaccepted_reason="incomplete",
        )

        if df is not None and not df.empty:
            if df.attrs.get("spot_complete", False):
                self._set_dynamic_cached(cache_key, df)
                _LOGGER.debug("[cache] spot write (complete): rows=%d", len(df))
            else:
                _LOGGER.info("[cache] spot skip (incomplete): rows=%d", len(df))
            return df

        _LOGGER.warning("[cache] spot failed, no cache available")
        return None

    def get_dividend_history(self, symbol: str) -> Optional[pd.DataFrame]:
        """获取分红历史，自动故障转移"""
        return self._get_data_with_failover(
            "get_dividend_history", get_circuit_breaker("dividend"),
            f"{symbol} 分红历史", symbol,
        )

    def get_fund_holder(self, symbol: str, date: str = "") -> Optional[pd.DataFrame]:
        """获取基金持仓，自动故障转移"""
        return self._get_data_with_failover(
            "get_fund_holder", get_circuit_breaker("fund_holder"),
            f"{symbol} 基金持仓", symbol, date,
        )

    def get_top10_holders(self, symbol: str, holder_type: str = "main") -> Optional[pd.DataFrame]:
        """获取十大股东，自动故障转移"""
        return self._get_data_with_failover(
            "get_top10_holders", get_circuit_breaker("top10_holders"),
            f"{symbol} 十大股东", symbol, holder_type,
        )

    def get_earnings_forecast(self, symbol: str) -> Optional[pd.DataFrame]:
        """获取业绩预告，自动故障转移"""
        return self._get_data_with_failover(
            "get_earnings_forecast", get_circuit_breaker("financial_ext"),
            f"{symbol} 业绩预告", symbol,
        )

    def get_earnings_report(self, symbol: str) -> Optional[pd.DataFrame]:
        """获取业绩报表，自动故障转移"""
        return self._get_data_with_failover(
            "get_earnings_report", get_circuit_breaker("financial_ext"),
            f"{symbol} 业绩报表", symbol,
        )

    def get_earnings_express(self, symbol: str) -> Optional[pd.DataFrame]:
        """获取业绩快报，自动故障转移"""
        return self._get_data_with_failover(
            "get_earnings_express", get_circuit_breaker("financial_ext"),
            f"{symbol} 业绩快报", symbol,
        )

    def get_dividend_plan(self, symbol: str) -> Optional[pd.DataFrame]:
        """获取分红送配方案，自动故障转移"""
        return self._get_data_with_failover(
            "get_dividend_plan", get_circuit_breaker("financial_ext"),
            f"{symbol} 分红送配", symbol,
        )

    def get_dividend_cninfo(self, symbol: str) -> Optional[pd.DataFrame]:
        """获取巨潮分红明细，自动故障转移"""
        return self._get_data_with_failover(
            "get_dividend_cninfo", get_circuit_breaker("financial_ext"),
            f"{symbol} 巨潮分红", symbol,
        )

    def get_cctv_news(self, date: str = "") -> Optional[pd.DataFrame]:
        """获取新闻联播文字稿（单源 AkshareFetcher）"""
        fetcher = next((f for f in self._fetchers if f.name == "AkshareFetcher"), None)
        if fetcher is None:
            return None
        try:
            df = fetcher.get_cctv_news(date)
            if df is not None and not df.empty:
                df.attrs["source"] = fetcher.name
                return df
        except Exception as e:
            _LOGGER.warning("[%s] 获取新闻联播文字稿失败: %s", fetcher.name, e)
        return None

    def get_billboard(self, days: str = "5") -> Optional[pd.DataFrame]:
        """获取龙虎榜统计，自动故障转移"""
        return self._get_data_with_failover(
            "get_billboard", get_circuit_breaker("billboard"),
            "龙虎榜数据", days,
        )

    def get_margin_detail(self, stock_code: str, market: str = "sh") -> Optional[pd.DataFrame]:
        """
        获取融资融券明细，自动故障转移

        优先尝试交易所明细接口，失败后尝试融资融券比例接口作为备用

        Args:
            stock_code: 股票代码
            market: 市场 'sh'(上交所) 或 'sz'(深交所)

        Returns:
            DataFrame 或 None，包含 source 属性标记来源
        """
        circuit_breaker = get_circuit_breaker("margin")
        tried_sources = []
        failed_backend_scopes: set = set()

        # 第一轮：尝试获取融资融券明细
        for fetcher in self._fetchers:
            source_name = fetcher.name

            if not circuit_breaker.is_available(source_name):
                _LOGGER.debug(f"[{source_name}] 熔断中，跳过融资融券明细")
                continue

            should_skip, backend_scope = self._is_backend_scope_failed(
                failed_backend_scopes,
                fetcher,
                "get_margin_detail",
                stock_code,
                market,
            )
            if should_skip:
                _LOGGER.debug(f"[{source_name}] 后端作用域 {backend_scope} 已故障，跳过融资融券")
                continue

            try:
                df = fetcher.get_margin_detail(stock_code, market)
                if df is not None and not df.empty:
                    circuit_breaker.record_success(source_name)
                    df.attrs['source'] = source_name
                    _LOGGER.debug(f"[{source_name}] 成功获取 {stock_code} 融资融券明细")
                    return df
                tried_sources.append(source_name)
            except Exception as e:
                circuit_breaker.record_failure(source_name, str(e))
                if _is_network_error(e):
                    self._mark_backend_scope_failed(
                        failed_backend_scopes,
                        fetcher,
                        "get_margin_detail",
                        stock_code,
                        market,
                    )
                _LOGGER.warning(f"[{source_name}] 获取 {stock_code} 融资融券明细失败: {e}")
                tried_sources.append(source_name)
                continue

        # 第二轮：尝试另一个市场（可能是双重上市）
        other_market = "sz" if market == "sh" else "sh"
        for fetcher in self._fetchers:
            source_name = fetcher.name

            if not circuit_breaker.is_available(source_name):
                continue

            should_skip, _ = self._is_backend_scope_failed(
                failed_backend_scopes,
                fetcher,
                "get_margin_detail",
                stock_code,
                other_market,
            )
            if should_skip:
                continue

            try:
                df = fetcher.get_margin_detail(stock_code, other_market)
                if df is not None and not df.empty:
                    circuit_breaker.record_success(source_name)
                    df.attrs['source'] = source_name
                    df.attrs['market'] = other_market
                    _LOGGER.debug(f"[{source_name}] 成功获取 {stock_code} 融资融券明细（备用市场）")
                    return df
            except Exception as e:
                if _is_network_error(e):
                    self._mark_backend_scope_failed(
                        failed_backend_scopes,
                        fetcher,
                        "get_margin_detail",
                        stock_code,
                        other_market,
                    )
                _LOGGER.debug(f"[{source_name}] 备用市场获取失败: {e}")
                continue

        # 第三轮：尝试融资融券比例作为备用
        for fetcher in self._fetchers:
            source_name = fetcher.name

            if not circuit_breaker.is_available(source_name):
                continue

            should_skip, _ = self._is_backend_scope_failed(
                failed_backend_scopes,
                fetcher,
                "get_margin_ratio",
                stock_code,
            )
            if should_skip:
                continue

            try:
                df = fetcher.get_margin_ratio(stock_code)
                if df is not None and not df.empty:
                    circuit_breaker.record_success(source_name)
                    df.attrs['source'] = f"{source_name}_ratio"
                    df.attrs['is_ratio_data'] = True
                    _LOGGER.debug(f"[{source_name}] 成功获取 {stock_code} 融资融券比例（备用）")
                    return df
            except Exception as e:
                if _is_network_error(e):
                    self._mark_backend_scope_failed(
                        failed_backend_scopes,
                        fetcher,
                        "get_margin_ratio",
                        stock_code,
                    )
                _LOGGER.debug(f"[{source_name}] 融资融券比例获取失败: {e}")
                continue

        _LOGGER.error(f"所有数据源均无法获取 {stock_code} 融资融券数据，已尝试: {tried_sources}")
        return None

    # ==================== 美股多数据源方法 ====================

    def _get_us_fetcher(self, fetcher_name: str):
        """获取指定的美股数据源"""
        for fetcher in self._fetchers:
            if fetcher.name == fetcher_name and fetcher.is_available:
                return fetcher
        return None

    def _get_us_fetchers_for_financials(self) -> List[BaseFetcher]:
        """
        获取美股基本面数据源列表（按优先级）
        AlphaVantage (需要 API key) -> YfinanceFetcher (免费)
        """
        fetchers = []

        # AlphaVantage 优先（如果可用）
        av = self._get_us_fetcher("AlphaVantage")
        if av:
            fetchers.append(av)

        # YFinance 作为后备
        yf = self._get_us_fetcher("YfinanceFetcher")
        if yf:
            fetchers.append(yf)

        return fetchers

    def get_us_company_overview(self, symbol: str) -> Optional[Dict[str, Any]]:
        """获取美股公司概览，优先级: AlphaVantage -> yfinance"""
        cache_key = f"us:overview:{symbol.upper()}"
        cached = self._store.get(cache_key)
        if isinstance(cached, dict):
            return cached

        circuit_breaker = get_circuit_breaker("us_financials")
        failed_backend_scopes: set = set()

        for fetcher in self._get_us_fetchers_for_financials():
            source_name = fetcher.name
            if not circuit_breaker.is_available(source_name):
                continue
            should_skip, backend_scope = self._is_backend_scope_failed(
                failed_backend_scopes, fetcher, "get_company_overview", symbol,
            )
            if should_skip:
                continue
            try:
                result = fetcher.get_company_overview(symbol)
                if result is not None:
                    circuit_breaker.record_success(source_name)
                    result['_data_source'] = source_name
                    self._store.set(cache_key, result, expire=CACHE_TTLS["us_overview"])
                    return result
            except Exception as e:
                circuit_breaker.record_failure(source_name, str(e))
                if _is_network_error(e):
                    self._mark_backend_scope_failed(
                        failed_backend_scopes, fetcher, "get_company_overview", symbol,
                    )
                _LOGGER.warning(f"[{source_name}] 获取 {symbol} 公司概览失败: {e}")
        return None

    def get_us_balance_sheet(self, symbol: str, quarterly: bool = True) -> Optional[Dict[str, Any]]:
        """获取美股资产负债表，优先级: AlphaVantage -> yfinance"""
        variant = "quarterly" if quarterly else "annual"
        cache_key = f"us:balance_sheet:{symbol.upper()}:{variant}"
        cached = self._store.get(cache_key)
        if isinstance(cached, dict):
            return cached

        circuit_breaker = get_circuit_breaker("us_financials")
        for fetcher in self._get_us_fetchers_for_financials():
            source_name = fetcher.name
            if not circuit_breaker.is_available(source_name):
                continue
            try:
                result = fetcher.get_balance_sheet(symbol, quarterly)
                if result is not None and result.get("reports"):
                    circuit_breaker.record_success(source_name)
                    result['_data_source'] = source_name
                    self._store.set(cache_key, result, expire=CACHE_TTLS["us_report"])
                    return result
            except Exception as e:
                circuit_breaker.record_failure(source_name, str(e))
                _LOGGER.warning(f"[{source_name}] 获取 {symbol} 资产负债表失败: {e}")
        return None

    def get_us_income_statement(self, symbol: str, quarterly: bool = True) -> Optional[Dict[str, Any]]:
        """获取美股利润表，优先级: AlphaVantage -> yfinance"""
        variant = "quarterly" if quarterly else "annual"
        cache_key = f"us:income_statement:{symbol.upper()}:{variant}"
        cached = self._store.get(cache_key)
        if isinstance(cached, dict):
            return cached

        circuit_breaker = get_circuit_breaker("us_financials")
        for fetcher in self._get_us_fetchers_for_financials():
            source_name = fetcher.name
            if not circuit_breaker.is_available(source_name):
                continue
            try:
                result = fetcher.get_income_statement(symbol, quarterly)
                if result is not None and result.get("reports"):
                    circuit_breaker.record_success(source_name)
                    result['_data_source'] = source_name
                    self._store.set(cache_key, result, expire=CACHE_TTLS["us_report"])
                    return result
            except Exception as e:
                circuit_breaker.record_failure(source_name, str(e))
                _LOGGER.warning(f"[{source_name}] 获取 {symbol} 利润表失败: {e}")
        return None

    def get_us_cash_flow(self, symbol: str, quarterly: bool = True) -> Optional[Dict[str, Any]]:
        """获取美股现金流量表，优先级: AlphaVantage -> yfinance"""
        variant = "quarterly" if quarterly else "annual"
        cache_key = f"us:cash_flow:{symbol.upper()}:{variant}"
        cached = self._store.get(cache_key)
        if isinstance(cached, dict):
            return cached

        circuit_breaker = get_circuit_breaker("us_financials")
        for fetcher in self._get_us_fetchers_for_financials():
            source_name = fetcher.name
            if not circuit_breaker.is_available(source_name):
                continue
            try:
                result = fetcher.get_cash_flow(symbol, quarterly)
                if result is not None and result.get("reports"):
                    circuit_breaker.record_success(source_name)
                    result['_data_source'] = source_name
                    self._store.set(cache_key, result, expire=CACHE_TTLS["us_report"])
                    return result
            except Exception as e:
                circuit_breaker.record_failure(source_name, str(e))
                _LOGGER.warning(f"[{source_name}] 获取 {symbol} 现金流量表失败: {e}")
        return None

    def get_us_earnings(self, symbol: str) -> Optional[Dict[str, Any]]:
        """获取美股盈利数据，优先级: AlphaVantage -> yfinance"""
        cache_key = f"us:earnings:{symbol.upper()}"
        cached = self._store.get(cache_key)
        if isinstance(cached, dict):
            return cached

        circuit_breaker = get_circuit_breaker("us_financials")
        for fetcher in self._get_us_fetchers_for_financials():
            source_name = fetcher.name
            if not circuit_breaker.is_available(source_name):
                continue
            try:
                result = fetcher.get_earnings(symbol)
                if result is not None:
                    circuit_breaker.record_success(source_name)
                    result['_data_source'] = source_name
                    self._store.set(cache_key, result, expire=CACHE_TTLS["us_earnings"])
                    return result
            except Exception as e:
                circuit_breaker.record_failure(source_name, str(e))
                _LOGGER.warning(f"[{source_name}] 获取 {symbol} 盈利数据失败: {e}")
        return None

    def get_us_news_sentiment(
        self,
        symbol: str = None,
        topics: str = None,
        limit: int = 50
    ) -> Optional[Dict[str, Any]]:
        """
        获取美股新闻情绪（通过 Alpha Vantage，无后备源）

        Args:
            symbol: 股票代码（可选）
            topics: 主题过滤（可选）
            limit: 返回数量限制

        Returns:
            新闻情绪数据
        """
        fetcher = self._get_us_fetcher("AlphaVantage")
        if fetcher is None:
            _LOGGER.warning("AlphaVantage 数据源未配置或不可用")
            return None

        try:
            return fetcher.get_news_sentiment(symbol, topics, limit)
        except Exception as e:
            _LOGGER.warning(f"获取美股新闻情绪失败: {e}")
            return None

    def get_us_insider_transactions(self, symbol: str) -> Optional[Dict[str, Any]]:
        """获取美股内部交易，优先级: AlphaVantage -> yfinance"""
        cache_key = f"us:insider:{symbol.upper()}"
        cached = self._store.get(cache_key)
        if isinstance(cached, dict):
            return cached

        circuit_breaker = get_circuit_breaker("us_financials")
        for fetcher in self._get_us_fetchers_for_financials():
            source_name = fetcher.name
            if not circuit_breaker.is_available(source_name):
                continue
            try:
                result = fetcher.get_insider_transactions(symbol)
                if result is not None and result.get("data"):
                    circuit_breaker.record_success(source_name)
                    result['_data_source'] = source_name
                    self._store.set(cache_key, result, expire=CACHE_TTLS["us_insider"])
                    return result
            except Exception as e:
                circuit_breaker.record_failure(source_name, str(e))
                _LOGGER.warning(f"[{source_name}] 获取 {symbol} 内部交易失败: {e}")
        return None

    def get_us_technical_indicator(
        self,
        symbol: str,
        indicator: str,
        interval: str = "daily",
        time_period: int = 14,
    ) -> Optional[Dict[str, Any]]:
        """
        获取美股技术指标（通过 Alpha Vantage）

        Args:
            symbol: 美股代码
            indicator: 指标类型 (SMA, EMA, RSI, MACD, BBANDS, STOCH, ADX)
            interval: 时间间隔 (daily, weekly, monthly)
            time_period: 计算周期

        Returns:
            技术指标数据
        """
        fetcher = self._get_us_fetcher("AlphaVantage")
        if fetcher is None:
            _LOGGER.warning("AlphaVantage 数据源未配置或不可用")
            return None

        try:
            return fetcher.get_technical_indicator(symbol, indicator, interval, time_period)
        except Exception as e:
            _LOGGER.warning(f"获取美股技术指标失败: {e}")
            return None

    def format_us_overview_report(self, overview: Dict[str, Any]) -> str:
        """格式化美股公司概览报告（支持多数据源）"""
        if not overview:
            return "无数据"

        source = overview.get('_data_source', 'unknown')

        # AlphaVantage 有专用格式化方法
        av = self._get_us_fetcher("AlphaVantage")
        if av and source == "AlphaVantage":
            return av.format_overview_report(overview)

        # 通用 CSV 格式（yfinance 或其他）
        lines = [
            f"# {overview.get('Name', '')} ({overview.get('Symbol', '')})",
            f"# 数据来源: {source}",
            "",
            "# 基本信息",
            "行业,板块,国家,交易所",
            f"{overview.get('Industry', '-')},{overview.get('Sector', '-')},{overview.get('Country', '-')},{overview.get('Exchange', '-')}",
            "",
            "# 估值指标",
            "市值,市盈率(PE),远期市盈率,市净率(PB),市销率(PS),PEG比率",
            f"${self._format_large_number(overview.get('MarketCapitalization'))},{overview.get('PERatio', '-')},{overview.get('ForwardPE', '-')},{overview.get('PriceToBookRatio', '-')},{overview.get('PriceToSalesRatioTTM', '-')},{overview.get('PEGRatio', '-')}",
            "",
            "# 盈利指标",
            "每股收益(EPS),每股净资产,净利润率,营业利润率,ROE,ROA",
            f"${overview.get('EPS', '-')},${overview.get('BookValue', '-')},{overview.get('ProfitMargin', '-')},{overview.get('OperatingMarginTTM', '-')},{overview.get('ReturnOnEquityTTM', '-')},{overview.get('ReturnOnAssetsTTM', '-')}",
            "",
            "# 股息信息",
            "股息率,每股股息,除息日",
            f"{overview.get('DividendYield', '-')},${overview.get('DividendPerShare', '-')},{overview.get('ExDividendDate', '-')}",
            "",
            "# 价格区间",
            "52周最高,52周最低,50日均价,200日均价",
            f"${overview.get('52WeekHigh', '-')},${overview.get('52WeekLow', '-')},${overview.get('50DayMovingAverage', '-')},${overview.get('200DayMovingAverage', '-')}",
            "",
            "# 分析师评级",
            "目标价,强烈买入,买入,持有,卖出,强烈卖出",
            f"${overview.get('AnalystTargetPrice', '-')},{overview.get('AnalystRatingStrongBuy', '-')},{overview.get('AnalystRatingBuy', '-')},{overview.get('AnalystRatingHold', '-')},{overview.get('AnalystRatingSell', '-')},{overview.get('AnalystRatingStrongSell', '-')}",
        ]
        return "\n".join(lines)

    def _format_large_number(self, value) -> str:
        """格式化大数字"""
        if not value or value in ("", "None", "nan"):
            return "-"
        try:
            num = float(value)
            if num >= 1e12:
                return f"{num/1e12:.2f}T"
            elif num >= 1e9:
                return f"{num/1e9:.2f}B"
            elif num >= 1e6:
                return f"{num/1e6:.2f}M"
            else:
                return f"{num:,.0f}"
        except (ValueError, TypeError):
            return str(value)

    def format_us_news_report(self, news_data: Dict[str, Any], limit: int = 10) -> str:
        """格式化美股新闻报告"""
        fetcher = self._get_us_fetcher("AlphaVantage")
        if fetcher is None:
            return "AlphaVantage 数据源未配置"
        return fetcher.format_news_report(news_data, limit)
