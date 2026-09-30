#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
斐波那契回撤反弹系统——2026-09-29应宝贝要求新增，经典技术分析工具，
Leonardo Fibonacci本人无关(数列是他的，回撤位应用是后人几十年公开总结
出来的市场习惯)，规则完全公开、任何人拿收盘价就能手算复现。

核心思想：跟本仓库其余纯趋势策略(HMA拐头/Keltner突破/Turtle通道突破)
不同，这是唯一一个"顺大趋势，但在回撤到特定比例位置才反手进场"的
打法——先确认摆动方向(N根K线内创了新高还是新低)，再等价格回撤到
0.618(黄金分割位，最经典、最常被引用的回撤比例)附近出现反弹确认才
进场，不是摆动一确认就立刻追。

规则：
  · swing_period(默认50)根K线内的最高价/最低价定义这段摆动的区间
  · 摆动方向按"高点/低点哪个更晚出现"判断，不是按"收盘价离哪个更近"——
    后者会在0.618这种深回撤时失真(回撤61.8%后价格数值上已经比中点更
    靠近低点，会被误判成下行摆动，早期版本踩过这个坑，2026-09-29改)。
    高点比低点更晚出现 = 上行摆动(最近在创新高，现在回调)；反之为
    下行摆动。
  · 上行摆动：回撤位 = 高点 - 0.618×(高点-低点)；本根K线最低价跌破
    回撤位、但收盘价收回到回撤位之上(反弹确认，不是继续破位) → 做多
  · 下行摆动：回撤位 = 低点 + 0.618×(高点-低点)；本根K线最高价冲破
    回撤位、但收盘价收回到回撤位之下 → 做空
  · 离场：摆动方向反转，另设ATR×atr_stop_mult(默认2.0)硬止损保底

跟turtle_breakout的关键区别：海龟是"创新高/新低就顺势追"，这套是
"顺势但等它先回调到黄金分割位、出现反弹信号才进场"——同样看多头/空头
大方向，但进场时机完全相反(追突破 vs 等回撤反弹)，能验证"回撤买/追
突破"这两种经典风格在本账户品种池里到底谁更有edge。

周期：4h(跟仓库其余中速趋势战法同一批选择)。0.618是黄金分割位里最
常引用、最不容易被质疑"为什么选这个数"的经典比例，不做hyperopt调参。
"""
from __future__ import annotations

from typing import Dict, List, Optional

from strategy_engine import indicators

DEFAULT_PARAMS = {
    "swing_period": 50,
    "fib_ratio": 0.618,
    "atr_len": 14,
    "atr_stop_mult": 2.0,
}


def generate_signal(bars_by_tf: Dict[str, List[dict]], params: Optional[dict] = None, position: Optional[dict] = None) -> Optional[dict]:
    p = {**DEFAULT_PARAMS, **(params or {})}
    bars = bars_by_tf.get("base") or []
    swing_period = int(p["swing_period"])
    atr_len = int(p["atr_len"])
    need = max(swing_period, atr_len) + 5
    if len(bars) < need:
        return None

    window = bars[-swing_period:]
    highs = [float(b["h"]) for b in window]
    lows = [float(b["l"]) for b in window]
    swing_high = max(highs)
    swing_low = min(lows)
    swing_range = swing_high - swing_low
    if swing_range <= 0:
        return None
    # 用"最近一次触及极值"的下标判断摆动方向，同价打平时偏向更晚的下标
    # (更贴近"最近发生"的定义)。
    high_idx = max(i for i, h in enumerate(highs) if h == swing_high)
    low_idx = max(i for i, l in enumerate(lows) if l == swing_low)

    last = bars[-1]
    close = float(last["c"])
    high = float(last["h"])
    low = float(last["l"])
    bar_time = int(last["t"])

    atr_ser = indicators.atr_series(bars, atr_len)
    if not atr_ser:
        return None
    atr = float(atr_ser[-1])

    # 摆动方向：高点比低点更晚触及 = 上行摆动，反之为下行摆动
    uptrend = high_idx > low_idx

    fib_ratio = float(p["fib_ratio"])

    if position:
        side = str(position.get("side") or "").upper()
        # 摆动方向反转即离场
        if side == "LONG" and not uptrend:
            return {"action": "CLOSE_QUICK_EXIT", "price": round(close, 6),
                    "reason": "斐波那契摆动方向转空", "bar_time": bar_time}
        if side == "SHORT" and uptrend:
            return {"action": "CLOSE_QUICK_EXIT", "price": round(close, 6),
                    "reason": "斐波那契摆动方向转多", "bar_time": bar_time}
        return None

    if uptrend:
        retrace_level = swing_high - fib_ratio * swing_range
        # 本根最低价跌破回撤位、收盘收回上方 = 反弹确认
        if low <= retrace_level < close:
            return {
                "action": "LONG", "price": round(close, 6), "atr": round(atr, 6),
                "stop_loss": round(close - atr * float(p["atr_stop_mult"]), 6),
                "tier": 1, "bar_time": bar_time,
                "reason": f"斐波那契{fib_ratio}回撤位反弹({retrace_level:.6g})",
            }
    else:
        retrace_level = swing_low + fib_ratio * swing_range
        if high >= retrace_level > close:
            return {
                "action": "SHORT", "price": round(close, 6), "atr": round(atr, 6),
                "stop_loss": round(close + atr * float(p["atr_stop_mult"]), 6),
                "tier": 1, "bar_time": bar_time,
                "reason": f"斐波那契{fib_ratio}回撤位受阻({retrace_level:.6g})",
            }
    return None
