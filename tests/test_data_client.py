import pandas as pd
import pytest

from open_stock_data.client import OpenStockDataClient
from open_stock_data.data_provider.base import BaseFetcher
from open_stock_data.data_provider.circuit_breaker import get_circuit_breaker
from open_stock_data.data_provider.contracts import CachePolicy, Operation, RouteSpec
from open_stock_data.data_provider.routing import RouteRegistry
from open_stock_data.data_provider.stock_code import StockType
from open_stock_data.data_provider.types import ChipDistribution, RealtimeSource, UnifiedRealtimeQuote
from open_stock_data.exceptions import AllSourcesFailed, BatchIncomplete


class ClientFetcher(BaseFetcher):
    def __init__(self, name, *, daily=None, quotes=None, snapshot=None):
        super().__init__()
        self.name = name
        self.daily = daily
        self.quotes = quotes or {}
        self.snapshot = snapshot

    def _fetch_raw_data(self, stock_code, start_date, end_date):
        return None

    def _normalize_data(self, df, stock_code):
        return df

    def get_daily_data(self, stock_code, start_date=None, end_date=None, days=30):
        return self.daily

    def get_realtime_quote(self, stock_code):
        value = self.quotes.get(stock_code)
        if isinstance(value, Exception):
            raise value
        return value

    def get_a_stock_spot(self):
        return self.snapshot


def quote(code):
    return UnifiedRealtimeQuote(
        code=code,
        source=RealtimeSource.FALLBACK,
        price=10.0,
        change_pct=1.0,
    )


def client_routes():
    return RouteRegistry(
        [
            RouteSpec(
                Operation.DAILY_PRICES,
                StockType.A_STOCK,
                ("dummy",),
                "get_daily_data",
                "daily",
            ),
            RouteSpec(
                Operation.REALTIME_QUOTE,
                StockType.A_STOCK,
                ("dummy",),
                "get_realtime_quote",
                "realtime",
                validator=lambda value: value.has_basic_data(),
            ),
            RouteSpec(
                Operation.A_STOCK_SNAPSHOT,
                StockType.A_STOCK,
                ("dummy",),
                "get_a_stock_spot",
                "spot",
            ),
        ]
    )


@pytest.fixture(autouse=True)
def reset_breakers():
    for name in (
        "daily", "realtime", "spot", "fund_flow", "chip", "board",
        "billboard", "margin", "industry_pe", "dividend", "fund_holder", "top10_holders",
        "us_financials",
    ):
        get_circuit_breaker(name).reset()


def test_daily_prices_returns_english_dataframe_and_metadata():
    fetcher = ClientFetcher("dummy", daily=pd.DataFrame([{"日期": "2026-01-01", "收盘": 10.0}]))
    client = OpenStockDataClient({"dummy": fetcher}, client_routes(), cache=None)

    result = client.daily_prices("600519", days=1)

    assert list(result.data.columns) == ["date", "close"]
    assert result.source == "dummy"
    assert result.from_cache is False


def _daily_bars(dates):
    return pd.DataFrame([
        {"日期": d, "开盘": 10.0, "最高": 12.0, "最低": 9.0, "收盘": 11.0, "成交量": 100}
        for d in dates
    ])


def test_daily_prices_weekly_resamples_and_returns_english():
    daily = _daily_bars([
        "2026-01-05", "2026-01-06", "2026-01-07",   # 第 1 周
        "2026-01-12", "2026-01-13", "2026-01-14",   # 第 2 周
    ])
    fetcher = ClientFetcher("dummy", daily=daily)
    client = OpenStockDataClient({"dummy": fetcher}, client_routes(), cache=None)

    result = client.daily_prices("600519", days=10, period="weekly")

    assert "date" in result.data.columns and "close" in result.data.columns
    assert len(result.data) == 2  # 两个自然周
    assert result.data["volume"].tolist() == [300, 300]  # 每周 3 日成交量求和


