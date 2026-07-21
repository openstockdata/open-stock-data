"""列名标准化与中英文互转。"""

import pandas as pd


# 标准列名定义（内部数据流统一使用英文）
STANDARD_COLUMNS = ['date', 'open', 'high', 'low', 'close', 'volume', 'amount', 'pct_chg']


# 英文列名 → 中文列名
COLUMN_MAPPING_TO_CN = {
    'code': '代码',
    'name': '名称',
    'date': '日期',
    'open': '开盘',
    'high': '最高',
    'low': '最低',
    'close': '收盘',
    'volume': '成交量',
    'amount': '成交额',
    'pct_chg': '涨跌幅',
    'price': '最新价',
    'change_amount': '涨跌额',
    'pre_close': '昨收',
    'turnover_rate': '换手率',
    'pe_ratio': '市盈率',
    'pb_ratio': '市净率',
    'total_mv': '总市值',
    'circ_mv': '流通市值',
    'volume_ratio': '量比',
    'amplitude': '振幅',
}


# 中文列名 → 英文列名
COLUMN_MAPPING_TO_EN = {v: k for k, v in COLUMN_MAPPING_TO_CN.items()}


def to_chinese_columns(df: pd.DataFrame) -> pd.DataFrame:
    """将 DataFrame 的英文列名转换为中文。"""
    mapped = df.rename(columns=COLUMN_MAPPING_TO_CN)

    remaining_cols = {}
    for col in mapped.columns:
        if col not in df.columns or col in COLUMN_MAPPING_TO_CN:
            continue
        col_lower = col.lower()
        if col_lower in COLUMN_MAPPING_TO_CN:
            remaining_cols[col] = COLUMN_MAPPING_TO_CN[col_lower]

    if remaining_cols:
        mapped = mapped.rename(columns=remaining_cols)

    return mapped


def to_english_columns(df: pd.DataFrame) -> pd.DataFrame:
    """将 DataFrame 的中文列名转换为英文。"""
    return df.rename(columns=COLUMN_MAPPING_TO_EN)
