"""
A股股东与风险模块

包含限售解禁、股权质押、十大股东等工具
"""

import pandas as pd
import akshare as ak
from datetime import datetime, timedelta
from pydantic import Field

from ...utils import (
    get_data_manager,
    format_source_name,
    get_akshare_source,
    field_symbol,
    resolve_field,
)
from ...client import get_default_client


# ==================== 限售解禁 ====================

def stock_locked_shares(
    start_date: str = Field("", description="开始日期，格式: 20250211，默认今日"),
    end_date: str = Field("", description="结束日期，格式: 20250311，默认未来30天"),
    mode: str = Field("detail", description="模式: 'detail'(个股明细), 'summary'(每日汇总)"),
    limit: int = Field(50, description="返回数量限制"),
):
    try:
        start_date = resolve_field(start_date, "")
        end_date = resolve_field(end_date, "")
        mode = resolve_field(mode, "detail")
        limit = resolve_field(limit, 50)
        if not isinstance(start_date, str) or not start_date:
            start_date = datetime.now().strftime("%Y%m%d")
        if not isinstance(end_date, str) or not end_date:
            end_date = (datetime.now() + timedelta(days=30)).strftime("%Y%m%d")

        if mode == "summary":
            df = get_data_manager().fetch_akshare(
                ak.stock_restricted_release_summary_em,
                start_date=start_date,
                end_date=end_date,
                ttl=43200,
            )
            if df is None or df.empty:
                return f"未获取到限售解禁汇总数据 ({start_date} ~ {end_date})"

            lines = [
                f"# 限售解禁日历 (汇总)",
                f"# 数据来源: {get_akshare_source(ak.stock_restricted_release_summary_em)}",
                f"# 日期范围: {start_date} ~ {end_date}",
                "# 每日解禁汇总",
            ]

            cols = ["解禁时间", "当日解禁股票家数", "解禁数量", "实际解禁数量", "实际解禁市值"]
            available_cols = [c for c in cols if c in df.columns]
            if available_cols:
                df = df[available_cols].head(limit)
                if "实际解禁市值" in df.columns:
                    df["实际解禁市值(亿)"] = (df["实际解禁市值"] / 1e8).round(2)
                    df = df.drop(columns=["实际解禁市值"])
                lines.append(df.to_csv(index=False, float_format="%.2f").strip())
            else:
                lines.append(df.head(limit).to_csv(index=False, float_format="%.2f").strip())

            return "\n".join(lines)

        else:
            df = get_data_manager().fetch_akshare(
                ak.stock_restricted_release_detail_em,
                start_date=start_date,
                end_date=end_date,
                ttl=43200,
            )
            if df is None or df.empty:
                return f"未获取到限售解禁明细数据 ({start_date} ~ {end_date})"

            lines = [
                f"# 限售解禁日历 (明细)",
                f"# 数据来源: {get_akshare_source(ak.stock_restricted_release_detail_em)}",
                f"# 日期范围: {start_date} ~ {end_date}",
                f"# 共 {len(df)} 只股票即将解禁",
            ]

            if "实际解禁市值" in df.columns:
                df = df.sort_values("实际解禁市值", ascending=False)

            cols = ["股票代码", "股票简称", "解禁时间", "限售股类型", "实际解禁数量", "实际解禁市值", "占解禁前流通市值比例"]
            available_cols = [c for c in cols if c in df.columns]
            if available_cols:
                df_out = df[available_cols].head(limit).copy()
                if "实际解禁市值" in df_out.columns:
                    df_out["实际解禁市值(万)"] = (df_out["实际解禁市值"] / 1e4).round(2)
                    df_out = df_out.drop(columns=["实际解禁市值"])
                lines.append(df_out.to_csv(index=False, float_format="%.2f").strip())
            else:
                lines.append(df.head(limit).to_csv(index=False, float_format="%.2f").strip())

            if "占解禁前流通市值比例" in df.columns:
                high_impact = df[df["占解禁前流通市值比例"] > 10].head(5)
                if not high_impact.empty:
                    lines.append("# 高冲击风险股票(解禁占比>10%)")
                    hi_header = ["代码", "名称", "解禁占比(%)", "解禁日"]
                    hi_rows = []
                    for _, row in high_impact.iterrows():
                        code = row.get("股票代码", "-")
                        name = row.get("股票简称", "-")
                        ratio = row.get("占解禁前流通市值比例", 0)
                        unlock_date = row.get("解禁时间", "-")
                        hi_rows.append([str(code), str(name), f"{ratio:.1f}", str(unlock_date)])
                    lines.append(",".join(hi_header))
                    lines.extend([",".join(row) for row in hi_rows])

            return "\n".join(lines)
    except Exception as e:
        return f"获取限售解禁日历失败: {e}"


