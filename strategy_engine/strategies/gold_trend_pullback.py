#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
黄金系列专属：趋势跟随 + 回踩快线进场 + 吊灯止损（无固定止盈）
——2026-09-10 应宝贝要求，为「币圈ETH / 黄金 / 美股」三个板块各做一套
板块特化战法之一。只挂 XAUUSDT / PAXGUSDT。

为什么黄金要单独一套、而且是"趋势+回踩"：
  三个板块里黄金的趋势是最干净的——波动率相对低、回撤浅而有序、很少
  出现加密那种插针式反转，价格长期尊重中长期均线。对这种资产：
    · 逆着趋势做均值回归 = 经典爆仓方式（趋势里越偏离越不回归）
    · 追突破 = 黄金突破后经常先回抽确认，追进去容易被最后一档洗掉
  所以选"顺日线大方向 + 等 4h 浅回调到快线企稳再进 + 用吊灯跟着大趋势
  跑"，是最贴黄金脾气的打法。

规则：
  · 日线方向：EMA50 vs EMA200。EMA50>EMA200 且收盘站上 EMA50 → 只做多；
    镜像 → 只做空；其余（纠缠/逆位）不开新仓。
  · 4h 自身趋势：EMA(fast=20) > EMA(slow=50) 才认为顺大方向的回调有效。
  · 回踩事件（做多）：最近 pullback_lookback(6) 根 4h 里，有某根的最低价
    曾贴近 EMA20（距离 ≤ near_atr(0.6)×ATR），且当前这根收盘重新站上
    EMA20、收盘 > 前一根收盘（企稳/反抽）。ADX(14) ≥ adx_min(18) 过滤死盘。
  · 不追：当前收盘离 EMA20 不能超过 entry_max_ext_atr(2.5)×ATR。
  · 止损：回踩段最低点 − 0.5×ATR（做多）。
  · 离场（无固定止盈，让它跟着黄金的大趋势跑）：
      - 吊灯：入场以来最高价 − chand_mult(3.0)×ATR，收盘跌破即平；
      - 或日线大方向翻转/转纠缠。
  · tier：4h ADX ≥ adx_strong(28) 记 2 档，否则 1 档。

