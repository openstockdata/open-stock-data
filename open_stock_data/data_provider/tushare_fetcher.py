"""
Tushare 数据获取器
使用 tushare 库获取 A 股数据，需要配置 TUSHARE_TOKEN 环境变量。
配置后优先级最高（优先级 0）。
"""

import os
import logging
import threading
import time
from collections import deque
from typing import Optional

import pandas as pd
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from .base import BaseFetcher, DataFetchError, RateLimitError, NETWORK_EXCEPTIONS
from .types import UnifiedRealtimeQuote, RealtimeSource, safe_float
from ..cache import CACHE_TTLS, CacheStore
from .capability_definitions import create_tushare_capability

_LOGGER = logging.getLogger(__name__)


class TushareFetcher(BaseFetcher):
    """Tushare 数据获取器"""

    name = "TushareFetcher"
    priority = 0  # A 股首选（需配置 token）
    backend_group = "tushare"

    # 限流配置：免费版 50次/分钟
    RATE_LIMIT = 50
    RATE_WINDOW = 60  # 秒
    RATE_SAFETY_MARGIN = 5
    RATE_WAIT_BUFFER = 2.0
    _rate_limit_lock = threading.Lock()
    _request_timestamps = deque()

    def __init__(self):
        super().__init__()
        self._api = None
        self.capability = create_tushare_capability()

        # 从环境变量获取 token
        token = os.getenv("TUSHARE_TOKEN")
        if token:
            try:
                import tushare as ts
                ts.set_token(token)
                self._api = ts.pro_api()
                self._available = True
                _LOGGER.info("Tushare API 初始化成功")
            except Exception as e:
                _LOGGER.warning(f"Tushare API 初始化失败: {e}")
                self._available = False
        else:
            _LOGGER.info("未配置 TUSHARE_TOKEN，TushareFetcher 不可用")
            self._available = False

    def _check_rate_limit(self):
        """检查限流，按服务端配额预留安全余量，避免卡点触发 50次/分钟。"""
        effective_limit = max(1, self.RATE_LIMIT - self.RATE_SAFETY_MARGIN)
        while True:
            wait_time = 0.0
            with self._rate_limit_lock:
                current_time = time.monotonic()
                window_start = current_time - self.RATE_WINDOW

                while self._request_timestamps and self._request_timestamps[0] <= window_start:
                    self._request_timestamps.popleft()

                if len(self._request_timestamps) < effective_limit:
                    self._request_timestamps.append(current_time)
                    return

                wait_time = self.RATE_WINDOW - (current_time - self._request_timestamps[0]) + self.RATE_WAIT_BUFFER

            wait_time = max(wait_time, 0.01)
            _LOGGER.warning(
                f"[{self.name}] 达到限流，等待 {wait_time:.1f}s "
                f"(window={self.RATE_WINDOW}s, limit={effective_limit}/{self.RATE_LIMIT})"
            )
            time.sleep(wait_time)

    def _convert_stock_code(self, stock_code: str) -> str:
        """转换股票代码为 Tushare 格式"""
        code = stock_code.upper()
        if '.' in code:
            return code

        # 根据代码前缀判断市场
        if code.startswith(('6', '9')):
            return f"{code}.SH"
        elif code.startswith(('0', '3', '2')):
            return f"{code}.SZ"
        elif code.startswith('4') or code.startswith('8'):
            return f"{code}.BJ"  # 北交所

        return f"{code}.SH"  # 默认上交所

    @staticmethod
    def _is_rate_limit_error(error_msg: str) -> tuple[bool, str, int]:
        """
        检查是否为限流/权限错误

        返回: (is_rate_limit, limit_type, retry_after_seconds)
        limit_type: 'minute_limit'(分钟限制) | 'daily_limit'(日限制) | 'no_permission'(无权限) | 'unknown'
        retry_after: 建议等待秒数
        """
        msg_lower = error_msg.lower()

        # 分钟级限制：每分钟最多访问N次。Tushare 常见文案是 “50次/分钟”。
        if (
            '每分钟' in error_msg
            or '次/分钟' in error_msg
            or '/分钟' in error_msg
            or 'per minute' in msg_lower
            or 'frequency' in msg_lower
            or ('频率超限' in error_msg and '分钟' in error_msg)
        ):
            # 分钟限制应该等待2分钟（120秒）让额度重置
            return True, 'minute_limit', 120

        # 小时级限制：每小时最多访问N次
        if '每小时' in error_msg or 'per hour' in msg_lower:
            # 小时限制应该等待5分钟（300秒）
            return True, 'daily_limit', 300

        # 日限制：天总量上限
        if '日' in error_msg or 'daily' in msg_lower:
            # 日限制应该等到第二天，暂时用6小时（21600秒）
            return True, 'daily_limit', 21600

        # 无权限：没有接口访问权限
        if '权限' in error_msg or 'permission' in msg_lower or '无权限' in error_msg:
            # 无权限应该长期熔断（1小时）
            return True, 'no_permission', 3600

        # 配额相关
        if any(kw in msg_lower for kw in ('quota', 'limit', '配额')):
            # 通用配额限制，等待5分钟
            return True, 'unknown', 300

        return False, 'none', 0

    def _raise_rate_limit_error(self, error: Exception, context: str = ""):
        """检测限流错误并抛出带类型的RateLimitError，非限流错误不处理"""
        is_limit, limit_type, retry_after = self._is_rate_limit_error(str(error))
        if not is_limit:
            return
        type_label = {
            'minute_limit': '【分钟限制】',
            'daily_limit': '【日限制】',
            'no_permission': '【无权限】',
        }.get(limit_type, '【配额超限】')
        msg = f"Tushare 配额超限: {error}"
        _LOGGER.warning(f"[{self.name}] {type_label}{context}: {error} (冷却{retry_after}秒)")
        raise RateLimitError(msg, limit_type=limit_type, retry_after=retry_after)

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=30),
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
        if not self._available or self._api is None:
            return None

        self._check_rate_limit()

        try:
            ts_code = self._convert_stock_code(stock_code)

            df = self._api.daily(
                ts_code=ts_code,
                start_date=start_date,
                end_date=end_date
            )

            if df is None or df.empty:
                # 尝试获取复权数据
                self._check_rate_limit()
                df = self._api.daily(
                    ts_code=ts_code,
                    start_date=start_date,
                    end_date=end_date,
                    adj='qfq'  # 前复权
                )

            return df

        except NETWORK_EXCEPTIONS:
            raise
        except Exception as e:
            self._raise_rate_limit_error(e, f"获取 {stock_code} K线")
            _LOGGER.warning(f"[{self.name}] 获取 {stock_code} 数据失败: {e}")
            raise DataFetchError(f"获取数据失败: {e}")

    def _fetch_raw_daily_data(
        self,
        stock_code: str,
        start_date: str,
        end_date: str
    ) -> Optional[pd.DataFrame]:
        """获取未复权日线数据。"""
        if not self._available or self._api is None:
            return None

        self._check_rate_limit()
        try:
            ts_code = self._convert_stock_code(stock_code)
            return self._api.daily(
                ts_code=ts_code,
                start_date=start_date,
                end_date=end_date,
            )
        except NETWORK_EXCEPTIONS:
            raise
        except Exception as e:
            self._raise_rate_limit_error(e, f"获取 {stock_code} raw K线")
            _LOGGER.warning(f"[{self.name}] 获取 {stock_code} 未复权日线失败: {e}")
            raise DataFetchError(f"获取数据失败: {e}")

    def _normalize_data(
        self,
        df: pd.DataFrame,
        stock_code: str
    ) -> pd.DataFrame:
        """标准化数据"""
        if df is None or df.empty:
            return pd.DataFrame()

        # Tushare 列名映射
        column_mapping = {
            'trade_date': 'date',
            'open': 'open',
            'high': 'high',
            'low': 'low',
            'close': 'close',
            'vol': 'volume',
            'amount': 'amount',
            'pct_chg': 'pct_chg',
        }

        df = df.rename(columns=column_mapping)

        # 单位转换
        # vol: 手 -> 股 (× 100)
        if 'volume' in df.columns:
            df['volume'] = df['volume'] * 100

        # amount: 千元 -> 元 (× 1000)
        if 'amount' in df.columns:
            df['amount'] = df['amount'] * 1000

        # 日期格式转换 YYYYMMDD -> YYYY-MM-DD
        if 'date' in df.columns:
            df['date'] = pd.to_datetime(df['date'], format='%Y%m%d').dt.strftime('%Y-%m-%d')

        # 按日期排序（Tushare 默认降序）
        df = df.sort_values('date', ascending=True)

        # 选择标准列
        result_cols = ['date', 'open', 'high', 'low', 'close', 'volume', 'amount', 'pct_chg']
        available_cols = [col for col in result_cols if col in df.columns]
        df = df[available_cols].copy()

        return df

    def get_fund_flow(self, stock_code: str) -> Optional[pd.DataFrame]:
        """获取资金流向"""
        if not self._available or self._api is None:
            return None

        try:
            self._check_rate_limit()
            ts_code = self._convert_stock_code(stock_code)

            # 获取最近10天的资金流向
            df = self._api.moneyflow(ts_code=ts_code)
            if df is None or df.empty:
                return None

            # 重命名列为中文
            column_mapping = {
                'trade_date': '日期',
                'buy_sm_vol': '小单买入量',
                'buy_sm_amount': '小单买入金额',
                'sell_sm_vol': '小单卖出量',
                'sell_sm_amount': '小单卖出金额',
                'buy_md_vol': '中单买入量',
                'buy_md_amount': '中单买入金额',
                'sell_md_vol': '中单卖出量',
                'sell_md_amount': '中单卖出金额',
                'buy_lg_vol': '大单买入量',
                'buy_lg_amount': '大单买入金额',
                'sell_lg_vol': '大单卖出量',
                'sell_lg_amount': '大单卖出金额',
                'buy_elg_vol': '超大单买入量',
                'buy_elg_amount': '超大单买入金额',
                'sell_elg_vol': '超大单卖出量',
                'sell_elg_amount': '超大单卖出金额',
            }
            df = df.rename(columns=column_mapping)
            return df.head(10)
        except Exception as e:
            self._raise_rate_limit_error(e, "获取资金流向")
            _LOGGER.warning(f"[{self.name}] 获取资金流向失败: {e}")
            return None

    def get_billboard(self, days: str = "5") -> Optional[pd.DataFrame]:
        """获取龙虎榜统计"""
        if not self._available or self._api is None:
            return None

        try:
            self._check_rate_limit()
            from datetime import datetime, timedelta

            # 获取最近交易日的龙虎榜
            end_date = datetime.now().strftime('%Y%m%d')
            start_date = (datetime.now() - timedelta(days=int(days) + 5)).strftime('%Y%m%d')

            df = self._api.top_list(start_date=start_date, end_date=end_date)
            if df is None or df.empty:
                return None

            # 重命名列为中文
            column_mapping = {
                'trade_date': '上榜日期',
                'ts_code': '股票代码',
                'name': '股票名称',
                'close': '收盘价',
                'pct_change': '涨跌幅',
                'turnover_rate': '换手率',
                'amount': '龙虎榜成交额',
                'l_sell': '龙虎榜卖出额',
                'l_buy': '龙虎榜买入额',
                'net_amount': '龙虎榜净买额',
                'reason': '上榜原因',
            }
            df = df.rename(columns=column_mapping)
            return df
        except Exception as e:
            self._raise_rate_limit_error(e, "获取龙虎榜")
            _LOGGER.warning(f"[{self.name}] 获取龙虎榜失败: {e}")
            return None

    # 板块相关接口优先使用离线快照，避免频繁触发 stock_basic 的小时配额限制
    _board_store = CacheStore.get_store("tushare")

    def _get_board_stock_basic_snapshot(self) -> Optional[pd.DataFrame]:
        """获取全量行业板块快照，优先使用离线缓存。"""
        key = "tushare_board_stock_basic_snapshot"
        cached = self._board_store.get(key)
        if isinstance(cached, pd.DataFrame) and not cached.empty:
            _LOGGER.debug(f"[{self.name}] 命中离线行业快照: rows={len(cached)}")
            return cached

        self._check_rate_limit()
        df = self._api.stock_basic(
            list_status='L',
            fields='ts_code,symbol,name,industry,market,list_date'
        )
        if df is None or df.empty:
            _LOGGER.warning(f"[{self.name}] stock_basic 全量快照为空")
            return None

        self._board_store.set(key, df, expire=CACHE_TTLS["tushare_board_snapshot"])
        _LOGGER.debug(f"[{self.name}] 已刷新离线行业快照: rows={len(df)}")
        return df

    def get_belong_board(self, stock_code: str) -> Optional[pd.DataFrame]:
        """获取所属板块，优先从离线行业快照读取。"""
        if not self._available or self._api is None:
            return None

        try:
            ts_code = self._convert_stock_code(stock_code)
            snapshot = self._get_board_stock_basic_snapshot()
            if snapshot is None or snapshot.empty:
                return None

            df = snapshot[snapshot['ts_code'] == ts_code].copy()
            if df.empty:
                _LOGGER.debug(f"[{self.name}] 离线行业快照中未找到股票: {stock_code}")
                return None

            column_mapping = {
                'ts_code': '股票代码',
                'name': '股票名称',
                'industry': '行业',
                'market': '市场',
                'list_date': '上市日期',
            }
            df = df.rename(columns=column_mapping)
            return df
        except Exception as e:
            self._raise_rate_limit_error(e, "获取所属板块")
            _LOGGER.warning(f"[{self.name}] 获取所属板块失败: {e}")
            return None

    def get_board_cons(self, board_name: str, board_type: str = "industry") -> Optional[pd.DataFrame]:
        """获取板块成分股，行业板块优先从离线快照过滤。"""
        if not self._available or self._api is None:
            _LOGGER.debug(f"[{self.name}] API不可用，跳过get_board_cons({board_name})")
            return None

        cache_key = f"tushare_board_cons_{board_type}_{board_name}"
        cached = self._board_store.get(cache_key)
        if cached is not None:
            _LOGGER.debug(f"[{self.name}] 命中缓存: {cache_key}")
            return cached

        try:
            _LOGGER.debug(f"[{self.name}] 开始获取{board_type}板块成分股: {board_name}")

            if board_type == "industry":
                _LOGGER.debug(f"[{self.name}] 从离线行业快照筛选板块: {board_name}")
                snapshot = self._get_board_stock_basic_snapshot()
                if snapshot is None or snapshot.empty:
                    return None
                df = snapshot[snapshot['industry'] == board_name].copy()
                _LOGGER.debug(f"[{self.name}] 离线行业快照筛选结果 {len(df)} 条")
            else:
                self._check_rate_limit()
                _LOGGER.debug(f"[{self.name}] 调用 concept API 查询概念板块")
                concepts = self._api.concept()
                if concepts is None or concepts.empty:
                    _LOGGER.warning(f"[{self.name}] concept API 返回空结果")
                    return None

                matched = concepts[concepts['name'].str.contains(board_name, na=False)]
                if matched.empty:
                    _LOGGER.debug(f"[{self.name}] 未找到匹配的概念板块: {board_name}")
                    return None

                concept_code = matched.iloc[0]['code']
                _LOGGER.debug(f"[{self.name}] 概念板块 '{board_name}' 对应代码: {concept_code}, 调用 concept_detail API")
                df = self._api.concept_detail(id=concept_code)
                _LOGGER.debug(f"[{self.name}] concept_detail 返回 {len(df) if df is not None and not df.empty else 0} 条记录")

            if df is None or df.empty:
                _LOGGER.debug(f"[{self.name}] 板块成分股查询结果为空: {board_name}")
                return None

            column_mapping = {
                'ts_code': '代码',
                'symbol': '股票代码',
                'name': '名称',
                'industry': '行业',
                'market': '市场',
            }
            df = df.rename(columns=column_mapping)
            self._board_store.set(cache_key, df, expire=CACHE_TTLS["tushare_board"])
            _LOGGER.debug(f"[{self.name}] 成功获取{board_type}板块成分股: {board_name}, 共 {len(df)} 条")
            return df
        except Exception as e:
            self._raise_rate_limit_error(e, f"获取{board_type}板块成分股")
            _LOGGER.warning(f"[{self.name}] 获取{board_type}板块成分股失败: {e}")
            return None

    def get_realtime_quote(self, stock_code: str) -> Optional[UnifiedRealtimeQuote]:
        """获取实时行情"""
        if not self._available or self._api is None:
            return None

        try:
            self._check_rate_limit()
            ts_code = self._convert_stock_code(stock_code)

            # 使用已导入的 tushare 模块（__init__ 中已设置 token）
            import tushare as ts
            df = ts.realtime_quote(ts_code=ts_code)

            if df is None or df.empty:
                return None

            row = df.iloc[0]

            price = safe_float(row.get('PRICE'))
            pre_close = safe_float(row.get('PRE_CLOSE'))

            # 计算涨跌额和涨跌幅
            change_amount = None
            change_pct = None
            if price is not None and pre_close is not None and pre_close != 0:
                change_amount = round(price - pre_close, 2)
                change_pct = round((price - pre_close) / pre_close * 100, 2)

            # VOLUME 单位：股，转换为手（/100）以与其他数据源保持一致
            volume_raw = safe_float(row.get('VOLUME'))
            volume = volume_raw / 100 if volume_raw is not None else None

            quote = UnifiedRealtimeQuote(
                code=stock_code,
                name=row.get('NAME', None),
                source=RealtimeSource.TUSHARE,
                price=price,
                change_pct=change_pct,
                change_amount=change_amount,
                volume=volume,
                amount=safe_float(row.get('AMOUNT')),
                open_price=safe_float(row.get('OPEN')),
                high=safe_float(row.get('HIGH')),
                low=safe_float(row.get('LOW')),
                pre_close=pre_close,
            )

            return quote
        except Exception as e:
            self._raise_rate_limit_error(e, "获取实时行情")
            _LOGGER.warning(f"[{self.name}] 获取实时行情失败: {e}")
            return None

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=15),
        retry=retry_if_exception_type(NETWORK_EXCEPTIONS),
        reraise=True
    )
    def get_dividend_history(self, symbol: str) -> Optional[pd.DataFrame]:
        """获取分红历史"""
        if not self._available or self._api is None:
            return None

        try:
            self._check_rate_limit()
            ts_code = self._convert_stock_code(symbol)

            df = self._api.dividend(ts_code=ts_code)
            if df is not None and not df.empty:
                col_map = {
                    "ann_date": "公告日期",
                    "stk_div": "送股",
                    "stk_bo_rate": "转增",
                    "cash_div_tax": "派息",
                    "div_proc": "进度",
                    "ex_date": "除权除息日",
                }
                df = df.rename(columns={k: v for k, v in col_map.items() if k in df.columns})
            return df
        except NETWORK_EXCEPTIONS:
            raise
        except Exception as e:
            self._raise_rate_limit_error(e, "获取分红历史")
            _LOGGER.warning(f"[{self.name}] 获取分红历史失败: {e}")
            raise DataFetchError(f"获取数据失败: {e}")

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=15),
        retry=retry_if_exception_type(NETWORK_EXCEPTIONS),
        reraise=True
    )
    def get_fund_holder(self, symbol: str, date: str = "") -> Optional[pd.DataFrame]:
        """获取基金持仓"""
        if not self._available or self._api is None:
            return None
        if not symbol:
            return None

        try:
            self._check_rate_limit()
            ts_code = self._convert_stock_code(symbol)

            df = self._api.fund_holder(ts_code=ts_code)
            return df
        except NETWORK_EXCEPTIONS:
            raise
        except Exception as e:
            self._raise_rate_limit_error(e, "获取基金持仓")
            _LOGGER.warning(f"[{self.name}] 获取基金持仓失败: {e}")
            raise DataFetchError(f"获取数据失败: {e}")

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=15),
        retry=retry_if_exception_type(NETWORK_EXCEPTIONS),
        reraise=True
    )
    def get_top10_holders(self, symbol: str, holder_type: str = "main") -> Optional[pd.DataFrame]:
        """获取十大股东"""
        if not self._available or self._api is None:
            return None

        try:
            self._check_rate_limit()
            ts_code = self._convert_stock_code(symbol)

            if holder_type == "circulate":
                df = self._api.top10_floatholders(ts_code=ts_code)
            else:
                df = self._api.top10_holders(ts_code=ts_code)
            return df
        except NETWORK_EXCEPTIONS:
            raise
        except Exception as e:
            self._raise_rate_limit_error(e, "获取十大股东")
            _LOGGER.warning(f"[{self.name}] 获取十大股东失败: {e}")
            raise DataFetchError(f"获取数据失败: {e}")
