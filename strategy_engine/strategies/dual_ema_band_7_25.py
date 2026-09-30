#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
双均线(7/25)站上/跌破系统——2026-09-29应宝贝要求新增，宝贝原话："K线站上
双均线做多，跌破双均线开空"。

跟已有的ema_cross_7_30是两回事，别搞混：
  - ema_cross_7_30：判EMA(7)和EMA(30)**两条线自己互相交叉**(金叉/死叉)，
    价格本身不参与判断方向，只是被两条线各自的走势带动。
  - 这套：判**收盘价相对两条线的位置**——收盘价同时站在EMA(7)和EMA(25)
    上方 → 多头；同时跌破两条均线下方 → 空头。价格是主角，两条均线是
    价格所处位置的"上下轨"，不是两条线互相交叉。

规则(宝贝原话直译，不加多余确认条件)：
  · 收盘价 > EMA(7) 且 收盘价 > EMA(25) → 做多
  · 收盘价 < EMA(7) 且 收盘价 < EMA(25) → 做空
  · 持仓中，出现反向条件(多头持仓时价格转为同时跌破两线，反之亦然)
    → 反手离场，价格夹在两线之间(不满足任一方向条件)时持仓不动，跟
    ema_cross_7_30同一套"反向信号离场，不额外发明退出逻辑"的标准做法
  · 止损：ATR(14)×2.5，跟ema_cross_7_30同一数量级(这类均线系统本身没有
    天然止损位，止损是执行层风险管理需要，不是规则本身的一部分)

周期：应宝贝要求同一份代码注册1h和90m两个独立时间周期，各自单独
跟踪，不是同一笔信号跨周期确认(即互相独立，不是"两个周期都要站上
才算数"的过滤)——90m是币安非原生周期，靠klines.py::get_bars内建的
15m合成，跟仓库里150m/45m用的是同一条既有机制。
"""
from __future__ import annotations

from typing import Dict, List, Optional

from strategy_engine import indicators

DEFAULT_PARAMS = {
    "fast_period": 7,
    "slow_period": 25,
    "atr_len": 14,
    "atr_stop_mult": 2.5,
}


def generate_signal(bars_by_tf: Dict[str, List[dict]], params: Optional[dict] = None, position: Optional[dict] = None) -> Optional[dict]:
    p = {**DEFAULT_PARAMS, **(params or {})}
    bars = bars_by_tf.get("base") or []
    fast_period = int(p["fast_period"])
    slow_period = int(p["slow_period"])
    atr_len = int(p["atr_len"])
    need = max(fast_period, slow_period, atr_len) + 5
    if len(bars) < need:
        return None

    cs = indicators.closes(bars)
    ema_fast = indicators.ema(cs, fast_period)
    ema_slow = indicators.ema(cs, slow_period)
    if not ema_fast or not ema_slow:
        return None

    atr_ser = indicators.atr_series(bars, atr_len)
    if not atr_ser:
        return None
    atr = float(atr_ser[-1])
    if atr <= 0:
        return None

    last = bars[-1]
    close = float(last["c"])
    bar_time = int(last["t"])
    fast = float(ema_fast[-1])
    slow = float(ema_slow[-1])

    above_both = close > fast and close > slow
    below_both = close < fast and close < slow

    if position:
        side = str(position.get("side") or "").upper()
        if side == "LONG" and below_both:
            return {"action": "CLOSE_QUICK_EXIT", "price": round(close, 6),
                    "reason": "跌破双均线，多头离场", "bar_time": bar_time}
        if side == "SHORT" and above_both:
            return {"action": "CLOSE_QUICK_EXIT", "price": round(close, 6),
                    "reason": "站上双均线，空头离场", "bar_time": bar_time}
        return None

    # reverse_now=True：这套系统离场条件(below_both/above_both)跟反向
    # 入场条件在数学上完全等价——多头离场的条件本身就是空头入场的条件，
    # 不是"大概率一致"，是同一个布尔表达式。2026-09-30应宝贝要求
    # ("反手和平仓可以同时进行...平仓和反手开仓几乎是一个东西")，让引擎
    # 侧的反手机制(_tick_single_symbol_entry里那段hma_trend_reverse_*
    # 同款逻辑，见该处注释)在平仓的同一个tick里，直接调用一次
    # fn(...,position=None)拿到这里的新鲜信号——如果它也返回了同方向的
    # LONG/SHORT，就带着reverse_now立即反手，不用等下一轮5分钟巡检。
    if above_both:
        return {
            "action": "LONG", "price": round(close, 6), "atr": round(atr, 6),
            "stop_loss": round(close - atr * float(p["atr_stop_mult"]), 6),
            "tier": 1, "bar_time": bar_time,
            "reason": f"站上双均线(EMA{fast_period}={fast:.6g}, EMA{slow_period}={slow:.6g})",
            "reverse_now": True,
        }
    if below_both:
        return {
            "action": "SHORT", "price": round(close, 6), "atr": round(atr, 6),
            "stop_loss": round(close + atr * float(p["atr_stop_mult"]), 6),
            "tier": 1, "bar_time": bar_time,
            "reason": f"跌破双均线(EMA{fast_period}={fast:.6g}, EMA{slow_period}={slow:.6g})",
            "reverse_now": True,
        }
    return None