def test_daily_prices_monthly_resamples():
    daily = _daily_bars(["2026-01-05", "2026-01-20", "2026-02-10", "2026-02-25"])
    fetcher = ClientFetcher("dummy", daily=daily)
    client = OpenStockDataClient({"dummy": fetcher}, client_routes(), cache=None)

    result = client.daily_prices("600519", days=10, period="monthly")

    assert len(result.data) == 2  # 1 月、2 月


def test_daily_prices_rejects_unsupported_period():
    fetcher = ClientFetcher("dummy", daily=pd.DataFrame([{"日期": "2026-01-01", "收盘": 10.0}]))
    client = OpenStockDataClient({"dummy": fetcher}, client_routes(), cache=None)

    with pytest.raises(ValueError):
        client.daily_prices("600519", period="hourly")


def test_realtime_quote_with_and_without_market():
    fetcher = ClientFetcher("dummy", quotes={"600519": quote("600519")})
    client = OpenStockDataClient({"dummy": fetcher}, client_routes(), cache=None)

    with_market = client.realtime_quote("600519", "sh")
    without_market = client.realtime_quote("600519")

    assert with_market.data.code == "600519"
    assert without_market.source == "dummy"


def test_batch_realtime_quotes_with_market_hint():
    fetcher = ClientFetcher("dummy", quotes={"600519": quote("600519")})
    client = OpenStockDataClient({"dummy": fetcher}, client_routes(), cache=None)

    result = client.batch_realtime_quotes(["600519"], market="sh")

    assert set(result.data) == {"600519"}


def test_get_default_client_returns_singleton():
    from open_stock_data.client import get_default_client

    first = get_default_client()
    second = get_default_client()

    assert first is second
    assert isinstance(first, OpenStockDataClient)


def test_default_routes_cache_policies_compute_ttl():
    from open_stock_data.data_provider.default_routes import create_default_routes

    routes = create_default_routes()
    policied = [r for r in routes.all() if r.cache_policy is not None]
    assert policied  # 至少日线/实时/快照/资金流有缓存策略
    for route in policied:
        assert route.cache_policy.current_ttl() >= 0


def test_snapshot_returns_english_dataframe():
    fetcher = ClientFetcher("dummy", snapshot=pd.DataFrame([{"代码": "600519", "最新价": 10.0}]))
    client = OpenStockDataClient({"dummy": fetcher}, client_routes(), cache=None)

    result = client.a_stock_snapshot()

    assert result.source == "dummy"
    assert list(result.data.columns) == ["code", "price"]


def test_batch_result_preserves_partial_failures():
    fetcher = ClientFetcher(
        "dummy",
        quotes={"600519": quote("600519"), "000001": ConnectionError("down")},
    )
    client = OpenStockDataClient({"dummy": fetcher}, client_routes(), cache=None)

    result = client.batch_realtime_quotes(["600519", "000001"])

    assert set(result.data) == {"600519"}
    assert set(result.failures) == {"000001"}
    assert result.is_partial is True


def test_strict_batch_raises_with_partial_result():
    fetcher = ClientFetcher(
        "dummy",
        quotes={"600519": quote("600519"), "000001": ConnectionError("down")},
    )
    client = OpenStockDataClient({"dummy": fetcher}, client_routes(), cache=None)

    with pytest.raises(BatchIncomplete) as caught:
        client.batch_realtime_quotes(["600519", "000001"], strict=True)

    assert set(caught.value.result.data) == {"600519"}


def test_batch_all_failed_raises_all_sources_failed():
    fetcher = ClientFetcher("dummy", quotes={"600519": ConnectionError("down")})
    client = OpenStockDataClient({"dummy": fetcher}, client_routes(), cache=None)

    with pytest.raises(AllSourcesFailed):
        client.batch_realtime_quotes(["600519"])


# ---------------------------------------------------------------------------
# 个股分析 / 资金流 / 板块 / 估值 / 股东（DataFrame 直通路由）
# ---------------------------------------------------------------------------


