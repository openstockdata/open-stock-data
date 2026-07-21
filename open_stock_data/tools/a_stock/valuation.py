"""
A股估值与财务模块

包含市场PE分位、行业PE对比、分红历史、基金持仓、财报日历、财务指标等工具
"""

import logging
import pandas as pd
import akshare as ak
from datetime import datetime
from pydantic import Field

from ...utils import (
    get_data_manager,
    format_source_name,
    field_symbol,
    recent_trade_date,
    resolve_field,
)
from ...client import get_default_client

_LOGGER = logging.getLogger(__name__)


# ==================== 市场PE分位 ====================

def stock_market_pe_percentile():
    try:
        manager = get_data_manager()
        pe_df = manager.fetch_akshare(ak.stock_a_ttm_lyr, ttl=43200)
        pb_df = manager.fetch_akshare(ak.stock_a_all_pb, ttl=43200)

        if pe_df is None or pe_df.empty:
            return "获取市场PE数据失败"

        latest_pe = pe_df.iloc[-1]
        pe_ttm_median = latest_pe.get("middlePETTM", None)
        pe_ttm_avg = latest_pe.get("averagePETTM", None)
        pe_percentile_all = latest_pe.get("quantileInAllHistoryMiddlePeTtm", None)
        pe_percentile_10y = latest_pe.get("quantileInRecent10YearsMiddlePeTtm", None)

        lines = [
            "# A股市场估值分位",
            "# 数据来源: akshare (乐咕乐股)",
        ]

        # 市盈率数据表
        lines.append("# 市盈率(PE-TTM)")
        pe_header = ["指标", "值", "估值水平"]
        pe_rows = []
        pe_rows.append(["中位数PE", f"{pe_ttm_median:.2f}" if pe_ttm_median else "-", "-"])
        pe_rows.append(["平均PE", f"{pe_ttm_avg:.2f}" if pe_ttm_avg else "-", "-"])
        if pe_percentile_all is not None:
            pct = pe_percentile_all * 100
            level = "极度高估" if pct > 80 else "高估" if pct > 60 else "合理" if pct > 40 else "低估" if pct > 20 else "极度低估"
            pe_rows.append(["历史分位(全部)", f"{pct:.1f}%", level])
        if pe_percentile_10y is not None:
            pct = pe_percentile_10y * 100
            level = "极度高估" if pct > 80 else "高估" if pct > 60 else "合理" if pct > 40 else "低估" if pct > 20 else "极度低估"
            pe_rows.append(["历史分位(近10年)", f"{pct:.1f}%", level])
        lines.append(",".join(pe_header))
        lines.extend([",".join(row) for row in pe_rows])

        # 市净率数据表
        if pb_df is not None and not pb_df.empty:
            latest_pb = pb_df.iloc[-1]
            pb_median = latest_pb.get("middlePB", None)
            pb_percentile_all = latest_pb.get("quantileInAllHistoryMiddlePB", None)
            pb_percentile_10y = latest_pb.get("quantileInRecent10YearsMiddlePB", None)

            lines.append("# 市净率(PB)")
            pb_header = ["指标", "值", "估值水平"]
            pb_rows = []
            if pb_median:
                pb_rows.append(["中位数PB", f"{pb_median:.2f}", "-"])
            if pb_percentile_all is not None:
                pct = pb_percentile_all * 100
                level = "极度高估" if pct > 80 else "高估" if pct > 60 else "合理" if pct > 40 else "低估" if pct > 20 else "极度低估"
                pb_rows.append(["历史分位(全部)", f"{pct:.1f}%", level])
            if pb_percentile_10y is not None:
                pct = pb_percentile_10y * 100
                pb_rows.append(["历史分位(近10年)", f"{pct:.1f}%", "-"])
            lines.append(",".join(pb_header))
            lines.extend([",".join(row) for row in pb_rows])

        # 市场估值建议
        lines.append("# 市场估值建议")
        if pe_percentile_10y is not None:
            pct = pe_percentile_10y * 100
            if pct < 30:
                suggestion = "当前市场估值处于历史低位，长期投资价值凸显"
            elif pct > 70:
                suggestion = "当前市场估值处于历史高位，需注意回调风险"
            else:
                suggestion = "当前市场估值处于历史中位，选股重于择时"
            lines.append("建议")
            lines.append(suggestion)

        return "\n".join(lines)
    except Exception as e:
        return f"获取市场PE分位失败: {e}"


# ==================== 行业PE对比 ====================

