#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
WaveTrend Oscillator（LazyBear 版）—— 2026-09-10 应宝贝"加著名指标"要求
新增。LazyBear 在 TradingView 上发布的 WaveTrend Oscillator，是加密圈
最流行的振荡器之一（Market Cipher 等付费脚本的核心也是它），规则公开。

构造（LazyBear 原版）：
  ap  = (h+l+c)/3
  esa = EMA(ap, n1=10)
  d   = EMA(|ap - esa|, n1)
  ci  = (ap - esa) / (0.015 * d)
  wt1 = EMA(ci, n2=21)
  wt2 = SMA(wt1, 4)

规则：
  · wt1 上穿 wt2 且 wt1 < os_level(-53) → 从超卖交叉，做多
  · wt1 下穿 wt2 且 wt1 > ob_level(+53) → 从超买交叉，做空
  · 离场：wt1 反向穿 wt2，或 wt1 穿越 0 轴反向，或 ATR 止损。不设固定止盈。

跟擂台已有振荡器的区别：connors_rsi2 是 RSI(2) 极值、adx_regime_switch
震荡腿是布林+RSI(2)、mtf_ema_macd_cci 用 CCI——WaveTrend 是"用通道指数
(类 CCI)构造再 EMA 平滑两层 + 信号线交叉"，转折信号比裸 CCI/RSI 更平滑，
超买超卖带(±53)也更宽。周期 4h。
"""
from __future__ import annotations

from typing import Dict, List, Optional

from strategy_engine import indicators

DEFAULT_PARAMS = {
    "n1": 10,
    "n2": 21,
    "sig_len": 4,
    "ob_level": 53.0,
    "os_level": -53.0,
    "atr_len": 14,
    "atr_stop_mult": 2.0,
}


def _wt(bars: List[dict], n1: int, n2: int, sig_len: int):
    ap = [(float(b["h"]) + float(b["l"]) + float(b["c"])) / 3.0 for b in bars]
    esa = indicators.ema(ap, n1)
    if not esa:
        return [], []
    m = len(esa)
    ap_tail = ap[-m:]
    absdev = [abs(ap_tail[i] - esa[i]) for i in range(m)]
    d = indicators.ema(absdev, n1)
    if not d:
        return [], []
    md = len(d)
    esa_t, ap_t = esa[-md:], ap_tail[-md:]
    ci = [(ap_t[i] - esa_t[i]) / (0.015 * d[i]) if d[i] > 0 else 0.0 for i in range(md)]
    wt1 = indicators.ema(ci, n2)
    if len(wt1) < 2:
        return [], []
    wt2 = indicators.sma(wt1, sig_len)
    return wt1, wt2


def generate_signal(bars_by_tf: Dict[str, List[dict]], params: Optional[dict] = None, position: Optional[dict] = None) -> Optional[dict]:
    p = {**DEFAULT_PARAMS, **(params or {})}
    bars = bars_by_tf.get("base") or []
    n1, n2, sig_len = int(p["n1"]), int(p["n2"]), int(p["sig_len"])
    atr_len = int(p["atr_len"])
    if len(bars) < n1 + n2 + sig_len + atr_len + 10:
        return None

    wt1, wt2 = _wt(bars, n1, n2, sig_len)
    if len(wt1) < 2 or len(wt2) < 2:
        return None
    m = min(len(wt1), len(wt2))
    a1, a2 = wt1[-m:], wt2[-m:]
    cross_up = a1[-2] <= a2[-2] and a1[-1] > a2[-1]
    cross_dn = a1[-2] >= a2[-2] and a1[-1] < a2[-1]

    last = bars[-1]
    price = float(last["c"])
    bar_time = int(last["t"])

    if position:
        side = str(position.get("side") or "").upper()
        if side == "LONG" and (cross_dn or (a1[-2] > 0 >= a1[-1])):
            return {"action": "CLOSE_QUICK_EXIT", "price": round(price, 6),
                    "reason": f"WT1 反向穿信号线/跌破0轴（{a1[-1]:.1f}）", "bar_time": bar_time}
        if side == "SHORT" and (cross_up or (a1[-2] < 0 <= a1[-1])):
            return {"action": "CLOSE_QUICK_EXIT", "price": round(price, 6),
                    "reason": f"WT1 反向穿信号线/升破0轴（{a1[-1]:.1f}）", "bar_time": bar_time}
        return None

    atr = indicators.wilder_atr(bars, atr_len)
    if atr <= 0:
        return None
    d = 0
    if cross_up and a1[-1] < float(p["os_level"]):
        d = 1
    elif cross_dn and a1[-1] > float(p["ob_level"]):
        d = -1
    if d == 0:
        return None
    return {
        "action": "LONG" if d == 1 else "SHORT",
        "price": round(price, 6), "atr": round(atr, 6),
        "stop_loss": round(price - d * atr * float(p["atr_stop_mult"]), 6),
        "tier": 1, "bar_time": bar_time,
        "reason": f"WT1({a1[-1]:.1f}){'上' if d == 1 else '下'}穿信号线，在{'超卖' if d == 1 else '超买'}带",
    }
