"""
A股个股分析模块

包含筹码分布、多周期统计、资金流向、所属板块、板块成分股等工具
"""

import pandas as pd
from pydantic import Field

from ...utils import (
    format_source_name,
    field_symbol,
    resolve_field,
)
from ...client import get_default_client


# ==================== 筹码分布 ====================

def stock_chip(
    symbol: str = field_symbol,
):
    symbol = resolve_field(symbol, "")
    if symbol.startswith(('51', '15', '16', '50', '52', '56', '58', '11', '12')):
        return f"{symbol} 是ETF/LOF/基金/可转债等产品，不支持筹码分布查询。筹码分布仅适用于普通A股。"

    chip = get_default_client().chip_distribution(symbol).data
    status = chip.get_chip_status()
    chip_level = status.get('chip_level', '-') if status else '-'

    lines = [
        f"# {chip.code} 筹码分布",
        f"# 数据来源: {chip.source}",
        f"# 日期: {chip.date or '-'}",
        "获利比例(%),平均成本,90%成本低,90%成本高,90%集中度(%),70%成本低,70%成本高,70%集中度(%),筹码状态",
        f"{chip.profit_ratio or '-'},{chip.avg_cost or '-'},{chip.cost_90_low or '-'},{chip.cost_90_high or '-'},{chip.concentration_90 or '-'},{chip.cost_70_low or '-'},{chip.cost_70_high or '-'},{chip.concentration_70 or '-'},{chip_level}",
    ]
    return "\n".join(lines)


# ==================== 资金流向 ====================

def stock_fund_flow(
    symbol: str = field_symbol,
):
    symbol = resolve_field(symbol, "")
    result = get_default_client().fund_flow(symbol)
    dfs = result.data.tail(10)

    lines = [
        f"# {symbol} 资金流向",
        f"# 数据来源: {format_source_name(result.source)}",
        "# 近期资金流向",
    ]
    cols_to_show = [c for c in dfs.columns if c not in ["序号"]]
    csv_data = dfs.to_csv(columns=cols_to_show, index=False, float_format="%.2f").strip()
    return "\n".join(lines) + "\n" + csv_data


# ==================== 所属板块 ====================

def stock_sector_spot(
    symbol: str = field_symbol,
):
    symbol = resolve_field(symbol, "")
    result = get_default_client().belong_board(symbol)
    boards = result.data

    lines = [
        f"# {symbol} 所属板块",
        f"# 数据来源: {format_source_name(result.source)}",
        "# 所属板块",
        boards.to_csv(index=False, float_format="%.2f").strip(),
    ]
    return "\n".join(lines)


# ==================== 板块成分股 ====================

def stock_board_cons(
    board_name: str = Field(description="板块名称，如: 酿酒行业、新能源、人工智能"),
    board_type: str = Field("industry", description="板块类型: industry(行业), concept(概念)"),
    limit: int = Field(30, description="返回数量(int)", strict=False),
):
    board_name = resolve_field(board_name, "")
    board_type = resolve_field(board_type, "industry")
    limit = resolve_field(limit, 30)
    result = get_default_client().board_cons(board_name, board_type)

    dfs = result.data.head(int(limit)).drop(columns=["序号"], errors='ignore')
    lines = [
        f"# {board_name} 成分股",
        f"# 数据来源: {format_source_name(result.source)}",
        dfs.to_csv(index=False, float_format="%.2f").strip(),
    ]
    return "\n".join(lines)
