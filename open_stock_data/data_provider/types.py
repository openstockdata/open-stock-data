"""统一数据类型定义。

熔断器迁至 circuit_breaker.py，列名转换迁至 columns.py，
股票代码归一/识别迁至 stock_code.py。
"""

from enum import Enum
from dataclasses import dataclass
from typing import Optional, Dict, Any
import numpy as np


def safe_float(val, default: Optional[float] = None) -> Optional[float]:
    """安全转换为浮点数"""
    if val is None:
        return default
    if isinstance(val, float):
        if np.isnan(val):
            return default
        return val
    if isinstance(val, (int, np.integer)):
        return float(val)
    if isinstance(val, str):
        val = val.strip()
        if not val or val in ('--', '-', 'nan', 'NaN', 'None'):
            return default
        try:
            return float(val.replace(',', ''))
        except ValueError:
            return default
    try:
        result = float(val)
        if np.isnan(result):
            return default
        return result
    except (ValueError, TypeError):
        return default


def safe_int(val, default: Optional[int] = None) -> Optional[int]:
    """安全转换为整数"""
    result = safe_float(val, None)
    if result is None:
        return default
    return int(result)


class RealtimeSource(Enum):
    """实时行情数据源"""
    TICKFLOW = "tickflow"
    TUSHARE = "tushare"
    EFINANCE = "efinance"
    AKSHARE_EM = "akshare_em"
    AKSHARE_SINA = "akshare_sina"
    AKSHARE_QQ = "akshare_qq"
    TENCENT = "tencent"
    SINA = "sina"
    YFINANCE = "yfinance"
    FALLBACK = "fallback"


@dataclass
class UnifiedRealtimeQuote:
    """统一实时行情数据结构"""
    code: str
    name: Optional[str] = None
    source: RealtimeSource = RealtimeSource.FALLBACK

    price: Optional[float] = None
    change_pct: Optional[float] = None
    change_amount: Optional[float] = None

    volume: Optional[float] = None
    amount: Optional[float] = None
    volume_ratio: Optional[float] = None
    turnover_rate: Optional[float] = None
    amplitude: Optional[float] = None

    open_price: Optional[float] = None
    high: Optional[float] = None
    low: Optional[float] = None
    pre_close: Optional[float] = None

    pe_ratio: Optional[float] = None
    pb_ratio: Optional[float] = None
    total_mv: Optional[float] = None
    circ_mv: Optional[float] = None

    change_60d: Optional[float] = None
    high_52w: Optional[float] = None
    low_52w: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            'code': self.code,
            'name': self.name,
            'source': self.source.value if self.source else None,
            'price': self.price,
            'change_pct': self.change_pct,
            'change_amount': self.change_amount,
            'volume': self.volume,
            'amount': self.amount,
            'volume_ratio': self.volume_ratio,
            'turnover_rate': self.turnover_rate,
            'amplitude': self.amplitude,
            'open_price': self.open_price,
            'high': self.high,
            'low': self.low,
            'pre_close': self.pre_close,
            'pe_ratio': self.pe_ratio,
            'pb_ratio': self.pb_ratio,
            'total_mv': self.total_mv,
            'circ_mv': self.circ_mv,
        }

    def has_basic_data(self) -> bool:
        return self.price is not None and self.change_pct is not None

    def has_volume_data(self) -> bool:
        return self.volume is not None and self.amount is not None


@dataclass
class ChipDistribution:
    """筹码分布数据结构"""
    code: str
    date: Optional[str] = None
    source: str = "akshare"

    profit_ratio: Optional[float] = None
    avg_cost: Optional[float] = None

    cost_90_low: Optional[float] = None
    cost_90_high: Optional[float] = None
    concentration_90: Optional[float] = None

    cost_70_low: Optional[float] = None
    cost_70_high: Optional[float] = None
    concentration_70: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            'code': self.code,
            'date': self.date,
            'source': self.source,
            'profit_ratio': self.profit_ratio,
            'avg_cost': self.avg_cost,
            'cost_90_low': self.cost_90_low,
            'cost_90_high': self.cost_90_high,
            'concentration_90': self.concentration_90,
            'cost_70_low': self.cost_70_low,
            'cost_70_high': self.cost_70_high,
            'concentration_70': self.concentration_70,
        }

    def get_chip_status(self, current_price: Optional[float] = None) -> Dict[str, Any]:
        status = {
            'profit_ratio': self.profit_ratio,
            'avg_cost': self.avg_cost,
            'concentration_90': self.concentration_90,
            'concentration_70': self.concentration_70,
        }

        if current_price and self.avg_cost:
            status['price_vs_avg_cost'] = (current_price - self.avg_cost) / self.avg_cost * 100

        if self.concentration_90:
            if self.concentration_90 < 10:
                status['chip_level'] = '高度集中'
            elif self.concentration_90 < 20:
                status['chip_level'] = '相对集中'
            elif self.concentration_90 < 30:
                status['chip_level'] = '中等分散'
            else:
                status['chip_level'] = '高度分散'

        return status
