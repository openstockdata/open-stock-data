"""
市场概览与系统工具模块

包含全球财经新闻、数据源状态等 MCP 工具
"""

import os
import re
import logging
import pandas as pd
from pydantic import Field

from ..utils import (
    get_data_manager,
    _http_session,
    USER_AGENT,
    resolve_field,
)
from ..client import get_default_client
from ..exceptions import AllSourcesFailed

_LOGGER = logging.getLogger(__name__)

# NewsNow API 配置
_NEWSNOW_BASE_URL = "https://newsnow.busiyi.world"
_NEWSNOW_CHANNELS_DEFAULT = "wallstreetcn-quick,cls-telegraph,jin10"


# ==================== 个股新闻 ====================

def stock_news(
    symbol: str = Field(description="股票代码/加密货币符号"),
    limit: int = Field(15, description="返回数量(int)", strict=False),
):
    try:
        symbol = resolve_field(symbol, "")
        limit = resolve_field(limit, 15)
        fetch = get_default_client().news(symbol, limit)
        raw = fetch.data
        if raw is None or (hasattr(raw, 'empty') and raw.empty):
            return f"未找到 {symbol} 相关新闻"

        # 清洗 + 组装新闻内容（剥离 em 标签；截断内容回退标题）
        titles = raw.get("title", pd.Series([""] * len(raw), index=raw.index)).map(_clean_em_news_text)
        contents = raw.get("content", pd.Series([""] * len(raw), index=raw.index)).map(_clean_em_news_text)
        composed = [
            _compose_em_news_content(title, content, symbol)
            for title, content in zip(titles, contents)
        ]
        news_df = pd.DataFrame({
            "时间": raw.get("date", pd.Series([""] * len(raw), index=raw.index)),
            "内容": composed,
        }).head(limit)

        lines = [f"# {symbol} 相关新闻", "# 数据来源: 东方财经"]
        lines.append(news_df.to_csv(index=False).strip())
        return "\n".join(lines)
    except AllSourcesFailed:
        return f"未找到 {symbol} 相关新闻"
    except Exception as e:
        _LOGGER.warning(f"获取新闻失败: {e}")
        return f"获取 {symbol} 新闻失败: {e}"


def _clean_em_news_text(value):
    if value is None or pd.isna(value):
        return ""
    text = re.sub(r"</?em>", "", str(value))
    return _normalize_news_value(text)



def _is_truncated_em_content(content, symbol):
    if not content:
        return True
    if symbol and content.endswith(symbol):
        return True

    has_sentence_punct = any(mark in content for mark in ("。", "！", "？"))
    stock_codes = re.findall(r"\b\d{6}\b", content)
    if len(stock_codes) >= 3 and not has_sentence_punct:
        return True

    tokens = content.split()
    numeric_like = 0
    for token in tokens:
        normalized = token.replace(".", "", 1).replace("-", "", 1).replace("%", "", 1)
        if normalized.isdigit():
            numeric_like += 1
    if len(tokens) >= 12 and numeric_like >= len(tokens) * 0.4 and not has_sentence_punct:
        return True
    return False



def _compose_em_news_content(title, content, symbol):
    if not title:
        return content
    if not content or _is_truncated_em_content(content, symbol):
        return title
    if title in content:
        return content
    if content in title:
        return title
    return f"{title}。{content}"


# ==================== 全球财经快讯 ====================

# NewsNow 频道名称映射
_NEWSNOW_CHANNEL_NAMES = {
    "wallstreetcn-quick": "华尔街见闻",
    "cls-telegraph": "财联社",
    "jin10": "金十数据",
    "gelonghui": "格隆汇",
    "fastbull-express": "快讯通",
    "yicai": "第一财经",
    "caixin": "财新",
    "36kr-newsflash": "36氪",
}

_GLOBAL_NEWS_TIME_KEYS = (
    "时间",
    "发布时间",
    "日期",
    "datetime",
    "time",
    "pub_time",
    "pubDate",
)

_GLOBAL_NEWS_CONTENT_KEYS = (
    "新闻",
    "内容",
    "新闻内容",
    "标题",
    "摘要",
    "快讯",
    "快讯内容",
    "text",
    "content",
    "title",
    "summary",
)

_GLOBAL_NEWS_SKIP_COLUMN_KEYWORDS = (
    "时间",
    "日期",
    "来源",
    "source",
    "链接",
    "url",
)


def _normalize_news_value(value):
    if value is None or pd.isna(value):
        return ""
    text = str(value).replace("\r", " ").replace("\n", " ").strip()
    return " ".join(text.split())


def _pick_row_value(row, candidates):
    if row is None:
        return ""

    normalized_columns = {str(column).strip().lower(): column for column in row.index}

    for candidate in candidates:
        key = str(candidate).strip().lower()
        column = normalized_columns.get(key)
        if column is not None:
            value = _normalize_news_value(row.get(column))
            if value:
                return value

    for column in row.index:
        column_name = str(column).strip().lower()
        for candidate in candidates:
            key = str(candidate).strip().lower()
            if key and (key in column_name or column_name in key):
                value = _normalize_news_value(row.get(column))
                if value:
                    return value
    return ""


