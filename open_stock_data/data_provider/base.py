"""数据获取基类。"""

from __future__ import annotations

import logging
import random
import time
from abc import abstractmethod
from datetime import datetime, timedelta
from typing import Optional, Any, TYPE_CHECKING

import pandas as pd
import numpy as np
import requests.exceptions

from ..cache import CACHE_TTLS
from .plugin import ProviderPlugin, ProviderMetadata, ProviderHealthEvent
from .context import ProviderContext
from .providers import create_default_providers

# 网络类异常：后端服务器不可达，向上传播由路由按统一健康体系处理
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


def _is_network_error(e: Exception) -> bool:
    """判断是否为网络连接错误（后端不可达）"""
    return isinstance(e, (*NETWORK_EXCEPTIONS, NetworkError))


class BaseFetcher(ProviderPlugin):
    """数据获取器基类，同时实现 ProviderPlugin 协议。"""

    name: str = "BaseFetcher"
    priority: int = 99

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
        self._health_event_listeners: list = []

    @property
    def metadata(self) -> ProviderMetadata:
        return ProviderMetadata(name=self.name, priority=self.priority, tags=tuple())

    @property
    def is_available(self) -> bool:
        """数据源是否可用"""
        return self._available

    def execute(self, method_name: str, *args, **kwargs) -> Any:
        """ProviderPlugin 入口：反射调用对应的方法名。"""
        method = getattr(self, method_name)
        return method(*args, **kwargs)

    def report_health(self, event: ProviderHealthEvent) -> None:
        """ProviderPlugin 协议：接收健康事件。"""
        pass

    def random_sleep(self, min_seconds: float = 1.0, max_seconds: float = 3.0):
        """随机延迟，用于反爬"""
        time.sleep(random.uniform(min_seconds, max_seconds))

    def get_random_user_agent(self) -> str:
        """获取随机 User-Agent"""
        return random.choice(self.USER_AGENTS)

    _TRADING_DAY_TO_CALENDAR_RATIO = 1.8
    _TRADING_DAY_BUFFER = 30

    @classmethod
    def _estimate_calendar_days(cls, trading_days: int) -> int:
        trading_days = max(int(trading_days), 1)
        return int(trading_days * cls._TRADING_DAY_TO_CALENDAR_RATIO) + cls._TRADING_DAY_BUFFER

    @abstractmethod
    def _fetch_daily_data(
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
            df = self._fetch_daily_data(stock_code, start_date, end_date)
            if df is None:
                return None
            if df.empty:
                # 配额冷却等原因的空结果（attrs 带 empty_reason）：原样透出，
                # 路由记 EMPTY 时可读出原因写入 FetchAttempt；普通空结果仍回 None。
                if isinstance(getattr(df, "attrs", None), dict) and df.attrs.get("empty_reason"):
                    return df
                return None

            df = self._normalize_data(df, stock_code)
            df = self._clean_data(df)

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
            if df is None:
                return None
            if df.empty:
                if isinstance(getattr(df, "attrs", None), dict) and df.attrs.get("empty_reason"):
                    return df
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
    """向后兼容包装：委托给 ProviderContext。

    已废弃，仅用于 tools/utils 中的遗留引用。
    """

    def __init__(self, auto_init: bool = True):
        self._ctx = ProviderContext.default()
        if auto_init and not self._ctx.provider_names:
            create_default_providers(self._ctx)

    def get_fetchers(self) -> list:
        return list(self._ctx.get_available_providers())

    def get_status(self) -> dict:
        return {
            'providers': self._ctx.provider_names,
            'health': {k: v.to_dict() for k, v in self._ctx.health_snapshot.items()},
        }

    def fetch_akshare(self, func, *args, **kwargs):
        """调用 akshare 函数，带磁盘缓存。"""
        from ..cache import CacheStore
        ttl = kwargs.pop("ttl", CACHE_TTLS["akshare_default"])
        namespace = kwargs.pop("namespace", "akshare")
        key = f"{func.__name__}:{args}:{sorted(kwargs.items())}"
        store = CacheStore.get_store(namespace)
        cached = store.get(key)
        if cached is not None:
            return cached
        result = func(*args, **kwargs)
        store.set(key, result, expire=ttl)
        return result

    def fetch_with_cache(self, func, *args, **kwargs):
        """通用：调用任意函数，带磁盘缓存。须传入 key 和 namespace。"""
        from ..cache import CacheStore
        ttl = kwargs.pop("ttl", 3600)
        key = kwargs.pop("key", None)
        namespace = kwargs.pop("namespace", "default")
        store = CacheStore.get_store(namespace)
        cached = store.get(key)
        if cached is not None:
            return cached
        result = func(*args, **kwargs)
        store.set(key, result, expire=ttl)
        return result



