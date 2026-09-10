#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Schaff Trend Cycle (STC) —— 2026-09-10 应宝贝"MACD 组合改良版"要求新增。
Doug Schaff 1999 提出，把 MACD 线再做两遍随机指标(stochastic)平滑，得到
一个 0–100 的循环振荡器，号称"更快、假信号更少的 MACD"，TradingView /
各大平台都内置，规则完全公开。

构造：
  1. MACD 线 = EMA(close, fast=23) − EMA(close, slow=50)
  2. 第一遍随机化：%K1 = (MACD − LL(MACD, cycle)) / (HH−LL) × 100，
     再 0.5 指数平滑成 %D1
  3. 第二遍随机化：对 %D1 再做一次同样的 stochastic + 0.5 平滑 = STC
  （cycle 默认 10）

规则：
  · STC 上穿 low_th(25) → 从超卖回升，做多
  · STC 下穿 high_th(75) → 从超买回落，做空
  · 离场：STC 反向穿越另一侧阈值(多头 STC 下穿 75 / 空头上穿 25)，或
    ATR 止损。不设固定止盈。

跟 macd_histogram 的区别：那套是 MACD(12,26,9) 柱状图零轴穿越，滞后明显、
在震荡里反复抽；STC 把 MACD 再随机化两遍 + 循环化，转折更利落、
阈值(25/75)天然带"只在偏离到一定程度才动"的过滤。两套并排能看出
"MACD 再加两层随机平滑"到底值不值。周期 4h（跟 macd_histogram 同）。
"""
from __future__ import annotations

from typing import Dict, List, Optional

from strategy_engine import indicators


DEFAULT_PARAMS = {
    "fast": 23,
    "slow": 50,
    "cycle": 10,
    "smooth": 0.5,
    "low_th": 25.0,
    "high_th": 75.0,
    "atr_len": 14,
    "atr_stop_mult": 2.0,
}


def _stoch_smooth(series: List[float], cycle: int, alpha: float) -> List[float]:
    """对 series 做一次 stochastic(cycle) 再 alpha 指数平滑，返回等长(前
    cycle-1 个点用第一个可算值填充)的平滑序列。"""
    n = len(series)
    if n < cycle:
        return []
    out = []
    prev = None
    for i in range(cycle - 1, n):
        w = series[i - cycle + 1:i + 1]
        lo, hi = min(w), max(w)
        st = 0.0 if hi - lo <= 0 else (series[i] - lo) / (hi - lo) * 100.0
        prev = st if prev is None else prev + alpha * (st - prev)
        out.append(prev)
    return out


def _stc_series(closes: List[float], p: dict) -> List[float]:
    ef = indicators.ema(closes, int(p["fast"]))
    es = indicators.ema(closes, int(p["slow"]))
    if not ef or not es:
        return []
    m = min(len(ef), len(es))
    macd = [ef[len(ef) - m + i] - es[len(es) - m + i] for i in range(m)]
    cyc = int(p["cycle"])
    a = float(p["smooth"])
    d1 = _stoch_smooth(macd, cyc, a)
    if not d1:
        return []
    return _stoch_smooth(d1, cyc, a)


def generate_signal(bars_by_tf: Dict[str, List[dict]], params: Optional[dict] = None, position: Optional[dict] = None) -> Optional[dict]:
    p = {**DEFAULT_PARAMS, **(params or {})}
    bars = bars_by_tf.get("base") or []
    atr_len = int(p["atr_len"])
    need = int(p["slow"]) + int(p["cycle"]) * 2 + atr_len + 10
    if len(bars) < need:
        return None

    cs = indicators.closes(bars)
    stc = _stc_series(cs, p)
    if len(stc) < 2:
        return None
    prev, now = stc[-2], stc[-1]
    last = bars[-1]
    price = float(last["c"])
    bar_time = int(last["t"])
    lo_th, hi_th = float(p["low_th"]), float(p["high_th"])

    if position:
        side = str(position.get("side") or "").upper()
        if side == "LONG" and prev >= hi_th > now:
            return {"action": "CLOSE_QUICK_EXIT", "price": round(price, 6),
                    "reason": f"STC 下穿 {hi_th:.0f}（{prev:.1f}->{now:.1f}）", "bar_time": bar_time}
        if side == "SHORT" and prev <= lo_th < now:
            return {"action": "CLOSE_QUICK_EXIT", "price": round(price, 6),
                    "reason": f"STC 上穿 {lo_th:.0f}（{prev:.1f}->{now:.1f}）", "bar_time": bar_time}
        return None

    atr = indicators.wilder_atr(bars, atr_len)
    if atr <= 0:
        return None
    d = 0
    if prev <= lo_th < now:
        d = 1
    elif prev >= hi_th > now:
        d = -1
    if d == 0:
        return None
    return {
        "action": "LONG" if d == 1 else "SHORT",
        "price": round(price, 6), "atr": round(atr, 6),
        "stop_loss": round(price - d * atr * float(p["atr_stop_mult"]), 6),
        "tier": 1, "bar_time": bar_time,
        "reason": f"STC {'上穿' if d == 1 else '下穿'} {(lo_th if d == 1 else hi_th):.0f}（{prev:.1f}->{now:.1f}）",
    }
