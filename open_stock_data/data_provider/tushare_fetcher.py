"""
Tushare 数据获取器
使用 tushare 库获取 A 股数据，需要配置 TUSHARE_TOKEN 环境变量。
"""

import os
import logging
import threading
import time
from collections import deque
from datetime import datetime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

import pandas as pd
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from .base import BaseFetcher, DataFetchError, NETWORK_EXCEPTIONS
from .boards import normalize_belong_board
from .types import UnifiedRealtimeQuote, RealtimeSource, safe_float
from ..cache import CACHE_TTLS, CacheStore

_LOGGER = logging.getLogger(__name__)

_BEIJING_TZ = ZoneInfo("Asia/Shanghai")


def seconds_until_beijing_midnight(now: Optional[datetime] = None) -> float:
    """到北京时间次日 0 点的秒数（Tushare 日配额按自然日重置）。

    固定 21600s 的问题：对 5 次/天 的接口，白天触发后 6 小时仍在同一天，
    重试必败还白烧一次配额。按次日 0 点 +60s 缓冲计算，一次到位。
    """
    beijing_now = now or datetime.now(_BEIJING_TZ)
    if beijing_now.tzinfo is None:
        beijing_now = beijing_now.replace(tzinfo=_BEIJING_TZ)
    else:
        beijing_now = beijing_now.astimezone(_BEIJING_TZ)
    next_midnight = (beijing_now + timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return max(60.0, (next_midnight - beijing_now).total_seconds() + 60.0)


def quota_empty_df(reason: str) -> pd.DataFrame:
    """配额冷却期的空结果：attrs 携带原因，路由记 EMPTY 时写入 FetchAttempt.reason。"""
    df = pd.DataFrame()
    df.attrs["empty_reason"] = reason
    return df


class TushareFetcher(BaseFetcher):
    """Tushare 数据获取器"""

    name = "TushareFetcher"
    priority = 9  # A 股（需配置 token）

    # 限流配置：免费版 50次/分钟
    RATE_LIMIT = 50
    RATE_WINDOW = 60  # 秒
    RATE_SAFETY_MARGIN = 5
    RATE_WAIT_BUFFER = 2.0
    _rate_limit_lock = threading.Lock()
    _request_timestamps = deque()
    # 各接口的配额冷却：api -> (截止时刻 monotonic, 原因)。
    # Tushare 的配额按接口独立计量，同一 token 全局共享，因此放在类上。
    _api_blocked_until: dict = {}

    def __init__(self):
        super().__init__()
        self._last_empty_reason: str = ""
        self._api = None

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

    def execute(self, method_name: str, *args, **kwargs):
        """每次调用前清空上次的配额原因，避免陈旧原因污染本次的 EMPTY 记录。"""
        self._last_empty_reason = ""
        return super().execute(method_name, *args, **kwargs)

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
        limit_type: 'minute_limit' | 'hourly_limit' | 'daily_limit' | 'no_permission' | 'unknown'
        retry_after: 建议等待秒数

        Tushare 的文案形如 “抱歉，您每分钟最多访问该接口50次” /
        “抱歉，您访问接口(concept)频率超限(1次/小时)” / “抱歉，您没有访问该接口的权限”。
        """
        msg_lower = error_msg.lower()
        quota_words = ('超限', '限制', '配额', '上限', 'limit', 'quota', 'exceed')
        mentions_quota = any(w in msg_lower for w in quota_words) or '频率' in error_msg

        # 分钟级：每分钟最多访问N次
        if (
            any(k in error_msg for k in ('每分钟', '次/分钟', '/分钟'))
            or 'per minute' in msg_lower
            or 'frequency' in msg_lower
            or ('频率超限' in error_msg and '分钟' in error_msg)
        ):
            # 分钟限制应该等待2分钟（120秒）让额度重置
            return True, 'minute_limit', 120

        # 小时级：频率超限(1次/小时)
        if (
            any(k in error_msg for k in ('每小时', '次/小时', '/小时'))
            or 'per hour' in msg_lower
            or ('频率超限' in error_msg and '小时' in error_msg)
        ):
            return True, 'hourly_limit', 3600

        # 日级：只在明确写出“每日/每天/次/日”或同时出现配额字样时判定，
        # 避免 “trade_date 日期格式错误”、接口名 “(daily)” 这类普通文本被当作日配额。
        if (
            any(k in error_msg for k in ('每日', '每天', '次/日', '次/天', '/日', '/天'))
            or 'per day' in msg_lower
            or 'daily limit' in msg_lower
            or ('日' in error_msg and mentions_quota)
        ):
            # 日配额按北京时间自然日重置：冷却到次日 0 点（+60s 缓冲）。
            # 固定 21600s 的问题：白天触发后 6 小时仍在同一天，重试必败还白烧配额。
            return True, 'daily_limit', seconds_until_beijing_midnight()

        # 无权限：没有接口访问权限
        if '权限' in error_msg or 'permission' in msg_lower:
            # 无权限应该长期熔断（1小时）
            return True, 'no_permission', 3600

        # 频率超限但未写明单位 / 通用配额
        if '频率超限' in error_msg or any(kw in msg_lower for kw in ('quota', 'limit', '配额')):
            return True, 'unknown', 300

        return False, 'none', 0

    @classmethod
    def _reset_quota_state(cls) -> None:
        """清空接口冷却状态（测试用）。"""
        with cls._rate_limit_lock:
            cls._api_blocked_until.clear()

    def _quota_block_reason(self, api: str) -> Optional[str]:
        """接口冷却中的原因（未冷却返回 None）。兼容旧的纯 float 存量值。"""
        with self._rate_limit_lock:
            blocked = self._api_blocked_until.get(api)
            if blocked is None:
                return None
            if isinstance(blocked, (tuple, list)):
                until, reason = blocked[0], blocked[1] if len(blocked) > 1 else ""
            else:
                until, reason = blocked, ""
            remaining = until - time.monotonic()
            if remaining <= 0:
                self._api_blocked_until.pop(api, None)
                return None
            return reason or f"tushare {api} 配额冷却中（剩余 {remaining:.0f}s）"

    def _quota_available(self, api: str) -> bool:
        """接口是否不在配额冷却期。冷却中调用方返回配额空结果让路由回退。"""
        return self._quota_block_reason(api) is None

    def _quota_empty(self, api: str, default: str = "") -> pd.DataFrame:
        """配额冷却的空结果：attrs + 实例级原因双通道，供路由记入 FetchAttempt。"""
        reason = self._quota_block_reason(api) or default or f"tushare {api} 配额冷却中"
        self._last_empty_reason = reason
        _LOGGER.debug(f"[{self.name}] 接口 {api} 处于配额冷却期，跳过: {reason}")
        return quota_empty_df(reason)

    def _note_rate_limit(self, error: Exception, context: str = "", api: Optional[str] = None) -> bool:
        """识别限流/配额错误：记下该接口的冷却截止时间并返回 True；非限流错误返回 False。

        Tushare 的配额按接口独立计量，所以只冷却出错的接口、由调用方返回配额空结果
        （attrs 携带原因）让路由记 EMPTY 回退到下一数据源；不向路由层抛 RateLimitError——
        那会把同组路由上的 Tushare 整体熔断（例如 concept 超限却连累走离线快照的 belong_board）。
        日配额冷却到北京时间次日 0 点（自然日重置），其它档位用固定秒数。
        """
        is_limit, limit_type, retry_after = self._is_rate_limit_error(str(error))
        if not is_limit:
            return False
        if limit_type == "daily_limit":
            # _is_rate_limit_error 内已按次日 0 点计算；此处兜底重算，防止调用方直接伪造类型。
            retry_after = seconds_until_beijing_midnight()
        reason = f"tushare {api or '-'} {limit_type}冷却{retry_after:.0f}s: {error}"
        if api:
            with self._rate_limit_lock:
                self._api_blocked_until[api] = (time.monotonic() + retry_after, reason)
        self._last_empty_reason = reason
        type_label = {
            'minute_limit': '【分钟限制】',
            'hourly_limit': '【小时限制】',
            'daily_limit': '【日限制】',
            'no_permission': '【无权限】',
        }.get(limit_type, '【配额超限】')
        _LOGGER.warning(
            f"[{self.name}] {type_label}{context}: {error} (接口 {api or '-'} 冷却 {retry_after}s)"
        )
        return True

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        retry=retry_if_exception_type(NETWORK_EXCEPTIONS),
        reraise=True
    )
    def _fetch_daily_data(
        self,
        stock_code: str,
        start_date: str,
        end_date: str
    ) -> Optional[pd.DataFrame]:
        """获取原始数据"""
        if not self._available or self._api is None:
            return None
        if not self._quota_available("daily"):
            return self._quota_empty("daily")

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
            if self._note_rate_limit(e, f"获取 {stock_code} K线", api="daily"):
                return self._quota_empty("daily")
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
        if not self._quota_available("daily"):
            return self._quota_empty("daily")

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
            if self._note_rate_limit(e, f"获取 {stock_code} raw K线", api="daily"):
                return self._quota_empty("daily")
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
        if not self._quota_available("moneyflow"):
            return self._quota_empty("moneyflow")

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
            if self._note_rate_limit(e, "获取资金流向", api="moneyflow"):
                return self._quota_empty("moneyflow")
            _LOGGER.warning(f"[{self.name}] 获取资金流向失败: {e}")
            return None

    def get_billboard(self, days: str = "5") -> Optional[pd.DataFrame]:
        """获取龙虎榜统计"""
        if not self._available or self._api is None:
            return None
        if not self._quota_available("top_list"):
            return self._quota_empty("top_list")

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
            if self._note_rate_limit(e, "获取龙虎榜", api="top_list"):
                return self._quota_empty("top_list")
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

        if not self._quota_available("stock_basic"):
            return self._quota_empty("stock_basic")
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

    def _get_concept_list(self) -> Optional[pd.DataFrame]:
        """概念板块列表（concept 接口配额仅 1 次/小时，命中磁盘缓存即免请求）。"""
        key = "tushare_concept_list"
        cached = self._board_store.get(key)
        if isinstance(cached, pd.DataFrame) and not cached.empty:
            _LOGGER.debug(f"[{self.name}] 命中概念板块列表缓存: rows={len(cached)}")
            return cached

        if not self._quota_available("concept"):
            return self._quota_empty("concept")
        self._check_rate_limit()
        try:
            _LOGGER.debug(f"[{self.name}] 调用 concept API 查询概念板块列表")
            concepts = self._api.concept()
        except NETWORK_EXCEPTIONS:
            raise
        except Exception as e:
            if self._note_rate_limit(e, "获取概念板块列表", api="concept"):
                return self._quota_empty("concept")
            _LOGGER.warning(f"[{self.name}] 获取概念板块列表失败: {e}")
            return None

        if concepts is None or concepts.empty:
            _LOGGER.warning(f"[{self.name}] concept API 返回空结果")
            return None
        self._board_store.set(key, concepts, expire=CACHE_TTLS["tushare_board"])
        return concepts

    def _fetch_concept_cons(self, board_name: str) -> Optional[pd.DataFrame]:
        concepts = self._get_concept_list()
        if concepts is None:
            return None
        if concepts.empty:
            if concepts.attrs.get("empty_reason"):
                return concepts
            return None

        matched = concepts[concepts['name'].astype(str).str.contains(board_name, na=False, regex=False)]
        if matched.empty:
            _LOGGER.debug(f"[{self.name}] 未找到匹配的概念板块: {board_name}")
            return None

        concept_code = matched.iloc[0]['code']
        if not self._quota_available("concept_detail"):
            return self._quota_empty("concept_detail")
        self._check_rate_limit()
        _LOGGER.debug(f"[{self.name}] 概念板块 '{board_name}' 对应代码: {concept_code}, 调用 concept_detail API")
        df = self._api.concept_detail(id=concept_code)
        _LOGGER.debug(f"[{self.name}] concept_detail 返回 {len(df) if df is not None and not df.empty else 0} 条记录")
        return df

    def get_belong_board(self, stock_code: str) -> Optional[pd.DataFrame]:
        """获取所属板块，优先从离线行业快照读取。输出为统一 schema（见 boards.py）。"""
        if not self._available or self._api is None:
            return None

        try:
            ts_code = self._convert_stock_code(stock_code)
            snapshot = self._get_board_stock_basic_snapshot()
            if snapshot is None:
                return None
            if snapshot.empty:
                # 配额冷却的空快照：原样透出，attrs 里的原因供路由记入 FetchAttempt
                if snapshot.attrs.get("empty_reason"):
                    return snapshot
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
            return normalize_belong_board(df)
        except Exception as e:
            if self._note_rate_limit(e, "获取所属板块", api="stock_basic"):
                return self._quota_empty("stock_basic")
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

        api_name = "stock_basic" if board_type == "industry" else "concept_detail"
        try:
            _LOGGER.debug(f"[{self.name}] 开始获取{board_type}板块成分股: {board_name}")

            if board_type == "industry":
                _LOGGER.debug(f"[{self.name}] 从离线行业快照筛选板块: {board_name}")
                snapshot = self._get_board_stock_basic_snapshot()
                if snapshot is None:
                    return None
                if snapshot.empty:
                    if snapshot.attrs.get("empty_reason"):
                        return snapshot
                    return None
                df = snapshot[snapshot['industry'] == board_name].copy()
                _LOGGER.debug(f"[{self.name}] 离线行业快照筛选结果 {len(df)} 条")
            else:
                df = self._fetch_concept_cons(board_name)

            if df is None:
                _LOGGER.debug(f"[{self.name}] 板块成分股查询结果为空: {board_name}")
                return None
            if df.empty:
                if df.attrs.get("empty_reason"):
                    return df
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
            if self._note_rate_limit(e, f"获取{board_type}板块成分股", api=api_name):
                return self._quota_empty(api_name)
            _LOGGER.warning(f"[{self.name}] 获取{board_type}板块成分股失败: {e}")
            return None

    def get_realtime_quote(self, stock_code: str) -> Optional[UnifiedRealtimeQuote]:
        """获取实时行情"""
        if not self._available or self._api is None:
            return None
        if not self._quota_available("realtime_quote"):
            # 行情返回标量对象、无法挂 attrs：原因记在实例上，路由回退记 EMPTY 时读取。
            self._last_empty_reason = self._quota_block_reason("realtime_quote") or ""
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
            if self._note_rate_limit(e, "获取实时行情", api="realtime_quote"):
                return None
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
        if not self._quota_available("dividend"):
            return self._quota_empty("dividend")

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
            if self._note_rate_limit(e, "获取分红历史", api="dividend"):
                return self._quota_empty("dividend")
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
        if not self._quota_available("fund_holder"):
            return self._quota_empty("fund_holder")

        try:
            self._check_rate_limit()
            ts_code = self._convert_stock_code(symbol)

            df = self._api.fund_holder(ts_code=ts_code)
            return df
        except NETWORK_EXCEPTIONS:
            raise
        except Exception as e:
            if self._note_rate_limit(e, "获取基金持仓", api="fund_holder"):
                return self._quota_empty("fund_holder")
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
        api_name = "top10_floatholders" if holder_type == "circulate" else "top10_holders"
        if not self._quota_available(api_name):
            return self._quota_empty(api_name)

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
            if self._note_rate_limit(e, "获取十大股东", api=api_name):
                return self._quota_empty(api_name)
            _LOGGER.warning(f"[{self.name}] 获取十大股东失败: {e}")
            raise DataFetchError(f"获取数据失败: {e}")
