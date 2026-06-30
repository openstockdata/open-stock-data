"""
A股基本信息与搜索模块

包含股票搜索、基本信息、财务指标、交易时间等工具
"""

import pandas as pd
import akshare as ak
from datetime import datetime, timedelta
from pydantic import Field

from ...utils import (
    field_symbol,
    field_market,
    get_data_manager,
    resolve_field,
)
from ...data_provider import market_to_stock_type, normalize_stock_code, validate_stock_type, StockType


# ==================== 辅助函数 ====================

def _search_us_stock_fast(symbol: str) -> pd.Series | None:
    """使用 yfinance 快速验证美股代码"""
    import yfinance as yf
    symbol = symbol.upper()

    def _fetch():
        try:
            ticker = yf.Ticker(symbol)
            info = ticker.info
            if info and info.get("symbol") and info.get("shortName"):
                return pd.Series({
                    "symbol": info.get("symbol", symbol),
                    "name": info.get("shortName", ""),
                    "cname": info.get("longName", info.get("shortName", "")),
                    "market": "us",
                })
        except Exception:
            return None
        return None

    return get_data_manager().fetch_with_cache(
        _fetch,
        ttl=86400 * 7,
        key=f"search_us_stock_fast:{symbol}",
    )


def _ak_search(symbol=None, keyword=None, market=None):
    """通用股票搜索"""
    symbol = resolve_field(symbol, None)
    keyword = resolve_field(keyword, None)
    market = resolve_field(market, None)
    normalized_symbol = normalize_stock_code(symbol, market) if symbol else None
    normalized_keyword = normalize_stock_code(keyword, market) if keyword else None

    if market == "us" and (symbol or keyword):
        us_result = _search_us_stock_fast(normalized_symbol or normalized_keyword or symbol or keyword)
        if us_result is not None:
            return us_result

    markets = [
        ["sh", ak.stock_info_a_code_name, "code", "name"],
        ["sh", ak.stock_info_sh_name_code, "证券代码", "证券简称"],
        ["sz", ak.stock_info_sz_name_code, "A股代码", "A股简称"],
        ["hk", ak.stock_hk_spot, "代码", "中文名称"],
        ["hk", ak.stock_hk_spot_em, "代码", "名称"],
        ["us", ak.get_us_stock_name, "symbol", "cname"],
        ["us", ak.get_us_stock_name, "symbol", "name"],
        ["sh", ak.fund_etf_spot_ths, "基金代码", "基金名称"],
        ["sz", ak.fund_etf_spot_ths, "基金代码", "基金名称"],
        ["sh", ak.fund_info_index_em, "基金代码", "基金名称"],
        ["sz", ak.fund_info_index_em, "基金代码", "基金名称"],
        ["sh", ak.fund_etf_spot_em, "代码", "名称"],
        ["sz", ak.fund_etf_spot_em, "代码", "名称"],
    ]
    for m in markets:
        if market and market != m[0]:
            continue
        all = get_data_manager().fetch_akshare(m[1], ttl=86400 * 7)
        if all is None or all.empty:
            continue
        for _, v in all.iterrows():
            code = str(v[m[2]]).upper()
            name = str(v[m[3]]).upper()
            normalized_code = normalize_stock_code(code, m[0])
            if symbol and (symbol.upper() == code or (normalized_symbol and normalized_symbol == normalized_code)):
                return v
            if keyword and (
                keyword.upper() in [code, name]
                or (normalized_keyword and normalized_keyword == normalized_code)
            ):
                return v
        for _, v in all.iterrows() if keyword else []:
            name = str(v[m[3]])
            if len(keyword) >= 4 and keyword in name:
                return v
            if name.startswith(keyword):
                return v
    return None


# ==================== 搜索与基本信息 ====================

def search(
    keyword: str = Field(description="搜索关键词，公司名称、股票名称、股票代码、证券简称"),
    market: str = field_market,
):
    keyword = resolve_field(keyword, "")
    market = resolve_field(market, "sh")
    info = _ak_search(None, keyword, market)
    if info is not None:
        lines = [f"# 搜索结果: {keyword}", f"# 数据来源: akshare", f"# 交易市场: {market}"]
        # 转为 CSV 格式：表头行 + 数据行
        if isinstance(info, pd.Series):
            lines.append(",".join(str(k) for k in info.index))
            lines.append(",".join(str(v) for v in info.values))
        else:
            lines.append(info.to_csv(index=False).strip())
        return "\n".join(lines)
    return f"Not Found for {keyword}"


