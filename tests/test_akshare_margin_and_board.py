"""Akshare：融资融券交易所全表按交易所缓存；所属板块改用个股信息接口并输出统一 schema。"""
import pandas as pd
import pytest

import open_stock_data.data_provider.akshare_fetcher as module
from open_stock_data.cache import CacheStore
from open_stock_data.data_provider.akshare_fetcher import AkshareFetcher


@pytest.fixture(autouse=True)
def _clean_cache():
    # 先创建命名空间，确保 clear_all 覆盖它（否则上次运行留下的磁盘缓存会让 fake 不被调用）
    CacheStore.get_store(module._MARGIN_TABLE_CACHE_NAMESPACE)
    CacheStore.clear_all()
    yield
    CacheStore.clear_all()


def _fetcher(monkeypatch) -> AkshareFetcher:
    fetcher = AkshareFetcher()
    monkeypatch.setattr(fetcher, "random_sleep", lambda *a, **k: None)
    return fetcher


def test_margin_detail_downloads_exchange_table_once_per_exchange(monkeypatch):
    fetcher = _fetcher(monkeypatch)
    calls = {"sse": 0, "szse": 0}

    def fake_sse(date=""):
        calls["sse"] += 1
        return pd.DataFrame([
            {"信用交易日期": "20260919", "标的证券代码": "601298", "融资余额": 1},
            {"信用交易日期": "20260919", "标的证券代码": "600519", "融资余额": 2},
        ])

    def fake_szse(date=""):
        calls["szse"] += 1
        return pd.DataFrame([{"证券代码": "000592", "融资余额": 3}])

    monkeypatch.setattr(module.ak, "stock_margin_detail_sse", fake_sse)
    monkeypatch.setattr(module.ak, "stock_margin_detail_szse", fake_szse)

    a = fetcher.get_margin_detail("601298", "sh")
    b = fetcher.get_margin_detail("600519", "sh")
    c = fetcher.get_margin_detail("000592", "sz")

    assert a["标的证券代码"].tolist() == ["601298"]
    assert b["标的证券代码"].tolist() == ["600519"]
    assert c["证券代码"].tolist() == ["000592"]
    assert calls == {"sse": 1, "szse": 1}


def test_margin_detail_empty_table_is_not_cached(monkeypatch):
    fetcher = _fetcher(monkeypatch)
    calls = {"n": 0}

    def fake_sse(date=""):
        calls["n"] += 1
        return pd.DataFrame()

    monkeypatch.setattr(module.ak, "stock_margin_detail_sse", fake_sse)

    assert fetcher.get_margin_detail("601298", "sh").empty
    assert fetcher.get_margin_detail("601298", "sh").empty
    assert calls["n"] == 2


def test_belong_board_uses_individual_info_and_unified_schema(monkeypatch):
    fetcher = _fetcher(monkeypatch)

    def fake_info(symbol):
        assert symbol == "000592"
        return pd.DataFrame([
            {"item": "股票代码", "value": "000592"},
            {"item": "股票简称", "value": "平潭发展"},
            {"item": "行业", "value": "农牧饲渔"},
            {"item": "总市值", "value": 1.0},
        ])

    monkeypatch.setattr(module.ak, "stock_individual_info_em", fake_info)
    # 旧实现的“行业涨跌榜”兜底必须不再被调用
    monkeypatch.setattr(module.ak, "stock_board_industry_name_em",
                        lambda: (_ for _ in ()).throw(AssertionError("不应回退到全市场行业榜")))

    out = fetcher.get_belong_board("sz000592")

    assert list(out.columns)[:5] == ["板块名称", "板块代码", "板块类型", "股票代码", "股票名称"]
    assert out.iloc[0]["板块名称"] == "农牧饲渔"
    assert out.iloc[0]["板块类型"] == "industry"
    assert out.iloc[0]["股票代码"] == "000592"
    assert out.iloc[0]["股票名称"] == "平潭发展"


def test_belong_board_without_industry_returns_none(monkeypatch):
    fetcher = _fetcher(monkeypatch)
    monkeypatch.setattr(module.ak, "stock_individual_info_em",
                        lambda symbol: pd.DataFrame([{"item": "股票代码", "value": symbol}, {"item": "行业", "value": "-"}]))
    assert fetcher.get_belong_board("000592") is None


def _fake_industry_boards():
    return pd.DataFrame([
        {"板块名称": "白酒", "板块代码": "BK0477"},
        {"板块名称": "AI+应用", "板块代码": "BK1001"},
        {"板块名称": "算力(Leader)", "板块代码": "BK1002"},
    ])


def test_board_cons_fuzzy_match_is_literal_not_regex(monkeypatch):
    """板块名含 +、( 等正则元字符时按字面匹配：不抛错、不错配到其它板块。"""
    fetcher = _fetcher(monkeypatch)
    monkeypatch.setattr(module.ak, "stock_board_industry_name_em", _fake_industry_boards)
    cons_calls = []

    def fake_cons(symbol):
        cons_calls.append(symbol)
        return pd.DataFrame([{"代码": "600519", "名称": "贵州茅台"}])

    monkeypatch.setattr(module.ak, "stock_board_industry_cons_em", fake_cons)

    out = fetcher.get_board_cons("AI+", "industry")
    assert out is not None and not out.empty
    assert cons_calls == ["AI+应用"]

    out = fetcher.get_board_cons("算力(Leader)", "industry")
    assert out is not None and not out.empty
    assert cons_calls[-1] == "算力(Leader)"


def test_board_cons_stock_code_like_name_matches_nothing(monkeypatch):
    """把股票代码（如 601298.SH）当板块名传入时：无匹配、直接回 None，不发起成分股查询。"""
    fetcher = _fetcher(monkeypatch)
    monkeypatch.setattr(module.ak, "stock_board_industry_name_em", _fake_industry_boards)
    monkeypatch.setattr(module.ak, "stock_board_industry_cons_em",
                        lambda symbol: (_ for _ in ()).throw(AssertionError("无匹配时不应查询成分股")))
    assert fetcher.get_board_cons("601298.SH", "industry") is None


def test_board_cons_empty_name_returns_none_without_network(monkeypatch):
    fetcher = _fetcher(monkeypatch)
    monkeypatch.setattr(module.ak, "stock_board_industry_name_em",
                        lambda: (_ for _ in ()).throw(AssertionError("空板块名不应请求板块列表")))
    assert fetcher.get_board_cons("", "industry") is None
    assert fetcher.get_board_cons("   ", "industry") is None


def test_margin_ratio_filters_by_literal_code(monkeypatch):
    """含正则元字符的查询串按字面过滤，不抛 re.error。"""
    fetcher = _fetcher(monkeypatch)
    monkeypatch.setattr(module.ak, "stock_margin_ratio_pa",
                        lambda: pd.DataFrame([{"证券代码": "600519", "融资比例": 0.3}]))
    assert fetcher.get_margin_ratio("600+519") is None
    out = fetcher.get_margin_ratio("600519")
    assert out is not None and out.iloc[0]["融资比例"] == 0.3
