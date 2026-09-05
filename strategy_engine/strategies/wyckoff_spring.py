#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
威科夫弹簧/派发(Wyckoff Spring/Upthrust)——Richard Wyckoff，20世纪初真实
交易员/证券分析教育家(跟道氏、江恩同时代)，公开出版《股票市场技术分析
研究课程》，"弹簧"(Spring)/"派发"(Upthrust)是他公开体系里最著名的
两个概念，几十年来被写进各类技术分析教材，不是网红自创。

核心思想：价格在一个区间里整理了一段时间(威科夫称为"吸筹"/"派发"
阶段)，庄家最后会故意把价格砸穿区间下沿制造恐慌抛盘(或反过来往上
假突破诱多)，把散户洗出去之后再真正拉升——这个"跌破支撑又迅速收回"
的动作就是"弹簧"，是威科夫体系里最经典的"假动作即真信号"入场点。
配合成交量看更可靠：真弹簧往往伴随这一根放量(抛压/买压真的很大，
不是绵绵阴跌)。

规则：
  - 区间：range_period(默认20)根K线的最高高点/最低低点，取confirm_
    bars(默认3)根之前那段(排除最近几根，保证区间是"已经稳定住的"，
    跟darvas_box同样的稳定性检查思路)
  - 弹簧：最近confirm_bars根内，某一根的最低价跌破区间下沿，且那根
    成交量 ≥ vol_spike_mult(默认1.3)倍近期均量(确认是真的一次性抛压
    冲刷，不是趋势性阴跌)
  - 确认：当前这根收盘价重新收回区间下沿之上 → 做多
  - 派发对称(突破区间上沿+放量+收回) → 做空
  - 离场：价格再次跌破(做多)/涨破(做空)弹簧/派发那根的极值(说明这次
    "假动作"其实是真的，结构判断错了)，或ATR止损兜底

跟本仓库其余"假突破"类战法的关键区别：breakout_retest是"先突破、
等回踩确认"，赌的是"真突破后会回踩"；这套是"先假突破(反方向)、再
迅速收回"，赌的是"看起来要跌破支撑其实是洗盘"，方向完全相反的两种
假动作逻辑。跟darvas_box都用区间边界，但darvas是"突破区间边界=真突破
进场"，这套是"跌破区间边界又收回=假跌破进场"，几乎是镜像关系。**本
擂台目前唯一一套要求"放量确认"的假突破反转战法**——volume_profile_
reversion用的是成交量的价格分布(空间维度)，这套用的是单根K线自己的
放量(时间维度)。

周期选择理由：4h，跟darvas_box/breakout_retest同一批区间结构类战法
同周期，方便横向对照三种不同的"区间边界交易"哲学。

数据要求：至少range_period+confirm_bars+vol_len+atr_len+4根
bars_by_tf["base"]。
"""
from __future__ import annotations

from typing import Dict, List, Optional

from strategy_engine import indicators

DEFAULT_PARAMS = {
    "range_period": 20,
    "confirm_bars": 3,
    "vol_len": 20,
    "vol_spike_mult": 1.3,
    "atr_len": 14,
    "atr_stop_mult": 2.0,
}


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def _stable_range(bars: List[dict], range_period: int, confirm_bars: int):
    """区间必须是"已经稳定住的"：range_period根之前那个窗口的最高/
    最低点。不要求confirm_bars期间价格没突破(那是darvas_box的稳定性
    定义)——这里恰恰相反，允许confirm_bars期间发生一次"假突破"，那正是
    弹簧/派发本身。"""
    n = len(bars)
    window_end = n - 1 - confirm_bars
    window_start = window_end - range_period
    if window_start < 0:
        return None, None
    window = bars[window_start:window_end]
    top = max(_f(b["h"]) for b in window)
    bottom = min(_f(b["l"]) for b in window)
    if top <= bottom:
        return None, None
    return top, bottom


def generate_signal(bars_by_tf: Dict[str, List[dict]], params: Optional[dict] = None, position: Optional[dict] = None) -> Optional[dict]:
    bars = bars_by_tf.get("base") or []
    p = {**DEFAULT_PARAMS, **(params or {})}
    range_period = int(p["range_period"])
    confirm_bars = int(p["confirm_bars"])
    vol_len = int(p["vol_len"])
    atr_len = int(p["atr_len"])
    need = max(range_period + confirm_bars, vol_len) + atr_len + 4
    if len(bars) < need:
        return None

    top, bottom = _stable_range(bars, range_period, confirm_bars)
    if top is None:
        return None

    vols = [_f(b.get("v")) for b in bars[-vol_len:]]
    avg_vol = sum(vols) / len(vols) if vols else 0.0
    if avg_vol <= 0:
        return None
    vol_mult = float(p["vol_spike_mult"])

    last = bars[-1]
    price = _f(last["c"])
    bar_time = int(last["t"])

    recent = bars[-1 - confirm_bars:-1]  # 排除当前这根，看confirm_bars根内有没有弹簧/派发

    if position:
        side = str(position.get("side") or "").upper()
        entry_price = _f(position.get("entry_price"))
        # 结构判断错了：又跌破/涨破了触发弹簧/派发的极值本身
        spring_low = min((_f(b["l"]) for b in recent), default=bottom)
        upthrust_high = max((_f(b["h"]) for b in recent), default=top)
        if side == "LONG" and price < min(spring_low, bottom):
            return {
                "action": "CLOSE_QUICK_EXIT", "price": round(price, 6),
                "reason": f"再次跌破弹簧低点({spring_low:.6f})，假突破其实是真的", "bar_time": bar_time,
            }
        if side == "SHORT" and price > max(upthrust_high, top):
            return {
                "action": "CLOSE_QUICK_EXIT", "price": round(price, 6),
                "reason": f"再次涨破派发高点({upthrust_high:.6f})，假突破其实是真的", "bar_time": bar_time,
            }
        return None

    spring_bar = None
    for b in recent:
        if _f(b["l"]) < bottom and _f(b.get("v")) >= vol_mult * avg_vol:
            spring_bar = b
            break
    upthrust_bar = None
    for b in recent:
        if _f(b["h"]) > top and _f(b.get("v")) >= vol_mult * avg_vol:
            upthrust_bar = b
            break

    if spring_bar is not None and price > bottom:
        action, d = "LONG", 1
        struct_note = f"弹簧(跌破{bottom:.6f}放量{_f(spring_bar.get('v'))/avg_vol:.1f}x均量后收回)"
    elif upthrust_bar is not None and price < top:
        action, d = "SHORT", -1
        struct_note = f"派发(突破{top:.6f}放量{_f(upthrust_bar.get('v'))/avg_vol:.1f}x均量后收回)"
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
        "reason": struct_note,
    }
