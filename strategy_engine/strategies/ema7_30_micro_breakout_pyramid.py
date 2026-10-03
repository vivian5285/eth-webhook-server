#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
EMA7/30微结构突破——试探仓确认加仓版。2026-10-01替换掉ema7_30_micro_
breakout_scaled.py(ADX三档静态缩放)，原因是宝贝发现了那版一个真实的
设计缺陷：ADX是滞后指标，真趋势刚启动的头几根K线ADX大概率还没走高，
那版恰好会在最该重仓的起涨点给最小仓位(截图实例：SNDK从1540拉到1800
那段真实上涨，最早那几根按ADX分类大概率还在弱档，会被打到0.4倍)。

新设计不在开仓那一刻判断"这是趋势还是震荡"(这个判断本身就是难题，
ADX/均线发散度都只能事后确认)，而是让价格自己证明自己——开仓先给
试探仓(PROBE_FRACTION=0.5倍)，如果价格继续朝有利方向走出至少
CONFIRM_ATR_MULT(0.5)×ATR，说明这次突破是真的在走、不是插一下就缩
回去，这时候才补满到满仓(再加0.5倍，合计1.0倍)；如果还没走出这段
确认距离就先触发正常的反手/跌破双均线平仓，那就只是半仓离场——真趋势
会很快给出确认(不需要等ADX转强)，震荡撑不到确认那一步，天然只吃半仓
的亏，效果上达成宝贝要的"震荡期少吃亏、真趋势别踏空"，但不需要预判。

这是经典海龟交易法"金字塔加仓"思路的简化两级版(试探→确认，不是多级
封顶1.5倍那种)——先验证"先半仓、确认再补"这个思路本身有没有用，如果
擂台数据证明有效，再考虑要不要做成多级、封顶1.5倍。

复用的是擂台早就验证过的"probe试探仓→ADD确认补仓"跑腿机制(跟
heikin_ashi_adaptive_probe.py同一套，multi_strategy_runner.py::
_add_to_position/_tick_single_symbol_entry里entry_stage=="probe"这条
分支)，不需要改跑腿代码——只需要策略自己按约定返回position_fraction/
entry_stage/ADD这几个字段。

