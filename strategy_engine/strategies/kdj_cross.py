#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
KDJ 金叉/死叉 —— 2026-09-10 应宝贝点名要求新增。KDJ 是随机指标(Stochastic)
在中文交易圈的通行变体：在 K、D 之外多一条 J = 3K − 2D 放大灵敏度。规则
完全确定、人人会手算。

计算（经典 9,3,3）：
  RSV = (close − LL(low, n=9)) / (HH(high, n) − LL(low, n)) × 100
  K   = 2/3 · 前K + 1/3 · RSV      （首值用 50）
  D   = 2/3 · 前D + 1/3 · K
  J   = 3K − 2D

规则（只在超买超卖区做，比裸交叉挑剔）：
  · K 上穿 D 且 K < os(20) → 低位金叉，做多；J < 0 记 tier2（极端超卖）
  · K 下穿 D 且 K > ob(80) → 高位死叉，做空；J > 100 记 tier2
  · 离场：K/D 反向交叉，或 K 回到中位(50)，或 ATR 止损。不设固定止盈。

⚠️ 诚实说明：KDJ 金叉死叉是"指标翻转型"信号，跟擂台里 ema_cross_7_30 /
macd_histogram / parabolic_sar_flip 一样，在震荡市里天然容易被反复抽。
这里加了"只在 20/80 极值区才动"的过滤想缓解这点，但它到底比裸交叉好
多少，用真实数据说话。周期 4h。
"""
from __future__ import annotations

from typing import Dict, List, Optional

from strategy_engine import indicators

DEFAULT_PARAMS = {
    "n": 9,
    "os": 20.0,
    "ob": 80.0,
    "mid": 50.0,
    "atr_len": 14,
    "atr_stop_mult": 2.0,
}


def _kdj(bars: List[dict], n: int):
    if len(bars) < n + 2:
        return [], [], []
    k = d = 50.0
    ks, ds, js = [], [], []
    for i in range(n - 1, len(bars)):
        w = bars[i - n + 1:i + 1]
        hh = max(float(b["h"]) for b in w)
        ll = min(float(b["l"]) for b in w)
        c = float(bars[i]["c"])
        rsv = 50.0 if hh - ll <= 0 else (c - ll) / (hh - ll) * 100.0
        k = 2.0 / 3.0 * k + 1.0 / 3.0 * rsv
        d = 2.0 / 3.0 * d + 1.0 / 3.0 * k
        ks.append(k); ds.append(d); js.append(3 * k - 2 * d)
    return ks, ds, js


def generate_signal(bars_by_tf: Dict[str, List[dict]], params: Optional[dict] = None, position: Optional[dict] = None) -> Optional[dict]:
    p = {**DEFAULT_PARAMS, **(params or {})}
    bars = bars_by_tf.get("base") or []
    n = int(p["n"])
    atr_len = int(p["atr_len"])
    if len(bars) < n + atr_len + 10:
        return None

    ks, ds, js = _kdj(bars, n)
    if len(ks) < 2:
        return None
    k0, k1 = ks[-2], ks[-1]
    d0, d1 = ds[-2], ds[-1]
    j1 = js[-1]
    cross_up = k0 <= d0 and k1 > d1
    cross_dn = k0 >= d0 and k1 < d1

    last = bars[-1]
    price = float(last["c"])
    bar_time = int(last["t"])
    mid = float(p["mid"])

    if position:
        side = str(position.get("side") or "").upper()
        if side == "LONG" and (cross_dn or k1 >= mid):
            return {"action": "CLOSE_QUICK_EXIT", "price": round(price, 6),
                    "reason": f"KDJ 死叉/K 回到中位（K={k1:.1f}）", "bar_time": bar_time}
        if side == "SHORT" and (cross_up or k1 <= mid):
            return {"action": "CLOSE_QUICK_EXIT", "price": round(price, 6),
                    "reason": f"KDJ 金叉/K 回到中位（K={k1:.1f}）", "bar_time": bar_time}
        return None

    atr = indicators.wilder_atr(bars, atr_len)
    if atr <= 0:
        return None
    d = 0
    if cross_up and k1 < float(p["os"]):
        d = 1
    elif cross_dn and k1 > float(p["ob"]):
        d = -1
    if d == 0:
        return None
    extreme = (j1 < 0) if d == 1 else (j1 > 100)
    return {
        "action": "LONG" if d == 1 else "SHORT",
        "price": round(price, 6), "atr": round(atr, 6),
        "stop_loss": round(price - d * atr * float(p["atr_stop_mult"]), 6),
        "tier": 2 if extreme else 1, "bar_time": bar_time,
        "reason": f"KDJ {'低位金叉' if d == 1 else '高位死叉'}（K={k1:.1f} D={d1:.1f} J={j1:.1f}）",
    }
