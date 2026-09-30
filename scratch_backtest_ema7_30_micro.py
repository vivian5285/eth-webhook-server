#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""2026-09-30临时脚本：回测ema7_30_micro_breakout.py(宝贝转发的DeepSeek
Pine脚本逐字复刻)，跑ETH(45m/1h)+SNDK(1h/90m)。不写shadow_log(纯本地
统计，不污染任何真实数据库)，跟backtest_runner.py同一套止损判定口径
(保守假设止损先触发)，但去掉了tp1(原Pine脚本没有固定止盈，只有反向
信号/双均线离场+ATR硬止损兜底)。"""
import sys
sys.path.insert(0, ".")
from strategy_engine import klines
from strategy_engine.strategies.ema7_30_micro_breakout import generate_signal

WARMUP = 60


def check_stop(pos, bar):
    side = pos["side"]
    stop = pos["stop_loss"]
    if stop is None:
        return False
    if side == "LONG":
        return float(bar["l"]) <= float(stop)
    return float(bar["h"]) >= float(stop)


def run(symbol, timeframe, days=120):
    minutes = klines.timeframe_to_minutes(timeframe) or 60
    bars_needed = int(days * 24 * 60 / minutes) + WARMUP
    bars = klines.get_bars(symbol, timeframe, limit=bars_needed)
    if len(bars) < WARMUP + 10:
        print(f"{symbol}@{timeframe}: 历史K线不足({len(bars)}根)")
        return

    open_pos = None
    trades = []
    for i in range(WARMUP, len(bars)):
        window = bars[: i + 1]
        bar = window[-1]
        bars_by_tf = {"base": window}

        if open_pos:
            if check_stop(open_pos, bar):
                exit_price = float(open_pos["stop_loss"])
                pnl_atr = (
                    (exit_price - open_pos["entry_price"]) / open_pos["atr0"]
                    if open_pos["side"] == "LONG"
                    else (open_pos["entry_price"] - exit_price) / open_pos["atr0"]
                )
                trades.append({"pnl_atr": pnl_atr, "reason": "stop", "bar_time": bar["t"]})
                open_pos = None

        position_arg = None
        if open_pos:
            position_arg = {"side": open_pos["side"], "entry_price": open_pos["entry_price"],
                             "entry_bar_time": open_pos["entry_bar_time"]}

        sig = generate_signal(bars_by_tf, None, position_arg)
        if not sig:
            continue
        action = sig.get("action")
        price = float(sig["price"])
        bar_time = int(sig["bar_time"])

        if action in ("LONG", "SHORT"):
            if open_pos and open_pos["side"] != action:
                pnl_atr = (
                    (price - open_pos["entry_price"]) / open_pos["atr0"]
                    if open_pos["side"] == "LONG"
                    else (open_pos["entry_price"] - price) / open_pos["atr0"]
                )
                trades.append({"pnl_atr": pnl_atr, "reason": "reverse", "bar_time": bar_time})
                open_pos = None
            if not open_pos:
                open_pos = {
                    "side": action, "entry_price": price, "entry_bar_time": bar_time,
                    "stop_loss": sig.get("stop_loss"), "atr0": float(sig.get("atr") or 0) or 1e-9,
                }
        elif action == "CLOSE_QUICK_EXIT" and open_pos:
            pnl_atr = (
                (price - open_pos["entry_price"]) / open_pos["atr0"]
                if open_pos["side"] == "LONG"
                else (open_pos["entry_price"] - price) / open_pos["atr0"]
            )
            trades.append({"pnl_atr": pnl_atr, "reason": "signal_close", "bar_time": bar_time})
            open_pos = None

    n = len(trades)
    if n == 0:
        print(f"\n{symbol}@{timeframe} ({days}天): 0笔交易")
        return
    wins = [t for t in trades if t["pnl_atr"] > 0]
    losses = [t for t in trades if t["pnl_atr"] <= 0]
    win_rate = 100.0 * len(wins) / n
    total_atr = sum(t["pnl_atr"] for t in trades)
    avg_atr = total_atr / n
    gross_win = sum(t["pnl_atr"] for t in wins) or 0.0
    gross_loss = abs(sum(t["pnl_atr"] for t in losses)) or 1e-9
    profit_factor = gross_win / gross_loss
    # 粗略最大回撤(用逐笔累计ATR曲线算)
    equity_curve = []
    cum = 0.0
    for t in trades:
        cum += t["pnl_atr"]
        equity_curve.append(cum)
    peak = -1e18
    max_dd = 0.0
    for v in equity_curve:
        peak = max(peak, v)
        max_dd = max(max_dd, peak - v)
    first_ts = bars[WARMUP]["t"]
    last_ts = bars[-1]["t"]
    age_days = max(1.0, (last_ts - first_ts) / 86400000.0)
    reason_counts = {}
    for t in trades:
        reason_counts[t["reason"]] = reason_counts.get(t["reason"], 0) + 1
    print(f"\n{symbol}@{timeframe} ({age_days:.0f}天, {len(bars)}根K线):")
    print(f"  n={n} win_rate={win_rate:.1f}% avg_atr={avg_atr:+.4f} total_atr={total_atr:+.2f} "
          f"atr/天={total_atr/age_days:+.4f} profit_factor={profit_factor:.2f} max_dd_atr={max_dd:.2f}")
    print(f"  离场原因分布: {reason_counts}")


if __name__ == "__main__":
    run("ETHUSDT", "45m", days=120)
    run("ETHUSDT", "1h", days=120)
    run("SNDKUSDT", "1h", days=120)
    run("SNDKUSDT", "90m", days=120)