class AtomFetcher(BaseFetcher):
    """按方法名返回预置结果的通用 fetcher，用于路由级离线测试。

    直接把方法写入实例 __dict__，覆盖 BaseFetcher 上返回 None 的同名 stub。
    """

    def __init__(self, name, **methods):
        super().__init__()
        self.name = name
        for method_name, value in methods.items():
            self.__dict__[method_name] = self._make_method(value)

    @staticmethod
    def _make_method(value):
        def _call(*_args, **_kwargs):
            if isinstance(value, Exception):
                raise value
            return value

        return _call

    def _fetch_raw_data(self, stock_code, start_date, end_date):
        return None

    def _normalize_data(self, df, stock_code):
        return df


def atom_route(operation, method, breaker, providers=("dummy",), **overrides):
    return RouteSpec(operation, None, providers, method, breaker, **overrides)


def test_fund_flow_route_returns_dataframe_with_source():
    frame = pd.DataFrame([{"日期": "2026-01-01", "主力净流入": 100.0}])
    fetcher = AtomFetcher("dummy", get_fund_flow=frame)
    routes = RouteRegistry([atom_route(Operation.FUND_FLOW, "get_fund_flow", "fund_flow")])
    client = OpenStockDataClient({"dummy": fetcher}, routes, cache=None)

    result = client.fund_flow("600519")

    assert result.source == "dummy"
    assert list(result.data.columns) == ["日期", "主力净流入"]


def test_fund_flow_all_sources_failed_raises():
    fetcher = AtomFetcher("dummy", get_fund_flow=ConnectionError("down"))
    routes = RouteRegistry([atom_route(Operation.FUND_FLOW, "get_fund_flow", "fund_flow")])
    client = OpenStockDataClient({"dummy": fetcher}, routes, cache=None)

    with pytest.raises(AllSourcesFailed):
        client.fund_flow("600519")


def test_chip_distribution_route_returns_model():
    chip = ChipDistribution(code="600519", source="akshare")
    fetcher = AtomFetcher("dummy", get_chip_distribution=chip)
    routes = RouteRegistry([atom_route(Operation.CHIP_DISTRIBUTION, "get_chip_distribution", "chip")])
    client = OpenStockDataClient({"dummy": fetcher}, routes, cache=None)

    result = client.chip_distribution("600519")

    assert result.data is chip
    assert result.source == "dummy"


def test_belong_board_falls_back_to_second_provider():
    frame = pd.DataFrame([{"板块名称": "白酒"}])
    first = AtomFetcher("first", get_belong_board=ConnectionError("down"))
    second = AtomFetcher("second", get_belong_board=frame)
    routes = RouteRegistry([
        atom_route(Operation.BELONG_BOARD, "get_belong_board", "board", providers=("first", "second"))
    ])
    client = OpenStockDataClient({"first": first, "second": second}, routes, cache=None)

    result = client.belong_board("600519")

    assert result.source == "second"
    assert list(result.data.columns) == ["板块名称"]


def test_board_cons_passes_board_type_argument():
    captured = {}

    class RecordingFetcher(AtomFetcher):
        def get_board_cons(self, board_name, board_type="industry"):
            captured["args"] = (board_name, board_type)
            return pd.DataFrame([{"代码": "600519"}])

    fetcher = RecordingFetcher("dummy")
    routes = RouteRegistry([atom_route(Operation.BOARD_CONS, "get_board_cons", "board")])
    client = OpenStockDataClient({"dummy": fetcher}, routes, cache=None)

    result = client.board_cons("白酒", "concept")

    assert captured["args"] == ("白酒", "concept")
    assert result.source == "dummy"


def test_billboard_route_returns_dataframe():
    frame = pd.DataFrame([{"名称": "贵州茅台"}])
    fetcher = AtomFetcher("dummy", get_billboard=frame)
    routes = RouteRegistry([atom_route(Operation.BILLBOARD, "get_billboard", "billboard")])
    client = OpenStockDataClient({"dummy": fetcher}, routes, cache=None)

    result = client.billboard("5")

    assert result.source == "dummy"
    assert list(result.data.columns) == ["名称"]


