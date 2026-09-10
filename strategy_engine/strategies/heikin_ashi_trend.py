#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Heikin Ashi 趋势 —— 2026-09-10 应宝贝"加著名指标"要求新增。平均K线
(Heikin-Ashi) 是把 OHLC 做递归平滑的经典 K 线变换，"骑趋势"最常用的
可视化方法之一，规则完全确定。

HA 计算：
  HA_close = (o + h + l + c) / 4
  HA_open  = (前一根 HA_open + 前一根 HA_close) / 2   （首根用 (o+c)/2）
  HA_high  = max(h, HA_open, HA_close)
  HA_low   = min(l, HA_open, HA_close)

规则：
  · 连续 streak_len(3) 根同色 HA K 线（阳：HA_close>HA_open）→ 第 streak_len
    根顺势进场。可选加强：要求这几根 HA 实体在放大（趋势在加速）。
  · 止损：ATR × atr_stop_mult。
  · 离场：出现第一根反色 HA K 线（趋势转弱信号），或出现明显反向影线
    （阳线里 HA_open−HA_low > 实体的 wick_frac 倍 = 下方承压），或 ATR 止损。
    不设固定止盈——HA 的用途就是"一直待在趋势里直到颜色变"。

跟擂台已有趋势跟随的区别：turtle/ema_cross/supertrend/hma 都是在**原始
价格**上算指标；HA 是先把 K 线本身平滑掉再看颜色，对单根插针不敏感，
连续同色 = 趋势的"视觉确认"。是这一簇里唯一"改造 K 线本身"的做法。
周期 4h。
"""
from __future__ import annotations

from typing import Dict, List, Optional

from strategy_engine import indicators

DEFAULT_PARAMS = {
    "streak_len": 3,
    "require_growing_body": True,
    "wick_frac": 1.0,
    "atr_len": 14,
    "atr_stop_mult": 2.0,
}


def _ha(bars: List[dict]) -> List[dict]:
    out = []
    prev_o = prev_c = None
    for b in bars:
        o, h, l, c = float(b["o"]), float(b["h"]), float(b["l"]), float(b["c"])
        hc = (o + h + l + c) / 4.0
        ho = (o + c) / 2.0 if prev_o is None else (prev_o + prev_c) / 2.0
        hh = max(h, ho, hc)
        hl = min(l, ho, hc)
        out.append({"t": int(b["t"]), "o": ho, "h": hh, "l": hl, "c": hc})
        prev_o, prev_c = ho, hc
    return out


def generate_signal(bars_by_tf: Dict[str, List[dict]], params: Optional[dict] = None, position: Optional[dict] = None) -> Optional[dict]:
    p = {**DEFAULT_PARAMS, **(params or {})}
    bars = bars_by_tf.get("base") or []
    sk = int(p["streak_len"])
    atr_len = int(p["atr_len"])
    if len(bars) < sk + atr_len + 10:
        return None

    ha = _ha(bars)
    last = bars[-1]
    price = float(last["c"])
    bar_time = int(last["t"])

    def _green(x):
        return x["c"] > x["o"]

    def _body(x):
        return abs(x["c"] - x["o"])

    cur = ha[-1]

    if position:
        side = str(position.get("side") or "").upper()
        if side == "LONG":
            if not _green(cur):
                return {"action": "CLOSE_QUICK_EXIT", "price": round(price, 6),
                        "reason": "HA 转阴，趋势转弱", "bar_time": bar_time}
            if (cur["o"] - cur["l"]) > float(p["wick_frac"]) * max(_body(cur), 1e-9):
                return {"action": "CLOSE_QUICK_EXIT", "price": round(price, 6),
                        "reason": "HA 阳线现明显下影，上涨承压", "bar_time": bar_time}
        elif side == "SHORT":
            if _green(cur):
                return {"action": "CLOSE_QUICK_EXIT", "price": round(price, 6),
                        "reason": "HA 转阳，趋势转弱", "bar_time": bar_time}
            if (cur["h"] - cur["o"]) > float(p["wick_frac"]) * max(_body(cur), 1e-9):
                return {"action": "CLOSE_QUICK_EXIT", "price": round(price, 6),
                        "reason": "HA 阴线现明显上影，下跌承压", "bar_time": bar_time}
        return None

    seg = ha[-sk:]
    all_green = all(_green(x) for x in seg)
    all_red = all(not _green(x) for x in seg)
    growing = True
    if bool(p["require_growing_body"]):
        bodies = [_body(x) for x in seg]
        growing = all(bodies[i] >= bodies[i - 1] for i in range(1, len(bodies)))
    if not ((all_green or all_red) and growing):
        return None

    atr = indicators.wilder_atr(bars, atr_len)
    if atr <= 0:
        return None
    d = 1 if all_green else -1
    return {
        "action": "LONG" if d == 1 else "SHORT",
        "price": round(price, 6), "atr": round(atr, 6),
        "stop_loss": round(price - d * atr * float(p["atr_stop_mult"]), 6),
        "tier": 1, "bar_time": bar_time,
        "reason": f"连续 {sk} 根 HA {'阳' if d == 1 else '阴'}线{'(实体放大)' if p['require_growing_body'] else ''}",
    }
