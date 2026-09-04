#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
凯特纳通道突破(Keltner Channel Breakout)——Chester W. Keltner在《How to
Make Money in Commodities》(1960)最早提出通道概念；本模块用的是后来被
Linda Raschke在公开教材/讲座里推广、现在各大图表软件默认实现的"现代版"
Keltner Channel(中线用EMA、通道宽度用ATR倍数)，而不是Keltner本人1960年
那版用简单移动平均+当日振幅均值的原始写法——这个"现代版"本身也已经是
几十年公开使用的标准配置，不是网红自创。

规则：
  - 中线 = EMA(ema_len，默认20)
  - 通道宽度 = atr_mult(默认2) × ATR(atr_len，默认10)
  - 上轨 = 中线 + 通道宽度，下轨 = 中线 - 通道宽度
  - 收盘价突破上轨 → 做多；跌破下轨 → 做空
  - 离场：价格收盘穿越回中线(EMA)

跟本仓库其余通道类战法的关键区别：bollinger_squeeze的通道宽度用**标准
差**(统计意义上的价格离散度)，且要求"先经历带宽收缩到近期最低(挤压)
才交易突破"；这套通道宽度直接用**ATR**(波动率本身的直接度量，不是
统计离散度)，且**不要求先挤压**——只要收盘价突破通道就交易，随时可能
触发，不用等"低波动率→高波动率"这个转换过程。跟turtle_breakout
(Donchian通道，纯价格结构、不管波动率大小)也不同：这套通道宽度会随
ATR实时热胀冷缩，波动率变大时通道自动变宽、需要更大的突破才触发，是
波动率自适应的通道，Donchian通道则是固定看N根K线的最高最低点、不管
波动率。

周期选择理由：4H，跟turtle_breakout/bollinger_squeeze同一批"4H日线
合理代理"选择，方便三种不同构造方式的通道突破战法(Donchian结构通道/
布林带统计通道+挤压/Keltner的ATR波动率通道)在同一周期上直接对照。

数据要求：EMA(ema_len)+ATR(atr_len)，至少max(ema_len,atr_len)+4根
bars_by_tf["base"]。
"""
from __future__ import annotations

from typing import Dict, List, Optional

from strategy_engine import indicators

DEFAULT_PARAMS = {
    "ema_len": 20,
    "atr_len": 10,
    "atr_mult": 2.0,
    "atr_stop_mult": 2.5,
}


def generate_signal(bars_by_tf: Dict[str, List[dict]], params: Optional[dict] = None, position: Optional[dict] = None) -> Optional[dict]:
    bars = bars_by_tf.get("base") or []
    p = {**DEFAULT_PARAMS, **(params or {})}
    ema_len = int(p["ema_len"])
    atr_len = int(p["atr_len"])
    need = max(ema_len, atr_len) + 4
    if len(bars) < need:
        return None

    cs = indicators.closes(bars)
    ema_mid = indicators.ema(cs, ema_len)
    atr_series = indicators.atr_series(bars, atr_len)
    if not ema_mid or not atr_series:
        return None

    last = bars[-1]
    price = float(last["c"])
    bar_time = int(last["t"])
    mid_now = ema_mid[-1]
    atr_now = atr_series[-1]
    mult = float(p["atr_mult"])
    upper = mid_now + mult * atr_now
    lower = mid_now - mult * atr_now

    if position:
        side = str(position.get("side") or "").upper()
        if side == "LONG" and price <= mid_now:
            return {
                "action": "CLOSE_QUICK_EXIT", "price": round(price, 6),
                "reason": f"回落穿越中线EMA{ema_len}({mid_now:.6f})", "bar_time": bar_time,
            }
        if side == "SHORT" and price >= mid_now:
            return {
                "action": "CLOSE_QUICK_EXIT", "price": round(price, 6),
                "reason": f"反弹穿越中线EMA{ema_len}({mid_now:.6f})", "bar_time": bar_time,
            }
        return None

    if price > upper:
        action, d = "LONG", 1
    elif price < lower:
        action, d = "SHORT", -1
    else:
        return None

    atr = indicators.wilder_atr(bars, atr_len)  # 复用同一个ATR长度算atr0
    if atr <= 0:
        return None

    return {
        "action": action,
        "price": round(price, 6),
        "atr": round(atr, 6),
        "stop_loss": round(price - d * atr * float(p["atr_stop_mult"]), 6),
        "tier": 1,
        "bar_time": bar_time,
        "reason": f"突破Keltner通道[{lower:.6f},{upper:.6f}]({mult}×ATR{atr_len})",
    }
