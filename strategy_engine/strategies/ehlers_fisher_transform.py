#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Ehlers Fisher Transform——John F. Ehlers公开发表(MESA Software创始人，
《Cybernetic Analysis for Stocks and Futures》等多本专著作者，原始论文
"Using The Fisher Transform"公开可查：
https://www.mesasoftware.com/papers/UsingTheFisherTransform.pdf)。

核心思想：价格本身的概率分布是有偏的、拖尾的(不是正态分布)，很难精确
判断"现在算不算极端"。Fisher Transform先把价格压缩映射到(-1,1)区间，
再用 y = 0.5×ln((1+x)/(1-x)) 这个变换把它转成近似高斯正态分布——正态
分布下"极端值"和"拐点"在统计意义上更清晰、更容易触发尖锐的转折信号，
这是Ehlers用来对付"传统震荡指标钝化(在极值区间来回贴着走、不好用来
判断真正拐点)"这个通病的公开解法。

算法(照抄原始论文公式，period默认9是Ehlers本人给的原始值)：
  1. value1 = 0.33×2×((close - 近period根最低价)/(近period根最高价-最
     低价) - 0.5) + 0.67×value1[上一根]  ——先压缩到大致(-1,1)，加权平滑
     降噪(0.33/0.67这两个系数是原始论文给定的固定值，不是拟合参数)
  2. value1 clamp到[-0.999, 0.999](避免下一步log在边界发散)
  3. fisher = 0.5×ln((1+value1)/(1-value1)) + 0.5×fisher[上一根]

交易规则(Ehlers论文原文建议)：fisher线上穿它自己上一根的值(trigger)
做多，下穿做空——本质是"变化率由负转正/由正转负"的拐点确认，不需要
额外的固定阈值。另配ATR止损安全网(通用runner需要)。

周期选择：Ehlers设计这套指标是为了捕捉相对短周期的拐点(不是慢周期
趋势跟随)，本仓库注册在1h(跟擂台里wavetrend/kdj_cross等"指标翻转型"
战法的周期档位一致，但机制完全不同——那些是原始价格上的震荡指标，
这套是先把价格分布正态化再看拐点，擂台里独一份)。
"""
from __future__ import annotations

import math
from typing import Dict, List, Optional

from strategy_engine import indicators

DEFAULT_PARAMS = {
    "period": 9,
    "atr_len": 14,
    "atr_stop_mult": 2.0,
}


def _fisher_series(bars: List[dict], period: int) -> List[float]:
    n = len(bars)
    if n < period + 2:
        return []
    highs = [float(b["h"]) for b in bars]
    lows = [float(b["l"]) for b in bars]
    closes = [float(b["c"]) for b in bars]
    value1 = 0.0
    fisher = 0.0
    out = []
    for i in range(period - 1, n):
        hh = max(highs[i - period + 1:i + 1])
        ll = min(lows[i - period + 1:i + 1])
        rng = hh - ll
        raw = 0.0 if rng <= 0 else 2.0 * ((closes[i] - ll) / rng - 0.5)
        value1 = 0.33 * raw + 0.67 * value1
        value1 = max(-0.999, min(0.999, value1))
        fisher = 0.5 * math.log((1 + value1) / (1 - value1)) + 0.5 * fisher
        out.append(fisher)
    return out


def generate_signal(bars_by_tf: Dict[str, List[dict]], params: Optional[dict] = None, position: Optional[dict] = None) -> Optional[dict]:
    p = {**DEFAULT_PARAMS, **(params or {})}
    bars = bars_by_tf.get("base") or []
    period = int(p["period"])
    atr_len = int(p["atr_len"])
    need = max(period * 3, atr_len + 2) + 5
    if len(bars) < need:
        return None

    fisher = _fisher_series(bars, period)
    if len(fisher) < 3:
        return None

    last = bars[-1]
    price = float(last["c"])
    bar_time = int(last["t"])

    f_now, f_prev = fisher[-1], fisher[-2]
    trigger_now, trigger_prev = fisher[-2], fisher[-3]  # trigger=fisher自己上一根(Ehlers原文口径)
    cross_up = f_prev <= trigger_prev and f_now > trigger_now
    cross_dn = f_prev >= trigger_prev and f_now < trigger_now

    if position:
        side = str(position.get("side") or "").upper()
        if side == "LONG" and cross_dn:
            return {"action": "CLOSE_QUICK_EXIT", "price": round(price, 6),
                    "reason": f"Fisher({f_now:.3f})下穿trigger，拐点转弱", "bar_time": bar_time}
        if side == "SHORT" and cross_up:
            return {"action": "CLOSE_QUICK_EXIT", "price": round(price, 6),
                    "reason": f"Fisher({f_now:.3f})上穿trigger，拐点转强", "bar_time": bar_time}
        return None

    if cross_up:
        action, d = "LONG", 1
    elif cross_dn:
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
        "reason": f"Fisher Transform({period}) {'上穿' if d == 1 else '下穿'}trigger，值={f_now:+.3f}",
    }
