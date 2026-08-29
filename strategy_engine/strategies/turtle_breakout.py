#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Turtle海龟突破系统——Richard Dennis 1980s公开系统，Curtis Faith《Way of
the Turtle》公开过完整规则，几十年真实业绩公开可查。核心思想：极简的
Donchian通道突破入场 + ATR("N")定义的仓位/止损单位，不用任何震荡指标，
纯粹"创新高就买、创新低就卖"的趋势跟随。

跟本仓库其余策略的关键区别：这是唯一一个刻意排除所有震荡类确认指标
(RSI/StochK/KDJ等)的系统——海龟系统的哲学就是"趋势本身就是理由，不需要
额外确认"，加震荡指标反而会滤掉系统赖以盈利的大趋势尾部。

经典规则(System 1简化版，本模块用20日突破入场+10日反向突破离场)：
  - entry_period(默认20)日Donchian通道突破 → 开仓方向
  - N = ATR(atr_len,默认20) → 止损距离 = entry ± atr_stop_mult(默认2)×N
  - exit_period(默认10)日反向通道突破 → 主动离场(不等止损/止盈)
  - tp1/tp2/tp3按1N/2N/3N分档，是为了适配本仓库通用的分批止盈框架，
    不是海龟原始规则的一部分——原始系统只有"反向通道破位"和"2N止损"
    两种离场方式，没有分批止盈。

品种覆盖：适合本账户里趋势性强的品种(PAXG/XAUUSDT等贵金属系，历史上
海龟系统本来就是在商品期货上验证出来的)，不适合频繁震荡的品种。

数据要求：Donchian(20)+ATR(20)至少需要21根bars_by_tf["base"]历史，
本模块设计给较慢的周期用(4h/1d)，不是给品种自己原有的TV分钟级周期用。
"""
from __future__ import annotations

from typing import Dict, List, Optional

from strategy_engine import indicators

DEFAULT_PARAMS = {
    "entry_period": 20,
    "exit_period": 10,
    "atr_len": 20,
    "atr_stop_mult": 2.0,
}


def generate_signal(bars_by_tf: Dict[str, List[dict]], params: Optional[dict] = None, position: Optional[dict] = None) -> Optional[dict]:
    bars = bars_by_tf.get("base") or []
    p = {**DEFAULT_PARAMS, **(params or {})}
    entry_period = int(p["entry_period"])
    exit_period = int(p["exit_period"])
    atr_len = int(p["atr_len"])
    need = max(entry_period, exit_period, atr_len) + 2
    if len(bars) < need:
        return None

    last = bars[-1]
    price = float(last["c"])
    bar_time = int(last["t"])

    if position:
        # 持仓中：只处理"反向短通道破位主动离场"这一种提前离场信号——
        # 止损/止盈价格本身的触碰由通用runner逻辑处理(价格穿过就平仓)。
        side = str(position.get("side") or "").upper()
        exit_ch = indicators.donchian_high_low(bars, exit_period)
        if not exit_ch:
            return None
        ex_hh, ex_ll = exit_ch[-1]
        if side == "LONG" and price <= ex_ll:
            return {
                "action": "CLOSE_QUICK_EXIT",
                "price": round(price, 6),
                "reason": f"反向{exit_period}日通道破位离场(跌破{ex_ll:.6f})",
                "bar_time": bar_time,
            }
        if side == "SHORT" and price >= ex_hh:
            return {
                "action": "CLOSE_QUICK_EXIT",
                "price": round(price, 6),
                "reason": f"反向{exit_period}日通道破位离场(突破{ex_hh:.6f})",
                "bar_time": bar_time,
            }
        return None

    # 空仓：Donchian(entry_period)突破入场
    entry_ch = indicators.donchian_high_low(bars, entry_period)
    if not entry_ch:
        return None
    hh, ll = entry_ch[-1]
    if price > hh:
        action = "LONG"
    elif price < ll:
        action = "SHORT"
    else:
        return None

    atr = indicators.wilder_atr(bars, atr_len)
    if atr <= 0:
        return None
    direction = 1 if action == "LONG" else -1
    n = atr  # 海龟规则里的"N"

    return {
        "action": action,
        "price": round(price, 6),
        "atr": round(atr, 6),
        "stop_loss": round(price - direction * n * float(p["atr_stop_mult"]), 6),
        "tp1": round(price + direction * n * 1.0, 6),
        "tp2": round(price + direction * n * 2.0, 6),
        "tp3": round(price + direction * n * 3.0, 6),
        "tier": 1,
        "bar_time": bar_time,
    }