def _pick_sina_news_content(row):
    content = _pick_row_value(row, _GLOBAL_NEWS_CONTENT_KEYS)
    if content:
        return content

    for column in row.index:
        column_name = str(column).strip().lower()
        if any(keyword.lower() in column_name for keyword in _GLOBAL_NEWS_SKIP_COLUMN_KEYWORDS):
            continue
        value = _normalize_news_value(row.get(column))
        if value:
            return value
    return ""


def _sina_global_news(dfs):
    """从新浪快讯原帧中挑选时间/内容列（输入为 client.news_global() 的返回帧）。"""
    if dfs is None or (hasattr(dfs, "empty") and dfs.empty):
        return []

    rows = []
    for _, row in dfs.iterrows():
        time_str = _pick_row_value(row, _GLOBAL_NEWS_TIME_KEYS)
        content = _pick_sina_news_content(row)
        if not content:
            continue
        rows.append({"时间": time_str, "内容": content, "来源": "新浪财经"})

    if not rows:
        _LOGGER.debug(
            "新浪财经快讯未解析出有效内容，返回列名: %s",
            [str(column) for column in getattr(dfs, "columns", [])],
        )
    return rows


def stock_news_global():
    lines = ["# 全球财经快讯", "# 数据来源: 新浪财经, NewsNow"]
    news_rows = []

    # 获取新浪财经快讯（client 路由）
    try:
        sina = get_default_client().news_global()
        news_rows.extend(_sina_global_news(sina.data))
    except Exception as e:
        _LOGGER.debug(f"获取新浪财经快讯失败: {e}")

    # 获取 NewsNow 快讯
    news_rows.extend(_newsnow_news())

    if news_rows:
        news_df = pd.DataFrame(news_rows, columns=["时间", "内容", "来源"])
        lines.append(news_df.to_csv(index=False).strip())
    else:
        lines.append("时间,内容,来源")
    return "\n".join(lines)


def _newsnow_news(channels=None):
    """从 NewsNow 获取财经快讯"""
    if not channels:
        channels = os.getenv("NEWSNOW_CHANNELS") or _NEWSNOW_CHANNELS_DEFAULT
    if isinstance(channels, str):
        channels = channels.split(",")
    _LOGGER.debug(f"NewsNow 请求: base={_NEWSNOW_BASE_URL}, channels={channels}")
    all_news = []
    try:
        res = _http_session.post(
            f"{_NEWSNOW_BASE_URL}/api/s/entire",
            json={"sources": channels},
            headers={
                "User-Agent": USER_AGENT,
                "Referer": _NEWSNOW_BASE_URL,
            },
            timeout=60,
        )
        _LOGGER.debug(f"NewsNow 响应状态: {res.status_code}")
        lst = res.json() or []
        _LOGGER.debug(f"NewsNow 获取到 {len(lst)} 个频道数据")
        for item in lst:
            source_id = item.get("id", "")
            source_name = _NEWSNOW_CHANNEL_NAMES.get(source_id, source_id)
            for v in item.get("items", [])[0:15]:
                title = v.get("title", "")
                extra = v.get("extra") or {}
                hover = extra.get("hover") or title
                info = extra.get("info") or ""
                content = _normalize_news_value(f"{hover} {info}".strip())
                if not content:
                    continue
                pub_date = _normalize_news_value(v.get("pubDate", ""))
                time_str = pub_date or _normalize_news_value(extra.get("time"))
                all_news.append({"时间": time_str, "内容": content, "来源": source_name})
    except Exception as e:
        _LOGGER.warning(f"NewsNow 请求失败: {e}")
    return all_news


# ==================== 数据源状态 ====================

def data_source_status():
    try:
        manager = get_data_manager()
        status = manager.get_status()

        lines = ["# 数据源状态"]

        # 数据源列表
        lines.append("# 数据源")
        lines.append("名称,状态,熔断态")
        health = status.get('health', {})
        for name in status.get('providers', []):
            state = health.get(name, {})
            available = "熔断跳过" if state.get('circuit_state') == 'OPEN' else "OK"
            lines.append(f"{name},{available},{state.get('circuit_state') or '-'}")

        # 健康状态（统一 ProviderHealth 体系：成功率/延迟/连续失败/熔断态）
        lines.append("# 健康状态")
        lines.append("数据源,状态,连续失败,成功率,平均延迟ms,评分,冷却原因")
        for source, state in status.get('health', {}).items():
            state_label = "熔断跳过" if state.get('circuit_state') == 'OPEN' else (
                "半开探测" if state.get('circuit_state') == 'HALF_OPEN' else "正常")
            lines.append(
                f"{source},{state_label},{state.get('failure_count')},"
                f"{state.get('success_rate')},{state.get('avg_latency_ms')},"
                f"{state.get('score')},{state.get('block_reason') or '-'}"
            )

        return "\n".join(lines)
    except Exception as e:
        return f"获取数据源状态失败: {e}"