# ==================== 股权质押 ====================

def stock_pledge_ratio(
    mode: str = Field("industry", description="模式: 'industry'(行业统计), 'market'(市场整体趋势)"),
    limit: int = Field(30, description="返回数量限制"),
):
    try:
        mode = resolve_field(mode, "industry")
        limit = resolve_field(limit, 30)
        if mode == "industry":
            df = get_data_manager().fetch_akshare(
                ak.stock_gpzy_industry_data_em,
                ttl=86400,
            )
            if df is None or df.empty:
                return "获取行业质押数据失败"

            if "平均质押比例" in df.columns:
                df = df.sort_values("平均质押比例", ascending=False)

            lines = [
                "# 行业股权质押统计",
                f"# 数据来源: {get_akshare_source(ak.stock_gpzy_industry_data_em)}",
                "# 各行业质押情况 (按质押比例降序)",
            ]

            cols = ["行业", "公司家数", "质押总笔数", "平均质押比例", "质押总股本", "最新质押市值"]
            available_cols = [c for c in cols if c in df.columns]
            if available_cols:
                df_out = df[available_cols].head(limit).copy()
                if "最新质押市值" in df_out.columns:
                    df_out["质押市值(亿)"] = (df_out["最新质押市值"] / 1e8).round(2)
                    df_out = df_out.drop(columns=["最新质押市值"])
                if "质押总股本" in df_out.columns:
                    df_out["质押股本(亿股)"] = (df_out["质押总股本"] / 1e8).round(2)
                    df_out = df_out.drop(columns=["质押总股本"])
                lines.append(df_out.to_csv(index=False, float_format="%.2f").strip())
            else:
                lines.append(df.head(limit).to_csv(index=False, float_format="%.2f").strip())

            if "平均质押比例" in df.columns:
                high_pledge = df[df["平均质押比例"] > 20].head(5)
                if not high_pledge.empty:
                    lines.append("# 高质押风险行业(平均质押比例>20%)")
                    hp_header = ["行业", "平均质押(%)", "公司家数"]
                    hp_rows = []
                    for _, row in high_pledge.iterrows():
                        industry = row.get("行业", "-")
                        ratio = row.get("平均质押比例", 0)
                        count = row.get("公司家数", 0)
                        hp_rows.append([str(industry), f"{ratio:.1f}", str(count)])
                    lines.append(",".join(hp_header))
                    lines.extend([",".join(row) for row in hp_rows])

            return "\n".join(lines)

        else:
            df = get_data_manager().fetch_akshare(
                ak.stock_gpzy_profile_em,
                ttl=86400,
            )
            if df is None or df.empty:
                return "获取市场质押趋势数据失败"

            lines = [
                "# A股市场股权质押趋势",
                f"# 数据来源: {get_akshare_source(ak.stock_gpzy_profile_em)}",
            ]

            df = df.tail(limit)

            cols = ["统计时间", "A股质押总比例", "A股质押总股数", "A股质押总市值", "A股质押公司数量"]
            available_cols = [c for c in cols if c in df.columns]
            if available_cols:
                df_out = df[available_cols].copy()
                if "A股质押总市值" in df_out.columns:
                    df_out["质押市值(万亿)"] = (df_out["A股质押总市值"] / 1e12).round(2)
                    df_out = df_out.drop(columns=["A股质押总市值"])
                if "A股质押总股数" in df_out.columns:
                    df_out["质押股数(亿股)"] = (df_out["A股质押总股数"] / 1e8).round(2)
                    df_out = df_out.drop(columns=["A股质押总股数"])
                lines.append(df_out.to_csv(index=False, float_format="%.2f").strip())
            else:
                lines.append(df.to_csv(index=False, float_format="%.2f").strip())

            if "A股质押总比例" in df.columns and len(df) >= 2:
                latest = df.iloc[-1]["A股质押总比例"]
                prev = df.iloc[-2]["A股质押总比例"]
                change = latest - prev
                trend = "上升" if change > 0 else "下降" if change < 0 else "持平"
                lines.append("# 趋势分析")
                lines.append("最新质押比例(%),变化趋势,变化幅度(%)")
                lines.append(f"{latest:.2f},{trend},{change:+.2f}")

            return "\n".join(lines)
    except Exception as e:
        return f"获取股权质押数据失败: {e}"