def test_industry_pe_route_preserves_attrs():
    frame = pd.DataFrame([{"行业层级": 1.0, "静态市盈率-加权平均": 12.0}])
    frame.attrs["data_date"] = "20260710"
    fetcher = AtomFetcher("dummy", get_industry_pe=frame)
    routes = RouteRegistry([atom_route(Operation.INDUSTRY_PE, "get_industry_pe", "industry_pe")])
    client = OpenStockDataClient({"dummy": fetcher}, routes, cache=None)

    result = client.industry_pe("20260710")

    assert result.source == "dummy"
    assert result.data.attrs["data_date"] == "20260710"


def test_dividend_history_falls_back_to_akshare():
    frame = pd.DataFrame([{"公告日期": "2026-01-01", "派息": 1.0}])
    tushare = AtomFetcher("TushareFetcher", get_dividend_history=ConnectionError("down"))
    akshare = AtomFetcher("AkshareFetcher", get_dividend_history=frame)
    routes = RouteRegistry([
        atom_route(Operation.DIVIDEND_HISTORY, "get_dividend_history", "dividend",
                   providers=("TushareFetcher", "AkshareFetcher"))
    ])
    client = OpenStockDataClient({"TushareFetcher": tushare, "AkshareFetcher": akshare}, routes, cache=None)

    result = client.dividend_history("600519")

    assert result.source == "AkshareFetcher"


def test_fund_holder_passes_date_kwarg():
    captured = {}

    class RecordingFetcher(AtomFetcher):
        def get_fund_holder(self, symbol, date=""):
            captured["call"] = (symbol, date)
            return pd.DataFrame([{"股票代码": "600519"}])

    fetcher = RecordingFetcher("dummy")
    routes = RouteRegistry([atom_route(Operation.FUND_HOLDER, "get_fund_holder", "fund_holder")])
    client = OpenStockDataClient({"dummy": fetcher}, routes, cache=None)

    result = client.fund_holder("", date="20260630")

    assert captured["call"] == ("", "20260630")
    assert result.source == "dummy"


def test_top10_holders_passes_holder_type_and_falls_back():
    captured = {}

    class RecordingAkshare(AtomFetcher):
        def get_top10_holders(self, symbol, holder_type="main"):
            captured["call"] = (symbol, holder_type)
            return pd.DataFrame([{"股东名称": "证金公司"}])

    tushare = AtomFetcher("TushareFetcher", get_top10_holders=ConnectionError("down"))
    akshare = RecordingAkshare("AkshareFetcher")
    routes = RouteRegistry([
        atom_route(Operation.TOP10_HOLDERS, "get_top10_holders", "top10_holders",
                   providers=("TushareFetcher", "AkshareFetcher"))
    ])
    client = OpenStockDataClient({"TushareFetcher": tushare, "AkshareFetcher": akshare}, routes, cache=None)

    result = client.top10_holders("600519", holder_type="circulate")

    assert captured["call"] == ("600519", "circulate")
    assert result.source == "AkshareFetcher"


def _margin_routes():
    return RouteRegistry([
        atom_route(Operation.MARGIN_DETAIL, "get_margin_detail", "margin", providers=("AkshareFetcher",)),
        atom_route(Operation.MARGIN_RATIO, "get_margin_ratio", "margin", providers=("AkshareFetcher",)),
    ])


def test_margin_detail_returns_primary_market():
    frame = pd.DataFrame([{"融资余额": 100}])
    fetcher = AtomFetcher("AkshareFetcher", get_margin_detail=frame)
    client = OpenStockDataClient({"AkshareFetcher": fetcher}, _margin_routes(), cache=None)

    result = client.margin_detail("600519", "sh")

    assert result.source == "AkshareFetcher"
    assert "is_ratio_data" not in result.data.attrs


def test_margin_detail_falls_back_to_other_market():
    class MarketFetcher(AtomFetcher):
        def get_margin_detail(self, code, market="sh"):
            if market == "sz":
                return pd.DataFrame([{"融资余额": 5}])
            return None

    fetcher = MarketFetcher("AkshareFetcher")
    client = OpenStockDataClient({"AkshareFetcher": fetcher}, _margin_routes(), cache=None)

    result = client.margin_detail("600519", "sh")

    assert result.source == "AkshareFetcher"
    assert int(result.data.iloc[0]["融资余额"]) == 5


