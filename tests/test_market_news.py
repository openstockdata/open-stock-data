import io
import json
import sys
import types

import pandas as pd

from open_stock_data.tools import market as market_module


def _read_csv_payload(result: str) -> pd.DataFrame:
    payload = "\n".join(line for line in result.splitlines() if not line.startswith("#"))
    return pd.read_csv(io.StringIO(payload))


def test_stock_news_global_falls_back_to_current_sina_content_columns(monkeypatch):
    fake_ak = types.ModuleType("akshare")
    fake_ak.stock_info_global_sina = lambda: pd.DataFrame(
        [
            {"时间": "2026-03-26 09:33:31", "内容": "美联储发言, 市场重新定价"},
            {"时间": "2026-03-26 09:33:06", "新闻内容": "欧股期货走高\n银行板块领涨"},
        ]
    )
    monkeypatch.setitem(sys.modules, "akshare", fake_ak)
    monkeypatch.setattr(
        market_module,
        "_newsnow_news",
        lambda channels=None: [
            {"时间": "2026-03-26 09:32:20", "内容": "原油走强", "来源": "财联社"}
        ],
    )

    result = market_module.stock_news_global()
    df = _read_csv_payload(result)

    assert list(df.columns) == ["时间", "内容", "来源"]
    assert df["内容"].tolist() == [
        "美联储发言, 市场重新定价",
        "欧股期货走高 银行板块领涨",
        "原油走强",
    ]
    assert df["来源"].tolist() == ["新浪财经", "新浪财经", "财联社"]


class _DummyResponse:
    def __init__(self, text: str):
        self.text = text


def test_stock_news_em_prefers_title_when_eastmoney_content_is_truncated(monkeypatch):
    payload = {
        "result": {
            "cmsArticleWebOld": [
                {
                    "date": "2026-03-26 09:17:00",
                    "title": "<em>221</em>股融资余额增幅超<em>5</em>%",
                    "content": "600545 卓郎智能 16505.84 22.26 4.24 机械设备 002475 立讯精密 779434.37 18.76 9.99 电子 <em>000592</em>",
                },
                {
                    "date": "2026-03-25 16:27:10",
                    "title": "平潭发展(<em>000592</em>)龙虎榜数据(<em>03</em>-<em>25</em>)",
                    "content": "交易所2026年3月25日公布的交易公开信息显示，平潭发展因成为日涨幅偏离值达到7%的前5只证券上榜。",
                },
            ]
        }
    }
    cbk = "jQuery351013927587392975826_1763361926020"
    monkeypatch.setattr(
        market_module._http_session,
        "get",
        lambda *args, **kwargs: _DummyResponse(f"{cbk}({json.dumps(payload, ensure_ascii=False)})"),
    )

    df = market_module._stock_news_em("000592")

    assert df.iloc[0]["新闻内容"] == "221股融资余额增幅超5%"
    assert df.iloc[1]["新闻内容"].startswith("平潭发展(000592)龙虎榜数据(03-25)。交易所2026年3月25日公布")
