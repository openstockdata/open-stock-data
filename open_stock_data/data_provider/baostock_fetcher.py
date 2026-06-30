"""
Baostock 数据获取器 (优先级 3)
使用 baostock 库获取 A 股历史数据
免费，无需 token
"""

import logging
from contextlib import contextmanager
from typing import Optional

import pandas as pd
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from .base import BaseFetcher, DataFetchError, NETWORK_EXCEPTIONS

_LOGGER = logging.getLogger(__name__)


class BaostockFetcher(BaseFetcher):
    """Baostock 数据获取器"""

    name = "BaostockFetcher"
    priority = 3
    backend_group = "baostock"

    def __init__(self):
        super().__init__()
        self._bs = None

        # 延迟导入
        try:
            import baostock as bs
            self._bs = bs
            self._available = True
        except ImportError:
            _LOGGER.warning("baostock 库未安装")
            self._available = False

    @contextmanager
    def _baostock_session(self):
        """Baostock 会话管理器"""
        if not self._available:
            yield None
            return

        try:
            lg = self._bs.login()
            if lg.error_code != '0':
                _LOGGER.warning(f"Baostock 登录失败: {lg.error_msg}")
                yield None
                return
            yield self._bs
        finally:
            try:
                self._bs.logout()
            except Exception:  # 忽略登出时的异常
                pass

    def _convert_stock_code(self, stock_code: str) -> str:
        """转换股票代码为 Baostock 格式"""
        code = stock_code.upper()
        if '.' in code:
            # 已经是 baostock 格式
            parts = code.split('.')
            if len(parts) == 2:
                return f"{parts[1].lower()}.{parts[0]}"
            return code.lower()

        # 根据代码前缀判断市场
        if code.startswith(('6', '9')):
            return f"sh.{code}"
        elif code.startswith(('0', '3', '2')):
            return f"sz.{code}"

        return f"sh.{code}"  # 默认上交所

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
        if not self._available:
            return None

        with self._baostock_session() as bs:
            if bs is None:
                return None

            try:
                bs_code = self._convert_stock_code(stock_code)

                # 日期格式转换 YYYYMMDD -> YYYY-MM-DD
                if len(start_date) == 8:
                    start_date = f"{start_date[:4]}-{start_date[4:6]}-{start_date[6:]}"
                if len(end_date) == 8:
                    end_date = f"{end_date[:4]}-{end_date[4:6]}-{end_date[6:]}"

                rs = bs.query_history_k_data_plus(
                    bs_code,
                    "date,open,high,low,close,volume,amount,pctChg",
                    start_date=start_date,
                    end_date=end_date,
                    frequency="d",
                    adjustflag="2"  # 前复权
                )

                if rs.error_code != '0':
                    _LOGGER.warning(f"[{self.name}] Baostock 查询失败: {rs.error_msg}")
                    return None

                data_list = []
                while (rs.error_code == '0') and rs.next():
                    data_list.append(rs.get_row_data())

                if not data_list:
                    return None

                df = pd.DataFrame(data_list, columns=rs.fields)
                return df

            except Exception as e:
                _LOGGER.warning(f"[{self.name}] 获取 {stock_code} 数据失败: {e}")
                raise DataFetchError(f"获取数据失败: {e}")

    def _fetch_raw_daily_data(
        self,
        stock_code: str,
        start_date: str,
        end_date: str
    ) -> Optional[pd.DataFrame]:
        """获取未复权日线数据。"""
        if not self._available:
            return None

        with self._baostock_session() as bs:
            if bs is None:
                return None

            try:
                bs_code = self._convert_stock_code(stock_code)
                if len(start_date) == 8:
                    start_date = f"{start_date[:4]}-{start_date[4:6]}-{start_date[6:]}"
                if len(end_date) == 8:
                    end_date = f"{end_date[:4]}-{end_date[4:6]}-{end_date[6:]}"

                rs = bs.query_history_k_data_plus(
                    bs_code,
                    "date,open,high,low,close,volume,amount,pctChg",
                    start_date=start_date,
                    end_date=end_date,
                    frequency="d",
                    adjustflag="3",
                )
                if rs.error_code != '0':
                    _LOGGER.warning(f"[{self.name}] Baostock raw 查询失败: {rs.error_msg}")
                    return None

                data_list = []
                while (rs.error_code == '0') and rs.next():
                    data_list.append(rs.get_row_data())
                if not data_list:
                    return None
                return pd.DataFrame(data_list, columns=rs.fields)
            except Exception as e:
                _LOGGER.warning(f"[{self.name}] 获取 {stock_code} 未复权日线失败: {e}")
                raise DataFetchError(f"获取数据失败: {e}")

    def _query_stock_basic(self, bs, bs_code: str) -> Optional[pd.DataFrame]:
        """调用 query_stock_basic 并转换为 DataFrame。"""
        query_variants = [
            {"code_name": bs_code},
            {"code": bs_code},
        ]

        for kwargs in query_variants:
            try:
                rs = bs.query_stock_basic(**kwargs)
            except TypeError:
                continue

            if rs.error_code != '0':
                continue

            rows = []
            while (rs.error_code == '0') and rs.next():
                rows.append(rs.get_row_data())
            if not rows:
                continue

            return pd.DataFrame(rows, columns=rs.fields)

        return None

    def get_belong_board(self, stock_code: str) -> Optional[pd.DataFrame]:
        """通过 query_stock_basic 获取行业/上市信息。"""
        if not self._available:
            return None

        with self._baostock_session() as bs:
            if bs is None:
                return None

            try:
                bs_code = self._convert_stock_code(stock_code)
                df = self._query_stock_basic(bs, bs_code)
                if df is None or df.empty:
                    return None

                if 'code' in df.columns:
                    df = df[df['code'].astype(str).str.lower() == bs_code.lower()]
                    if df.empty:
                        return None

                rename_map = {
                    'code': '股票代码',
                    'code_name': '股票代码',
                    'name': '股票名称',
                    'industry': '行业',
                    'market': '市场',
                    'type': '证券类型',
                    'status': '状态',
                    'ipoDate': '上市日期',
                    'outDate': '退市日期',
                }
                df = df.rename(columns={k: v for k, v in rename_map.items() if k in df.columns})

                if '股票代码' in df.columns:
                    df['股票代码'] = (
                        df['股票代码']
                        .astype(str)
                        .str.lower()
                        .str.replace('sh.', '', regex=False)
                        .str.replace('sz.', '', regex=False)
                    )

                preferred = ['股票代码', '股票名称', '行业', '市场', '证券类型', '状态', '上市日期', '退市日期']
                cols = [c for c in preferred if c in df.columns]
                return df[cols].copy() if cols else df
            except Exception as e:
                _LOGGER.warning(f"[{self.name}] query_stock_basic 获取 {stock_code} 所属板块失败: {e}")
                return None

    def _normalize_data(
        self,
        df: pd.DataFrame,
        stock_code: str
    ) -> pd.DataFrame:
        """标准化数据"""
        if df is None or df.empty:
            return pd.DataFrame()

        # Baostock 列名映射
        column_mapping = {
            'date': 'date',
            'open': 'open',
            'high': 'high',
            'low': 'low',
            'close': 'close',
            'volume': 'volume',
            'amount': 'amount',
            'pctChg': 'pct_chg',
        }

        df = df.rename(columns=column_mapping)

        # Baostock 返回的都是字符串，转换为数值类型
        numeric_cols = ['open', 'high', 'low', 'close', 'volume', 'amount', 'pct_chg']
        for col in numeric_cols:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors='coerce')

        # 选择标准列
        result_cols = ['date', 'open', 'high', 'low', 'close', 'volume', 'amount', 'pct_chg']
        available_cols = [col for col in result_cols if col in df.columns]
        df = df[available_cols].copy()

        return df
