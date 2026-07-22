#!/usr/bin/env python3
"""联网测试台账（增量）。

逐个工具做真实联网验证，并基于两类哈希决定是否需要重测：
  - 工具源码哈希：inspect.getsource(tool_fn)
  - 基础设施哈希：data_provider/*.py + client.py + utils.py + indicators.py + cache.py + exceptions.py

再次运行时，若某工具"上次通过 且 源码未变 且 基础设施未变"，则跳过联网验证，
沿用上次结果；否则重新联网测试并更新台账。

用法:
  uv run python tests/run_network_tests.py            # 增量：只测有变更/上次失败的工具
  uv run python tests/run_network_tests.py --all      # 强制全部重测
  uv run python tests/run_network_tests.py --only=stock_prices,stock_realtime

输出:
  tests/network_test_ledger.json   机器可读台账（增量判定依据）
  tests/NETWORK_TEST_REPORT.md     人类可读报告
"""
from __future__ import annotations

import contextlib
import glob
import hashlib
import inspect
import io
import json
import os
import signal
import sys
import time
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
LEDGER = os.path.join(HERE, "network_test_ledger.json")
REPORT = os.path.join(HERE, "NETWORK_TEST_REPORT.md")
PER_TOOL_TIMEOUT = 75  # 秒

# ---- 加载 tests/.env（与 conftest 一致）----
_env = os.path.join(HERE, ".env")
if os.path.exists(_env):
    for _line in open(_env, encoding="utf-8"):
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            if _v.strip():
                os.environ.setdefault(_k.strip(), _v.strip())
os.environ.setdefault("LOG_LEVEL", "WARNING")

from open_stock_data.tools import TOOL_REGISTRY  # noqa: E402

# ---- 每个工具的代表性联网调用参数 ----
ARGS = {
    "index_prices": {"symbol": "000300"},
    "stock_prices": {"symbol": "600519", "market": "sh"},
    "stock_realtime": {"symbol": "600519", "market": "sh"},
    "stock_batch_realtime": {"symbols": "600519,000858"},
    "search": {"keyword": "贵州茅台"},
    "stock_info": {"symbol": "600519"},
    "stock_indicators": {"symbol": "600519"},
    "get_current_time": {},
    "stock_zt_pool": {},
    "stock_lhb_ggtj_sina": {"days": "5"},
    "stock_sector_fund_flow_rank": {"days": "今日", "cate": "行业资金流"},
    "stock_north_flow": {},
    "stock_margin_trading": {"symbol": "600519", "market": "sh"},
    "stock_block_trade": {"symbol": "600519"},
    "stock_holder_num": {"symbol": "600519"},
    "stock_chip": {"symbol": "600519"},
    "stock_fund_flow": {"symbol": "600519"},
    "stock_sector_spot": {"symbol": "600519"},
    "stock_board_cons": {"board_name": "银行", "board_type": "industry"},
    "stock_market_pe_percentile": {},
    "stock_industry_pe": {},
    "stock_dividend_history": {"symbol": "600519"},
    "stock_institutional_holdings": {},
    "stock_earnings_calendar": {},
    "stock_financial_compare": {"symbol": "600519"},
    "stock_locked_shares": {},
    "stock_pledge_ratio": {},
    "stock_top10_holders": {"symbol": "600519"},
    "backtest_strategy": {"symbol": "600519", "strategy": "ma_cross"},
    "stock_prices_us": {"symbol": "PDD"},
    "stock_overview_us": {"symbol": "PDD"},
    "stock_financials_us": {"symbol": "PDD"},
    "stock_news_us": {"symbol": "PDD"},
    "stock_earnings_us": {"symbol": "PDD"},
    "stock_insider_us": {"symbol": "PDD"},
    "stock_tech_indicators_us": {"symbol": "PDD", "indicator": "RSI"},
    "okx_prices": {"instId": "BTC-USDT"},
    "okx_loan_ratios": {"symbol": "BTC"},
    "okx_taker_volume": {"symbol": "BTC"},
    "binance_ai_report": {"symbol": "BTC"},
    "stock_news": {"symbol": "600519"},
    "stock_news_global": {},
    "data_source_status": {},
}

FAIL_MARKERS = ("失败", "错误:", "错误：", "Not Found", "未配置", "未获取到", "无数据", "请提供", "不支持的")