接口同 strategies/__init__.py 约定。日线K线通过 roster 的 "mtf": ["1d"]
注入到 bars_by_tf["1d"]。
"""
from __future__ import annotations

from typing import Dict, List, Optional

from strategy_engine import indicators

DEFAULT_PARAMS = {
    "d_ema_fast": 50,
    "d_ema_slow": 200,
    "ema_fast": 20,
    "ema_slow": 50,
    "pullback_lookback": 8,
    "near_atr": 0.8,
    "entry_max_ext_atr": 2.5,
    "stop_pad_atr": 0.5,
    "chand_mult": 3.0,
    "adx_len": 14,
    "adx_min": 18.0,
    "adx_strong": 28.0,
}


def _daily_bias(dbars: List[dict], p: dict) -> int:
    cs = indicators.closes(dbars)
    ef = indicators.ema(cs, int(p["d_ema_fast"]))
    es = indicators.ema(cs, int(p["d_ema_slow"]))
    if not ef or not es:
        return 0
    price = cs[-1]
    if ef[-1] > es[-1] and price > ef[-1]:
        return 1
    if ef[-1] < es[-1] and price < ef[-1]:
        return -1
    return 0


def generate_signal(bars_by_tf: Dict[str, List[dict]], params: Optional[dict] = None, position: Optional[dict] = None) -> Optional[dict]:
    p = {**DEFAULT_PARAMS, **(params or {})}
    bars = bars_by_tf.get("base") or []
    dbars = bars_by_tf.get("1d") or []
    ef_len, es_len = int(p["ema_fast"]), int(p["ema_slow"])
    atr_len = int(p["adx_len"])
    lb = int(p["pullback_lookback"])
    need = es_len + atr_len + lb + 10
    if len(bars) < need or len(dbars) < int(p["d_ema_slow"]) + 2:
        return None

    cs = indicators.closes(bars)
    ema_f = indicators.ema(cs, ef_len)
    ema_s = indicators.ema(cs, es_len)
    if len(ema_f) < lb + 2 or len(ema_s) < 2:
        return None

    last = bars[-1]
    price = float(last["c"])
    bar_time = int(last["t"])
    atr = indicators.wilder_atr(bars, atr_len)
    if atr <= 0:
        return None
    ema20_now = ema_f[-1]
    bias = _daily_bias(dbars, p)

    # ── 持仓：吊灯止损 + 日线方向翻转 ──────────────────────────────────
    if position:
        side = str(position.get("side") or "").upper()
        entry_bt = int(position.get("entry_bar_time") or 0)
        seg = [b for b in bars if int(b["t"]) >= entry_bt] or [last]
        if side == "LONG":
            hh = max(float(b["h"]) for b in seg)
            if price < hh - float(p["chand_mult"]) * atr:
                return {"action": "CLOSE_QUICK_EXIT", "price": round(price, 6),
                        "reason": f"吊灯止损(最高{hh:.4f}-{p['chand_mult']}ATR)", "bar_time": bar_time}
            if bias <= 0:
                return {"action": "CLOSE_QUICK_EXIT", "price": round(price, 6),
                        "reason": "日线大方向转弱/翻转", "bar_time": bar_time}
        elif side == "SHORT":
            ll = min(float(b["l"]) for b in seg)
            if price > ll + float(p["chand_mult"]) * atr:
                return {"action": "CLOSE_QUICK_EXIT", "price": round(price, 6),
                        "reason": f"吊灯止损(最低{ll:.4f}+{p['chand_mult']}ATR)", "bar_time": bar_time}
            if bias >= 0:
                return {"action": "CLOSE_QUICK_EXIT", "price": round(price, 6),
                        "reason": "日线大方向转强/翻转", "bar_time": bar_time}
        return None

    # ── 空仓：找顺大方向的回踩进场 ────────────────────────────────────
    if bias == 0:
        return None
    adx = indicators.wilder_adx(bars, atr_len)
    if adx < float(p["adx_min"]):
        return None

    prev_c = float(bars[-2]["c"])
    win = bars[-lb:]
    fast_up = ema_f[-1] > ema_s[-1]
    fast_dn = ema_f[-1] < ema_s[-1]

    if bias == 1 and fast_up:
        touched = any(float(b["l"]) <= ema20_now + float(p["near_atr"]) * atr for b in win)
        stabilized = price > ema20_now and price > prev_c
        not_extended = price <= ema20_now + float(p["entry_max_ext_atr"]) * atr
        if touched and stabilized and not_extended:
            swing_low = min(float(b["l"]) for b in win)
            stop = min(swing_low - float(p["stop_pad_atr"]) * atr, price - atr)
            tier = 2 if adx >= float(p["adx_strong"]) else 1
            return {"action": "LONG", "price": round(price, 6), "atr": round(atr, 6),
                    "stop_loss": round(stop, 6), "tier": tier, "bar_time": bar_time,
                    "reason": f"日线多头+4h回踩EMA20企稳(ADX={adx:.1f})"}

    if bias == -1 and fast_dn:
        touched = any(float(b["h"]) >= ema20_now - float(p["near_atr"]) * atr for b in win)
        stabilized = price < ema20_now and price < prev_c
        not_extended = price >= ema20_now - float(p["entry_max_ext_atr"]) * atr
        if touched and stabilized and not_extended:
            swing_high = max(float(b["h"]) for b in win)
            stop = max(swing_high + float(p["stop_pad_atr"]) * atr, price + atr)
            tier = 2 if adx >= float(p["adx_strong"]) else 1
            return {"action": "SHORT", "price": round(price, 6), "atr": round(atr, 6),
                    "stop_loss": round(stop, 6), "tier": tier, "bar_time": bar_time,
                    "reason": f"日线空头+4h回抽EMA20走弱(ADX={adx:.1f})"}

    return None
