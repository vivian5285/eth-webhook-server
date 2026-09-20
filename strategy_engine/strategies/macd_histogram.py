#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MACD柱状图系统——Gerald Appel在1970年代公开发表MACD(Moving Average
Convergence Divergence)指标本人原创，柱状图(histogram)这个可视化+交易
规则由Thomas Aspray在1986年补充发表，两部分都是公开发表、几十年教材
标准配置，不是网红自创。

规则(经典参数，Appel原始发表值：快线12、慢线26、信号线9，完全不改)：
  - DIF(MACD线) = EMA(12) - EMA(26)
  - DEA(信号线) = EMA(DIF, 9)
  - 柱状图 = DIF - DEA
  - 柱状图由负转正(=DIF上穿DEA，同一件事的两种说法) → 做多
  - 柱状图由正转负 → 做空
  - 离场：反向穿越(柱状图变号)

跟本仓库其余均线类战法的关键区别：ema_cross_7_30比较的是两条**原始
价格**的EMA；这套比较的是"两条EMA的差值(DIF)"和"这个差值本身的EMA
(DEA)"，是对价格的二阶平滑，天然比单纯双均线交叉更慢地对反转反应、
但也更能过滤掉双EMA交叉那种价格贴着均线来回的高频假交叉。这是"趋势
确认"类指标里被引用最多的公开方法之一。

周期选择理由：4H，跟本仓库其余中速趋势类战法(ema_cross_7_30/
adx_regime_switch)同一批"4H日线合理代理"选择，MACD(12,26,9)这几个数字
本身只是K线根数、不像Ichimoku那样绑定了具体的日历含义，不需要为了
保留原始设计比例而放慢周期。

数据要求：DIF需要至少slow(26)根，DEA是DIF的EMA(9)，还要再多signal(9)
根DIF的历史，所以至少需要slow+signal+atr_len+4根bars_by_tf["base"]
才能有两个连续的柱状图点判断变号。
"""
from __future__ import annotations

from typing import Dict, List, Optional

from strategy_engine import indicators

DEFAULT_PARAMS = {
    "fast": 12,
    "slow": 26,
    "signal": 9,
    "atr_len": 14,
    "atr_stop_mult": 2.0,
    # 2026-09-20新增(macd_histogram_v2用，原版默认0.0不生效、逐字不变)：
    # 排查过擂台真实平仓记录——97笔里一大批是柱状图在0.00001~0.0001量级
    # (零轴附近噪音)变号触发的假信号，不是真反转。hist_min_atr_frac要求
    # 变号那一刻|柱状图值|至少达到这个比例×ATR才算数，过滤零轴抖动。
    "hist_min_atr_frac": 0.0,
    # 2026-09-20新增(macd_histogram_v2用，原版默认0.0不生效)：ADX(14)至少
    # 达到这个值才允许开仓——只在真趋势里吃MACD交叉信号，跟tv_multiscore_
    # v1的MIN_ADX=17.0同一个经典Wilder用法，这里取ADX文献常引用的20。
    "adx_gate": 0.0,
}


def generate_signal(bars_by_tf: Dict[str, List[dict]], params: Optional[dict] = None, position: Optional[dict] = None) -> Optional[dict]:
    bars = bars_by_tf.get("base") or []
    p = {**DEFAULT_PARAMS, **(params or {})}
    fast, slow, signal = int(p["fast"]), int(p["slow"]), int(p["signal"])
    atr_len = int(p["atr_len"])
    need = slow + signal + atr_len + 4
    if len(bars) < need:
        return None

    cs = indicators.closes(bars)
    _, _, hist = indicators.macd(cs, fast, slow, signal)
    if len(hist) < 2:
        return None

    last = bars[-1]
    price = float(last["c"])
    bar_time = int(last["t"])
    h_prev, h_now = hist[-2], hist[-1]
    crossed_up = h_prev <= 0 and h_now > 0
    crossed_down = h_prev >= 0 and h_now < 0

    if position:
        side = str(position.get("side") or "").upper()
        if side == "LONG" and crossed_down:
            return {
                "action": "CLOSE_QUICK_EXIT", "price": round(price, 6),
                "reason": f"MACD柱状图转负({h_now:.6f})，动量转空", "bar_time": bar_time,
            }
        if side == "SHORT" and crossed_up:
            return {
                "action": "CLOSE_QUICK_EXIT", "price": round(price, 6),
                "reason": f"MACD柱状图转正({h_now:.6f})，动量转多", "bar_time": bar_time,
            }
        return None

    if not crossed_up and not crossed_down:
        return None

    atr = indicators.wilder_atr(bars, atr_len)
    if atr <= 0:
        return None

    hist_min_frac = float(p.get("hist_min_atr_frac") or 0)
    if hist_min_frac > 0 and abs(h_now) < hist_min_frac * atr:
        return None  # 零轴附近噪音变号，幅度不够，不算数

    adx_gate = float(p.get("adx_gate") or 0)
    if adx_gate > 0:
        adx = indicators.wilder_adx(bars, atr_len)
        if adx < adx_gate:
            return None  # 不在真趋势里，MACD交叉信号先不吃

    action = "LONG" if crossed_up else "SHORT"
    d = 1 if action == "LONG" else -1

    return {
        "action": action,
        "price": round(price, 6),
        "atr": round(atr, 6),
        "stop_loss": round(price - d * atr * float(p["atr_stop_mult"]), 6),
        "tier": 1,
        "bar_time": bar_time,
        "reason": f"MACD({fast},{slow},{signal})柱状图{'转正' if action == 'LONG' else '转负'}({h_now:+.6f})",
    }
