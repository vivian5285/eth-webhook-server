#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
考夫曼自适应均线(Kaufman's Adaptive Moving Average, KAMA/AMA)——量化
交易员Perry Kaufman在其公开出版的著作《Trading Systems and Methods》里
发表的公开算法，Kaufman本人是真实管理过量化基金的从业者，这套均线是
他公开发表、几十年被写进各大图表软件(TradingView/Bloomberg等)的标准
指标，不是网红自创黑箱。

核心思想：普通均线(SMA/EMA)不管市场是不是真的有方向，平滑速度永远
一样快；KAMA会先算一个"效率比"(Efficiency Ratio, ER = 这段时间净涨跌
的绝对值 / 这段时间每根K线涨跌幅绝对值之和)——ER接近1说明价格几乎是
直线单向运动(效率高、真趋势)，ER接近0说明来回震荡、净移动很小(效率低、
假动)。用ER动态决定这一根该用多快的平滑常数：真趋势里均线跟得几乎
贴着价格走，震荡里均线几乎走平、不被噪音带偏。

**这是本擂台目前唯一一套"均线自己会根据市场状态自动变速"的战法**——
不需要像adx_regime_switch那样另外接一个ADX开关去判断"现在该用哪套
逻辑"，自适应机制内建在均线的构造公式里，调用方感知不到状态切换，
只看到一条速度会变的均线。如果把"AI/智能"理解成"根据数据自动调整自己
的行为"，这套在本擂台里是唯一一个规则完全公开透明、发明人真实可考、
又确实带有这种自适应特质的战法。

规则：
  - KAMA(er_period默认10, 最快平滑常数对应2根, 最慢对应30根——都是
    Kaufman原始论文的默认值)
  - 入场：价格收盘穿越KAMA(上一根在下/上，这一根在上/下) 且 KAMA自身
    也在朝同一方向走(kama[-1] vs kama[-2]，避免在KAMA本身走平/反向的
    时候追一次价格的假穿越)
  - 离场：反向穿越

跟本仓库其余均线类战法的关键区别：ema_cross_7_30是两条固定速度EMA的
交叉；这套只用一条均线，但均线本身的速度是浮动的，价格穿越它的意义
因此也不一样——被KAMA判定为"高效率趋势"时才会贴得紧、才容易被穿越
触发信号，噪音行情里KAMA走平、价格来回穿越均线不容易被同时满足"均线
自己也在同向走"这个条件，天然带一层降噪。

周期选择理由：4H，跟本仓库其余中速趋势战法同一批周期选择，KAMA本身
不绑定具体日历含义(参数是根数不是天数/周数)，不需要为保留原始设计
比例而改变周期。

数据要求：ER需要er_period根算净变化，KAMA序列从第er_period个点开始，
至少需要er_period+atr_len+6根bars_by_tf["base"]才能有两个连续的KAMA点
判断穿越+斜率。
"""
from __future__ import annotations

from typing import Dict, List, Optional

from strategy_engine import indicators

DEFAULT_PARAMS = {
    "er_period": 10,
    "fast": 2,
    "slow": 30,
    "atr_len": 14,
    "atr_stop_mult": 2.0,
}


def generate_signal(bars_by_tf: Dict[str, List[dict]], params: Optional[dict] = None, position: Optional[dict] = None) -> Optional[dict]:
    bars = bars_by_tf.get("base") or []
    p = {**DEFAULT_PARAMS, **(params or {})}
    er_period = int(p["er_period"])
    atr_len = int(p["atr_len"])
    need = er_period + atr_len + 6
    if len(bars) < need:
        return None

    cs = indicators.closes(bars)
    kama = indicators.kama(cs, er_period, int(p["fast"]), int(p["slow"]))
    if len(kama) < 2:
        return None

    last = bars[-1]
    price = float(last["c"])
    bar_time = int(last["t"])
    prev_price = cs[-2]
    k_now, k_prev = kama[-1], kama[-2]

    if position:
        side = str(position.get("side") or "").upper()
        if side == "LONG" and price < k_now and prev_price >= k_prev:
            return {
                "action": "CLOSE_QUICK_EXIT", "price": round(price, 6),
                "reason": f"收盘跌破KAMA({k_now:.6f})", "bar_time": bar_time,
            }
        if side == "SHORT" and price > k_now and prev_price <= k_prev:
            return {
                "action": "CLOSE_QUICK_EXIT", "price": round(price, 6),
                "reason": f"收盘突破KAMA({k_now:.6f})", "bar_time": bar_time,
            }
        return None

    crossed_up = prev_price < k_prev and price >= k_now
    crossed_down = prev_price > k_prev and price <= k_now
    kama_rising = k_now > k_prev
    kama_falling = k_now < k_prev

    if crossed_up and kama_rising:
        action, d = "LONG", 1
    elif crossed_down and kama_falling:
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
        "reason": f"价格穿越KAMA({k_now:.6f})且均线同向({'升' if action == 'LONG' else '降'})",
    }