def stock_info(
    symbol: str = field_symbol,
    market: str = field_market,
):
    symbol = resolve_field(symbol, "")
    market = resolve_field(market, "sh")
    normalized_symbol = normalize_stock_code(symbol, market)
    markets = [
        ["sh", ak.stock_individual_info_em],
        ["sz", ak.stock_individual_info_em],
        ["hk", ak.stock_hk_security_profile_em],
    ]
    for m in markets:
        if m[0] != market:
            continue
        all = get_data_manager().fetch_akshare(
            m[1],
            symbol=normalized_symbol,
            ttl=86400 * 7,
        )
        if all is None or all.empty:
            continue
        lines = [f"# {symbol} 基本信息", f"# 数据来源: akshare", f"# 市场: {market}"]
        lines.append(all.to_csv(index=False).strip())
        return "\n".join(lines)

    info = _ak_search(symbol=symbol, market=market)
    if info is not None:
        lines = [f"# {symbol} 基本信息", f"# 数据来源: akshare"]
        # 转为 CSV 格式：表头行 + 数据行
        if isinstance(info, pd.Series):
            lines.append(",".join(str(k) for k in info.index))
            lines.append(",".join(str(v) for v in info.values))
        else:
            lines.append(info.to_csv(index=False).strip())
        return "\n".join(lines)
    return f"Not Found for {symbol}.{market}"


# ==================== 财务指标 ====================

def stock_indicators(
    symbol: str = field_symbol,
    market: str = Field("sh", description="市场: 'sh'/'sz'(A股), 'hk'(港股), 'us'(美股)"),
):
    try:
        symbol = resolve_field(symbol, "")
        market = resolve_field(market, "sh")
        normalized_symbol = normalize_stock_code(symbol, market)
        stock_type, validated_market = validate_stock_type(normalized_symbol, market)

        if stock_type == StockType.A_STOCK:
            dfs = get_data_manager().fetch_akshare(
                ak.stock_financial_abstract_ths,
                symbol=normalized_symbol,
                ttl=86400 * 7,
            )
            if dfs is None or dfs.empty:
                return f"获取A股指标失败: {normalized_symbol}"
            keys = dfs.to_csv(index=False, float_format="%.3f").strip().split("\n")
            lines = [f"# {symbol} 财务指标", f"# 数据来源: akshare", f"# 市场: A股"]
            lines.append("\n".join([keys[0], *keys[-15:]]))
            return "\n".join(lines)
        elif stock_type == StockType.HK:
            dfs = get_data_manager().fetch_akshare(
                ak.stock_financial_hk_analysis_indicator_em,
                symbol=normalized_symbol,
                indicator="报告期",
                ttl=86400 * 7,
            )
            if dfs is None or dfs.empty:
                return f"获取港股指标失败: {normalized_symbol}"
            keys = dfs.to_csv(index=False, float_format="%.3f").strip().split("\n")
            lines = [f"# {symbol} 财务指标", f"# 数据来源: akshare", f"# 市场: 港股"]
            lines.append("\n".join(keys[0:15]))
            return "\n".join(lines)
        elif stock_type == StockType.US:
            dfs = get_data_manager().fetch_akshare(
                ak.stock_financial_us_analysis_indicator_em,
                symbol=normalized_symbol,
                indicator="单季报",
                ttl=86400 * 7,
            )
            if dfs is None or dfs.empty:
                return f"获取美股指标失败: {normalized_symbol}"
            keys = dfs.to_csv(index=False, float_format="%.3f").strip().split("\n")
            lines = [f"# {symbol} 财务指标", f"# 数据来源: akshare", f"# 市场: 美股"]
            lines.append("\n".join(keys[0:15]))
            return "\n".join(lines)
        else:
            return f"不支持的市场类型: {validated_market}"
    except Exception as exc:
        return f"获取财务指标失败: {exc}"


# ==================== 交易时间 ====================

def get_current_time():
    now = datetime.now()
    week = "日一二三四五六日"[now.isoweekday()]
    texts = [f"当前时间: {now.isoformat()}, 星期{week}"]
    dfs = get_data_manager().fetch_akshare(
        ak.tool_trade_date_hist_sina,
        ttl=86400 * 7,
    )
    if dfs is not None:
        start = now.date() - timedelta(days=5)
        ended = now.date() + timedelta(days=5)
        dates = [
            d.strftime("%Y-%m-%d")
            for d in dfs["trade_date"]
            if start <= d <= ended
        ]
        texts.append(f", 最近交易日有: {','.join(dates)}")
    return "".join(texts)
