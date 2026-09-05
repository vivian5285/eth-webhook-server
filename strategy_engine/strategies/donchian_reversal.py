#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
唐奇安原始反转系统(Donchian's Original 4-Week Rule)——Richard Donchian，
被公认为"趋势跟随之父"，本人真实管理过公开的Donchian期货基金(1949年
成立，是最早的公开量化趋势跟随基金之一)，Turtle海龟系统的Donchian
通道突破就是从他这套发展来的，本仓库已有的turtle_breakout正是海龟
后来的改良版。这套是Donchian**最原始**的版本，公开发表于其市场通讯，
后来几乎所有讲趋势跟随历史的书都会提到。

规则(教科书标准现代复现，"4周法则"换算成交易日等于20个交易日，本模块
直接用20日Donchian通道)：
  - 创channel_period(默认20)根新高 → 做多(如果当前是空头，直接反手)
  - 创channel_period根新低 → 做空(如果当前是多头，直接反手)
  - **永远在场内**，没有区间盘整期的"空仓观望"这个状态——只要没有
    反向突破，就一直持有现有方向的仓位；这是Donchian原始设计的核心
    特征，跟本仓库其余突破战法都不一样

跟本仓库其余"反手型"战法的关键区别：parabolic_sar_flip也是"永远持仓
反手"，但反手依据的是**指标值**(SAR线本身)；这套反手依据的是**纯价格
结构**(N日最高/最低)，是本擂台里唯一一套"价格结构驱动的反手系统"。
跟turtle_breakout的关系：turtle_breakout是Donchian通道突破**加上**
ATR止损/止盈这套后来的风控层，进场后止损可能先于反向突破触发离场
(不是永远在场内)；这套是去掉那层风控、回归Donchian原始设计的"纯反手"
版本，止损只是本仓库统一加的安全网(见下)，不是原始规则的一部分。

诚实说明：Donchian原始规则本身没有独立止损——止损就是"反向突破"这个
事件本身。本模块额外加了一道ATR止损兜底(不是原始规则，是本仓库对
"如果反向突破迟迟不来、单向仓位敞口失控"这个极端风险的统一处理，
跟本仓库其余战法一致)，属于对原始设计的实用主义补丁，如实说明不是
冒充"这就是原汁原味的唐奇安规则"。

周期选择理由：1d，"4周法则"直接换算成20个交易日，本模块channel_
period=20正是这个换算的直接体现，不需要额外压缩——这是教科书里最常见
的现代等价表述，不是本仓库自己发明的换算。

数据要求：至少channel_period+atr_len+4根bars_by_tf["base"]。
"""
from __future__ import annotations

from typing import Dict, List, Optional

from strategy_engine import indicators

DEFAULT_PARAMS = {
    "channel_period": 20,
    "atr_len": 14,
    "atr_stop_mult": 2.5,
}


def generate_signal(bars_by_tf: Dict[str, List[dict]], params: Optional[dict] = None, position: Optional[dict] = None) -> Optional[dict]:
    bars = bars_by_tf.get("base") or []
    p = {**DEFAULT_PARAMS, **(params or {})}
    period = int(p["channel_period"])
    atr_len = int(p["atr_len"])
    need = period + atr_len + 4
    if len(bars) < need:
        return None

    hl = indicators.donchian_high_low(bars, period)
    if not hl:
        return None
    hh, ll = hl[-1]

    last = bars[-1]
    price = float(last["c"])
    bar_time = int(last["t"])

    if price > hh:
        action, d = "LONG", 1
    elif price < ll:
        action, d = "SHORT", -1
    else:
        return None  # 通道内部，维持现状——原始规则里这就是"继续持有现有方向"，本函数不需要做任何事

    if position:
        side = str(position.get("side") or "").upper()
        if side == action:
            return None  # 突破方向没变，继续持有原方向仓位
        # 方向反了：先平仓，下一轮巡检(仍是同一根K线，因为这是全新突破
        # 事件)会在空仓状态下自然按同一套逻辑反手，是本仓库反手型战法
        # (parabolic_sar_flip等)统一的两步实现方式
        rev_extreme = hh if action == "LONG" else ll
        return {
            "action": "CLOSE_QUICK_EXIT", "price": round(price, 6),
            "reason": f"反向突破{'新高' if action == 'LONG' else '新低'}({rev_extreme:.6f})，反手离场",
            "bar_time": bar_time,
        }

    atr = indicators.wilder_atr(bars, atr_len)
    if atr <= 0:
        return None

    extreme = hh if action == "LONG" else ll
    return {
        "action": action,
        "price": round(price, 6),
        "atr": round(atr, 6),
        "stop_loss": round(price - d * atr * float(p["atr_stop_mult"]), 6),
        "tier": 1,
        "bar_time": bar_time,
        "reason": f"创{period}根{'新高' if action == 'LONG' else '新低'}({extreme:.6f})，永远持仓反手系统",
    }
