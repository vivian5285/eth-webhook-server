#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
一次性诊断脚本：拉真实历史K线，逐bar回放，验证4套新战法(turtle_breakout/
connors_rsi2/bollinger_squeeze/cross_momentum)在真实市场数据上不会崩、
逻辑走得通(能开仓、能离场，止损/止盈方向没写反)。只在scratchpad式脚本
里跑，不提交，不接入任何real runner。
"""
from __future__ import annotations

import sys

sys.path.insert(0, "/root/strategy-engine")

from strategy_engine import klines
from strategy_engine.strategies import get_strategy


def _check_stop_tp(pos, bar):
    side = pos["side"]
    stop = pos.get("stop_loss")
    tp1 = pos.get("tp1")
    if side == "LONG":
        hit_stop = stop is not None and float(bar["l"]) <= float(stop)
        hit_tp = tp1 is not None and float(bar["h"]) >= float(tp1)
    else:
        hit_stop = stop is not None and float(bar["h"]) >= float(stop)
        hit_tp = tp1 is not None and float(bar["l"]) <= float(tp1)
    return hit_stop, hit_tp


def run_single_symbol(strategy_name, symbol, timeframe="4h", limit=800):
    fn = get_strategy(strategy_name)
    bars = klines.get_bars(symbol, timeframe, limit=limit)
    print(f"\n=== {strategy_name} on {symbol}({timeframe}) — {len(bars)}根 ===")
    if len(bars) < 250:
        print("  数据不够，跳过")
        return

    pos = None
    opens, closes_stop, closes_tp, closes_quick = 0, 0, 0, 0
    warmup = 210
    for i in range(warmup, len(bars)):
        window = bars[: i + 1]
        bar_by_tf = {"base": window}
        if pos is None:
            sig = fn(bar_by_tf, {}, None)
            if sig and sig.get("action") in ("LONG", "SHORT"):
                assert sig["stop_loss"] is not None
                if sig["action"] == "LONG":
                    assert sig["stop_loss"] < sig["price"], "多头止损应低于入场价"
                    assert sig["tp1"] > sig["price"], "多头TP应高于入场价"
                else:
                    assert sig["stop_loss"] > sig["price"], "空头止损应高于入场价"
                    assert sig["tp1"] < sig["price"], "空头TP应低于入场价"
                pos = {
                    "side": sig["action"], "entry_price": sig["price"],
                    "stop_loss": sig["stop_loss"], "tp1": sig["tp1"],
                    "entry_bar_time": sig["bar_time"],
                }
                opens += 1
        else:
            cur_bar = bars[i]
            hit_stop, hit_tp = _check_stop_tp(pos, cur_bar)
            if hit_stop:
                closes_stop += 1
                pos = None
                continue
            if hit_tp:
                closes_tp += 1
                pos = None
                continue
            sig = fn(bar_by_tf, {}, pos)
            if sig and str(sig.get("action", "")).startswith("CLOSE"):
                closes_quick += 1
                pos = None

    print(f"  开仓{opens}次 | 止损离场{closes_stop} | TP1离场{closes_tp} | 策略主动离场{closes_quick} | 收盘仍持仓={pos is not None}")


def run_cross_momentum(symbols, timeframe="4h", limit=500, lookback=20):
    fn = get_strategy("cross_momentum")
    bars_map = {s: klines.get_bars(s, timeframe, limit=limit) for s in symbols}
    min_len = min(len(b) for b in bars_map.values())
    print(f"\n=== cross_momentum 篮子({len(symbols)}个品种, {timeframe}) — 对齐后{min_len}根 ===")
    if min_len < lookback + 50:
        print("  数据不够，跳过")
        return
    bars_map = {s: b[-min_len:] for s, b in bars_map.items()}

    positions = {s: None for s in symbols}
    opens, closes_stop, closes_tp, closes_quick = 0, 0, 0, 0
    for i in range(lookback + 5, min_len):
        universe_returns = {}
        for s, b in bars_map.items():
            c_now = float(b[i]["c"])
            c_then = float(b[i - lookback]["c"])
            if c_then > 0:
                universe_returns[s] = c_now / c_then - 1.0
        for s, b in bars_map.items():
            window = b[: i + 1]
            params = {"symbol": s, "universe_returns": universe_returns, "lookback_bars": lookback}
            pos = positions[s]
            if pos is None:
                sig = fn({"base": window}, params, None)
                if sig and sig.get("action") in ("LONG", "SHORT"):
                    positions[s] = {
                        "side": sig["action"], "entry_price": sig["price"],
                        "stop_loss": sig["stop_loss"], "tp1": sig["tp1"],
                    }
                    opens += 1
            else:
                cur_bar = b[i]
                hit_stop, hit_tp = _check_stop_tp(pos, cur_bar)
                if hit_stop:
                    closes_stop += 1
                    positions[s] = None
                    continue
                if hit_tp:
                    closes_tp += 1
                    positions[s] = None
                    continue
                sig = fn({"base": window}, params, pos)
                if sig and str(sig.get("action", "")).startswith("CLOSE"):
                    closes_quick += 1
                    positions[s] = None
    still_open = sum(1 for v in positions.values() if v is not None)
    print(f"  开仓{opens}次 | 止损离场{closes_stop} | TP1离场{closes_tp} | 策略主动离场{closes_quick} | 收盘仍持仓品种数={still_open}")


if __name__ == "__main__":
    run_single_symbol("turtle_breakout", "PAXGUSDT", "4h", 900)
    run_single_symbol("turtle_breakout", "XAUUSDT", "4h", 900)
    run_single_symbol("connors_rsi2", "TSLAUSDT", "4h", 900)
    run_single_symbol("connors_rsi2", "ETHUSDT", "4h", 900)
    run_single_symbol("bollinger_squeeze", "XAUUSDT", "4h", 900)
    run_single_symbol("bollinger_squeeze", "ETHUSDT", "4h", 900)
    run_cross_momentum(
        ["ETHUSDT", "BNBUSDT", "BCHUSDT", "XMRUSDT", "PAXGUSDT", "XAUUSDT",
         "TSLAUSDT", "METAUSDT", "ASMLUSDT"],
        "4h", 500, 20,
    )
    print("\n全部通过，没有断言失败/崩溃。")