# 内部状态工具：输出天然含"失败"字样（如熔断器失败计数），不做失败标记检查，
# 只要无异常且非空即视为通过。
STATE_TOOLS = {"data_source_status"}

# 环境限制而从联网测试中排除的工具：本环境无法访问对应外部服务。
# 这些工具标记为 excluded（不联网、不计入失败）。若配置了对应代理/网络后，
# 从此表移除即可重新纳入测试。
EXCLUDED = {
    "okx_prices": "www.okx.com 本环境不可达（需配置 OKX_BASE_URL 代理）",
    "okx_loan_ratios": "www.okx.com 本环境不可达（需配置 OKX_BASE_URL 代理）",
    "okx_taker_volume": "www.okx.com 本环境不可达（需配置 OKX_BASE_URL 代理）",
}

# 工具分类（用于报告分组）
CATEGORY = {
    "A股-行情信息": ["index_prices", "stock_prices", "stock_realtime", "stock_batch_realtime",
                 "search", "stock_info", "stock_indicators", "get_current_time"],
    "A股-市场资金": ["stock_zt_pool", "stock_lhb_ggtj_sina", "stock_sector_fund_flow_rank",
                 "stock_north_flow", "stock_margin_trading", "stock_block_trade", "stock_holder_num"],
    "A股-个股分析": ["stock_chip", "stock_fund_flow", "stock_sector_spot", "stock_board_cons"],
    "A股-估值财务": ["stock_market_pe_percentile", "stock_industry_pe", "stock_dividend_history",
                 "stock_institutional_holdings", "stock_earnings_calendar", "stock_financial_compare"],
    "A股-股东量化": ["stock_locked_shares", "stock_pledge_ratio", "stock_top10_holders", "backtest_strategy"],
    "美股": ["stock_prices_us", "stock_overview_us", "stock_financials_us", "stock_news_us",
           "stock_earnings_us", "stock_insider_us", "stock_tech_indicators_us"],
    "加密货币": ["okx_prices", "okx_loan_ratios", "okx_taker_volume", "binance_ai_report"],
    "新闻状态": ["stock_news", "stock_news_global", "data_source_status"],
}


class _Timeout(Exception):
    pass


@contextlib.contextmanager
def _time_limit(seconds):
    def _handler(signum, frame):
        raise _Timeout(f"timeout>{seconds}s")
    old = signal.signal(signal.SIGALRM, _handler)
    signal.alarm(seconds)
    try:
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old)


def infra_hash() -> str:
    files = sorted(glob.glob(os.path.join(ROOT, "open_stock_data/data_provider/*.py")))
    files += [os.path.join(ROOT, "open_stock_data", f)
              for f in ("client.py", "utils.py", "indicators.py", "cache.py", "exceptions.py")]
    h = hashlib.sha256()
    for f in files:
        if os.path.exists(f):
            h.update(os.path.basename(f).encode())
            h.update(open(f, "rb").read())
    return h.hexdigest()[:16]


def source_hash(fn) -> str:
    try:
        return hashlib.sha256(inspect.getsource(fn).encode("utf-8")).hexdigest()[:16]
    except Exception:
        return "nosrc"


def evaluate(name, fn, kwargs):
    try:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf), _time_limit(PER_TOOL_TIMEOUT):
            out = fn(**kwargs)
    except _Timeout as e:
        return "fail", str(e)
    except Exception as e:
        return "fail", f"{type(e).__name__}: {e}"[:300]
    if not isinstance(out, str) or not out.strip():
        return "fail", "empty/non-str result"
    if name not in STATE_TOOLS:
        for m in FAIL_MARKERS:
            if m in out:
                return "fail", f"marker '{m}': {out.strip().splitlines()[0][:160]}"
    return "pass", f"{len(out)} chars"


def load_ledger():
    if os.path.exists(LEDGER):
        try:
            return json.load(open(LEDGER, encoding="utf-8"))
        except Exception:
            pass
    return {"tools": {}}


def _save(infra, results):
    ledger = {
        "infra_hash": infra,
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "tools": results,
    }
    with open(LEDGER, "w", encoding="utf-8") as fh:
        json.dump(ledger, fh, ensure_ascii=False, indent=2)
    return ledger