# ==================== 十大股东 ====================

def stock_top10_holders(
    symbol: str = field_symbol,
    holder_type: str = Field("main", description="股东类型: 'main'(十大股东), 'circulate'(十大流通股东)"),
    limit: int = Field(30, description="返回数量限制（多期数据）"),
):
    symbol = resolve_field(symbol, "")
    holder_type = resolve_field(holder_type, "main")
    limit = resolve_field(limit, 30)
    result = get_default_client().top10_holders(symbol, holder_type=holder_type)
    df = result.data

    title = "十大流通股东" if holder_type == "circulate" else "十大股东"
    source = format_source_name(result.source)
    date_col = "截至日期"

    lines = [
        f"# {symbol} {title}",
        f"# 数据来源: {source}",
    ]

    if date_col in df.columns:
        dates = df[date_col].unique()[:3]
        for date in dates:
            period_df = df[df[date_col] == date].head(10)

            lines.append(f"# {date}")

            cols = ["编号", "股东名称", "持股数量", "持股比例", "股本性质"]

            available_cols = [c for c in cols if c in period_df.columns]
            if available_cols:
                df_out = period_df[available_cols].copy()
                if "持股数量" in df_out.columns:
                    df_out["持股(万股)"] = (df_out["持股数量"] / 1e4).round(2)
                    df_out = df_out.drop(columns=["持股数量"])
                lines.append(df_out.to_csv(index=False, float_format="%.2f").strip())
            else:
                lines.append(period_df.to_csv(index=False, float_format="%.2f").strip())

        if "股东总数" in df.columns:
            latest = df.iloc[0]
            holder_count = latest.get("股东总数")
            avg_shares = latest.get("平均持股数")
            if holder_count:
                lines.append("# 股东统计")
                lines.append("股东总数,平均持股")
                lines.append(f"{holder_count},{avg_shares or '-'}")

        if len(dates) >= 2:
            latest_date = dates[0]
            prev_date = dates[1]
            latest_holders = set(df[df[date_col] == latest_date]["股东名称"].tolist())
            prev_holders = set(df[df[date_col] == prev_date]["股东名称"].tolist())

            new_holders = latest_holders - prev_holders
            exit_holders = prev_holders - latest_holders

            if new_holders or exit_holders:
                lines.append(f"# 股东变化({prev_date}→{latest_date})")
                lines.append("类型,股东")
                if new_holders:
                    lines.append(f"新进,{' '.join(list(new_holders)[:5])}")
                if exit_holders:
                    lines.append(f"退出,{' '.join(list(exit_holders)[:5])}")
    else:
        lines.append(df.head(limit).to_csv(index=False, float_format="%.2f").strip())

    return "\n".join(lines)
