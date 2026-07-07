import pandas as pd

import open_stock_data.data_provider.akshare_fetcher as akshare_fetcher_module
import open_stock_data.tools.a_stock.valuation as valuation_module
from open_stock_data.data_provider.akshare_fetcher import AkshareFetcher


def _build_industry_pe_df() -> pd.DataFrame:
    return pd.DataFrame([
        {
            "行业层级": 1.0,
            "行业名称": "银行",
            "公司数量": 42,
            "静态市盈率-加权平均": 6.5,
            "静态市盈率-中位数": 6.1,
        },
        {
            "行业层级": 1.0,
            "行业名称": "证券",
            "公司数量": 18,
            "静态市盈率-加权平均": 14.2,
            "静态市盈率-中位数": 13.8,
        },
    ])


def test_get_industry_pe_falls_back_to_previous_trade_date(monkeypatch):
    fetcher = AkshareFetcher()
    monkeypatch.setattr(fetcher, "random_sleep", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        akshare_fetcher_module.ak,
        "tool_trade_date_hist_sina",
        lambda: pd.DataFrame({"trade_date": pd.to_datetime(["2026-03-24", "2026-03-25"])}),
    )

    calls: list[str] = []

    def fake_industry_pe_ratio_cninfo(symbol: str, date: str):
        assert symbol == "证监会行业分类"
        calls.append(date)
        if date == "20260325":
            raise ValueError("Length mismatch: Expected axis has 0 elements, new values have 12 elements")
        if date == "20260324":
            return _build_industry_pe_df()
        raise AssertionError(f"unexpected date: {date}")

    monkeypatch.setattr(
        akshare_fetcher_module.ak,
        "stock_industry_pe_ratio_cninfo",
        fake_industry_pe_ratio_cninfo,
    )

    df = fetcher.get_industry_pe("20260325")

    assert calls == ["20260325", "20260324"]
    assert df is not None
    assert df.attrs["requested_date"] == "20260325"
    assert df.attrs["data_date"] == "20260324"


def test_stock_industry_pe_uses_actual_data_date(monkeypatch):
    df = _build_industry_pe_df()
    df.attrs["source"] = "akshare"
    df.attrs["requested_date"] = "20260325"
    df.attrs["data_date"] = "20260324"

    class DummyManager:
        def get_industry_pe(self, date: str):
            assert date == "20260325"
            return df

    monkeypatch.setattr(valuation_module, "get_data_manager", lambda: DummyManager())

    result = valuation_module.stock_industry_pe(date="20260325")

    assert "# 数据日期: 20260324" in result
    assert "# 请求日期: 20260325" in result
    assert "银行" in result
