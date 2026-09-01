#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ADX Regime Switch(ADX市场状态切换·趋势+震荡自适应)——2026-09-01应用户
要求新增："擂台里大多数是1d，有没有波段+趋势、能自动识别震荡期和趋势期
的策略"。这套不是抄某一篇具体论文/某个具体项目，而是把技术分析里
一个非常经典、非黑箱的公开思路直接实现：ADX本身的发明者J. Welles
Wilder在提出ADX时就明确说过它的用途之一是"判断当前该用趋势跟随系统
还是震荡指标系统"——ADX高代表真趋势(该用趋势跟随)，ADX低代表横盘
(该用均值回归)，这是ADX诞生以来公开教科书级别的经典用法，不是网红
自创，每个组成部分(ADX/EMA金叉死叉/布林带+RSI均值回归)单独拿出来都
是本仓库已经在用、有据可查的东西，这套只是把"用ADX做状态开关"这个
经典思路第一次单独实现成一套完整战法。

规则：
  - 状态判断：ADX(14)
    - ADX ≥ trend_adx(默认25) → 判定"趋势市"，用趋势跟随信号
    - ADX ≤ range_adx(默认18) → 判定"震荡市"，用均值回归信号
    - 两者之间 → 状态不明确，不开新仓(已持仓的按当前最新状态继续管理)
  - 趋势市信号：EMA(10)上穿EMA(30) → 做多；下穿 → 做空(经典金叉死叉，
    比connors_rsi2/dual_momentum的"顺势"判断更直接，专门服务这套战法
    自己的趋势腿)
  - 震荡市信号：价格触及/跌破布林带(20,2)下轨 且 RSI(2)<10 → 做多
    (跌深了、情绪极端，赌均值回归)；触及/突破上轨且RSI(2)>90 → 做空
    (跟bollinger_rsi_contrarian同一个思路，但换成更短的RSI(2)——震荡
    市里等RSI(14)才降到30/70经常已经在反弹了，短周期RSI能更快抓住
    极端点，是Connors本人在RSI-2那篇公开研究里验证过的口径)
  - 离场：**每次都用离场那一刻的最新ADX重新判断该走哪条离场规则**，
    不是记住开仓时的状态——这是这套战法的核心自适应特征：开仓时是
    趋势市，持仓途中真的转成震荡市了，就该切换到均值回归离场逻辑
    (别死等一个已经不存在的趋势掉头信号)；反过来同理。状态不明确
    时按兵不动，不强行离场。
    - 当前ADX≥trend_adx：趋势离场——EMA金叉死叉反向出现就平仓
    - 当前ADX≤range_adx：震荡离场——价格回到中轨(SMA20)就平仓
    - 状态不明确：持有不动

跟本仓库其余战法的关键区别：唯一一套"同一个品种自己根据实时市场状态
换打法"的战法，其余8+1套都是从头到尾用同一套逻辑打到底。跟
turtle_breakout/time_series_momentum(纯趋势)、connors_rsi2/bollinger_
rsi_contrarian(纯均值回归)分别都有交集但不重合——那几套战法在"用错
状态"的市场里(比如纯趋势系统撞上真震荡市)只能硬扛，这套理论上应该
在两种市场状态切换频繁的品种上更有优势，用真实数据看这个假设站不站
得住。

