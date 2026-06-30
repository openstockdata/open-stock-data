"""
A股价格与行情模块

包含历史价格、实时行情等工具
"""

import logging
import pandas as pd
import akshare as ak
from datetime import datetime, timedelta
from pydantic import Field

from ...utils import (
    get_data_manager,
    format_source_name,
    field_symbol,
    field_market,
    resolve_field,
)
from ...data_provider import to_chinese_columns, validate_stock_type, normalize_stock_code, StockType
from ...indicators import add_technical_indicators, STOCK_PRICE_COLUMNS

_LOGGER = logging.getLogger(__name__)


# ==================== 辅助函数 ====================

def _fund_etf_hist_sina(symbol, market="sh", start_date="2025-01-01", period="daily"):
    """获取 ETF 历史数据"""
    dfs = ak.fund_etf_hist_sina(symbol=f"{market}{symbol}")
    if dfs is None or dfs.empty:
        return None
    dfs = to_chinese_columns(dfs)
    dfs["换手率"] = None
    dfs.index = pd.to_datetime(dfs["日期"], errors="coerce")
    return dfs.loc[start_date:]


def _infer_index_market_prefix(symbol: str) -> str:
    """Infer the Sina index market prefix from an A-share index code."""
    symbol = str(symbol).strip().lower()
    if symbol.startswith(("sh", "sz")):
        return symbol[:2]
    if symbol.startswith(("399", "980")):
        return "sz"
    return "sh"


def _resample_index_period(df: pd.DataFrame, period: str) -> pd.DataFrame:
    """Resample daily index bars to weekly/monthly bars for fallback sources."""
    if period == "daily":
        return df

    rule = {"weekly": "W", "monthly": "ME"}.get(period)
    if not rule:
        return df

    work = df.copy()
    work["日期"] = pd.to_datetime(work["日期"], errors="coerce")
    work = work.dropna(subset=["日期"]).sort_values("日期")
    if work.empty:
        return work

    agg_map = {
        "开盘": "first",
        "最高": "max",
        "最低": "min",
        "收盘": "last",
        "成交量": "sum",
    }
    if "成交额" in work.columns:
        agg_map["成交额"] = "sum"

    grouped = work.set_index("日期").resample(rule).agg(agg_map).dropna(subset=["收盘"]).reset_index()
    grouped["日期"] = grouped["日期"].dt.strftime("%Y-%m-%d")
    return grouped


def _index_hist_sina(symbol: str, start_date: str, period: str) -> pd.DataFrame | None:
    """Fetch A-share index history from Sina as a fallback source."""
    market_prefix = _infer_index_market_prefix(symbol)
    sina_symbol = symbol if str(symbol).startswith(("sh", "sz")) else f"{market_prefix}{symbol}"
    dfs = get_data_manager().fetch_akshare(ak.stock_zh_index_daily, symbol=sina_symbol, ttl=86400)
    if dfs is None or dfs.empty:
        return None

    dfs = dfs.reset_index() if "date" not in dfs.columns else dfs.copy()
    dfs = to_chinese_columns(dfs)
    if "日期" not in dfs.columns:
        return None
    dfs["日期"] = pd.to_datetime(dfs["日期"], errors="coerce")
    dfs = dfs.dropna(subset=["日期"]).sort_values("日期")
    dfs = dfs[dfs["日期"] >= pd.to_datetime(start_date, format="%Y%m%d", errors="coerce")].copy()
    if dfs.empty:
        return None

    if "成交额" not in dfs.columns:
        dfs["成交额"] = None
    if "换手率" not in dfs.columns:
        dfs["换手率"] = None
    dfs["日期"] = dfs["日期"].dt.strftime("%Y-%m-%d")
    return _resample_index_period(dfs, period)


# ==================== 历史价格 ====================

