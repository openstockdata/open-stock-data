"""
A股策略回测模块（原子工具）

backtest_strategy: daily → 策略回测
"""

import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from pydantic import Field

from ...utils import (
    get_data_manager,
    format_source_name,
    field_symbol,
    resolve_field,
)
from ...data_provider import to_chinese_columns
from ...indicators import add_technical_indicators


def backtest_strategy(
    symbol: str = field_symbol,
    strategy: str = Field("ma_cross", description="策略类型: 'ma_cross'(均线交叉), 'macd'(MACD金叉死叉), 'kdj'(KDJ超买超卖), 'rsi'(RSI超买超卖), 'boll'(布林带突破)"),
    start_date: str = Field("", description="开始日期，格式: 20240101，默认一年前"),
    end_date: str = Field("", description="结束日期，格式: 20241231，默认今天"),
    initial_capital: float = Field(100000, description="初始资金(元)"),
    ma_short: int = Field(5, description="短期均线周期(ma_cross策略)"),
    ma_long: int = Field(20, description="长期均线周期(ma_cross策略)"),
):
    try:
        symbol = resolve_field(symbol, "")
        strategy = resolve_field(strategy, "ma_cross")
        start_date = resolve_field(start_date, "")
        end_date = resolve_field(end_date, "")
        initial_capital = resolve_field(initial_capital, 100000)
        ma_short = resolve_field(ma_short, 5)
        ma_long = resolve_field(ma_long, 20)
        if not isinstance(end_date, str) or not end_date:
            end_date = datetime.now().strftime("%Y%m%d")
        if not isinstance(start_date, str) or not start_date:
            start_date = (datetime.now() - timedelta(days=365)).strftime("%Y%m%d")

        manager = get_data_manager()
        days = (datetime.strptime(end_date, "%Y%m%d") - datetime.strptime(start_date, "%Y%m%d")).days + 60
        df = manager.get_daily_data(symbol, days=days)

        if df is None or df.empty:
            return f"未获取到 {symbol} 的历史数据"

        source = format_source_name(df.attrs.get('source', ''))
        df = to_chinese_columns(df)

        df.index = pd.to_datetime(df["日期"], errors="coerce")
        start_dt = pd.to_datetime(start_date)
        end_dt = pd.to_datetime(end_date)
        df = df[(df.index >= start_dt) & (df.index <= end_dt)].copy()

        if len(df) < 30:
            return f"数据量不足（{len(df)}条），至少需要30个交易日"

        close = df["收盘"]
        high = df["最高"]
        low = df["最低"]

        add_technical_indicators(df, close, low, high, df.get("成交量"))

        signals = pd.Series(0, index=df.index)

        if strategy == "ma_cross":
            ma_s = close.rolling(window=ma_short, min_periods=1).mean()
            ma_l = close.rolling(window=ma_long, min_periods=1).mean()
            signals[(ma_s > ma_l) & (ma_s.shift(1) <= ma_l.shift(1))] = 1
            signals[(ma_s < ma_l) & (ma_s.shift(1) >= ma_l.shift(1))] = -1
            strategy_name = f"MA{ma_short}/MA{ma_long}交叉"
        elif strategy == "macd":
            macd = df.get("MACD")
            signal_line = df.get("DEA")
            if macd is None or signal_line is None:
                return "MACD指标计算失败"
            signals[(macd > signal_line) & (macd.shift(1) <= signal_line.shift(1))] = 1
            signals[(macd < signal_line) & (macd.shift(1) >= signal_line.shift(1))] = -1
            strategy_name = "MACD金叉死叉"
        elif strategy == "kdj":
            k = df.get("KDJ.K")
            d = df.get("KDJ.D")
            j = df.get("KDJ.J")
            if k is None or d is None or j is None:
                return "KDJ指标计算失败"
            signals[(j < 20) & (k > d) & (k.shift(1) <= d.shift(1))] = 1
            signals[(j > 80) & (k < d) & (k.shift(1) >= d.shift(1))] = -1
            strategy_name = "KDJ超买超卖"
        elif strategy == "rsi":
            rsi = df.get("RSI")
            if rsi is None:
                return "RSI指标计算失败"
            signals[(rsi < 30) & (rsi.shift(1) >= 30)] = 1
            signals[(rsi > 70) & (rsi.shift(1) <= 70)] = -1
            strategy_name = "RSI超买超卖"
        elif strategy == "boll":
            boll_upper = df.get("BOLL.U")
            boll_lower = df.get("BOLL.L")
            if boll_upper is None or boll_lower is None:
                return "布林带指标计算失败"
            signals[(close <= boll_lower) & (close.shift(1) > boll_lower.shift(1))] = 1
            signals[(close >= boll_upper) & (close.shift(1) < boll_upper.shift(1))] = -1
            strategy_name = "布林带突破"
        else:
            return f"不支持的策略类型: {strategy}"

        capital = initial_capital
        position = 0
        entry_price = 0
        trades = []
        equity_curve = []

        for i, (date, row) in enumerate(df.iterrows()):
            price = row["收盘"]
            signal = signals.iloc[i]
            current_equity = capital + position * price
            equity_curve.append({"日期": date, "权益": current_equity})

            if signal == 1 and position == 0:
                shares = int(capital / price / 100) * 100
                if shares > 0:
                    cost = shares * price
                    capital -= cost
                    position = shares
                    entry_price = price
                    trades.append({"日期": date.strftime("%Y-%m-%d"), "类型": "买入", "价格": price, "数量": shares, "金额": cost})
            elif signal == -1 and position > 0:
                revenue = position * price
                profit = (price - entry_price) * position
                profit_pct = (price / entry_price - 1) * 100
                capital += revenue
                trades.append({"日期": date.strftime("%Y-%m-%d"), "类型": "卖出", "价格": price, "数量": position, "金额": revenue, "盈亏": profit, "盈亏%": profit_pct})
                position = 0
                entry_price = 0

        final_price = close.iloc[-1]
        final_equity = capital + position * final_price
        total_return = (final_equity / initial_capital - 1) * 100
        equity_df = pd.DataFrame(equity_curve)
        equity_series = equity_df["权益"]

        cummax = equity_series.cummax()
        drawdown = (equity_series - cummax) / cummax
        max_drawdown = drawdown.min() * 100
        trading_days = len(df)
        annual_return = total_return * (252 / trading_days) if trading_days > 0 else 0

        if len(equity_df) > 1:
            daily_returns = equity_series.pct_change().dropna()
            sharpe = (daily_returns.mean() * 252 - 0.02) / (daily_returns.std() * np.sqrt(252)) if daily_returns.std() > 0 else 0
        else:
            sharpe = 0

        sell_trades = [t for t in trades if t["类型"] == "卖出"]
        win_trades = [t for t in sell_trades if t.get("盈亏", 0) > 0]
        win_rate = len(win_trades) / len(sell_trades) * 100 if sell_trades else 0
        wins = [t["盈亏"] for t in sell_trades if t.get("盈亏", 0) > 0]
        losses = [-t["盈亏"] for t in sell_trades if t.get("盈亏", 0) < 0]
        avg_win = sum(wins) / len(wins) if wins else 0
        avg_loss = sum(losses) / len(losses) if losses else 1
        profit_factor = avg_win / avg_loss if avg_loss > 0 else float('inf')
        buy_hold_return = (final_price / close.iloc[0] - 1) * 100

        lines = [
            f"# {symbol} 策略回测报告",
            f"# 策略: {strategy_name}",
            f"# 数据来源: {source}",
            f"# 回测区间: {start_date} ~ {end_date} ({trading_days}个交易日)",
            "",
            "# 收益指标",
            "初始资金,最终权益,总收益率(%),年化收益率(%),买入持有收益(%),超额收益(%)",
            f"{initial_capital:.0f},{final_equity:.0f},{total_return:.2f},{annual_return:.2f},{buy_hold_return:.2f},{total_return - buy_hold_return:.2f}",
            "",
            "# 风险指标",
            "最大回撤(%),夏普比率,波动率(%)",
            f"{max_drawdown:.2f},{sharpe:.2f},{daily_returns.std() * np.sqrt(252) * 100:.2f}" if len(equity_df) > 1 else f"{max_drawdown:.2f},0,0",
            "",
            "# 交易统计",
            "交易次数,胜率(%),盈亏比,平均盈利,平均亏损",
            f"{len(sell_trades)},{win_rate:.1f},{profit_factor:.2f},{avg_win:.0f},{avg_loss:.0f}",
        ]

        if trades:
            lines.append("")
            lines.append("# 最近交易记录")
            lines.append("日期,类型,价格,数量,金额,盈亏,盈亏%")
            for t in trades[-10:]:
                profit_str = f"{t.get('盈亏', 0):.0f}" if "盈亏" in t else "-"
                pct_str = f"{t.get('盈亏%', 0):.2f}" if "盈亏%" in t else "-"
                lines.append(f"{t['日期']},{t['类型']},{t['价格']:.2f},{t['数量']},{t['金额']:.0f},{profit_str},{pct_str}")

        lines.append("")
        lines.append("# 策略评价")
        if total_return > buy_hold_return and max_drawdown > -20:
            evaluation = "优秀 - 跑赢大盘且回撤可控"
        elif total_return > 0 and max_drawdown > -30:
            evaluation = "良好 - 盈利且风险适中"
        elif total_return > buy_hold_return:
            evaluation = "一般 - 跑赢大盘但回撤较大"
        else:
            evaluation = "较差 - 未能跑赢买入持有策略"
        lines.append(f"评价: {evaluation}")
        lines.append("注意: 回测结果仅供参考，历史表现不代表未来收益")

        return "\n".join(lines)
    except Exception as e:
        return f"回测 {symbol} 失败: {e}"