跟对照组(ema7_30_micro_breakout.py，固定1.0倍、无试探仓)入场判断条件
逐字一致，只改"怎么把仓位分批建起来"这一个维度。
"""
from __future__ import annotations

from typing import Dict, List, Optional

from strategy_engine import indicators

DEFAULT_PARAMS = {
    "fast_period": 7,
    "slow_period": 30,
    "atr_len": 14,
    "atr_stop_mult": 2.5,
    "breakout_bars": 5,
    "body_atr_mult": 0.2,
    "probe_fraction": 0.5,
    "confirm_atr_mult": 0.5,
}


def _core_signals(bars: List[dict], p: dict):
    """算一遍跟对照组逐字一致的核心条件，返回(price, atr_now, bar_time,
    long_condition, short_condition, close_long_condition,
    close_short_condition)，供下面几处分支复用，避免重复写四遍。"""
    fast_n = int(p["fast_period"])
    slow_n = int(p["slow_period"])
    atr_len = int(p["atr_len"])
    breakout_n = int(p["breakout_bars"])
    body_mult = float(p["body_atr_mult"])

    need = max(slow_n + 2, atr_len + 2, breakout_n + 2)
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

    return {
        "price": price, "atr_now": atr_now, "bar_time": bar_time,
        "long_condition": long_condition, "short_condition": short_condition,
        "close_long_condition": close_long_condition, "close_short_condition": close_short_condition,
    }


def generate_signal(bars_by_tf: Dict[str, List[dict]], params: Optional[dict] = None, position: Optional[dict] = None) -> Optional[dict]:
    bars = bars_by_tf.get("base") or []
    p = {**DEFAULT_PARAMS, **(params or {})}
    core = _core_signals(bars, p)
    if core is None:
        return None
    price, atr_now, bar_time = core["price"], core["atr_now"], core["bar_time"]
    long_condition, short_condition = core["long_condition"], core["short_condition"]
    close_long_condition, close_short_condition = core["close_long_condition"], core["close_short_condition"]
    probe_frac = float(p["probe_fraction"])
    confirm_mult = float(p["confirm_atr_mult"])

    if position:
        side = str(position.get("side") or "").upper()
        stage = str(position.get("entry_stage") or "")

        if stage == "probe":
            entry_px = float(position.get("entry_price") or position.get("entry") or 0)
            probe_atr = float(position.get("atr0") or atr_now) or atr_now
            if side == "LONG":
                # 先检查正常的反手/跌破双均线——试探仓阶段一样要保护下行，
                # 不因为还没确认就裸奔到下一根。
                if short_condition:
                    return {
                        "action": "SHORT", "price": round(price, 6), "atr": round(atr_now, 6),
                        "stop_loss": round(price + atr_now * float(p["atr_stop_mult"]), 6),
                        "reason": "反转平多开空(试探仓阶段)", "tier": 1, "bar_time": bar_time,
                        "position_fraction": probe_frac, "entry_stage": "probe",
                    }
                if close_long_condition:
                    return {
                        "action": "CLOSE_QUICK_EXIT", "price": round(price, 6),
                        "reason": "跌破双均线平多(试探仓未确认)", "bar_time": bar_time,
                    }
                # 确认：价格已经朝有利方向走出≥confirm_atr_mult×ATR
                if entry_px > 0 and probe_atr > 0 and (price - entry_px) >= confirm_mult * probe_atr:
                    return {
                        "action": "ADD", "side": "LONG", "price": round(price, 6),
                        "atr": round(atr_now, 6), "stop_loss": round(price - atr_now * float(p["atr_stop_mult"]), 6),
                        "position_fraction": round(1.0 - probe_frac, 3), "bar_time": bar_time,
                        "reason": f"试探仓确认(+{(price - entry_px) / probe_atr:.2f}×ATR)，补满仓",
                    }
                return None
            if side == "SHORT":
                if long_condition:
                    return {
                        "action": "LONG", "price": round(price, 6), "atr": round(atr_now, 6),
                        "stop_loss": round(price - atr_now * float(p["atr_stop_mult"]), 6),
                        "reason": "反转平空开多(试探仓阶段)", "tier": 1, "bar_time": bar_time,
                        "position_fraction": probe_frac, "entry_stage": "probe",
                    }
                if close_short_condition:
                    return {
                        "action": "CLOSE_QUICK_EXIT", "price": round(price, 6),
                        "reason": "站上双均线平空(试探仓未确认)", "bar_time": bar_time,
                    }
                if entry_px > 0 and probe_atr > 0 and (entry_px - price) >= confirm_mult * probe_atr:
                    return {
                        "action": "ADD", "side": "SHORT", "price": round(price, 6),
                        "atr": round(atr_now, 6), "stop_loss": round(price + atr_now * float(p["atr_stop_mult"]), 6),
                        "position_fraction": round(1.0 - probe_frac, 3), "bar_time": bar_time,
                        "reason": f"试探仓确认(+{(entry_px - price) / probe_atr:.2f}×ATR)，补满仓",
                    }
                return None
            return None

        # 已经补满仓(entry_stage=="confirmed"或缺省) —— 跟对照组逐字一致，
        # 不再打折，正常反手/离场。
        if side == "LONG":
            if short_condition:
                return {
                    "action": "SHORT", "price": round(price, 6), "atr": round(atr_now, 6),
                    "stop_loss": round(price + atr_now * float(p["atr_stop_mult"]), 6),
                    "reason": "反转平多开空(微结构突破)", "tier": 1, "bar_time": bar_time,
                    "position_fraction": probe_frac, "entry_stage": "probe",
                }
            if close_long_condition:
                return {
                    "action": "CLOSE_QUICK_EXIT", "price": round(price, 6),
                    "reason": "跌破双均线平多", "bar_time": bar_time,
                }
            return None
        if side == "SHORT":
            if long_condition:
                return {
                    "action": "LONG", "price": round(price, 6), "atr": round(atr_now, 6),
                    "stop_loss": round(price - atr_now * float(p["atr_stop_mult"]), 6),
                    "reason": "反转平空开多(微结构突破)", "tier": 1, "bar_time": bar_time,
                    "position_fraction": probe_frac, "entry_stage": "probe",
                }
            if close_short_condition:
                return {
                    "action": "CLOSE_QUICK_EXIT", "price": round(price, 6),
                    "reason": "站上双均线平空", "bar_time": bar_time,
                }
            return None
        return None

    # 空仓：全新开仓，一律先从试探仓开始，不再按ADX/均线发散度预判仓位。
    if not long_condition and not short_condition:
        return None
    action = "LONG" if long_condition else "SHORT"
    direction = 1 if action == "LONG" else -1
    return {
        "action": action,
        "price": round(price, 6),
        "atr": round(atr_now, 6),
        "stop_loss": round(price - direction * atr_now * float(p["atr_stop_mult"]), 6),
        "tier": 1,
        "bar_time": bar_time,
        "position_fraction": probe_frac,
        "entry_stage": "probe",
        "reason": "试探仓开仓，等待确认补满",
    }
