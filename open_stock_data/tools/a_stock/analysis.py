"""
A股个股分析模块

包含筹码分布、多周期统计、资金流向、所属板块、板块成分股等工具
"""

import pandas as pd
from pydantic import Field

from ...utils import (
    get_data_manager,
    format_source_name,
    field_symbol,
    resolve_field,
)


# ==================== 筹码分布 ====================

def stock_chip(
    symbol: str = field_symbol,
):
    symbol = resolve_field(symbol, "")
    if symbol.startswith(('51', '15', '16', '50', '52', '56', '58', '11', '12')):
        return f"{symbol} 是ETF/LOF/基金/可转债等产品，不支持筹码分布查询。筹码分布仅适用于普通A股。"

    try:
        manager = get_data_manager()
        chip = manager.get_chip_distribution(symbol)
        if chip is None:
            return f"未找到 {symbol} 的筹码分布数据，请确认是有效的A股代码"

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
    except Exception as e:
        return f"获取 {symbol} 筹码分布失败: {e}"


# ==================== 资金流向 ====================

def stock_fund_flow(
    symbol: str = field_symbol,
):
    try:
        symbol = resolve_field(symbol, "")
        manager = get_data_manager()
        dfs = manager.get_fund_flow(symbol)

        if dfs is None or dfs.empty:
            return f"Not Found for {symbol}"

        source = format_source_name(dfs.attrs.get('source', ''))
        dfs = dfs.tail(10)

        lines = [f"# {symbol} 资金流向"]
        lines.append(f"# 数据来源: {source}")
        lines.append("# 近期资金流向")

        cols_to_show = [c for c in dfs.columns if c not in ["序号"]]
        csv_data = dfs.to_csv(columns=cols_to_show, index=False, float_format="%.2f").strip()
        return "\n".join(lines) + "\n" + csv_data
    except Exception as e:
        return f"获取 {symbol} 资金流向失败: {e}"


# ==================== 所属板块 ====================

def stock_sector_spot(
    symbol: str = field_symbol,
):
    try:
        symbol = resolve_field(symbol, "")
        manager = get_data_manager()
        boards = manager.get_belong_board(symbol)

        lines = [f"# {symbol} 所属板块"]

        if boards is not None and not boards.empty:
            source = format_source_name(boards.attrs.get('source', ''))
            lines.append(f"# 数据来源: {source}")
            lines.append("# 所属板块")
            lines.append(boards.to_csv(index=False, float_format="%.2f").strip())
        else:
            lines.append("未获取到板块数据")

        return "\n".join(lines)
    except Exception as e:
        return f"获取 {symbol} 板块信息失败: {e}"


# ==================== 板块成分股 ====================

def stock_board_cons(
    board_name: str = Field(description="板块名称，如: 酿酒行业、新能源、人工智能"),
    board_type: str = Field("industry", description="板块类型: industry(行业), concept(概念)"),
    limit: int = Field(30, description="返回数量(int)", strict=False),
):
    try:
        board_name = resolve_field(board_name, "")
        board_type = resolve_field(board_type, "industry")
        limit = resolve_field(limit, 30)
        manager = get_data_manager()
        dfs = manager.get_board_cons(board_name, board_type)

        if dfs is None or dfs.empty:
            return f"Not Found for {board_name}"

        source = format_source_name(dfs.attrs.get('source', ''))
        dfs = dfs.head(int(limit))
        dfs = dfs.drop(columns=["序号"], errors='ignore')

        lines = [f"# {board_name} 成分股", f"# 数据来源: {source}"]
        lines.append(dfs.to_csv(index=False, float_format="%.2f").strip())
        return "\n".join(lines)
    except Exception as e:
        return f"获取 {board_name} 成分股失败: {e}"
