#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
EMA7/30微结构突破——趋势强弱三档动态仓位版。2026-10-01应宝贝要求新增
对照实验，两轮迭代：
  第一版(已废弃)：ADX+EMA价差两个维度各自连续插值取平均，自己发明一套
    新的归一化区间。
  第二版(当前这版)：宝贝追问"如何做到趋势没那么强少下单，趋势强最高
    1倍？最高1倍，然后30%分别递减"——想清楚了应该直接复用
    defense_profiles.py::resolve_adx_tier/reentry_profiles.py::
    adx_to_tier这套**已经在实盘跑了好几个月**的弱/中/强三档分类(ADX<20
    弱/20-30中/>30强)，硬止损K_tier、杠杆分档(B_TIER_LEVERAGE/
    TIER_LEVERAGE)全都是这同一套边界——不该为了仓位缩放这一个新用途
    再发明一套新的连续插值公式，那样测出来的东西也没法直接套回实盘。
    仓位映射：强档1.0倍(满仓)/中档0.7倍/弱档0.4倍，每降一档绝对减
    30个百分点，是宝贝直接给的数字。

跟对照组(ema7_30_micro_breakout.py，固定1.0倍)入场/离场条件逐字一致，
只改仓位这一个维度，方便干净对比"三档动态缩放到底有没有用"。

擂台这边没有直接复用defense_profiles.py(那是币安/CoinW实盘目录下的
文件，跟擂台strategy_engine是两个独立代码树)，这里用
strategy_engine.indicators.wilder_adx现算ADX、按同样的20/30边界分档，
数值口径完全对齐，不是另一套算法。

position_fraction字段是multi_strategy_runner.py::_open_from_signal
已经原生支持的乘数(`desired_qty *= float(sig.get("position_fraction")
or 1.0)`)，2026-09-30搭本金等值仓位那批就顺带建好了，这里直接复用，
不需要改跑腿代码。
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from strategy_engine import indicators

DEFAULT_PARAMS = {
    "fast_period": 7,
    "slow_period": 30,
    "atr_len": 14,
    "atr_stop_mult": 2.5,
    "breakout_bars": 5,
    "body_atr_mult": 0.2,
    "adx_period": 14,
    # 跟defense_profiles.py::resolve_adx_tier/reentry_profiles.py::
    # adx_to_tier逐字同一套边界(ADX_WEAK_LT=20.0/ADX_STRONG_GT=30.0)，
    # 已经在实盘硬止损/杠杆分档上跑了好几个月，不是新拍的数字。
    "adx_weak_lt": 20.0,
    "adx_strong_gt": 30.0,
    # 强/中/弱三档仓位倍数——宝贝原话"最高1倍，然后30%分别递减"。
    "tier_fraction": {2: 1.0, 1: 0.7, 0: 0.4},
}


def _adx_tier(adx_now: float, p: dict) -> int:
    weak_lt = float(p["adx_weak_lt"])
    strong_gt = float(p["adx_strong_gt"])
    if adx_now < weak_lt:
        return 0
    if adx_now > strong_gt:
        return 2
    return 1


def _tier_position_fraction(bars: List[dict], p: dict) -> Tuple[float, dict]:
    adx_period = int(p["adx_period"])
    adx_now = 0.0
    if len(bars) >= adx_period * 2 + 2:
        try:
            adx_now = float(indicators.wilder_adx(bars, adx_period) or 0.0)
        except Exception:
            adx_now = 0.0
    tier = _adx_tier(adx_now, p)
    frac_map = p["tier_fraction"]
    fraction = float(frac_map.get(tier, frac_map.get(1, 0.7)))
    meta = {"adx": round(adx_now, 2), "tier": tier}
    return fraction, meta