def index_prices(
    symbol: str = Field("000300", description="A股指数代码，如: 000300(沪深300), 000001(上证指数)"),
    period: str = Field("daily", description="周期，如: daily(日线), weekly(周线), monthly(月线)"),
    limit: int = Field(30, description="返回数量(int)", strict=False),
):
    symbol = resolve_field(symbol, "000300")
    period = resolve_field(period, "daily")
    limit = resolve_field(limit, 30)

    delta = {"weeks": limit + 62} if period == "weekly" else {"days": limit + 62}
    start_date = (datetime.now() - timedelta(**delta)).strftime("%Y%m%d")
    end_date = datetime.now().strftime("%Y%m%d")

    try:
        dfs = get_data_manager().fetch_akshare(
            ak.index_zh_a_hist,
            symbol=symbol,
            period=period,
            start_date=start_date,
            end_date=end_date,
            ttl=86400,
        )
    except Exception:
        dfs = None

    source = "akshare (eastmoney)"
    if dfs is None or dfs.empty:
        dfs = _index_hist_sina(symbol, start_date, period)
        source = "akshare (sina)"

    if dfs is None or dfs.empty:
        return f"Not Found for index {symbol}"

    dfs = to_chinese_columns(dfs)
    if "换手率" not in dfs.columns:
        dfs["换手率"] = None
    add_technical_indicators(dfs, dfs["收盘"], dfs["最低"], dfs["最高"], dfs.get("成交量"))
    available_cols = [c for c in STOCK_PRICE_COLUMNS if c in dfs.columns]
    all_lines = dfs.to_csv(columns=available_cols, index=False, float_format="%.2f").strip().split("\n")
    lines = [f"# {symbol} 指数历史价格", f"# 数据来源: {source}", f"# 市场: A股指数"]
    lines.append("\n".join([all_lines[0], *all_lines[-limit:]]))
    return "\n".join(lines)


def stock_prices(
    symbol: str = field_symbol,
    market: str = field_market,
    period: str = Field("daily", description="周期，如: daily(日线), weekly(周线，不支持美股)"),
    limit: int = Field(30, description="返回数量(int)", strict=False),
):
    symbol = resolve_field(symbol, "")
    market = resolve_field(market, "sh")
    period = resolve_field(period, "daily")
    limit = resolve_field(limit, 30)
    normalized_symbol = normalize_stock_code(symbol, market)
    stock_type, validated_market = validate_stock_type(normalized_symbol, market)

    # 对于 A 股，优先使用多数据源管理器
    if stock_type == StockType.A_STOCK:
        try:
            manager = get_data_manager()
            df = manager.get_daily_data(normalized_symbol, days=limit + 62)
            if df is not None and not df.empty:
                source = format_source_name(df.attrs.get('source', ''))
                df = to_chinese_columns(df)
                if "换手率" not in df.columns:
                    df["换手率"] = None
                add_technical_indicators(df, df["收盘"], df["最低"], df["最高"], df.get("成交量"))
                available_cols = [c for c in STOCK_PRICE_COLUMNS if c in df.columns]
                all_lines = df.to_csv(columns=available_cols, index=False, float_format="%.2f").strip().split("\n")
                lines = [f"# {symbol} 历史价格", f"# 数据来源: {source}", f"# 市场: A股"]
                lines.append("\n".join([all_lines[0], *all_lines[-limit:]]))
                return "\n".join(lines)
        except Exception as e:
            _LOGGER.debug(f"[A股] manager.get_daily_data 失败 {normalized_symbol}: {e}")

    if period == "weekly":
        delta = {"weeks": limit + 62}
    else:
        delta = {"days": limit + 62}
    start_date = (datetime.now() - timedelta(**delta)).strftime("%Y%m%d")

    if stock_type in (StockType.HK, StockType.US):
        from ..us_stock import _fetch_global_prices
        dfs = _fetch_global_prices(normalized_symbol, validated_market, start_date, period)
        if dfs is not None and not dfs.empty:
            add_technical_indicators(dfs, dfs["收盘"], dfs["最低"], dfs["最高"], dfs.get("成交量"))
            all_lines = dfs.to_csv(columns=STOCK_PRICE_COLUMNS, index=False, float_format="%.2f").strip().split("\n")
            source = dfs.attrs.get('source', 'unknown')
            market_label = "港股" if stock_type == StockType.HK else "美股"
            lines = [f"# {symbol} 历史价格", f"# 数据来源: {source}", f"# 市场: {market_label}"]
            lines.append("\n".join([all_lines[0], *all_lines[-limit:]]))
            return "\n".join(lines)
        return f"Not Found for {normalized_symbol}.{validated_market}"

    markets = [
        ["sh", ak.stock_zh_a_hist, {}],
        ["sz", ak.stock_zh_a_hist, {}],
        ["sh", _fund_etf_hist_sina, {"market": "sh"}],
        ["sz", _fund_etf_hist_sina, {"market": "sz"}],
    ]
    for m in markets:
        if m[0] != validated_market:
            continue
        kws = {"period": period, "start_date": start_date, **m[2]}
        dfs = get_data_manager().fetch_akshare(
            m[1],
            symbol=normalized_symbol,
            ttl=86400,
            **kws,
        )
        if dfs is None or dfs.empty:
            continue
        add_technical_indicators(dfs, dfs["收盘"], dfs["最低"], dfs["最高"], dfs.get("成交量"))
        all_lines = dfs.to_csv(columns=STOCK_PRICE_COLUMNS, index=False, float_format="%.2f").strip().split("\n")
        lines = [f"# {symbol} 历史价格", f"# 数据来源: akshare", f"# 市场: A股/ETF"]
        lines.append("\n".join([all_lines[0], *all_lines[-limit:]]))
        return "\n".join(lines)
    return f"Not Found for {normalized_symbol}.{validated_market}"