def write_report(ledger):
    tools = ledger["tools"]
    total = len(tools)
    passed = sum(1 for v in tools.values() if v.get("status") == "pass")
    excluded = sum(1 for v in tools.values() if v.get("status") == "excluded")
    failed = total - passed - excluded
    lines = [
        "# 联网测试报告",
        "",
        f"更新时间: {ledger.get('updated_at', '-')}",
        f"基础设施哈希: `{ledger.get('infra_hash', '-')}`",
        f"总计: {total} · 通过: {passed} · 失败: {failed} · 排除: {excluded}",
        "",
        "> 增量机制：工具满足『上次通过 且 源码未变 且 基础设施未变』时跳过联网重测"
        "（skipped=true 表示沿用上次结果）。",
        "> 排除项（⊘）因本环境无法访问对应外部服务而不联网测试，见各行说明。",
        "",
    ]
    icons = {"pass": "✅", "excluded": "⊘"}
    for cat, names in CATEGORY.items():
        lines.append(f"## {cat}")
        lines.append("")
        lines.append("| 工具 | 状态 | 说明 | 源码哈希 | 上次测试 |")
        lines.append("|------|------|------|---------|---------|")
        for name in names:
            v = tools.get(name)
            if not v:
                lines.append(f"| {name} | - | 未测 | - | - |")
                continue
            icon = icons.get(v.get("status"), "❌")
            skip = " (skip)" if v.get("skipped") else ""
            msg = (v.get("message") or "").replace("|", "/").replace("\n", " ")[:80]
            lines.append(
                f"| {name} | {icon}{skip} | {msg} | `{v.get('source_hash', '-')[:10]}` | {v.get('tested_at', '-')} |"
            )
        lines.append("")
    with open(REPORT, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))


def main():
    force_all = "--all" in sys.argv
    only = None
    for a in sys.argv:
        if a.startswith("--only="):
            only = set(a.split("=", 1)[1].split(","))

    prev = load_ledger().get("tools", {})
    ih = infra_hash()
    results = {}
    infra_changed = any(p.get("infra_hash") != ih for p in prev.values()) if prev else True
    print(f"基础设施哈希: {ih}  (相比上次{'已变化→全部重测' if infra_changed and prev else '未变化'})")

    n_pass = n_fail = n_skip = n_excl = 0
    for name, (fn, _title, _desc) in TOOL_REGISTRY.items():
        if only is not None and name not in only:
            if name in prev:
                results[name] = prev[name]
            continue
        if name in EXCLUDED:
            results[name] = {
                "status": "excluded", "source_hash": source_hash(fn), "infra_hash": ih,
                "tested_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "latency_s": 0.0, "message": EXCLUDED[name], "skipped": False,
            }
            n_excl += 1
            print(f"EXCL {name}  (排除: {EXCLUDED[name]})")
            _save(ih, results)
            continue
        sh = source_hash(fn)
        p = prev.get(name)
        if (not force_all and p and p.get("status") == "pass"
                and p.get("source_hash") == sh and p.get("infra_hash") == ih):
            entry = dict(p)
            entry["skipped"] = True
            results[name] = entry
            n_skip += 1
            print(f"SKIP {name}  (未变更, 上次通过)")
            continue
        kwargs = ARGS.get(name, {})
        t0 = time.time()
        status, msg = evaluate(name, fn, kwargs)
        dt = round(time.time() - t0, 1)
        results[name] = {
            "status": status, "source_hash": sh, "infra_hash": ih,
            "tested_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "latency_s": dt, "message": msg, "skipped": False,
        }
        if status == "pass":
            n_pass += 1
        else:
            n_fail += 1
        print(f"{status.upper():4} {name}  ({dt}s)  {msg[:90]}", flush=True)
        # 增量落盘：长时间联网过程中断也不丢进度
        _save(ih, results)

    ledger = _save(ih, results)
    write_report(ledger)

    total_pass = sum(1 for v in results.values() if v.get("status") == "pass")
    total_test = sum(1 for v in results.values() if v.get("status") != "excluded")
    print("\n" + "=" * 60)
    print(f"本轮: 新测通过 {n_pass} · 失败 {n_fail} · 跳过(沿用) {n_skip} · 排除 {n_excl}")
    print(f"台账合计: {total_pass}/{total_test} 通过（另排除 {len(EXCLUDED)}）")
    print(f"台账: {LEDGER}")
    print(f"报告: {REPORT}")


if __name__ == "__main__":
    main()