def stock_industry_pe(
    date: str = Field("", description="日期(可选)，格式: 20250210，默认最新"),
):
    if not isinstance(date, str) or not date:
        date = recent_trade_date().strftime("%Y%m%d")

    result = get_default_client().industry_pe(date)
    df = result.data
    source = format_source_name(result.source)
    data_date = str(df.attrs.get('data_date') or date)
    requested_date = str(df.attrs.get('requested_date') or date)

    df_l1 = df[df["行业层级"] == 1.0].copy()
    if df_l1.empty:
        df_l1 = df.head(20)

    df_l1 = df_l1.sort_values("静态市盈率-加权平均", ascending=True)

    lines = [
        "# A股行业PE对比",
        f"# 数据来源: {source}",
        f"# 数据日期: {data_date}",
    ]
    if requested_date and requested_date != data_date:
        lines.append(f"# 请求日期: {requested_date}")

    cols = ["行业名称", "公司数量", "静态市盈率-加权平均", "静态市盈率-中位数"]
    df_out = df_l1[cols].copy()
    df_out.columns = ["行业", "公司数", "加权PE", "中位PE"]
    lines.append(df_out.to_csv(index=False, float_format="%.2f").strip())

    # 估值提示
    low_pe = df_l1.head(3)["行业名称"].tolist()
    high_pe = df_l1.tail(3)["行业名称"].tolist()
    lines.append("# 估值提示")
    lines.append("类型,行业")
    lines.append(f"低估值行业,{' '.join(low_pe)}")
    lines.append(f"高估值行业,{' '.join(high_pe)}")

    return "\n".join(lines)


# ==================== 分红历史 ====================

def stock_dividend_history(
    symbol: str = field_symbol,
    limit: int = Field(10, description="返回数量限制"),
):
    symbol = resolve_field(symbol, "")
    limit = resolve_field(limit, 10)
    result = get_default_client().dividend_history(symbol)

    source = format_source_name(result.source)
    lines = [f"# {symbol} 分红历史", f"# 数据来源: {source}"]

    df = result.data.head(limit)

    dividend_header = ["公告日期", "送股", "转增", "派息(元/10股)", "进度", "除权除息日"]
    dividend_rows = []
    has_valid_data = False
    for _, row in df.iterrows():
        date = str(row.get("公告日期", "-"))[:10]
        song = row.get("送股", 0) or 0
        zhuan = row.get("转增", 0) or 0
        pai = row.get("派息", 0) or 0
        status = row.get("进度", "-")
        ex_date = str(row.get("除权除息日", "-"))[:10] if pd.notna(row.get("除权除息日")) else "-"
        if pai > 0 or song > 0 or zhuan > 0:
            has_valid_data = True
        dividend_rows.append([date, str(song), str(zhuan), f"{pai:.2f}", str(status), ex_date])

    if not has_valid_data:
        _LOGGER.warning(f"[stock_dividend_history] {symbol} 分红数据字段全为空, 列名: {list(df.columns)}")

    lines.append(",".join(dividend_header))
    lines.extend([",".join(row) for row in dividend_rows])

    return "\n".join(lines)


# ==================== 基金持仓 ====================

def stock_institutional_holdings(
    date: str = Field("", description="报告期，格式: 20240930，默认最新季度"),
    limit: int = Field(30, description="返回数量限制"),
):
    date = resolve_field(date, "")
    limit = resolve_field(limit, 30)

    result = get_default_client().fund_holder("", date=date)
    df = result.data.head(limit)
    source = format_source_name(result.source)

    lines = [
        "# 基金重仓股",
        f"# 数据来源: {source}",
    ]

    cols = ["股票代码", "股票简称", "持有基金家数", "持股总数", "持股市值", "持股变化", "持股变动比例"]
    available_cols = [c for c in cols if c in df.columns]

    if available_cols:
        df_out = df[available_cols].copy()
        if "持股市值" in df_out.columns:
            df_out["持股市值"] = (df_out["持股市值"] / 1e8).round(2)
        if "持股总数" in df_out.columns:
            df_out["持股总数"] = (df_out["持股总数"] / 1e4).round(2)
        rename_map = {
            "股票代码": "代码", "股票简称": "名称", "持有基金家数": "基金数",
            "持股总数": "持股(万)", "持股市值": "市值(亿)",
            "持股变化": "变化", "持股变动比例": "变动%",
        }
        df_out = df_out.rename(columns=rename_map)
        lines.append(df_out.to_csv(index=False, float_format="%.2f").strip())
    else:
        lines.append(df.to_csv(index=False, float_format="%.2f").strip())

    # 持仓变化统计
    if "持股变化" in df.columns:
        increase = len(df[df["持股变化"] == "增仓"])
        decrease = len(df[df["持股变化"] == "减仓"])
        new_hold = len(df[df["持股变化"] == "新进"])
        lines.append("# 持仓变化统计")
        lines.append("增仓,减仓,新进")
        lines.append(f"{increase},{decrease},{new_hold}")

    return "\n".join(lines)


# ==================== 财报日历 ====================