# ==================== 实时行情 ====================

def stock_realtime(
    symbol: str = field_symbol,
    market: str = Field("sh", description="股票市场，仅支持: sh(上证), sz(深证), hk(港股)"),
):
    try:
        symbol = resolve_field(symbol, "")
        market = resolve_field(market, "sh")
        normalized_symbol = normalize_stock_code(symbol, market)
        stock_type, validated_market = validate_stock_type(normalized_symbol, market)

        manager = get_data_manager()
        quote = manager.get_realtime_quote(normalized_symbol, stock_type=stock_type)
        if quote is None:
            return f"Not Found for {normalized_symbol}.{validated_market}"

        row = {
            "代码": quote.code,
            "名称": quote.name or "-",
            "最新价": quote.price,
            "涨跌幅": quote.change_pct,
            "涨跌额": quote.change_amount,
            "今开": quote.open_price,
            "最高": quote.high,
            "最低": quote.low,
            "昨收": quote.pre_close,
            "成交量": quote.volume,
            "成交额": quote.amount,
            "换手率": quote.turnover_rate,
            "量比": quote.volume_ratio,
            "振幅": quote.amplitude,
            "市盈率": quote.pe_ratio,
            "市净率": quote.pb_ratio,
            "总市值": quote.total_mv,
            "流通市值": quote.circ_mv,
        }
        df = pd.DataFrame([row])
        source = quote.source.value if quote.source else "-"
        lines = [f"# {symbol} 实时行情", f"# 数据来源: {source}"]
        lines.append(df.to_csv(index=False, float_format="%.2f").strip())
        return "\n".join(lines)
    except Exception as e:
        return f"获取 {symbol} 实时行情失败: {e}"


# ==================== 批量实时行情 ====================

def stock_batch_realtime(
    symbols: str = Field(description="股票代码列表，用逗号分隔，如: 600519,000858,601318"),
    limit: int = Field(20, description="返回数量(int)", strict=False),
):
    try:
        symbols = resolve_field(symbols, "")
        limit = resolve_field(limit, 20)
        codes = [s.strip() for s in symbols.split(",") if s.strip()]
        if not codes:
            return "请提供有效的股票代码"

        codes = codes[:limit]
        manager = get_data_manager()
        quotes = manager.prefetch_realtime_quotes(codes)

        if not quotes:
            return "未获取到任何行情数据"

        rows = []
        sources = set()
        for code, quote in quotes.items():
            rows.append({
                "代码": quote.code,
                "名称": quote.name or "-",
                "最新价": quote.price,
                "涨跌幅": quote.change_pct,
                "涨跌额": quote.change_amount,
                "今开": quote.open_price,
                "最高": quote.high,
                "最低": quote.low,
                "昨收": quote.pre_close,
                "成交量": quote.volume,
                "成交额": quote.amount,
                "换手率": quote.turnover_rate,
                "市盈率": quote.pe_ratio,
                "市净率": quote.pb_ratio,
            })
            if quote.source:
                sources.add(quote.source.value)

        df = pd.DataFrame(rows)
        source_str = ", ".join(sorted(sources)) if sources else "-"
        lines = [f"# 批量实时行情", f"# 数据来源: {source_str}"]
        lines.append(df.to_csv(index=False, float_format="%.2f").strip())
        return "\n".join(lines)
    except Exception as e:
        return f"批量获取实时行情失败: {e}"
