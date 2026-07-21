"""
A股市场资金流模块

包含涨停股池、龙虎榜、板块资金流、北向资金、融资融券、大宗交易等工具
"""

import logging
import pandas as pd
import akshare as ak
from pydantic import Field

from ...utils import (
    get_data_manager,
    format_source_name,
    get_akshare_source,
    field_symbol,
    recent_trade_date,
    fetch_with_retry,
    _detect_stock_market,
    resolve_field,
)
from ...client import get_default_client
from ...exceptions import AllSourcesFailed

_LOGGER = logging.getLogger(__name__)


def _fetch_sector_fund_flow_rank(indicator: str, sector_type: str):
    return fetch_with_retry(
        ak.stock_sector_fund_flow_rank,
        max_retries=2,
        delay=2.0,
        initial_delay=0.5,
        indicator=indicator,
        sector_type=sector_type,
    )


def _fetch_board_industry_name():
    return fetch_with_retry(
        ak.stock_board_industry_name_em,
        max_retries=2,
        delay=2.0,
        initial_delay=0.5,
    )


def _fetch_board_industry_hist(period: str):
    return fetch_with_retry(
        ak.stock_board_industry_hist_em,
        max_retries=2,
        delay=2.0,
        initial_delay=0.5,
        period=period,
    )


def _fetch_board_concept_name():
    return fetch_with_retry(
        ak.stock_board_concept_name_em,
        max_retries=2,
        delay=2.0,
        initial_delay=0.5,
    )


# ==================== 涨停/强势股池 ====================

def stock_zt_pool(
    pool_type: str = Field("涨停", description="股池类型: '涨停'(涨停股池), '强势'(强势股池), '跌停'(跌停股池), '昨日涨停'(昨日涨停股今日表现)"),
    date: str = Field("", description="交易日日期(可选)，默认为最近的交易日，格式: 20251231"),
    limit: int = Field(50, description="返回数量(int,30-100)", strict=False),
):
    pool_type = resolve_field(pool_type, "涨停")
    limit = resolve_field(limit, 50)
    if not isinstance(date, str) or not date:
        date = recent_trade_date().strftime("%Y%m%d")

    try:
        manager = get_data_manager()
        if pool_type == "强势":
            dfs = manager.fetch_akshare(ak.stock_zt_pool_strong_em, date=date, ttl=1200)
            title = "强势股池"
        elif pool_type == "跌停":
            dfs = manager.fetch_akshare(ak.stock_zt_pool_dtgc_em, date=date, ttl=1200)
            title = "跌停股池"
        elif pool_type == "昨日涨停":
            dfs = manager.fetch_akshare(ak.stock_zt_pool_zbgc_em, date=date, ttl=1200)
            title = "昨日涨停股今日表现"
        else:
            dfs = manager.fetch_akshare(ak.stock_zt_pool_em, date=date, ttl=1200)
            title = "涨停股池"

        if dfs is None or dfs.empty:
            return f"获取{title}数据失败"

        cnt = len(dfs)
        dfs.drop(columns=["序号", "流通市值", "总市值"], inplace=True, errors='ignore')
        if "成交额" in dfs.columns:
            dfs.sort_values("成交额", ascending=False, inplace=True)
        dfs = dfs.head(int(limit))
        lines = [f"# {title}", f"# 数据来源: akshare", f"# 共{cnt}只股票"]
        lines.append(dfs.to_csv(index=False, float_format="%.2f").strip())
        return "\n".join(lines)
    except Exception as exc:
        return f"获取股池数据失败: {exc}"


# ==================== 龙虎榜 ====================

def stock_lhb_ggtj_sina(
    days: str = Field("5", description="统计最近天数，仅支持: [5/10/30/60]"),
    limit: int = Field(50, description="返回数量(int,30-100)", strict=False),
):
    days = resolve_field(days, "5")
    limit = resolve_field(limit, 50)
    result = get_default_client().billboard(days)

    dfs = result.data.head(int(limit))
    lines = [
        "# 龙虎榜统计",
        f"# 数据来源: {format_source_name(result.source)}",
        dfs.to_csv(index=False, float_format="%.2f").strip(),
    ]
    return "\n".join(lines)


# ==================== 板块资金流 ====================