def stock_earnings_calendar(
    period: str = Field("", description="报告期，如: 2024年报、2024三季报，默认最新"),
    limit: int = Field(50, description="返回数量限制"),
):
    try:
        period = resolve_field(period, "")
        limit = resolve_field(limit, 50)
        if not isinstance(period, str) or not period:
            now = datetime.now()
            year = now.year
            month = now.month
            if month <= 4:
                period = f"{year-1}年报"
            elif month <= 8:
                period = f"{year}半年报"
            elif month <= 10:
                period = f"{year}三季报"
            else:
                period = f"{year}年报"

        df = get_data_manager().fetch_akshare(
            ak.stock_report_disclosure,
            market="沪深京",
            period=period,
            ttl=43200,
        )
        if df is None or df.empty:
            return f"未获取到财报披露数据，报告期: {period}"

        lines = [
            f"# 财报披露日历 ({period})",
            "# 数据来源: akshare (巨潮资讯)",
        ]

        if "首次预约时间" in df.columns:
            df = df.sort_values("首次预约时间")

        today = datetime.now().strftime("%Y-%m-%d")
        today_count = len(df[df.get("首次预约时间", "").astype(str).str.startswith(today)]) if "首次预约时间" in df.columns else 0

        lines.append(f"# 今日披露: {today_count}家")
        lines.append("# 即将披露")

        df = df.head(limit)
        cols_available = [c for c in ["股票代码", "股票简称", "首次预约时间", "实际披露时间", "修改次数"] if c in df.columns]
        if cols_available:
            lines.append(df[cols_available].to_csv(index=False).strip())
        else:
            lines.append(df.to_csv(index=False).strip())

        return "\n".join(lines)
    except Exception as e:
        return f"获取财报日历失败: {e}"


# ==================== 财务指标对比 ====================

def stock_financial_compare(
    symbol: str = field_symbol,
):
    try:
        symbol = resolve_field(symbol, "")
        df = get_data_manager().fetch_akshare(
            ak.stock_financial_analysis_indicator,
            symbol=symbol,
            start_year=str(datetime.now().year - 2),
            ttl=86400 * 7,
        )
        if df is None or df.empty:
            return f"未获取到 {symbol} 的财务指标数据"

        lines = [f"# {symbol} 财务指标分析", "# 数据来源: akshare"]

        df = df.head(4)

        # 盈利能力
        lines.append("# 盈利能力")
        profit_cols = ["日期", "净资产收益率(%)", "销售毛利率(%)", "销售净利率(%)", "总资产利润率(%)"]
        profit_cols = [c for c in profit_cols if c in df.columns]
        if profit_cols:
            lines.append(df[profit_cols].to_csv(index=False, float_format="%.2f").strip())

        # 成长能力
        lines.append("# 成长能力")
        growth_cols = ["日期", "主营业务收入增长率(%)", "净利润增长率(%)", "净资产增长率(%)", "总资产增长率(%)"]
        growth_cols = [c for c in growth_cols if c in df.columns]
        if growth_cols:
            lines.append(df[growth_cols].to_csv(index=False, float_format="%.2f").strip())

        # 偿债能力
        lines.append("# 偿债能力")
        debt_cols = ["日期", "流动比率", "速动比率", "资产负债率(%)", "股东权益比率(%)"]
        debt_cols = [c for c in debt_cols if c in df.columns]
        if debt_cols:
            lines.append(df[debt_cols].to_csv(index=False, float_format="%.2f").strip())

        # 运营能力
        lines.append("# 运营能力")
        ops_cols = ["日期", "应收账款周转率(次)", "存货周转率(次)", "总资产周转率(次)"]
        ops_cols = [c for c in ops_cols if c in df.columns]
        if ops_cols:
            lines.append(df[ops_cols].to_csv(index=False, float_format="%.2f").strip())

        # 每股指标
        lines.append("# 每股指标")
        share_cols = ["日期", "摊薄每股收益(元)", "每股净资产_调整前(元)", "每股经营性现金流(元)"]
        share_cols = [c for c in share_cols if c in df.columns]
        if share_cols:
            lines.append(df[share_cols].to_csv(index=False, float_format="%.4f").strip())

        # 趋势分析
        if len(df) >= 2:
            latest = df.iloc[0]
            prev = df.iloc[1]
            lines.append("# 趋势分析")
            trend_header = ["指标", "变化", "值", "评价"]
            trend_rows = []

            if "净资产收益率(%)" in df.columns:
                roe_change = (latest["净资产收益率(%)"] or 0) - (prev["净资产收益率(%)"] or 0)
                trend = "↑" if roe_change > 0 else "↓" if roe_change < 0 else "→"
                trend_rows.append(["ROE变化", trend, f"{abs(roe_change):.2f}%", "-"])

            if "净利润增长率(%)" in df.columns and pd.notna(latest.get("净利润增长率(%)")):
                growth = latest["净利润增长率(%)"]
                level = "高速增长" if growth > 30 else "稳定增长" if growth > 0 else "下滑"
                trend_rows.append(["净利润增速", "-", f"{growth:.2f}%", level])

            if "资产负债率(%)" in df.columns:
                debt_ratio = latest["资产负债率(%)"]
                risk = "高" if debt_ratio > 70 else "中" if debt_ratio > 50 else "低"
                trend_rows.append(["资产负债率", "-", f"{debt_ratio:.2f}%", f"风险{risk}"])

            if trend_rows:
                lines.append(",".join(trend_header))
                lines.extend([",".join(row) for row in trend_rows])

        return "\n".join(lines)
    except Exception as e:
        return f"获取 {symbol} 财务指标失败: {e}"