def generate_signal(bars_by_tf: Dict[str, List[dict]], params: Optional[dict] = None, position: Optional[dict] = None) -> Optional[dict]:
    bars = bars_by_tf.get("base") or []
    p = {**DEFAULT_PARAMS, **(params or {})}
    if "tier_fraction" not in p or not isinstance(p.get("tier_fraction"), dict):
        p["tier_fraction"] = DEFAULT_PARAMS["tier_fraction"]
    fast_n = int(p["fast_period"])
    slow_n = int(p["slow_period"])
    atr_len = int(p["atr_len"])
    breakout_n = int(p["breakout_bars"])
    body_mult = float(p["body_atr_mult"])

    need = max(slow_n + 2, atr_len + 2, breakout_n + 2, int(p["adx_period"]) * 2 + 2)
    if len(bars) < need:
        return None

    closes = indicators.closes(bars)
    fast = indicators.ema(closes, fast_n)
    slow = indicators.ema(closes, slow_n)
    if len(fast) < 2 or len(slow) < 2:
        return None

    donchian = indicators.donchian_high_low(bars, breakout_n)
    if not donchian:
        return None

    atr_ser = indicators.atr_series(bars, atr_len)
    if not atr_ser:
        return None

    last = bars[-1]
    price = float(last["c"])
    o = float(last["o"])
    bar_time = int(last["t"])
    atr_now = float(atr_ser[-1])
    if atr_now <= 0:
        return None

    fast_curr, fast_prev = fast[-1], fast[-2]
    slow_curr = slow[-1]
    hh, ll = donchian[-1]

    ema_fast_up = fast_curr > fast_prev
    ema_fast_down = fast_curr < fast_prev
    is_bullish = price > o
    is_bearish = price < o
    body_ok = abs(price - o) >= atr_now * body_mult

    long_condition = (
        ema_fast_up and is_bullish and price > fast_curr and price > slow_curr
        and body_ok and price > hh
    )
    short_condition = (
        ema_fast_down and is_bearish and price < fast_curr and price < slow_curr
        and body_ok and price < ll
    )
    close_long_condition = price < fast_curr and price < slow_curr
    close_short_condition = price > fast_curr and price > slow_curr

    # 离场逻辑跟对照组逐字一致——position_fraction只打折开仓量，离场
    # 天然是"这笔仓全部平掉"，不存在"离场也打折"这回事。
    if position:
        side = str(position.get("side") or "").upper()
        if side == "LONG":
            if short_condition:
                frac, meta = _tier_position_fraction(bars, p)
                return {
                    "action": "SHORT",
                    "price": round(price, 6),
                    "atr": round(atr_now, 6),
                    "stop_loss": round(price + atr_now * float(p["atr_stop_mult"]), 6),
                    "reason": f"反转平多开空(微结构突破) 趋势档={meta}",
                    "tier": meta["tier"],
                    "bar_time": bar_time,
                    "position_fraction": frac,
                }
            if close_long_condition:
                return {
                    "action": "CLOSE_QUICK_EXIT",
                    "price": round(price, 6),
                    "reason": "跌破双均线平多",
                    "bar_time": bar_time,
                }
            return None
        if side == "SHORT":
            if long_condition:
                frac, meta = _tier_position_fraction(bars, p)
                return {
                    "action": "LONG",
                    "price": round(price, 6),
                    "atr": round(atr_now, 6),
                    "stop_loss": round(price - atr_now * float(p["atr_stop_mult"]), 6),
                    "reason": f"反转平空开多(微结构突破) 趋势档={meta}",
                    "tier": meta["tier"],
                    "bar_time": bar_time,
                    "position_fraction": frac,
                }
            if close_short_condition:
                return {
                    "action": "CLOSE_QUICK_EXIT",
                    "price": round(price, 6),
                    "reason": "站上双均线平空",
                    "bar_time": bar_time,
                }
            return None
        return None

    if not long_condition and not short_condition:
        return None

    action = "LONG" if long_condition else "SHORT"
    direction = 1 if action == "LONG" else -1
    frac, meta = _tier_position_fraction(bars, p)
    return {
        "action": action,
        "price": round(price, 6),
        "atr": round(atr_now, 6),
        "stop_loss": round(price - direction * atr_now * float(p["atr_stop_mult"]), 6),
        "tier": meta["tier"],
        "bar_time": bar_time,
        "position_fraction": frac,
        "reason": f"趋势档={meta}",
    }