def stock_sector_fund_flow_rank(
    days: str = Field("今日", description="天数，仅支持: {'今日','5日','10日'}，如果需要获取今日数据，请确保是交易日"),
    cate: str = Field("行业资金流", description="仅支持: {'行业资金流','概念资金流','地域资金流'}"),
):
    days = resolve_field(days, "今日")
    cate = resolve_field(cate, "行业资金流")
    primary_ttl = 600 if days == "今日" else 3600
    # 主数据源：东方财富板块资金流
    try:
        dfs = get_data_manager().fetch_with_cache(
            _fetch_sector_fund_flow_rank,
            days,
            cate,
            ttl=primary_ttl,
            key=f"stock_sector_fund_flow_rank:{days}:{cate}",
        )
        if dfs is not None and not dfs.empty:
            if "今日涨跌幅" in dfs.columns:
                dfs.sort_values("今日涨跌幅", ascending=False, inplace=True)
            dfs.drop(columns=["序号"], inplace=True, errors='ignore')
            dfs = pd.concat([dfs.head(20), dfs.tail(20)])
            lines = [f"# {cate}", f"# 数据来源: {get_akshare_source(ak.stock_sector_fund_flow_rank)}"]
            lines.append(dfs.to_csv(index=False, float_format="%.2f").strip())
            return "\n".join(lines)
    except Exception as e:
        _LOGGER.debug(f"主数据源获取{cate}失败: {e}")

    # 备用数据源：行业板块实时行情（仅支持行业板块+今日）
    if cate == "行业资金流":
        try:
            if days == "今日":
                dfs = get_data_manager().fetch_with_cache(
                    _fetch_board_industry_name,
                    ttl=600,
                    key="stock_board_industry_name_em:today",
                )
            else:
                dfs = get_data_manager().fetch_with_cache(
                    _fetch_board_industry_hist,
                    days.replace("日", ""),
                    ttl=3600,
                    key=f"stock_board_industry_hist_em:{days}",
                )
            if dfs is not None and not dfs.empty:
                if "涨跌幅" in dfs.columns:
                    dfs.sort_values("涨跌幅", ascending=False, inplace=True)
                elif "涨幅" in dfs.columns:
                    dfs.sort_values("涨幅", ascending=False, inplace=True)
                dfs.drop(columns=["排名"], inplace=True, errors='ignore')
                dfs = pd.concat([dfs.head(20), dfs.tail(20)])
                lines = [f"# {cate}", f"# 数据来源: {get_akshare_source(ak.stock_board_industry_name_em)}"]
                lines.append(dfs.to_csv(index=False, float_format="%.2f").strip())
                return "\n".join(lines)
        except Exception as e:
            _LOGGER.debug(f"备用行业板块数据源失败: {e}")

    # 第三备用：概念板块
    if cate == "概念资金流":
        try:
            dfs = get_data_manager().fetch_with_cache(
                _fetch_board_concept_name,
                ttl=600,
                key="stock_board_concept_name_em:today",
            )
            if dfs is not None and not dfs.empty:
                if "涨跌幅" in dfs.columns:
                    dfs.sort_values("涨跌幅", ascending=False, inplace=True)
                dfs.drop(columns=["排名"], inplace=True, errors='ignore')
                dfs = pd.concat([dfs.head(20), dfs.tail(20)])
                lines = [f"# {cate}", f"# 数据来源: {get_akshare_source(ak.stock_board_concept_name_em)}"]
                lines.append(dfs.to_csv(index=False, float_format="%.2f").strip())
                return "\n".join(lines)
        except Exception as e:
            _LOGGER.debug(f"备用概念板块数据源失败: {e}")

    return f"获取{cate}数据失败（数据源可能暂时不可用，请稍后重试）"


# ==================== 北向资金 ====================

def stock_north_flow(
    indicator: str = Field("北向资金", description="指标类型，可选: '北向资金', '沪股通', '深股通'"),
):
    indicator = resolve_field(indicator, "北向资金")
    try:
        df = get_data_manager().fetch_akshare(ak.stock_hsgt_fund_flow_summary_em, ttl=600)
        if df is None or df.empty:
            return "获取北向资金数据失败"

        if indicator == "沪股通":
            if "沪股通-净流入" in df.columns:
                df = df[["日期", "沪股通-净流入"]].copy()
                df.columns = ["日期", "净流入(亿)"]
        elif indicator == "深股通":
            if "深股通-净流入" in df.columns:
                df = df[["日期", "深股通-净流入"]].copy()
                df.columns = ["日期", "净流入(亿)"]
        else:
            if "北向资金-净流入" in df.columns:
                df = df[["日期", "北向资金-净流入"]].copy()
                df.columns = ["日期", "净流入(亿)"]
            elif "沪股通-净流入" in df.columns and "深股通-净流入" in df.columns:
                df["净流入(亿)"] = df["沪股通-净流入"] + df["深股通-净流入"]
                df = df[["日期", "净流入(亿)"]].copy()

        df = df.head(30)
        lines = [f"# {indicator}流向", f"# 数据来源: akshare"]
        lines.append(df.to_csv(index=False, float_format="%.2f").strip())
        return "\n".join(lines)
    except Exception as exc:
        return f"获取北向资金数据失败: {exc}"


