"""股票代码归一化、市场识别与 StockType 枚举。"""

import logging
from enum import Enum
from typing import Optional

_LOGGER = logging.getLogger(__name__)


class StockType(Enum):
    """股票类型枚举"""
    A_STOCK = "a"       # A股个股
    ETF = "etf"         # ETF基金
    HK = "hk"           # 港股
    US = "us"           # 美股
    UNKNOWN = "unknown"


def _normalize_mainland_code(stock_code: str) -> str:
    """标准化 A 股/ETF 代码为 6 位纯数字。"""
    code = str(stock_code).strip().lower()
    if not code:
        return ""
    if code.startswith(("sh", "sz")):
        code = code[2:]
    elif code.endswith((".sh", ".sz")):
        code = code[:-3]
    if code.isdigit() and len(code) <= 6:
        return code.zfill(6)
    return ""


def normalize_hk_code(stock_code: str) -> str:
    """标准化港股代码为 5 位纯数字。"""
    code = str(stock_code).strip().lower()
    if not code:
        return ""
    if code.startswith("hk"):
        code = code[2:]
    if code.endswith(".hk"):
        code = code[:-3]
    if code.isdigit() and 1 <= len(code) <= 5:
        return code.zfill(5)
    return ""


def is_etf_code(stock_code: str) -> bool:
    """判断是否为 ETF 代码"""
    code = _normalize_mainland_code(stock_code)
    if len(code) != 6 or not code.isdigit():
        return False
    prefix = code[:2]
    return prefix in ('51', '52', '56', '58', '15', '16', '18')


def is_hk_code(stock_code: str) -> bool:
    """判断是否为港股代码"""
    return bool(normalize_hk_code(stock_code))


def is_us_code(stock_code: str) -> bool:
    """判断是否为美股代码"""
    stock_code = str(stock_code).strip()
    code = stock_code.upper().split('.')[0]
    return len(code) <= 5 and code.isalpha()


def is_a_stock_code(stock_code: str) -> bool:
    """判断是否为A股代码（含ETF）"""
    code = _normalize_mainland_code(stock_code)
    if len(code) != 6 or not code.isdigit():
        return False
    prefix = code[:2]
    a_stock_prefixes = ('60', '68', '00', '30', '51', '52', '56', '58', '15', '16', '18', '11', '12')
    return prefix in a_stock_prefixes


def normalize_stock_code(stock_code: str, market: Optional[str] = None) -> str:
    """按市场语义标准化股票代码。"""
    raw = str(stock_code).strip()
    if not raw:
        return ""

    market = (market or "").strip().lower()
    if market in ("sh", "sz"):
        return _normalize_mainland_code(raw) or raw
    if market == "hk":
        return normalize_hk_code(raw) or raw
    if market == "us":
        return raw.upper()

    hk_code = normalize_hk_code(raw)
    if hk_code:
        return hk_code

    mainland_code = _normalize_mainland_code(raw)
    if mainland_code:
        return mainland_code

    if is_us_code(raw):
        return raw.upper()
    return raw


def detect_stock_type(stock_code: str) -> StockType:
    """自动检测股票类型"""
    stock_code = str(stock_code).strip()
    if is_hk_code(stock_code):
        return StockType.HK
    if is_us_code(stock_code):
        return StockType.US
    if is_etf_code(stock_code):
        return StockType.ETF
    if is_a_stock_code(stock_code):
        return StockType.A_STOCK
    return StockType.UNKNOWN


def validate_stock_type(stock_code: str, user_market: str) -> tuple[StockType, str]:
    """综合校验股票类型：结合用户传入的 market 和自动检测结果。

    Returns:
        (StockType, market_str): 最终确定的股票类型和市场字符串
    """
    stock_code = normalize_stock_code(stock_code, user_market)
    user_market = str(user_market).strip().lower()
    detected_type = detect_stock_type(stock_code)

    market_to_type = {
        'sh': StockType.A_STOCK,
        'sz': StockType.A_STOCK,
        'hk': StockType.HK,
        'us': StockType.US,
    }
    user_type = market_to_type.get(user_market, StockType.UNKNOWN)

    if user_type == StockType.A_STOCK and detected_type == StockType.ETF:
        final_type = StockType.ETF
        final_market = user_market
    elif detected_type == StockType.UNKNOWN:
        _LOGGER.warning(
            f"无法自动检测股票类型: code={stock_code}, 使用用户指定的 market={user_market}"
        )
        final_type = user_type
        final_market = user_market
    elif user_type != detected_type and user_type != StockType.UNKNOWN:
        _LOGGER.warning(
            f"股票类型不一致: code={stock_code}, user_market={user_market}({user_type.value}), "
            f"detected={detected_type.value}, 优先使用检测结果"
        )
        final_type = detected_type
        if detected_type == StockType.HK:
            final_market = 'hk'
        elif detected_type == StockType.US:
            final_market = 'us'
        elif detected_type in (StockType.A_STOCK, StockType.ETF):
            final_market = user_market if user_market in ('sh', 'sz') else 'sh'
        else:
            final_market = user_market
    else:
        final_type = detected_type
        final_market = user_market

    return final_type, final_market


def market_to_stock_type(market: str) -> StockType:
    """市场字符串 → StockType"""
    market_map = {
        'sh': StockType.A_STOCK,
        'sz': StockType.A_STOCK,
        'hk': StockType.HK,
        'us': StockType.US,
    }
    return market_map.get(market.lower() if market else '', StockType.UNKNOWN)


def stock_type_to_market(stock_type: StockType, default: str = 'sh') -> str:
    """StockType → 市场字符串"""
    type_map = {
        StockType.A_STOCK: default,
        StockType.ETF: default,
        StockType.HK: 'hk',
        StockType.US: 'us',
    }
    return type_map.get(stock_type, default)
