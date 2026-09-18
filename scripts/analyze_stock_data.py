#!/usr/bin/env python3
"""测试脚本：验证 open-stock-data 多市场行情获取 + 日志观察"""

import logging
import os
import sys

# --- 配置日志：输出到 stdout，便于观察 ---
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)-8s] %(name)s — %(message)s",
    stream=sys.stdout,
)

from open_stock_data.client import OpenStockDataClient

STOCK_LIST = os.environ.get("STOCK_LIST", "600036,09988.HK,AAPL")
symbols = [s.strip() for s in STOCK_LIST.split(",") if s.strip()]

print(f"=== 测试股票列表: {symbols} ===\n")

client = OpenStockDataClient()

# --- 1. 批量实时行情 ---
print("--- 1. batch_realtime_quotes ---")
try:
    result = client.batch_realtime_quotes(symbols)
    for sym, quote in result.data.items():
        print(f"  {sym}: price={quote.price}, change_pct={quote.change_pct}, source={quote.source}")
    if result.failures:
        print(f"  失败: {result.failures}")
    if result.is_partial:
        print(f"  ⚠️  部分失败 (is_partial=True)")
except Exception as e:
    print(f"  ❌ 异常: {type(e).__name__}: {e}")

print()

# --- 2. 个股实时行情 ---
print("--- 2. 单股实时行情 ---")
for sym in symbols:
    try:
        result = client.realtime_quote(sym)
        print(f"  {sym}: price={result.data.price}, source={result.data.source}")
    except Exception as e:
        print(f"  {sym}: ❌ {type(e).__name__}: {e}")

print()

# --- 3. 日线数据 ---
print("--- 3. daily_prices ---")
for sym in symbols:
    market = "sh" if sym.startswith("6") else None
    if sym.endswith(".HK"):
        market = "hk"
        sym_clean = sym.replace(".HK", "")
    elif sym == "AAPL":
        market = "us"
        sym_clean = sym
    else:
        sym_clean = sym
    try:
        result = client.daily_prices(sym_clean, market=market, days=5)
        print(f"  {sym}: {len(result.data)} 行, source={result.source}, from_cache={result.from_cache}")
    except Exception as e:
        print(f"  {sym}: ❌ {type(e).__name__}: {e}")

print()
print("=== 测试完成 ===")