# ==================== 融资融券 ====================

def stock_margin_trading(
    symbol: str = Field("", description="股票代码（可选），留空则获取市场整体数据"),
    market: str = Field("sh", description="市场: 'sh'(沪市), 'sz'(深市)"),
    limit: int = Field(30, description="返回数据条数"),
):
    try:
        symbol = resolve_field(symbol, "")
        market = resolve_field(market, "sh")
        limit = resolve_field(limit, 30)
        if isinstance(symbol, str) and symbol:
            stock_market = _detect_stock_market(symbol)
            result = get_default_client().margin_detail(symbol, stock_market)
            df = result.data
            source = format_source_name(result.source)
            is_ratio = df.attrs.get('is_ratio_data', False)

            if is_ratio:
                lines = [
                    f"# {symbol} 融资融券比例",
                    f"# 数据来源: {source}",
                    "# 注: 交易所明细接口暂不可用，以下为融资融券比例数据",
                ]
                lines.append(df.head(limit).to_csv(index=False, float_format="%.2f").strip())
                return "\n".join(lines)
            else:
                lines = [f"# {symbol} 融资融券", f"# 数据来源: {source}"]
                lines.append(df.head(limit).to_csv(index=False, float_format="%.2f").strip())
                return "\n".join(lines)
        else:
            if market == "sh":
                df = get_data_manager().fetch_akshare(ak.stock_margin_sse, start_date="", end_date="", ttl=1800)
            else:
                df = get_data_manager().fetch_akshare(ak.stock_margin_szse, start_date="", end_date="", ttl=1800)

            if df is None or df.empty:
                return f"获取{market}市场融资融券数据失败"

            market_name = "沪市" if market == "sh" else "深市"
            df = df.tail(limit)
            lines = [f"# {market_name}融资融券", f"# 数据来源: akshare"]
            lines.append(df.to_csv(index=False, float_format="%.2f").strip())
            return "\n".join(lines)
    except AllSourcesFailed:
        raise  # 个股融资融券三源全失败：按契约向上抛，不吞成错误字符串
    except Exception as exc:
        return f"获取融资融券数据失败: {exc}"


# ==================== 大宗交易 ====================

def stock_block_trade(
    symbol: str = Field("", description="股票代码（可选），留空则获取当日全市场数据"),
    limit: int = Field(50, description="返回数据条数"),
):
    try:
        symbol = resolve_field(symbol, "")
        limit = resolve_field(limit, 50)
        from datetime import datetime, timedelta
        end_date = datetime.now().strftime("%Y%m%d")
        start_date = (datetime.now() - timedelta(days=30)).strftime("%Y%m%d")

        if isinstance(symbol, str) and symbol:
            # 获取 A 股大宗交易明细，按股票代码过滤
            df = get_data_manager().fetch_akshare(
                ak.stock_dzjy_mrmx,
                symbol="A股",
                start_date=start_date,
                end_date=end_date,
                ttl=1800,
            )
            if df is not None and not df.empty:
                code_col = next((c for c in df.columns if "代码" in c), None)
                if code_col:
                    df = df[df[code_col].astype(str).str.contains(symbol)]
                if not df.empty:
                    lines = [f"# {symbol} 大宗交易", f"# 数据来源: akshare (东方财富)"]
                    lines.append(df.head(limit).to_csv(index=False, float_format="%.2f").strip())
                    return "\n".join(lines)
            return f"未找到股票 {symbol} 的大宗交易数据"
        else:
            df = get_data_manager().fetch_akshare(
                ak.stock_dzjy_mrtj,
                start_date=start_date,
                end_date=end_date,
                ttl=1800,
            )
            if df is None or df.empty:
                return "获取大宗交易数据失败"
            df = df.head(limit)
            lines = ["# 大宗交易统计", "# 数据来源: akshare (东方财富)"]
            lines.append(df.to_csv(index=False, float_format="%.2f").strip())
            return "\n".join(lines)
    except Exception as exc:
        return f"获取大宗交易数据失败: {exc}"


# ==================== 股东人数 ====================

def stock_holder_num(
    symbol: str = Field(description="股票代码，如: 300058, 600036"),
):
    symbol = resolve_field(symbol, "")
    try:
        df = get_data_manager().fetch_akshare(
            ak.stock_zh_a_gdhs_detail_em,
            symbol=symbol,
            ttl=86400,
        )
        if df is not None and not df.empty:
            lines = [f"# {symbol} 股东人数", f"# 数据来源: akshare"]
            lines.append(df.to_csv(index=False, float_format="%.2f").strip())
            return "\n".join(lines)
        return f"未找到股票 {symbol} 的股东人数数据"
    except Exception as exc:
        return f"获取股东人数数据失败: {exc}"