数据要求：EMA30+ADX14+BB20+RSI2需要至少35根bars_by_tf["base"]历史。
用4h周期——比现有清一色1d/4h战法里更贴近"波段"节奏，且ADX/EMA在4h
上信号质量通常比1h噪声更小、比1d更及时，是这套"趋势/震荡切换"战法
比较经典的适用周期区间。
"""
from __future__ import annotations

from typing import Dict, List, Optional

from strategy_engine import indicators

DEFAULT_PARAMS = {
    "adx_len": 14,
    "trend_adx": 25.0,
    "range_adx": 18.0,
    "ema_fast_len": 10,
    "ema_slow_len": 30,
    "bb_len": 20,
    "bb_mult": 2.0,
    "rsi_len": 2,
    "rsi_entry_long": 10.0,
    "rsi_entry_short": 90.0,
    "atr_len": 14,
    "trend_atr_stop_mult": 2.0,
    "range_atr_stop_mult": 1.2,
}


def _ema_cross(ema_fast: List[float], ema_slow: List[float]) -> Optional[str]:
    """返回'up'(金叉)/'down'(死叉)/None(没有交叉)。"""
    if len(ema_fast) < 2 or len(ema_slow) < 2:
        return None
    f0, f1 = ema_fast[-2], ema_fast[-1]
    s0, s1 = ema_slow[-2], ema_slow[-1]
    if f0 <= s0 and f1 > s1:
        return "up"
    if f0 >= s0 and f1 < s1:
        return "down"
    return None


def generate_signal(bars_by_tf: Dict[str, List[dict]], params: Optional[dict] = None, position: Optional[dict] = None) -> Optional[dict]:
    bars = bars_by_tf.get("base") or []
    p = {**DEFAULT_PARAMS, **(params or {})}
    adx_len = int(p["adx_len"])
    ema_fast_len = int(p["ema_fast_len"])
    ema_slow_len = int(p["ema_slow_len"])
    bb_len = int(p["bb_len"])
    rsi_len = int(p["rsi_len"])
    atr_len = int(p["atr_len"])
    need = max(adx_len * 2 + 2, ema_slow_len + 2, bb_len, rsi_len + 1, atr_len) + 2
    if len(bars) < need:
        return None

    cs = indicators.closes(bars)
    adx_now = indicators.wilder_adx(bars, adx_len)
    ema_f = indicators.ema(cs, ema_fast_len)
    ema_s = indicators.ema(cs, ema_slow_len)
    mid = indicators.sma(cs, bb_len)
    sd = indicators.stdev(cs, bb_len)
    rsi_series = indicators.rsi(cs, rsi_len)
    if not ema_f or not ema_s or not mid or not sd or not rsi_series:
        return None

    last = bars[-1]
    price = float(last["c"])
    bar_time = int(last["t"])
    trend_adx = float(p["trend_adx"])
    range_adx = float(p["range_adx"])
    mid_now = mid[-1]
    upper_now = mid_now + float(p["bb_mult"]) * sd[-1]
    lower_now = mid_now - float(p["bb_mult"]) * sd[-1]
    rsi_now = rsi_series[-1]

    if position:
        side = str(position.get("side") or "").upper()
        if adx_now >= trend_adx:
            cross = _ema_cross(ema_f, ema_s)
            if side == "LONG" and cross == "down":
                return {
                    "action": "CLOSE_QUICK_EXIT", "price": round(price, 6),
                    "reason": f"当前状态=趋势市(ADX={adx_now:.1f})，EMA死叉离场",
                    "bar_time": bar_time,
                }
            if side == "SHORT" and cross == "up":
                return {
                    "action": "CLOSE_QUICK_EXIT", "price": round(price, 6),
                    "reason": f"当前状态=趋势市(ADX={adx_now:.1f})，EMA金叉离场",
                    "bar_time": bar_time,
                }
            return None
        if adx_now <= range_adx:
            if side == "LONG" and price >= mid_now:
                return {
                    "action": "CLOSE_QUICK_EXIT", "price": round(price, 6),
                    "reason": f"当前状态=震荡市(ADX={adx_now:.1f})，回到中轨离场",
                    "bar_time": bar_time,
                }
            if side == "SHORT" and price <= mid_now:
                return {
                    "action": "CLOSE_QUICK_EXIT", "price": round(price, 6),
                    "reason": f"当前状态=震荡市(ADX={adx_now:.1f})，回到中轨离场",
                    "bar_time": bar_time,
                }
            return None
        return None  # 状态不明确，持有不动

    atr = indicators.wilder_atr(bars, atr_len)
    if atr <= 0:
        return None

    if adx_now >= trend_adx:
        cross = _ema_cross(ema_f, ema_s)
        if cross == "up":
            action = "LONG"
        elif cross == "down":
            action = "SHORT"
        else:
            return None
        direction = 1 if action == "LONG" else -1
        stop_mult = float(p["trend_atr_stop_mult"])
        return {
            "action": action, "price": round(price, 6), "atr": round(atr, 6),
            "stop_loss": round(price - direction * atr * stop_mult, 6),
            "tp1": round(price + direction * atr * 1.5, 6),
            "tp2": round(price + direction * atr * 3.0, 6),
            "tp3": round(price + direction * atr * 5.0, 6),
            "tier": 1, "bar_time": bar_time,
            "reason": f"趋势市(ADX={adx_now:.1f}) EMA{ema_fast_len}/{ema_slow_len}{cross}叉",
        }

    if adx_now <= range_adx:
        if price <= lower_now and rsi_now < float(p["rsi_entry_long"]):
            action = "LONG"
        elif price >= upper_now and rsi_now > float(p["rsi_entry_short"]):
            action = "SHORT"
        else:
            return None
        direction = 1 if action == "LONG" else -1
        stop_mult = float(p["range_atr_stop_mult"])
        return {
            "action": action, "price": round(price, 6), "atr": round(atr, 6),
            "stop_loss": round(price - direction * atr * stop_mult, 6),
            "tp1": round(mid_now, 6), "tp2": round(mid_now, 6), "tp3": round(mid_now, 6),
            "tier": 1, "bar_time": bar_time,
            "reason": f"震荡市(ADX={adx_now:.1f}) 触及布林带极值+RSI{rsi_len}={rsi_now:.1f}",
        }

    return None  # 状态不明确，不开新仓
