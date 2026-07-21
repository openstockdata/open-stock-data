"""
Akshare 数据获取器 (优先级 2)
使用 akshare 库获取股票数据，支持多数据源（东财、新浪、腾讯）及多市场
"""

import logging
import random
import time
from datetime import datetime
from typing import Optional, Dict, List, Tuple, Callable, Any

import pandas as pd
import akshare as ak
import requests
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from .base import BaseFetcher, DataFetchError, NETWORK_EXCEPTIONS
from .types import (
    UnifiedRealtimeQuote,
    ChipDistribution,
    RealtimeSource,
    safe_float,
    safe_int,
)
from .stock_code import is_etf_code, is_hk_code, normalize_hk_code

_LOGGER = logging.getLogger(__name__)

# Sina / Tencent direct API endpoints for single-stock realtime quotes
SINA_REALTIME_ENDPOINT = "hq.sinajs.cn/list"
TENCENT_REALTIME_ENDPOINT = "qt.gtimg.cn/q"


def _to_sina_tx_symbol(stock_code: str) -> str:
    """Convert 6-digit A-share code to sh/sz/bj prefixed symbol for Sina/Tencent APIs."""
    base = (stock_code.strip().split(".")[0] if "." in stock_code else stock_code).strip()
    # Beijing Stock Exchange: 8xxxxx, 4xxxxx, 92xxxx
    if base.startswith(("8", "4", "92")):
        return f"bj{base}"
    # Shanghai: 6xxxxx, 5xxxxx (ETF), 90xxxx (B-shares)
    if base.startswith(("6", "5", "90")):
        return f"sh{base}"
    return f"sz{base}"


def _detect_rate_limit(error_msg: str) -> bool:
    """Check if an error message indicates rate limiting / anti-bot blocking."""
    lowered = error_msg.lower()
    return any(kw in lowered for kw in (
        "banned", "blocked", "频率", "rate", "限制",
        "too many requests", "429", "forbidden", "403",
    ))