def test_margin_detail_falls_back_to_ratio_when_no_detail():
    class RatioFetcher(AtomFetcher):
        def get_margin_detail(self, code, market="sh"):
            return None

        def get_margin_ratio(self, code):
            return pd.DataFrame([{"融资比例": 0.3}])

    fetcher = RatioFetcher("AkshareFetcher")
    client = OpenStockDataClient({"AkshareFetcher": fetcher}, _margin_routes(), cache=None)

    result = client.margin_detail("600519", "sh")

    assert result.data.attrs["is_ratio_data"] is True


def test_margin_detail_all_rounds_fail_raises():
    fetcher = AtomFetcher("AkshareFetcher")  # 明细与比例均返回 None
    client = OpenStockDataClient({"AkshareFetcher": fetcher}, _margin_routes(), cache=None)

    with pytest.raises(AllSourcesFailed):
        client.margin_detail("600519", "sh")


# ---------------------------------------------------------------------------
# 美股基本面（dict 路由，AlphaVantage > YFinance）
# ---------------------------------------------------------------------------


def _us_routes():
    specs = [
        (Operation.US_OVERVIEW, "get_company_overview"),
        (Operation.US_BALANCE_SHEET, "get_balance_sheet"),
        (Operation.US_INCOME_STATEMENT, "get_income_statement"),
        (Operation.US_CASH_FLOW, "get_cash_flow"),
        (Operation.US_EARNINGS, "get_earnings"),
        (Operation.US_NEWS_SENTIMENT, "get_news_sentiment"),
        (Operation.US_INSIDER, "get_insider_transactions"),
        (Operation.US_TECH_INDICATOR, "get_technical_indicator"),
    ]
    return RouteRegistry([
        atom_route(op, method, "us_financials", providers=("AlphaVantage",)) for op, method in specs
    ])


def _us_client():
    fetcher = AtomFetcher(
        "AlphaVantage",
        get_company_overview={"Name": "Apple", "_data_source": "AlphaVantage"},
        get_balance_sheet={"reports": [{"totalAssets": "1"}]},
        get_income_statement={"reports": [{"totalRevenue": "1"}]},
        get_cash_flow={"reports": [{"operatingCashflow": "1"}]},
        get_earnings={"annualEarnings": [{"reportedEPS": "1"}]},
        get_news_sentiment={"feed": [{"title": "x"}]},
        get_insider_transactions={"data": [{"owner_name": "x"}]},
        get_technical_indicator={"data": [{"date": "2026-01-01"}]},
    )
    return OpenStockDataClient({"AlphaVantage": fetcher}, _us_routes(), cache=None)


def test_us_overview_and_financials_routes():
    client = _us_client()
    assert client.us_overview("AAPL").data["Name"] == "Apple"
    assert client.us_balance_sheet("AAPL", quarterly=True).data["reports"]
    assert client.us_income_statement("AAPL", quarterly=False).data["reports"]
    assert client.us_cash_flow("AAPL").data["reports"]


def test_us_earnings_news_insider_tech_routes():
    client = _us_client()
    assert "annualEarnings" in client.us_earnings("AAPL").data
    assert "feed" in client.us_news_sentiment(symbol="AAPL", topics="technology", limit=10).data
    assert "data" in client.us_insider("AAPL").data
    assert "data" in client.us_tech_indicator("AAPL", "RSI", "daily", 14).data


def test_us_route_all_sources_failed_raises():
    fetcher = AtomFetcher("AlphaVantage", get_company_overview=ConnectionError("down"))
    routes = RouteRegistry([atom_route(Operation.US_OVERVIEW, "get_company_overview", "us_financials", providers=("AlphaVantage",))])
    client = OpenStockDataClient({"AlphaVantage": fetcher}, routes, cache=None)
    with pytest.raises(AllSourcesFailed):
        client.us_overview("AAPL")
