"""Efinance 单股/批量行情走点查接口（ulist），不再下载全市场（clist 分页）。"""
import pandas as pd
import pytest

from open_stock_data.data_provider.efinance_fetcher import EfinanceFetcher


class _FakeStock:
    def __init__(self):
        self.latest_calls: list[tuple[list[str], dict]] = []
        self.all_market_calls = 0

    def get_latest_quote(self, secids, **kwargs):
        self.latest_calls.append((list(secids), dict(kwargs)))
        rows = []
        for secid in secids:
            market, code = secid.split(".")
            if code == "999999":
                continue  # 不存在的代码：接口不返回该行
            rows.append({
                "代码": code, "名称": f"股票{code}", "涨跌幅": 1.5, "最新价": 10.0, "最高": 11.0,
                "最低": 9.0, "今开": 9.5, "涨跌额": 0.15, "换手率": 2.0, "量比": 1.1,
                "动态市盈率": 20.0, "成交量": 1000, "成交额": 10000.0, "昨日收盘": 9.85,
                "总市值": 1e9, "流通市值": 8e8, "市场类型": "深A" if market == "0" else "沪A",
                "行情ID": secid,
            })
        return pd.DataFrame(rows)

    def get_realtime_quotes(self, *args, **kwargs):
        self.all_market_calls += 1
        raise AssertionError("单股/批量行情不应下载全市场")


class _FakeEf:
    def __init__(self):
        self.stock = _FakeStock()


def _fetcher(monkeypatch) -> EfinanceFetcher:
    fetcher = EfinanceFetcher()
    fetcher._available = True
    fetcher._ef = _FakeEf()
    monkeypatch.setattr(fetcher, "random_sleep", lambda *a, **k: None)
    return fetcher


@pytest.mark.parametrize(
    "code, secid",
    [
        ("000592", "0.000592"),
        ("600519", "1.600519"),
        ("600519.SH", "1.600519"),
        ("sz000001", "0.000001"),
        ("430047", "0.430047"),   # 北交所
        ("999999", "1.999999"),   # 9 开头按沪市
        ("AAPL", None),
        ("", None),
    ],
)
def test_to_secid(code, secid):
    assert EfinanceFetcher._to_secid(code) == secid


def test_single_quote_uses_latest_quote_endpoint_with_prebuilt_secid(monkeypatch):
    fetcher = _fetcher(monkeypatch)

    quote = fetcher.get_realtime_quote("000592")

    assert quote is not None
    assert quote.code == "000592"
    assert quote.price == 10.0
    assert quote.pre_close == 9.85           # ulist 的 昨日收盘 也被映射
    assert quote.pe_ratio == 20.0
    assert fetcher._ef.stock.latest_calls == [(["0.000592"], {"quote_id_mode": True})]
    assert fetcher._ef.stock.all_market_calls == 0


def test_batch_quotes_are_chunked_and_missing_codes_skipped(monkeypatch):
    fetcher = _fetcher(monkeypatch)
    fetcher._LATEST_QUOTE_BATCH_SIZE = 2

    result = fetcher.get_batch_realtime_quotes(["000001", "600519", "999999", "510050"])  # 510050 是 ETF

    assert set(result) == {"000001", "600519"}
    assert result["600519"].code == "600519"
    assert [call[0] for call in fetcher._ef.stock.latest_calls] == [["0.000001", "1.600519"], ["1.999999"]]


def test_batch_quotes_keep_caller_code_format(monkeypatch):
    fetcher = _fetcher(monkeypatch)
    result = fetcher.get_batch_realtime_quotes(["600519.SH"])
    assert list(result) == ["600519.SH"]
    assert result["600519.SH"].code == "600519.SH"


def test_etf_single_quote_is_skipped_without_request(monkeypatch):
    fetcher = _fetcher(monkeypatch)
    assert fetcher.get_realtime_quote("510050") is None
    assert fetcher._ef.stock.latest_calls == []


def test_belong_board_is_normalized(monkeypatch):
    fetcher = _fetcher(monkeypatch)
    raw = pd.DataFrame([
        {"股票名称": "贵州茅台", "股票代码": "600519", "板块代码": "BK0477", "板块名称": "酿酒行业", "板块涨幅": 0.5},
        {"股票名称": "贵州茅台", "股票代码": "600519", "板块代码": "BK0173", "板块名称": "贵州板块", "板块涨幅": -1.2},
    ])
    fetcher._ef.stock.get_belong_board = lambda code: raw

    out = fetcher.get_belong_board("600519")

    assert list(out.columns)[:3] == ["板块名称", "板块代码", "板块类型"]
    assert out["板块类型"].tolist() == ["industry", "region"]