class AkshareFetcher(BaseFetcher):
    """Akshare 数据获取器"""

    name = "AkshareFetcher"
    priority = 2  # 多市场支持
    backend_group = "eastmoney"
    _BACKEND_FAILURE_SCOPE_MAP = {
        "get_realtime_quote": "eastmoney:push2:realtime_quotes",
        "get_batch_realtime_quotes": "eastmoney:push2:realtime_quotes",
        "get_bid_ask": "eastmoney:push2:bid_ask",
        "get_chip_distribution": "eastmoney:cyq:chip_distribution",
        "get_fund_flow": "eastmoney:https:push2his:fund_flow",
        "get_belong_board": "eastmoney:push2:sector_spot",
        "get_billboard": "sina:billboard",
        "get_margin_ratio": "pingan:margin_ratio",
        "get_industry_pe": "cninfo:industry_pe",
        "get_dividend_history": "eastmoney:dividend_history",
        "get_fund_holder": "eastmoney:fund_holder",
        "get_top10_holders": "eastmoney:top10_holders",
        "get_earnings_forecast": "eastmoney:datacenter:yjyg",
        "get_earnings_report": "eastmoney:datacenter:yjbb",
        "get_earnings_express": "eastmoney:datacenter:yjkb",
        "get_dividend_plan": "eastmoney:datacenter:fhps",
        "get_dividend_cninfo": "cninfo:dividend",
        "get_cctv_news": "cctv:news",
    }

    def __init__(self):
        super().__init__()

    def get_backend_failure_scope(self, method_name: str, *args, **kwargs) -> Optional[str]:
        if method_name == "get_board_cons":
            board_type = kwargs.get("board_type") if "board_type" in kwargs else (args[1] if len(args) > 1 else "industry")
            return f"eastmoney:push2:board_cons:{board_type}"
        if method_name == "get_margin_detail":
            market = kwargs.get("market") if "market" in kwargs else (args[1] if len(args) > 1 else "sh")
            return f"exchange:margin_detail:{market}"
        if method_name == "get_a_stock_spot":
            return "eastmoney:push2:realtime_quotes"
        scope = self._BACKEND_FAILURE_SCOPE_MAP.get(method_name)
        if scope:
            return scope
        return super().get_backend_failure_scope(method_name, *args, **kwargs)


    @staticmethod
    def _normalize_mainland_code(value: str) -> str:
        code = str(value or "").strip().lower()
        if not code:
            return ""
        if code.startswith(("sh", "sz", "bj")):
            code = code[2:]
        if code.endswith((".sh", ".sz", ".bj")):
            code = code[:-3]
        if code.isdigit() and len(code) <= 6:
            return code.zfill(6)
        return code

    def _filter_df_by_stock_code(self, df: pd.DataFrame, stock_code: str) -> pd.DataFrame:
        if df is None or df.empty:
            return pd.DataFrame()

        target = self._normalize_mainland_code(stock_code)
        if not target:
            return pd.DataFrame()

        code_columns = [
            "代码", "股票代码", "证券代码", "symbol", "stock_code", "ts_code",
        ]
        for col in code_columns:
            if col not in df.columns:
                continue
            series = df[col].astype(str).map(self._normalize_mainland_code)
            matched = df[series == target]
            if not matched.empty:
                return matched.copy()

        return pd.DataFrame()

    def _call_ak_func_with_symbol_fallback(
        self,
        func: Callable[..., Any],
        stock_code: str,
        include_no_arg: bool = True,
        allow_unfiltered_no_arg: bool = True,
        require_stock_match: bool = False,
    ) -> Optional[pd.DataFrame]:
        normalized_code = self._normalize_mainland_code(stock_code)
        call_variants: List[Dict[str, Any]] = [
            {"symbol": stock_code},
            {"stock": stock_code},
            {"code": stock_code},
        ]

        if normalized_code and normalized_code != stock_code:
            call_variants.extend([
                {"symbol": normalized_code},
                {"stock": normalized_code},
                {"code": normalized_code},
            ])

        if normalized_code:
            if normalized_code.startswith(("6", "9")):
                ts_code = f"{normalized_code}.SH"
            elif normalized_code.startswith(("0", "2", "3")):
                ts_code = f"{normalized_code}.SZ"
            elif normalized_code.startswith(("4", "8")):
                ts_code = f"{normalized_code}.BJ"
            else:
                ts_code = ""
            if ts_code:
                call_variants.append({"ts_code": ts_code})

        if include_no_arg:
            call_variants.append({})

        for kwargs in call_variants:
            try:
                df = func(**kwargs) if kwargs else func()
            except TypeError:
                continue

            if df is None or not isinstance(df, pd.DataFrame) or df.empty:
                continue

            filtered = self._filter_df_by_stock_code(df, stock_code)
            if filtered is not None and not filtered.empty:
                return filtered

            if require_stock_match:
                continue
            if kwargs and any(k in kwargs for k in ("symbol", "stock", "code", "ts_code")):
                return df
            if not kwargs and allow_unfiltered_no_arg:
                return df

        return None

    @retry(
        stop=stop_after_attempt(2),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type(NETWORK_EXCEPTIONS),
        reraise=True
    )
    def _fetch_raw_data(
        self,
        stock_code: str,
        start_date: str,
        end_date: str
    ) -> Optional[pd.DataFrame]:
        """获取原始数据"""
        self.random_sleep(2.0, 5.0)

        try:
            if is_hk_code(stock_code):
                return self._fetch_hk_data(stock_code, start_date, end_date)
            elif is_etf_code(stock_code):
                return self._fetch_etf_data(stock_code, start_date, end_date)
            else:
                return self._fetch_stock_data(stock_code, start_date, end_date)
        except NETWORK_EXCEPTIONS:
            raise
        except Exception as e:
            _LOGGER.warning(f"[{self.name}] 获取 {stock_code} 原始数据失败: {e}")
            raise DataFetchError(f"获取数据失败: {e}")

    def _fetch_raw_daily_data(
        self,
        stock_code: str,
        start_date: str,
        end_date: str
    ) -> Optional[pd.DataFrame]:
        """获取 A 股未复权日线数据。"""
        try:
            if is_hk_code(stock_code):
                code = stock_code.lower().replace('hk', '').lstrip('0')
                return ak.stock_hk_hist(
                    symbol=code,
                    period="daily",
                    start_date=start_date,
                    end_date=end_date,
                    adjust="",
                )
            if is_etf_code(stock_code):
                return ak.fund_etf_hist_em(
                    symbol=stock_code,
                    period="daily",
                    start_date=start_date,
                    end_date=end_date,
                    adjust="",
                )
            return ak.stock_zh_a_hist(
                symbol=stock_code,
                period="daily",
                start_date=start_date,
                end_date=end_date,
                adjust="",
            )
        except NETWORK_EXCEPTIONS:
            raise
        except Exception as e:
            _LOGGER.warning(f"[{self.name}] 获取 {stock_code} 未复权日线失败: {e}")
            return None

    def _fetch_stock_data(
        self,
        stock_code: str,
        start_date: str,
        end_date: str
    ) -> Optional[pd.DataFrame]:
        """获取 A 股数据，内部三源降级: 东方财富 → 腾讯 → 新浪"""
        methods = [
            (self._fetch_stock_data_em, "东方财富"),
            (self._fetch_stock_data_tx, "腾讯财经"),
            (self._fetch_stock_data_sina, "新浪财经"),
        ]
        last_error = None
        for fetch_fn, source_name in methods:
            try:
                _LOGGER.debug(f"[{self.name}] 尝试 {source_name} 获取 {stock_code}")
                df = fetch_fn(stock_code, start_date, end_date)
                if df is not None and not df.empty:
                    _LOGGER.debug(f"[{self.name}] {source_name} 获取 {stock_code} 成功: {len(df)} 行")
                    return df
            except NETWORK_EXCEPTIONS:
                raise
            except Exception as e:
                last_error = e
                if _detect_rate_limit(str(e)):
                    from ..exceptions import RateLimitError
                    raise RateLimitError(f"Akshare({source_name}) 限流: {e}") from e
                _LOGGER.warning(f"[{self.name}] {source_name} 获取 {stock_code} 失败: {e}")
        if last_error:
            raise DataFetchError(f"Akshare 所有渠道获取 {stock_code} 失败: {last_error}")
        return None

    def _fetch_stock_data_em(
        self,
        stock_code: str,
        start_date: str,
        end_date: str
    ) -> Optional[pd.DataFrame]:
        """获取 A 股数据 (东方财富: ak.stock_zh_a_hist)"""
        try:
            df = ak.stock_zh_a_hist(
                symbol=stock_code,
                period="daily",
                start_date=start_date,
                end_date=end_date,
                adjust="qfq"
            )
            return df
        except NETWORK_EXCEPTIONS:
            raise
        except Exception as e:
            if _detect_rate_limit(str(e)):
                raise
            _LOGGER.warning(f"[{self.name}] 东方财富获取 A 股数据失败: {e}")
            raise

    def _fetch_stock_data_sina(
        self,
        stock_code: str,
        start_date: str,
        end_date: str
    ) -> Optional[pd.DataFrame]:
        """获取 A 股数据 (新浪财经: ak.stock_zh_a_daily)"""
        symbol = _to_sina_tx_symbol(stock_code)
        self.random_sleep(1.0, 2.0)
        try:
            df = ak.stock_zh_a_daily(
                symbol=symbol,
                start_date=start_date.replace('-', ''),
                end_date=end_date.replace('-', ''),
                adjust="qfq"
            )
            if df is None or df.empty:
                return pd.DataFrame()
            rename_map = {
                'date': '日期', 'open': '开盘', 'high': '最高',
                'low': '最低', 'close': '收盘', 'volume': '成交量',
                'amount': '成交额',
            }
            df = df.rename(columns=rename_map)
            if '收盘' in df.columns:
                df['涨跌幅'] = df['收盘'].pct_change() * 100
                df['涨跌幅'] = df['涨跌幅'].fillna(0)
            return df
        except NETWORK_EXCEPTIONS:
            raise
        except Exception as e:
            _LOGGER.warning(f"[{self.name}] 新浪财经获取 A 股数据失败: {e}")
            raise

    def _fetch_stock_data_tx(
        self,
        stock_code: str,
        start_date: str,
        end_date: str
    ) -> Optional[pd.DataFrame]:
        """获取 A 股数据 (腾讯财经: ak.stock_zh_a_hist_tx)"""
        symbol = _to_sina_tx_symbol(stock_code)
        self.random_sleep(1.0, 2.0)
        try:
            df = ak.stock_zh_a_hist_tx(
                symbol=symbol,
                start_date=start_date.replace('-', ''),
                end_date=end_date.replace('-', ''),
                adjust="qfq"
            )
            if df is None or df.empty:
                return pd.DataFrame()
            rename_map = {
                'date': '日期', 'open': '开盘', 'high': '最高',
                'low': '最低', 'close': '收盘', 'volume': '成交量',
                'amount': '成交额', 'pct_chg': '涨跌幅',
            }
            df = df.rename(columns=rename_map)
            if '涨跌幅' not in df.columns and '收盘' in df.columns:
                df['涨跌幅'] = df['收盘'].pct_change() * 100
                df['涨跌幅'] = df['涨跌幅'].fillna(0)
            return df
        except NETWORK_EXCEPTIONS:
            raise
        except Exception as e:
            _LOGGER.warning(f"[{self.name}] 腾讯财经获取 A 股数据失败: {e}")
            raise

    def _fetch_etf_data(
        self,
        stock_code: str,
        start_date: str,
        end_date: str
    ) -> Optional[pd.DataFrame]:
        """获取 ETF 数据"""
        try:
            df = ak.fund_etf_hist_em(
                symbol=stock_code,
                period="daily",
                start_date=start_date,
                end_date=end_date,
                adjust="qfq"
            )
            return df
        except NETWORK_EXCEPTIONS:
            raise
        except Exception as e:
            _LOGGER.warning(f"[{self.name}] 获取 ETF 数据失败: {e}")
            return None

    def _fetch_hk_data(
        self,
        stock_code: str,
        start_date: str,
        end_date: str
    ) -> Optional[pd.DataFrame]:
        """获取港股数据"""
        try:
            normalized = normalize_hk_code(stock_code)
            code = normalized.lstrip('0') or normalized or str(stock_code).strip()
            df = ak.stock_hk_hist(
                symbol=code,
                period="daily",
                start_date=start_date,
                end_date=end_date,
                adjust="qfq"
            )
            return df
        except NETWORK_EXCEPTIONS:
            raise
        except Exception as e:
            _LOGGER.warning(f"[{self.name}] 获取港股数据失败: {e}")
            return None

    def _normalize_data(
        self,
        df: pd.DataFrame,
        stock_code: str
    ) -> pd.DataFrame:
        """标准化数据"""
        if df is None or df.empty:
            return pd.DataFrame()

        # Akshare 列名映射
        column_mapping = {
            '日期': 'date',
            '开盘': 'open',
            '收盘': 'close',
            '最高': 'high',
            '最低': 'low',
            '成交量': 'volume',
            '成交额': 'amount',
            '涨跌幅': 'pct_chg',
            '换手率': 'turnover_rate',
        }

        df = df.rename(columns=column_mapping)

        # 选择标准列
        result_cols = ['date', 'open', 'high', 'low', 'close', 'volume', 'amount', 'pct_chg']
        available_cols = [col for col in result_cols if col in df.columns]
        df = df[available_cols].copy()

        return df

    def get_bid_ask(self, stock_code: str) -> Optional[pd.DataFrame]:
        """获取个股五档盘口（ak.stock_bid_ask_em）"""
        self.random_sleep(0.3, 0.8)
        return self._call_ak_func_with_symbol_fallback(
            ak.stock_bid_ask_em,
            stock_code,
            include_no_arg=False,
            allow_unfiltered_no_arg=False,
        )

    def get_realtime_quote(
        self,
        stock_code: str,
        source: str = "em"
    ) -> Optional[UnifiedRealtimeQuote]:
        """
        获取实时行情

        Args:
            stock_code: 股票代码
            source: 数据源，可选 "em"(东财), "sina"(新浪), "tencent"(腾讯)。
                    A 股在默认模式下按 东财 → 腾讯 → 新浪 依次回退。
        """
        try:
            self.random_sleep(0.5, 1.5)

            if is_hk_code(stock_code):
                return self._get_hk_realtime_quote(stock_code)
            elif is_etf_code(stock_code):
                return self._get_etf_realtime_quote(stock_code)
            else:
                source = str(source or "em").strip().lower()
                if source == "sina":
                    methods = [
                        (self._get_stock_realtime_quote_sina, "新浪"),
                        (self._get_stock_realtime_quote_tencent, "腾讯"),
                    ]
                elif source == "tencent":
                    methods = [
                        (self._get_stock_realtime_quote_tencent, "腾讯"),
                        (self._get_stock_realtime_quote_sina, "新浪"),
                    ]
                else:
                    methods = [
                        (self._get_stock_realtime_quote_em, "东财"),
                        (self._get_stock_realtime_quote_tencent, "腾讯"),
                        (self._get_stock_realtime_quote_sina, "新浪"),
                    ]

                for fetch_fn, source_name in methods:
                    quote = fetch_fn(stock_code)
                    if quote is not None and quote.has_basic_data():
                        return quote
                    _LOGGER.debug(
                        f"[{self.name}] {source_name} 实时行情为空或缺少基础字段，继续回退: {stock_code}"
                    )
                return None

        except NETWORK_EXCEPTIONS:
            raise
        except Exception as e:
            _LOGGER.warning(f"[{self.name}] 获取 {stock_code} 实时行情失败: {e}")
            return None

    def _get_stock_realtime_quote_em(self, stock_code: str) -> Optional[UnifiedRealtimeQuote]:
        """从东财获取 A 股实时行情（完整数据）"""
        try:
            df = ak.stock_zh_a_spot_em()
            if df is None or df.empty:
                return None

            # 查找股票
            row = df[df['代码'] == stock_code]
            if row.empty:
                return None

            row = row.iloc[0]
            quote = UnifiedRealtimeQuote(
                code=stock_code,
                name=row.get('名称'),
                source=RealtimeSource.AKSHARE_EM,
                price=safe_float(row.get('最新价')),
                change_pct=safe_float(row.get('涨跌幅')),
                change_amount=safe_float(row.get('涨跌额')),
                volume=safe_float(row.get('成交量')),
                amount=safe_float(row.get('成交额')),
                volume_ratio=safe_float(row.get('量比')),
                turnover_rate=safe_float(row.get('换手率')),
                amplitude=safe_float(row.get('振幅')),
                open_price=safe_float(row.get('今开')),
                high=safe_float(row.get('最高')),
                low=safe_float(row.get('最低')),
                pre_close=safe_float(row.get('昨收')),
                pe_ratio=safe_float(row.get('市盈率-动态')),
                pb_ratio=safe_float(row.get('市净率')),
                total_mv=safe_float(row.get('总市值')),
                circ_mv=safe_float(row.get('流通市值')),
                change_60d=safe_float(row.get('60日涨跌幅')),
                high_52w=safe_float(row.get('52周最高')),
                low_52w=safe_float(row.get('52周最低')),
            )

            return quote

        except NETWORK_EXCEPTIONS:
            raise
        except Exception as e:
            _LOGGER.warning(f"[{self.name}] 东财实时行情获取失败: {e}")
            return None

    def _get_stock_realtime_quote_sina(self, stock_code: str) -> Optional[UnifiedRealtimeQuote]:
        """从新浪获取 A 股实时行情（直连单股查询，轻量级）

        接口: http://hq.sinajs.cn/list=sh600519
        优点: 单股查询，负载小，速度快
        缺点: 数据字段较少，无量比/PE/PB等
        """
        symbol = _to_sina_tx_symbol(stock_code)
        url = f"http://{SINA_REALTIME_ENDPOINT}={symbol}"
        try:
            headers = {
                'Referer': 'http://finance.sina.com.cn',
                'User-Agent': self.get_random_user_agent(),
            }
            self.random_sleep(0.3, 1.0)
            response = requests.get(url, headers=headers, timeout=10)
            response.encoding = 'gbk'

            if response.status_code != 200:
                _LOGGER.warning(f"[{self.name}] 新浪直连 HTTP {response.status_code}")
                return None

            content = response.text.strip()
            if '=""' in content or not content:
                return None

            data_start = content.find('"')
            data_end = content.rfind('"')
            if data_start == -1 or data_end == -1:
                return None

            fields = content[data_start + 1:data_end].split(',')
            if len(fields) < 32:
                _LOGGER.warning(f"[{self.name}] 新浪字段不足: {len(fields)}")
                return None

            # 字段: 0:名称 1:今开 2:昨收 3:最新价 4:最高 5:最低
            #        8:成交量(股) 9:成交额(元)
            price = safe_float(fields[3])
            pre_close = safe_float(fields[2])
            change_pct = None
            change_amount = None
            if price and pre_close and pre_close > 0:
                change_amount = price - pre_close
                change_pct = (change_amount / pre_close) * 100

            return UnifiedRealtimeQuote(
                code=stock_code,
                name=fields[0],
                source=RealtimeSource.AKSHARE_SINA,
                price=price,
                change_pct=change_pct,
                change_amount=change_amount,
                volume=safe_int(fields[8]),
                amount=safe_float(fields[9]),
                open_price=safe_float(fields[1]),
                high=safe_float(fields[4]),
                low=safe_float(fields[5]),
                pre_close=pre_close,
            )

        except NETWORK_EXCEPTIONS:
            raise
        except Exception as e:
            _LOGGER.warning(f"[{self.name}] 新浪直连实时行情失败: {e}")
            return None

    def _get_stock_realtime_quote_tencent(self, stock_code: str) -> Optional[UnifiedRealtimeQuote]:
        """从腾讯获取 A 股实时行情（直连单股查询，含换手率/PE/PB/市值）

        接口: http://qt.gtimg.cn/q=sh600519
        优点: 单股查询，负载小，数据较丰富（含量比/PE/PB/市值）
        缺点: 字段解析依赖位置索引
        """
        symbol = _to_sina_tx_symbol(stock_code)
        url = f"http://{TENCENT_REALTIME_ENDPOINT}={symbol}"
        try:
            headers = {
                'Referer': 'http://finance.qq.com',
                'User-Agent': self.get_random_user_agent(),
            }
            self.random_sleep(0.3, 1.0)
            response = requests.get(url, headers=headers, timeout=10)
            response.encoding = 'gbk'

            if response.status_code != 200:
                _LOGGER.warning(f"[{self.name}] 腾讯直连 HTTP {response.status_code}")
                return None

            content = response.text.strip()
            if '=""' in content or not content:
                return None

            data_start = content.find('"')
            data_end = content.rfind('"')
            if data_start == -1 or data_end == -1:
                return None

            fields = content[data_start + 1:data_end].split('~')
            if len(fields) < 45:
                _LOGGER.warning(f"[{self.name}] 腾讯字段不足: {len(fields)}")
                return None

            # 腾讯字段索引:
            # 1:名称 3:最新价 4:昨收 5:今开 6:成交量(手)
            # 31:涨跌额 32:涨跌幅% 33:最高 34:最低
            # 38:换手率% 39:市盈率 43:振幅%
            # 44:流通市值(亿) 45:总市值(亿) 46:市净率 49:量比
            vol_raw = safe_int(fields[6]) if len(fields) > 6 else None
            volume = vol_raw * 100 if vol_raw else None  # 手 → 股

            circ_mv_raw = safe_float(fields[44]) if len(fields) > 44 else None
            total_mv_raw = safe_float(fields[45]) if len(fields) > 45 else None

            return UnifiedRealtimeQuote(
                code=stock_code,
                name=fields[1] if len(fields) > 1 else "",
                source=RealtimeSource.TENCENT,
                price=safe_float(fields[3]),
                change_pct=safe_float(fields[32]) if len(fields) > 32 else None,
                change_amount=safe_float(fields[31]) if len(fields) > 31 else None,
                volume=volume,
                open_price=safe_float(fields[5]) if len(fields) > 5 else None,
                high=safe_float(fields[33]) if len(fields) > 33 else None,
                low=safe_float(fields[34]) if len(fields) > 34 else None,
                pre_close=safe_float(fields[4]) if len(fields) > 4 else None,
                turnover_rate=safe_float(fields[38]) if len(fields) > 38 else None,
                amplitude=safe_float(fields[43]) if len(fields) > 43 else None,
                volume_ratio=safe_float(fields[49]) if len(fields) > 49 else None,
                pe_ratio=safe_float(fields[39]) if len(fields) > 39 else None,
                pb_ratio=safe_float(fields[46]) if len(fields) > 46 else None,
                circ_mv=circ_mv_raw * 1e8 if circ_mv_raw else None,   # 亿 → 元
                total_mv=total_mv_raw * 1e8 if total_mv_raw else None,  # 亿 → 元
            )

        except NETWORK_EXCEPTIONS:
            raise
        except Exception as e:
            _LOGGER.warning(f"[{self.name}] 腾讯直连实时行情失败: {e}")
            return None

    def _get_etf_realtime_quote(self, stock_code: str) -> Optional[UnifiedRealtimeQuote]:
        """获取 ETF 实时行情"""
        try:
            df = ak.fund_etf_spot_em()
            if df is None or df.empty:
                return None

            row = df[df['代码'] == stock_code]
            if row.empty:
                return None

            row = row.iloc[0]
            quote = UnifiedRealtimeQuote(
                code=stock_code,
                name=row.get('名称'),
                source=RealtimeSource.AKSHARE_EM,
                price=safe_float(row.get('最新价')),
                change_pct=safe_float(row.get('涨跌幅')),
                volume=safe_float(row.get('成交量')),
                amount=safe_float(row.get('成交额')),
                open_price=safe_float(row.get('今开')),
                high=safe_float(row.get('最高')),
                low=safe_float(row.get('最低')),
                pre_close=safe_float(row.get('昨收')),
            )

            return quote

        except NETWORK_EXCEPTIONS:
            raise
        except Exception as e:
            _LOGGER.warning(f"[{self.name}] ETF 实时行情获取失败: {e}")
            return None

    def _get_hk_realtime_quote(self, stock_code: str) -> Optional[UnifiedRealtimeQuote]:
        """获取港股实时行情"""
        try:
            df = ak.stock_hk_spot_em()
            if df is None or df.empty:
                return None

            normalized = normalize_hk_code(stock_code)
            code = normalized.lstrip('0') or normalized or str(stock_code).strip()
            row = df[df['代码'].astype(str) == code]
            if row.empty and normalized:
                row = df[df['代码'].astype(str).str.zfill(5) == normalized]
            if row.empty:
                return None

            row = row.iloc[0]
            quote = UnifiedRealtimeQuote(
                code=stock_code,
                name=row.get('名称'),
                source=RealtimeSource.AKSHARE_EM,
                price=safe_float(row.get('最新价')),
                change_pct=safe_float(row.get('涨跌幅')),
                volume=safe_float(row.get('成交量')),
                amount=safe_float(row.get('成交额')),
                open_price=safe_float(row.get('今开')),
                high=safe_float(row.get('最高')),
                low=safe_float(row.get('最低')),
                pre_close=safe_float(row.get('昨收')),
            )

            return quote

        except NETWORK_EXCEPTIONS:
            raise
        except Exception as e:
            _LOGGER.warning(f"[{self.name}] 港股实时行情获取失败: {e}")
            return None

    def get_batch_realtime_quotes(
        self,
        stock_codes: List[str]
    ) -> Dict[str, UnifiedRealtimeQuote]:
        """
        批量获取实时行情，按类型分组后批量查询

        Args:
            stock_codes: 股票代码列表

        Returns:
            股票代码 -> UnifiedRealtimeQuote 的映射
        """
        result: Dict[str, UnifiedRealtimeQuote] = {}

        # 按类型分组
        etf_codes = [c for c in stock_codes if is_etf_code(c)]
        hk_codes = [c for c in stock_codes if is_hk_code(c)]
        a_codes = [c for c in stock_codes if not is_etf_code(c) and not is_hk_code(c)]

        # 批量获取 ETF
        if etf_codes:
            try:
                self.random_sleep(0.5, 1.5)
                df = ak.fund_etf_spot_em()
                if df is not None and not df.empty:
                    for code in etf_codes:
                        row = df[df['代码'] == code]
                        if not row.empty:
                            row = row.iloc[0]
                            quote = UnifiedRealtimeQuote(
                                code=code,
                                name=row.get('名称'),
                                source=RealtimeSource.AKSHARE_EM,
                                price=safe_float(row.get('最新价')),
                                change_pct=safe_float(row.get('涨跌幅')),
                                volume=safe_float(row.get('成交量')),
                                amount=safe_float(row.get('成交额')),
                                open_price=safe_float(row.get('开盘价')),
                                high=safe_float(row.get('最高价')),
                                low=safe_float(row.get('最低价')),
                                pre_close=safe_float(row.get('昨收')),
                            )
                            result[code] = quote
            except NETWORK_EXCEPTIONS:
                raise
            except Exception as e:
                _LOGGER.warning(f"[{self.name}] 批量获取 ETF 实时行情失败: {e}")

        # 批量获取 A 股
        if a_codes:
            try:
                self.random_sleep(0.5, 1.5)
                df = ak.stock_zh_a_spot_em()
                if df is not None and not df.empty:
                    for code in a_codes:
                        row = df[df['代码'] == code]
                        if not row.empty:
                            row = row.iloc[0]
                            quote = UnifiedRealtimeQuote(
                                code=code,
                                name=row.get('名称'),
                                source=RealtimeSource.AKSHARE_EM,
                                price=safe_float(row.get('最新价')),
                                change_pct=safe_float(row.get('涨跌幅')),
                                change_amount=safe_float(row.get('涨跌额')),
                                volume=safe_float(row.get('成交量')),
                                amount=safe_float(row.get('成交额')),
                                volume_ratio=safe_float(row.get('量比')),
                                turnover_rate=safe_float(row.get('换手率')),
                                amplitude=safe_float(row.get('振幅')),
                                open_price=safe_float(row.get('今开')),
                                high=safe_float(row.get('最高')),
                                low=safe_float(row.get('最低')),
                                pre_close=safe_float(row.get('昨收')),
                                pe_ratio=safe_float(row.get('市盈率-动态')),
                                pb_ratio=safe_float(row.get('市净率')),
                                total_mv=safe_float(row.get('总市值')),
                                circ_mv=safe_float(row.get('流通市值')),
                            )
                            result[code] = quote
            except NETWORK_EXCEPTIONS:
                raise
            except Exception as e:
                _LOGGER.warning(f"[{self.name}] 批量获取 A股 实时行情失败: {e}")

        # 批量获取港股
        if hk_codes:
            try:
                self.random_sleep(0.5, 1.5)
                df = ak.stock_hk_spot_em()
                if df is not None and not df.empty:
                    for code in hk_codes:
                        normalized = normalize_hk_code(code)
                        clean_code = normalized.lstrip('0') or normalized or str(code).strip()
                        row = df[df['代码'].astype(str) == clean_code]
                        if row.empty and normalized:
                            row = df[df['代码'].astype(str).str.zfill(5) == normalized]
                        if not row.empty:
                            row = row.iloc[0]
                            quote = UnifiedRealtimeQuote(
                                code=code,
                                name=row.get('名称'),
                                source=RealtimeSource.AKSHARE_EM,
                                price=safe_float(row.get('最新价')),
                                change_pct=safe_float(row.get('涨跌幅')),
                                volume=safe_float(row.get('成交量')),
                                amount=safe_float(row.get('成交额')),
                                open_price=safe_float(row.get('今开')),
                                high=safe_float(row.get('最高')),
                                low=safe_float(row.get('最低')),
                                pre_close=safe_float(row.get('昨收')),
                            )
                            result[code] = quote
            except NETWORK_EXCEPTIONS:
                raise
            except Exception as e:
                _LOGGER.warning(f"[{self.name}] 批量获取港股实时行情失败: {e}")

        return result

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=3, min=3, max=30),
        retry=retry_if_exception_type(NETWORK_EXCEPTIONS),
        reraise=True
    )
    def get_chip_distribution(self, stock_code: str) -> Optional[ChipDistribution]:
        """获取筹码分布（使用 @retry 装饰器）"""
        # 增加延迟避免反爬虫
        self.random_sleep(2.0, 4.0)

        try:
            df = ak.stock_cyq_em(symbol=stock_code)
            if df is None or df.empty:
                return None

            # 取最新一天数据
            latest = df.iloc[-1]

            chip = ChipDistribution(
                code=stock_code,
                date=str(latest.get('日期', '')),
                source="akshare",
                profit_ratio=safe_float(latest.get('获利比例')),
                avg_cost=safe_float(latest.get('平均成本')),
                cost_90_low=safe_float(latest.get('90成本-低')),
                cost_90_high=safe_float(latest.get('90成本-高')),
                concentration_90=safe_float(latest.get('90集中度')),
                cost_70_low=safe_float(latest.get('70成本-低')),
                cost_70_high=safe_float(latest.get('70成本-高')),
                concentration_70=safe_float(latest.get('70集中度')),
            )

            return chip
        except NETWORK_EXCEPTIONS:
            raise
        except Exception as e:
            _LOGGER.warning(f"[{self.name}] 获取筹码分布失败: {e}")
            return None

    def get_fund_flow(self, stock_code: str) -> Optional[pd.DataFrame]:
        """获取资金流向（优先个股明细，失败后回退主力资金接口）"""
        try:
            self.random_sleep(1.0, 2.0)

            df = None
            for market in ("sh", "sz"):
                try:
                    df = ak.stock_individual_fund_flow(stock=stock_code, market=market)
                    if df is not None and not df.empty:
                        return df
                except Exception:
                    continue

            try:
                fallback_df = self._call_ak_func_with_symbol_fallback(
                    ak.stock_main_fund_flow,
                    stock_code,
                    include_no_arg=True,
                    allow_unfiltered_no_arg=False,
                )
                if fallback_df is not None and not fallback_df.empty:
                    return fallback_df
            except NETWORK_EXCEPTIONS:
                raise
            except Exception as exc:
                _LOGGER.debug(f"[{self.name}] stock_main_fund_flow 回退失败: {exc}")

            return df
        except NETWORK_EXCEPTIONS:
            raise
        except Exception as e:
            _LOGGER.warning(f"[{self.name}] 获取资金流向失败: {e}")
            return None

    def get_belong_board(self, stock_code: str) -> Optional[pd.DataFrame]:
        """获取所属板块（优先个股板块接口，失败后回退行业板块列表）"""
        try:
            self.random_sleep(1.0, 2.0)

            try:
                sector_df = self._call_ak_func_with_symbol_fallback(
                    ak.stock_sector_spot,
                    stock_code,
                    include_no_arg=True,
                    allow_unfiltered_no_arg=False,
                )
                if sector_df is not None and not sector_df.empty:
                    return sector_df
            except NETWORK_EXCEPTIONS:
                raise
            except Exception as exc:
                _LOGGER.debug(f"[{self.name}] stock_sector_spot 获取所属板块失败: {exc}")

            industry_boards = ak.stock_board_industry_name_em()
            if industry_boards is not None and not industry_boards.empty:
                industry_boards.sort_values("涨跌幅", ascending=False, inplace=True)
                top_bottom = pd.concat([industry_boards.head(15), industry_boards.tail(15)])
                return top_bottom

            return None
        except NETWORK_EXCEPTIONS:
            raise
        except Exception as e:
            _LOGGER.warning(f"[{self.name}] 获取板块信息失败: {e}")
            return None

    @retry(
        stop=stop_after_attempt(2),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type(NETWORK_EXCEPTIONS),
        reraise=True
    )
    def get_board_cons(self, board_name: str, board_type: str = "industry") -> Optional[pd.DataFrame]:
        """获取板块成分股（先查找精确板块名再获取成分股，带重试机制）"""
        self.random_sleep(0.3, 0.8)

        try:
            # 1. 先获取板块列表，查找精确匹配的板块名
            if board_type == "concept":
                boards = ak.stock_board_concept_name_em()
            else:
                boards = ak.stock_board_industry_name_em()
        except Exception as e:
            # 将任何连接错误转换为 NETWORK_EXCEPTIONS 中的类型
            if isinstance(e, NETWORK_EXCEPTIONS):
                raise
            error_str = str(e).lower()
            if any(kw in error_str for kw in ('connection', 'timeout', 'disconnected', 'remote', 'refused', 'reset')):
                raise ConnectionError(f"获取板块列表失败: {e}") from e
            raise

        if boards is None or boards.empty:
            _LOGGER.warning(f"[{self.name}] 获取板块列表为空")
            return None

        # 2. 精确匹配或模糊匹配板块名
        exact_match = boards[boards["板块名称"] == board_name]
        if exact_match.empty:
            # 尝试模糊匹配
            fuzzy_match = boards[boards["板块名称"].str.contains(board_name, na=False)]
            if fuzzy_match.empty:
                _LOGGER.warning(f"[{self.name}] 未找到板块: {board_name}")
                return None
            matched_name = fuzzy_match.iloc[0]["板块名称"]
            _LOGGER.info(f"[{self.name}] 模糊匹配板块: {board_name} -> {matched_name}")
        else:
            matched_name = exact_match.iloc[0]["板块名称"]

        # 3. 使用精确板块名获取成分股
        self.random_sleep(0.3, 0.8)
        try:
            if board_type == "concept":
                df = ak.stock_board_concept_cons_em(symbol=matched_name)
            else:
                df = ak.stock_board_industry_cons_em(symbol=matched_name)
        except Exception as e:
            # 将任何连接错误转换为 NETWORK_EXCEPTIONS 中的类型
            if isinstance(e, NETWORK_EXCEPTIONS):
                raise
            error_str = str(e).lower()
            if any(kw in error_str for kw in ('connection', 'timeout', 'disconnected', 'remote', 'refused', 'reset')):
                raise ConnectionError(f"获取成分股失败: {e}") from e
            raise

        return df

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=2, max=30),
        retry=retry_if_exception_type(NETWORK_EXCEPTIONS),
        reraise=True
    )
    def get_billboard(self, days: str = "5") -> Optional[pd.DataFrame]:
        """获取龙虎榜统计（带重试机制）"""
        self.random_sleep(1.0, 2.0)

        df = ak.stock_lhb_ggtj_sina(symbol=days)
        return df

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=2, max=30),
        retry=retry_if_exception_type(NETWORK_EXCEPTIONS),
        reraise=True
    )
    def get_margin_detail(self, stock_code: str, market: str = "sh") -> Optional[pd.DataFrame]:
        """
        获取融资融券明细（带重试机制）

        Args:
            stock_code: 股票代码
            market: 市场 'sh'(上交所) 或 'sz'(深交所)

        Returns:
            DataFrame 或 None
        """
        self.random_sleep(1.0, 2.0)

        try:
            if market == "sh":
                df = ak.stock_margin_detail_sse(date="")
                if df is not None and not df.empty and stock_code:
                    if "标的证券代码" in df.columns:
                        df = df[df["标的证券代码"].astype(str).str.contains(stock_code)]
                return df
            else:
                df = ak.stock_margin_detail_szse(date="")
                if df is not None and not df.empty and stock_code:
                    if "证券代码" in df.columns:
                        df = df[df["证券代码"].astype(str).str.contains(stock_code)]
                return df
        except TypeError as e:
            # akshare深交所接口bug: Expected file path name or file-like object, got <class 'bytes'> type
            _LOGGER.warning(f"[{self.name}] akshare深交所融资融券接口异常（可能是库版本bug）: {e}")
            return None

    def get_margin_ratio(self, stock_code: str) -> Optional[pd.DataFrame]:
        """获取融资融券比例（备用数据源）"""
        try:
            self.random_sleep(0.5, 1.5)
            df = ak.stock_margin_ratio_pa()
            if df is not None and not df.empty:
                if "证券代码" in df.columns:
                    filtered = df[df["证券代码"].astype(str).str.contains(stock_code)]
                    if not filtered.empty:
                        return filtered
            return None
        except Exception as e:
            _LOGGER.warning(f"[{self.name}] 获取融资融券比例失败: {e}")
            return None

    @staticmethod
    def _is_empty_industry_pe_response_error(exc: Exception) -> bool:
        """判断行业PE接口是否返回了“有响应但无数据”的异常。"""
        message = str(exc)
        return (
            isinstance(exc, ValueError) and "Length mismatch" in message
        ) or (
            isinstance(exc, KeyError) and "records" in message
        )

    def _get_industry_pe_candidate_dates(self, date: str, max_candidates: int = 3) -> List[str]:
        """获取行业PE可回退的交易日候选列表。"""
        requested = str(date or "").strip()
        if requested:
            try:
                target_date = datetime.strptime(requested, "%Y%m%d").date()
            except ValueError:
                return [requested]
        else:
            target_date = datetime.now().date()

        try:
            trade_dates = ak.tool_trade_date_hist_sina()
        except Exception as exc:
            _LOGGER.warning(f"[{self.name}] 获取交易日历失败，行业PE仅尝试原始日期 {requested or target_date.strftime('%Y%m%d')}: {exc}")
            return [requested or target_date.strftime("%Y%m%d")]

        if trade_dates is None or trade_dates.empty or "trade_date" not in trade_dates.columns:
            return [requested or target_date.strftime("%Y%m%d")]

        normalized_dates = pd.to_datetime(trade_dates["trade_date"], errors="coerce").dropna().dt.date
        candidates = [
            d.strftime("%Y%m%d")
            for d in sorted({d for d in normalized_dates if d <= target_date}, reverse=True)[:max_candidates]
        ]
        return candidates or [requested or target_date.strftime("%Y%m%d")]

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=2, max=30),
        retry=retry_if_exception_type(NETWORK_EXCEPTIONS),
        reraise=True
    )
    def get_industry_pe(self, date: str = "") -> Optional[pd.DataFrame]:
        """获取行业PE数据（巨潮资讯）"""
        self.random_sleep(0.5, 1.5)
        requested_date = str(date or "").strip()
        candidate_dates = self._get_industry_pe_candidate_dates(requested_date)
        last_error: Optional[Exception] = None

        for candidate_date in candidate_dates:
            try:
                df = ak.stock_industry_pe_ratio_cninfo(symbol="证监会行业分类", date=candidate_date)
            except Exception as exc:
                if not self._is_empty_industry_pe_response_error(exc):
                    raise
                last_error = exc
                _LOGGER.info(
                    "[%s] 行业PE数据暂不可用，尝试更早交易日: requested=%s, candidate=%s, error=%s",
                    self.name,
                    requested_date or "(latest)",
                    candidate_date,
                    exc,
                )
                continue

            if df is None or df.empty:
                last_error = ValueError(f"行业PE返回空数据: {candidate_date}")
                continue

            df = df.copy()
            df.attrs["requested_date"] = requested_date or candidate_date
            df.attrs["data_date"] = candidate_date
            if candidate_date != (requested_date or candidate_date):
                _LOGGER.info(
                    "[%s] 行业PE数据回退到最近可用交易日: requested=%s, used=%s",
                    self.name,
                    requested_date or "(latest)",
                    candidate_date,
                )
            return df

        if last_error is not None and len(candidate_dates) > 1:
            _LOGGER.warning(
                "[%s] 行业PE数据在最近 %s 个交易日均不可用: requested=%s, last_error=%s",
                self.name,
                len(candidate_dates),
                requested_date or "(latest)",
                last_error,
            )
            return None

        if last_error is not None:
            raise last_error
        return None

    # ---- 东财全市场 A 股行情（直接 HTTP，逐页重试） ----

    _EM_SPOT_URL = "https://82.push2.eastmoney.com/api/qt/clist/get"
    _EM_SPOT_PARAMS = {
        "po": "1",
        "np": "1",
        "ut": "bd1d9ddb04089700cf9c27f6f7426281",
        "fltt": "2",
        "invt": "2",
        "fid": "f12",
        "fs": "m:0 t:6,m:0 t:80,m:1 t:2,m:1 t:23,m:0 t:81 s:2048",
        "fields": (
            "f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f12,f13,f14,f15,f16,f17,f18,"
            "f20,f21,f23,f24,f25,f22,f11,f62,f128,f136,f115,f152"
        ),
    }
    _EM_SPOT_PAGE_SIZE = 100
    _EM_SPOT_COLUMNS = [
        "序号", "_", "最新价", "涨跌幅", "涨跌额", "成交量", "成交额",
        "振幅", "换手率", "市盈率-动态", "量比", "5分钟涨跌", "代码", "_",
        "名称", "最高", "最低", "今开", "昨收", "总市值", "流通市值",
        "涨速", "市净率", "60日涨跌幅", "年初至今涨跌幅",
        "-", "-", "-", "-", "-", "-", "-",
    ]
    _EM_SPOT_OUTPUT_COLUMNS = [
        "序号", "代码", "名称", "最新价", "涨跌幅", "涨跌额", "成交量", "成交额",
        "振幅", "最高", "最低", "今开", "昨收", "量比", "换手率",
        "市盈率-动态", "市净率", "总市值", "流通市值", "涨速",
        "5分钟涨跌", "60日涨跌幅", "年初至今涨跌幅",
    ]
    _EM_SPOT_NUMERIC_COLUMNS = [
        "最新价", "涨跌幅", "涨跌额", "成交量", "成交额", "振幅", "最高", "最低",
        "今开", "昨收", "量比", "换手率", "市盈率-动态", "市净率", "总市值",
        "流通市值", "涨速", "5分钟涨跌", "60日涨跌幅", "年初至今涨跌幅",
    ]

    def _fetch_em_spot_page(
        self, page: int, page_size: int, timeout: int = 15, max_retries: int = 3
    ) -> Optional[dict]:
        """请求东财全市场行情单页数据，带重试。"""
        params = {**self._EM_SPOT_PARAMS, "pn": str(page), "pz": str(page_size)}
        last_exc = None
        for attempt in range(max_retries):
            try:
                resp = requests.get(
                    self._EM_SPOT_URL,
                    params=params,
                    timeout=timeout,
                    headers={"User-Agent": self.get_random_user_agent()},
                )
                resp.raise_for_status()
                data = resp.json()
                if data.get("data") and data["data"].get("diff"):
                    return data["data"]
                return None
            except Exception as e:
                last_exc = e
                if attempt < max_retries - 1:
                    delay = (1.0 * (2 ** attempt)) + random.uniform(0.5, 1.5)
                    time.sleep(delay)
        _LOGGER.warning(
            "[%s] 东财行情第 %s 页 %s 次重试均失败: %s",
            self.name, page, max_retries, last_exc,
        )
        return None

    def _build_em_spot_df(self, pages_data: list[dict]) -> Optional[pd.DataFrame]:
        """将多页原始数据合并为标准化 DataFrame。"""
        frames = [pd.DataFrame(p["diff"]) for p in pages_data]
        df = pd.concat(frames, ignore_index=True)
        if df.empty:
            return None
        df["f3"] = pd.to_numeric(df["f3"], errors="coerce")
        df.sort_values(by=["f3"], ascending=False, inplace=True, ignore_index=True)
        df.reset_index(inplace=True)
        df["index"] = df["index"].astype(int) + 1
        df.columns = self._EM_SPOT_COLUMNS[: len(df.columns)]
        df = df[[c for c in self._EM_SPOT_OUTPUT_COLUMNS if c in df.columns]]
        for col in self._EM_SPOT_NUMERIC_COLUMNS:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
        return df

    def get_a_stock_spot(self) -> Optional[pd.DataFrame]:
        """获取全市场A股行情快照，直接请求东财接口，逐页重试。"""
        self.random_sleep(0.5, 1.5)
        page_size = self._EM_SPOT_PAGE_SIZE

        # 第一页：获取总数
        first_page = self._fetch_em_spot_page(1, page_size)
        if first_page is None:
            _LOGGER.warning("[%s] 东财全市场行情第 1 页获取失败", self.name)
            return None

        total = first_page.get("total", 0)
        total_pages = -(-total // page_size)  # ceil division
        _LOGGER.debug(
            "[%s] 东财全市场行情: total=%s, page_size=%s, pages=%s",
            self.name, total, page_size, total_pages,
        )

        pages_data = [first_page]
        failed_pages = []

        # 逐页拉取
        for page in range(2, total_pages + 1):
            time.sleep(random.uniform(0.3, 0.8))
            data = self._fetch_em_spot_page(page, page_size)
            if data is not None:
                pages_data.append(data)
            else:
                failed_pages.append(page)

        # 失败页重试：最多 4 轮，间隔递增
        retry_delays = [
            (1.0, 3.0),   # 第 1 轮
            (2.0, 5.0),   # 第 2 轮
            (3.0, 8.0),   # 第 3 轮
            (5.0, 10.0),  # 第 4 轮
        ]
        for round_idx, delay_range in enumerate(retry_delays, 1):
            if not failed_pages:
                break
            _LOGGER.info(
                "[%s] 东财行情重试失败页(第%s轮): %s",
                self.name, round_idx, failed_pages,
            )
            still_failed = []
            for page in failed_pages:
                time.sleep(random.uniform(*delay_range))
                data = self._fetch_em_spot_page(page, page_size, max_retries=2)
                if data is not None:
                    pages_data.append(data)
                else:
                    still_failed.append(page)
            failed_pages = still_failed

        if failed_pages:
            _LOGGER.warning(
                "[%s] 东财行情最终失败页: %s/%s",
                self.name, failed_pages, total_pages,
            )

        df = self._build_em_spot_df(pages_data)
        if df is not None and not df.empty:
            is_complete = len(failed_pages) == 0
            df.attrs["spot_complete"] = is_complete
            _LOGGER.debug(
                "[%s] 东财全市场行情完成: rows=%s, complete=%s",
                self.name, len(df), is_complete,
            )
        return df

    @retry(
        stop=stop_after_attempt(2),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type(NETWORK_EXCEPTIONS),
        reraise=True
    )
    def get_dividend_history(self, symbol: str) -> Optional[pd.DataFrame]:
        """获取分红历史"""
        self.random_sleep(0.3, 0.8)
        df = ak.stock_history_dividend_detail(symbol=symbol, indicator="分红")
        return df

    @retry(
        stop=stop_after_attempt(2),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type(NETWORK_EXCEPTIONS),
        reraise=True
    )
    def get_fund_holder(self, symbol: str, date: str = "") -> Optional[pd.DataFrame]:
        """获取基金持仓（全市场基金重仓股，忽略 symbol 参数）

        注意: ak.stock_report_fund_hold 返回全市场基金持仓汇总，不支持按个股查询。
        symbol 参数由 BaseFetcher 接口要求传入但此处不使用。
        """
        self.random_sleep(0.3, 0.8)
        if not isinstance(date, str) or not date:
            today = datetime.now()
            quarter_ends = [
                f"{today.year}0331", f"{today.year}0630",
                f"{today.year}0930", f"{today.year}1231",
                f"{today.year - 1}0930", f"{today.year - 1}1231",
            ]
            date = max(q for q in quarter_ends if q <= today.strftime("%Y%m%d"))
        df = ak.stock_report_fund_hold(symbol="基金持仓", date=date)
        return df

    @retry(
        stop=stop_after_attempt(2),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type(NETWORK_EXCEPTIONS),
        reraise=True
    )
    def get_top10_holders(self, symbol: str, holder_type: str = "main") -> Optional[pd.DataFrame]:
        """获取十大股东"""
        self.random_sleep(0.3, 0.8)
        if holder_type == "circulate":
            df = ak.stock_circulate_stock_holder(symbol=symbol)
        else:
            df = ak.stock_main_stock_holder(stock=symbol)
        return df


    @retry(
        stop=stop_after_attempt(2),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type(NETWORK_EXCEPTIONS),
        reraise=True
    )
    def get_earnings_forecast(self, symbol: str) -> Optional[pd.DataFrame]:
        """获取业绩预告（ak.stock_yjyg_em）"""
        self.random_sleep(0.3, 0.8)
        return self._call_ak_func_with_symbol_fallback(
            ak.stock_yjyg_em,
            symbol,
            include_no_arg=False,
            allow_unfiltered_no_arg=False,
            require_stock_match=True,
        )

    @retry(
        stop=stop_after_attempt(2),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type(NETWORK_EXCEPTIONS),
        reraise=True
    )
    def get_earnings_report(self, symbol: str) -> Optional[pd.DataFrame]:
        """获取业绩快报/业绩报表（ak.stock_yjbb_em）"""
        self.random_sleep(0.3, 0.8)
        return self._call_ak_func_with_symbol_fallback(
            ak.stock_yjbb_em,
            symbol,
            include_no_arg=False,
            allow_unfiltered_no_arg=False,
            require_stock_match=True,
        )

    @retry(
        stop=stop_after_attempt(2),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type(NETWORK_EXCEPTIONS),
        reraise=True
    )
    def get_earnings_express(self, symbol: str) -> Optional[pd.DataFrame]:
        """获取业绩快报（ak.stock_yjkb_em）"""
        self.random_sleep(0.3, 0.8)
        return self._call_ak_func_with_symbol_fallback(
            ak.stock_yjkb_em,
            symbol,
            include_no_arg=False,
            allow_unfiltered_no_arg=False,
            require_stock_match=True,
        )

    @retry(
        stop=stop_after_attempt(2),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type(NETWORK_EXCEPTIONS),
        reraise=True
    )
    def get_dividend_plan(self, symbol: str) -> Optional[pd.DataFrame]:
        """获取分红送配方案（ak.stock_fhps_detail_em）"""
        self.random_sleep(0.3, 0.8)
        return self._call_ak_func_with_symbol_fallback(
            ak.stock_fhps_detail_em,
            symbol,
            include_no_arg=False,
            allow_unfiltered_no_arg=False,
            require_stock_match=True,
        )

    @retry(
        stop=stop_after_attempt(2),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type(NETWORK_EXCEPTIONS),
        reraise=True
    )
    def get_dividend_cninfo(self, symbol: str) -> Optional[pd.DataFrame]:
        """获取分红实施明细（ak.stock_dividend_cninfo）"""
        self.random_sleep(0.3, 0.8)
        return self._call_ak_func_with_symbol_fallback(
            ak.stock_dividend_cninfo,
            symbol,
            include_no_arg=False,
            allow_unfiltered_no_arg=False,
            require_stock_match=True,
        )


    @retry(
        stop=stop_after_attempt(2),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type(NETWORK_EXCEPTIONS),
        reraise=True
    )
    def get_cctv_news(self, date: str = "") -> Optional[pd.DataFrame]:
        """获取新闻联播文字稿（ak.news_cctv）"""
        self.random_sleep(0.3, 0.8)
        if isinstance(date, str) and date:
            try:
                return ak.news_cctv(date=date)
            except TypeError:
                pass
        return ak.news_cctv()
