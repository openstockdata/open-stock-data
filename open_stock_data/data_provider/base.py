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
    """数据获取管理器，保留缓存工具（fetch_akshare/fetch_with_cache）与状态查询。"""

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
        """解析 fetcher 在当前方法上的后端失败作用域（美股多源方法仍复用）。"""
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

    # ==================== 美股多数据源方法 ====================

    def _get_us_fetcher(self, fetcher_name: str):
        """按名查找可用的美股数据源（格式化器委托专用格式化方法时复用）。"""
        for fetcher in self._fetchers:
            if fetcher.name == fetcher_name and fetcher.is_available:
                return fetcher
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
