"""所属板块统一 schema（boards.py）单测：覆盖各 provider 的原始形态。"""
import pandas as pd

from open_stock_data.data_provider.boards import (
    BELONG_BOARD_LEADING_COLUMNS,
    industry_board_name,
    infer_eastmoney_board_types,
    normalize_belong_board,
)


def test_efinance_frame_gets_types_by_eastmoney_order():
    df = pd.DataFrame([
        {"股票名称": "贵州茅台", "股票代码": "600519", "板块代码": "BK0477", "板块名称": "酿酒行业", "板块涨幅": 0.56},
        {"股票名称": "贵州茅台", "股票代码": "600519", "板块代码": "BK0173", "板块名称": "贵州板块", "板块涨幅": -1.27},
        {"股票名称": "贵州茅台", "股票代码": "600519", "板块代码": "BK0611", "板块名称": "上证50_", "板块涨幅": 0.60},
        {"股票名称": "贵州茅台", "股票代码": "600519", "板块代码": "BK0896", "板块名称": "白酒", "板块涨幅": 0.25},
    ])

    out = normalize_belong_board(df)

    assert list(out.columns)[:5] == list(BELONG_BOARD_LEADING_COLUMNS)
    assert out["板块类型"].tolist() == ["industry", "region", "concept", "concept"]
    assert "板块涨幅" in out.columns
    assert industry_board_name(out) == "酿酒行业"


def test_tushare_and_baostock_industry_frames_promote_industry_column():
    tushare = pd.DataFrame([{"股票代码": "601298.SH", "股票名称": "青岛港", "行业": "港口", "市场": "主板", "上市日期": "20190121"}])
    baostock = pd.DataFrame([{"股票代码": "000592", "股票名称": "平潭发展", "行业": "农林牧渔", "证券类型": "1"}])

    for raw in (tushare, baostock):
        out = normalize_belong_board(raw)
        assert list(out.columns)[:3] == ["板块名称", "板块代码", "板块类型"]
        assert out.iloc[0]["板块名称"] == raw.iloc[0]["行业"]
        assert out.iloc[0]["板块类型"] == "industry"
        assert pd.isna(out.iloc[0]["板块代码"])
        assert industry_board_name(out) == raw.iloc[0]["行业"]


def test_unknown_frame_without_board_or_industry_is_returned_unchanged():
    df = pd.DataFrame([{"foo": 1}])
    out = normalize_belong_board(df)
    assert list(out.columns) == ["foo"]


def test_none_and_empty_pass_through():
    assert normalize_belong_board(None) is None
    empty = pd.DataFrame()
    assert normalize_belong_board(empty) is empty


def test_stock_code_aliases_are_mapped():
    df = pd.DataFrame([{"板块名称": "银行", "代码": "601398", "名称": "工商银行"}])
    out = normalize_belong_board(df, default_type="industry")
    assert out.iloc[0]["股票代码"] == "601398"
    assert out.iloc[0]["股票名称"] == "工商银行"
    assert out.iloc[0]["板块类型"] == "industry"


def test_industry_board_name_prefers_industry_row_then_first_row():
    typed = pd.DataFrame([
        {"板块名称": "贵州板块", "板块代码": "BK0173", "板块类型": "region"},
        {"板块名称": "酿酒行业", "板块代码": "BK0477", "板块类型": "industry"},
    ])
    assert industry_board_name(typed) == "酿酒行业"

    untyped = pd.DataFrame([{"板块名称": "白酒"}])
    assert industry_board_name(untyped) == "白酒"
    assert industry_board_name(None) is None
    assert industry_board_name(pd.DataFrame()) is None


def test_infer_eastmoney_board_types_positional_rule():
    assert infer_eastmoney_board_types(["银行", "北京板块", "沪股通", "MSCI中国"]) == [
        "industry", "region", "concept", "concept",
    ]
