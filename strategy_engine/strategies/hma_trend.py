#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
赫尔均线趋势系统(Hull Moving Average Trend)——Alan Hull，2005年在自己
的网站公开发表HMA公式，"降低均线滞后"这个思路后来被写进各大图表软件
(TradingView内置ta.hma)，规则透明公开、任何人拿收盘价都能手算复现，
不是网红自创黑箱。

核心思想：传统均线(SMA/EMA)为了平滑噪音，天然要牺牲一部分反应速度——
价格已经转向了，均线还要过几根才跟上。HMA用两个不同周期的加权移动
均线(WMA)做差分再平滑的构造方式(公式见下)，在保持平滑效果的同时把
滞后降到比同周期SMA/EMA小得多，业内常用"HMA拐头"(颜色变化)当趋势
转向的早期信号。

规则(教科书标准公式，不做任何改动)：
  HMA(n) = WMA(2×WMA(close, n/2) - WMA(close, n), round(sqrt(n)))
  - HMA从下降转为上升(拐头向上) → 做多
  - HMA从上升转为下降(拐头向下) → 做空
  - 离场：反向拐头(结构跟parabolic_sar_flip/supertrend_adx同一套
    "指标翻转即反手"逻辑，只是这次翻转的是HMA斜率而不是SAR/SuperTrend
    通道)

跟本仓库其余均线类战法的关键区别：ema_cross_7_30是两条固定速度EMA的
交叉；kaufman_ama是均线自己根据效率比动态变速；这套只用**一条**均线，
既不是双线交叉、也不是自适应变速，而是通过"加权+差分"这个特殊构造
方式本身降低滞后——是本擂台第三种不同的"减少均线滞后"思路，跟前两种
机制完全不同。

周期选择理由：4h，跟本仓库其余中速趋势战法同一批"4h日线合理代理"
选择，HMA本身不绑定具体日历含义(参数是根数不是天数)。

数据要求：HMA构造涉及WMA(period)→WMA(period/2)→再嵌套一层WMA(sqrt)，
需要比单纯期望值大不少的历史热身，本模块要求至少period×3+atr_len+10根
bars_by_tf["base"](经验值，留足冗余)。
"""
from __future__ import annotations

from typing import Dict, List, Optional

from strategy_engine import indicators

DEFAULT_PARAMS = {
    "period": 20,
    "atr_len": 14,
    "atr_stop_mult": 2.0,
}


def generate_signal(bars_by_tf: Dict[str, List[dict]], params: Optional[dict] = None, position: Optional[dict] = None) -> Optional[dict]:
    bars = bars_by_tf.get("base") or []
    p = {**DEFAULT_PARAMS, **(params or {})}
    period = int(p["period"])
    atr_len = int(p["atr_len"])
    need = period * 3 + atr_len + 10
    if len(bars) < need:
        return None

    cs = indicators.closes(bars)
    h = indicators.hma(cs, period)
    if len(h) < 3:
        return None

    last = bars[-1]
    price = float(last["c"])
    bar_time = int(last["t"])

    dir_now = 1 if h[-1] > h[-2] else -1
    dir_prev = 1 if h[-2] > h[-3] else -1
    flipped_up = dir_prev < 0 and dir_now > 0
    flipped_down = dir_prev > 0 and dir_now < 0

    if position:
        side = str(position.get("side") or "").upper()
        if side == "LONG" and flipped_down:
            return {
                "action": "CLOSE_QUICK_EXIT", "price": round(price, 6),
                "reason": f"HMA拐头向下(HMA={h[-1]:.6f})，反手离场", "bar_time": bar_time,
            }
        if side == "SHORT" and flipped_up:
            return {
                "action": "CLOSE_QUICK_EXIT", "price": round(price, 6),
                "reason": f"HMA拐头向上(HMA={h[-1]:.6f})，反手离场", "bar_time": bar_time,
            }
        return None

    if not flipped_up and not flipped_down:
        return None

    atr = indicators.wilder_atr(bars, atr_len)
    if atr <= 0:
        return None

    action = "LONG" if flipped_up else "SHORT"
    d = 1 if action == "LONG" else -1
    return {
        "action": action,
        "price": round(price, 6),
        "atr": round(atr, 6),
        "stop_loss": round(price - d * atr * float(p["atr_stop_mult"]), 6),
        "tier": 1,
        "bar_time": bar_time,
        "reason": f"HMA({period})拐头{'向上' if action == 'LONG' else '向下'}(HMA={h[-1]:.6f})，颜色转变信号",
    }
