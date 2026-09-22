"""所属板块（belong_board）结果的统一 schema。

各 provider 的原始返回差异很大：

- Efinance：股票名称/股票代码/板块代码/板块名称/板块涨幅（行业、地域、概念混排，无类型列）
- Akshare：股票代码/股票名称/板块名称（东财个股信息接口，仅行业）
- Tushare：股票代码/股票名称/行业/市场/上市日期（离线快照，仅行业）
- Baostock：股票代码/股票名称/行业/...（申万行业）

调用方之前只能猜列名（拿不到 ``板块名称`` 就取第一列，结果把 ``601298.SH`` 当板块名去查
成分股）。统一后保证前几列固定为 板块名称 / 板块代码 / 板块类型 / 股票代码 / 股票名称，
原有其它列原样保留在后面。``板块类型`` ∈ {industry, region, concept, unknown}。
"""

from __future__ import annotations

from typing import Iterable, Optional

import pandas as pd

BOARD_NAME_COL = "板块名称"
BOARD_CODE_COL = "板块代码"
BOARD_TYPE_COL = "板块类型"
STOCK_CODE_COL = "股票代码"
STOCK_NAME_COL = "股票名称"

BELONG_BOARD_LEADING_COLUMNS = (
    BOARD_NAME_COL,
    BOARD_CODE_COL,
    BOARD_TYPE_COL,
    STOCK_CODE_COL,
    STOCK_NAME_COL,
)

BOARD_TYPE_INDUSTRY = "industry"
BOARD_TYPE_REGION = "region"
BOARD_TYPE_CONCEPT = "concept"
BOARD_TYPE_UNKNOWN = "unknown"

_INDUSTRY_ALIASES = ("行业", "所处行业", "所属行业", "行业名称", "industry")
_STOCK_CODE_ALIASES = ("代码", "证券代码", "ts_code", "symbol", "code")
_STOCK_NAME_ALIASES = ("名称", "股票简称", "证券简称", "code_name", "name")


def infer_eastmoney_board_types(names: Iterable[object]) -> list[str]:
    """东财 slist 接口（efinance.get_belong_board）的顺序约定：

    第 1 行是所属行业板块；名称以“板块”结尾的是地域板块（如“贵州板块”）；其余为概念板块
    （含 上证50_/HS300_ 这类指数成分伪概念）。
    """
    types: list[str] = []
    for index, name in enumerate(names):
        text = str(name or "")
        if text.endswith("板块"):
            types.append(BOARD_TYPE_REGION)
        elif index == 0:
            types.append(BOARD_TYPE_INDUSTRY)
        else:
            types.append(BOARD_TYPE_CONCEPT)
    return types


def normalize_belong_board(
    df: Optional[pd.DataFrame],
    *,
    default_type: Optional[str] = None,
) -> Optional[pd.DataFrame]:
    """把任一 provider 的所属板块结果统一成标准列；无法识别的帧原样返回。

    - 没有 ``板块名称`` 但有行业列（行业/所处行业/industry…）→ 以行业作为板块名称，类型 industry
    - 有 ``板块代码`` 且形如 BKxxxx（东财）而无类型列 → 按东财顺序约定推断类型
    - ``default_type`` 指定时，缺失的类型列统一填该值
    """
    if df is None or not isinstance(df, pd.DataFrame) or df.empty:
        return df

    out = df.copy()

    if BOARD_NAME_COL not in out.columns:
        industry_col = next((c for c in _INDUSTRY_ALIASES if c in out.columns), None)
        if industry_col is None:
            return out
        out[BOARD_NAME_COL] = out[industry_col]
        if default_type is None:
            default_type = BOARD_TYPE_INDUSTRY

    if BOARD_CODE_COL not in out.columns:
        out[BOARD_CODE_COL] = None

    if BOARD_TYPE_COL not in out.columns:
        if default_type is not None:
            out[BOARD_TYPE_COL] = default_type
        elif out[BOARD_CODE_COL].astype(str).str.startswith("BK").any():
            out[BOARD_TYPE_COL] = infer_eastmoney_board_types(out[BOARD_NAME_COL].tolist())
        else:
            out[BOARD_TYPE_COL] = BOARD_TYPE_UNKNOWN

    for target, aliases in ((STOCK_CODE_COL, _STOCK_CODE_ALIASES), (STOCK_NAME_COL, _STOCK_NAME_ALIASES)):
        if target not in out.columns:
            alias = next((c for c in aliases if c in out.columns), None)
            if alias is not None:
                out[target] = out[alias]

    leading = [c for c in BELONG_BOARD_LEADING_COLUMNS if c in out.columns]
    rest = [c for c in out.columns if c not in leading]
    return out[leading + rest].reset_index(drop=True)


def industry_board_name(df: Optional[pd.DataFrame]) -> Optional[str]:
    """从（已统一的）所属板块帧里取行业板块名；没有明确行业行时退回第一行的板块名称。"""
    if df is None or not isinstance(df, pd.DataFrame) or df.empty or BOARD_NAME_COL not in df.columns:
        return None
    if BOARD_TYPE_COL in df.columns:
        industry_rows = df[df[BOARD_TYPE_COL] == BOARD_TYPE_INDUSTRY]
        if not industry_rows.empty:
            value = industry_rows.iloc[0][BOARD_NAME_COL]
            return str(value) if pd.notna(value) else None
    value = df.iloc[0][BOARD_NAME_COL]
    return str(value) if pd.notna(value) else None
