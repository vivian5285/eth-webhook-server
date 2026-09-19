#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Williams Alligator——Bill Williams公开发表(《Trading Chaos》《New Trading
Dimensions》，真实交易员/作者，不是网红私有指标)。三条斐波那契周期的
平滑移动平均(SMMA，跟RSI/ATR同一族Wilder式递归平滑，不是普通SMA/EMA)，
构造"鳄鱼睡觉/张嘴/进食"的可视化趋势状态机，公开规则、公式确定。

三条线(全部基于(h+l)/2中价)：
  - 鳄鱼颚(jaw)  = SMMA(13)，前移8根
  - 鳄鱼齿(teeth)= SMMA(8)， 前移5根
  - 鳄鱼唇(lips) = SMMA(5)， 前移3根

"前移N根"在图表上是把N根之前算出来的SMMA值画在当前位置——对我们做
实时信号判断完全等价于"看当前这根K线，jaw用的是SMMA(13)算到(当前-8)
根那一刻的值，teeth用SMMA(8)算到(当前-5)根，lips用SMMA(5)算到(当前-3)
根"，全部是已经发生过的真实值，不存在未来函数。

规则(Bill Williams原书)：
  - "鳄鱼睡觉"：三条线缠绕交织，代表盘整，不操作。
  - "鳄鱼张嘴"：三条线按顺序展开(多头lips>teeth>jaw / 空头相反)且间距
    在扩大，代表趋势启动。
  - "鳄鱼进食"：价格站上/跌破整个鳄鱼嘴(三条线之外)，顺势进场。
  - 离场：三条线顺序被打破(缠绕/反向)，代表趋势结束，鳄鱼"吃饱睡觉"。
    另配ATR止损安全网。

周期：4h，跟擂台里其余趋势跟随战法同一档位，方便横向对比。
"""
from __future__ import annotations

from typing import Dict, List, Optional

from strategy_engine import indicators

DEFAULT_PARAMS = {
    "jaw_len": 13, "jaw_shift": 8,
    "teeth_len": 8, "teeth_shift": 5,
    "lips_len": 5, "lips_shift": 3,
    "atr_len": 14,
    "atr_stop_mult": 2.0,
}


def _smma_at(series: List[float], period: int, idx: int) -> Optional[float]:
    pos = idx - (period - 1)
    if pos < 0 or pos >= len(series):
        return None
    return series[pos]


def generate_signal(bars_by_tf: Dict[str, List[dict]], params: Optional[dict] = None, position: Optional[dict] = None) -> Optional[dict]:
    p = {**DEFAULT_PARAMS, **(params or {})}
    bars = bars_by_tf.get("base") or []
    jaw_len, jaw_shift = int(p["jaw_len"]), int(p["jaw_shift"])
    teeth_len, teeth_shift = int(p["teeth_len"]), int(p["teeth_shift"])
    lips_len, lips_shift = int(p["lips_len"]), int(p["lips_shift"])
    atr_len = int(p["atr_len"])
    need = jaw_len + jaw_shift + 10
    if len(bars) < need:
        return None

    median = [(float(b["h"]) + float(b["l"])) / 2.0 for b in bars]
    jaw_series = indicators.smma(median, jaw_len)
    teeth_series = indicators.smma(median, teeth_len)
    lips_series = indicators.smma(median, lips_len)

    n = len(bars) - 1
    jaw = _smma_at(jaw_series, jaw_len, n - jaw_shift)
    teeth = _smma_at(teeth_series, teeth_len, n - teeth_shift)
    lips = _smma_at(lips_series, lips_len, n - lips_shift)
    jaw_p = _smma_at(jaw_series, jaw_len, n - 1 - jaw_shift)
    teeth_p = _smma_at(teeth_series, teeth_len, n - 1 - teeth_shift)
    lips_p = _smma_at(lips_series, lips_len, n - 1 - lips_shift)
    if None in (jaw, teeth, lips, jaw_p, teeth_p, lips_p):
        return None

    last = bars[-1]
    price = float(last["c"])
    bar_time = int(last["t"])

    bullish = lips > teeth > jaw
    bearish = lips < teeth < jaw
    spread_now = abs(lips - jaw)
    spread_prev = abs(lips_p - jaw_p)
    mouth_opening = spread_now > spread_prev

    if position:
        side = str(position.get("side") or "").upper()
        # 三条线顺序被打破(缠绕/反转) = 鳄鱼吃饱了、趋势前提消失
        if side == "LONG" and not bullish:
            return {"action": "CLOSE_QUICK_EXIT", "price": round(price, 6),
                    "reason": "鳄鱼三线顺序打破(缠绕/反转)，趋势前提消失", "bar_time": bar_time}
        if side == "SHORT" and not bearish:
            return {"action": "CLOSE_QUICK_EXIT", "price": round(price, 6),
                    "reason": "鳄鱼三线顺序打破(缠绕/反转)，趋势前提消失", "bar_time": bar_time}
        return None

    if bullish and mouth_opening and price > lips:
        action, d = "LONG", 1
    elif bearish and mouth_opening and price < lips:
        action, d = "SHORT", -1
    else:
        return None

    atr = indicators.wilder_atr(bars, atr_len)
    if atr <= 0:
        return None

    return {
        "action": action,
        "price": round(price, 6),
        "atr": round(atr, 6),
        "stop_loss": round(price - d * atr * float(p["atr_stop_mult"]), 6),
        "tier": 1,
        "bar_time": bar_time,
        "reason": f"鳄鱼张嘴({'多头' if d == 1 else '空头'}排列)且间距扩大，价格站稳嘴外"
                  f"(lips={lips:.6f} teeth={teeth:.6f} jaw={jaw:.6f})",
    }
