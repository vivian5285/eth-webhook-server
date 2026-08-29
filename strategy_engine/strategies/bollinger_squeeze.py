#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Bollinger Band Squeeze突破——John Bollinger本人提出的"squeeze"概念(带宽
收缩到多周期低点，预示方向性突破临近)，加密货币/传统市场社区都广泛使用，
技术分析领域有几十年真实资历，不是网红臆造的指标。

规则：
  - 布林带：SMA(bb_len,默认20) ± bb_mult(默认2)×StDev(bb_len)
  - BandWidth = (上轨-下轨)/中轨
  - Squeeze：当前BandWidth是最近squeeze_lookback(默认120)根里的最小值
    (收缩到历史低位，说明波动率被压缩，方向性突破的概率上升)
  - 突破确认：squeeze状态下，收盘价突破上轨(做多)/跌破下轨(做空)，且
    量能配合(volume > SMA(volume, vol_len)，防止无量假突破)
  - 离场：价格回落穿越中轨(SMA(bb_len)) → 主动离场，止损/止盈本身的
    触碰由通用runner逻辑处理

品种覆盖：不挑品种类型，squeeze本身是纯波动率结构信号，跟资产类别无关，
但更适合有明显"平静期→剧烈期"交替节奏的品种，持续高波动的品种(比如
本身ATR常年很高的加密货币)squeeze可能很少出现。

数据要求：squeeze_lookback(120)+bb_len(20)需要至少140根bars_by_tf
["base"]历史，本模块设计给较慢的周期用(4h/1d)。
"""
from __future__ import annotations

from typing import Dict, List, Optional

from strategy_engine import indicators

DEFAULT_PARAMS = {
    "bb_len": 20,
    "bb_mult": 2.0,
    "squeeze_lookback": 120,
    "vol_len": 20,
    "atr_len": 14,
    "atr_stop_mult": 2.0,
}


def _f(x, default=0.0) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return float(default)


def generate_signal(bars_by_tf: Dict[str, List[dict]], params: Optional[dict] = None, position: Optional[dict] = None) -> Optional[dict]:
    bars = bars_by_tf.get("base") or []
    p = {**DEFAULT_PARAMS, **(params or {})}
    bb_len = int(p["bb_len"])
    bb_mult = float(p["bb_mult"])
    lookback = int(p["squeeze_lookback"])
    vol_len = int(p["vol_len"])
    atr_len = int(p["atr_len"])
    need = max(bb_len + lookback, vol_len, atr_len) + 2
    if len(bars) < need:
        return None

    cs = indicators.closes(bars)
    mid = indicators.sma(cs, bb_len)
    sd = indicators.stdev(cs, bb_len)
    if len(mid) < lookback + 1 or len(sd) < lookback + 1:
        return None

    upper = [m + bb_mult * s for m, s in zip(mid, sd)]
    lower = [m - bb_mult * s for m, s in zip(mid, sd)]
    bandwidth = [(u - l) / m if m > 0 else 0.0 for u, l, m in zip(upper, lower, mid)]

    last = bars[-1]
    price = float(last["c"])
    bar_time = int(last["t"])
    mid_now = mid[-1]

    if position:
        side = str(position.get("side") or "").upper()
        if side == "LONG" and price <= mid_now:
            return {
                "action": "CLOSE_QUICK_EXIT",
                "price": round(price, 6),
                "reason": f"回落穿越中轨SMA{bb_len}({mid_now:.6f})",
                "bar_time": bar_time,
            }
        if side == "SHORT" and price >= mid_now:
            return {
                "action": "CLOSE_QUICK_EXIT",
                "price": round(price, 6),
                "reason": f"反弹穿越中轨SMA{bb_len}({mid_now:.6f})",
                "bar_time": bar_time,
            }
        return None

    recent_bw = bandwidth[-lookback:]
    is_squeeze = bandwidth[-1] <= min(recent_bw)
    if not is_squeeze:
        return None

    vols = [_f(b.get("v")) for b in bars]
    vol_sma = indicators.sma(vols, vol_len)
    if not vol_sma:
        return None
    volume_ok = vols[-1] > vol_sma[-1]
    if not volume_ok:
        return None

    if price > upper[-1]:
        action = "LONG"
    elif price < lower[-1]:
        action = "SHORT"
    else:
        return None

    atr = indicators.wilder_atr(bars, atr_len)
    if atr <= 0:
        return None
    direction = 1 if action == "LONG" else -1

    return {
        "action": action,
        "price": round(price, 6),
        "atr": round(atr, 6),
        "stop_loss": round(price - direction * atr * float(p["atr_stop_mult"]), 6),
        "tp1": round(price + direction * atr * 1.0, 6),
        "tp2": round(price + direction * atr * 2.0, 6),
        "tp3": round(price + direction * atr * 3.5, 6),
        "tier": 1,
        "bar_time": bar_time,
        "reason": f"squeeze突破(bandwidth={bandwidth[-1]:.4f}近{lookback}根最低) + 量能确认",
    }
