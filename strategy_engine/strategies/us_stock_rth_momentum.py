#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
美股系列专属：美股现货时段(RTH)动量突破，收盘平仓、绝不留夜/过周末
——2026-09-10 应宝贝要求的三板块特化战法之一。只挂 10 个代币化美股
(SNDK/OPENAI/ANTHROPIC/GS/MU/LITE/TSLA/META/SKHYNIX/ASML)。

为什么美股要单独一套、而且严格锁在时段内：
  代币化美股 24/7 都在币安永续上挂，但真实的价格发现只发生在美股现货
  时段 9:30–16:00 ET。盘后 + 周末，代币价格是在很薄的流动性里漂移、
  经常跳空。所以这套：
    · 只在开盘后头 2.5 小时开仓（真实资金进场、方向最清晰的窗口）
    · 方向 = 收盘突破**昨日 RTH 区间**高/低点，且站在**当日 session
      VWAP** 正确一侧（机构成本线确认，不是假突破）
    · 收盘前强制平仓；周末（美股休市）不开新仓、已有仓位清掉
  ——赌"美股在自己的真实交易时段内会延续日内动量，代币化外壳在盘外
  是没法交易的垃圾时段"。

跟擂台已有的 opening_range_breakout(us_equity 锚)区别：
  · ORB 用的是**当日**开盘前 30 分钟自己的高低点作区间；
  · 这套用的是**昨日整个 RTH** 的高低点作区间 + 当日 VWAP 过滤 +
    跌破 VWAP 立即认输离场（ORB 没有 VWAP 这层）。
  两套都锚 9:30 ET、都日内平仓，但突破区间的定义和失败离场机制不同，
  是"区间取昨天 vs 取今早头半小时"这一个变量的对照。

复用 opening_range_breakout._session_anchor_ms 做 9:30 ET 锚点（含夏令时
自动切换），不重写一份。

周期 15m。数据要求：至少覆盖 2 个 RTH session + 缓冲，BARS_LIMIT(550)×15m
≈ 5.7 天足够。
"""
from __future__ import annotations

from typing import Dict, List, Optional

from strategy_engine import indicators
from strategy_engine.strategies import opening_range_breakout as _orb

_DAY_MS = 24 * 60 * 60 * 1000
_MIN_MS = 60 * 1000

DEFAULT_PARAMS = {
    "rth_minutes": 390,          # 一个美股交易日 6.5h
    "entry_start_min": 15,       # 开盘后 15 分钟才开始找突破（让首根 K 线走完）
    "entry_end_min": 150,        # 只在开盘后头 2.5h 开仓
    "session_end_min": 385,      # 收盘前 5 分钟强制平仓
    "atr_len": 14,
    "stop_atr_mult": 1.5,
    "vwap_fail_exit": True,
}


def _prev_session_anchor(today_anchor_ms: int) -> Optional[int]:
    """往回找最近一个有美股 session 的交易日的 9:30 ET 锚点（跳过周末）。"""
    day_start = (today_anchor_ms // _DAY_MS) * _DAY_MS
    for back in range(1, 6):
        cand = _orb._session_anchor_ms(day_start - back * _DAY_MS, "us_equity")
        if cand is not None:
            return cand
    return None


def _vwap(session_bars: List[dict]) -> Optional[float]:
    num = den = 0.0
    for b in session_bars:
        tp = (float(b["h"]) + float(b["l"]) + float(b["c"])) / 3.0
        v = float(b.get("v") or 0.0)
        num += tp * v
        den += v
    return (num / den) if den > 0 else None


def generate_signal(bars_by_tf: Dict[str, List[dict]], params: Optional[dict] = None, position: Optional[dict] = None) -> Optional[dict]:
    p = {**DEFAULT_PARAMS, **(params or {})}
    bars = bars_by_tf.get("base") or []
    atr_len = int(p["atr_len"])
    if len(bars) < atr_len + 40:
        return None

    last = bars[-1]
    price = float(last["c"])
    bar_time = int(last["t"])
    today_anchor = _orb._session_anchor_ms(bar_time, "us_equity")

    # ── 周末/休市：不开新仓，已有仓位清掉 ────────────────────────────
    if today_anchor is None:
        if position:
            return {"action": "CLOSE_QUICK_EXIT", "price": round(price, 6),
                    "reason": "美股休市(周末)，代币化外壳无真实定价，清仓", "bar_time": bar_time}
        return None

    mins_from_open = (bar_time - today_anchor) / _MIN_MS
    if mins_from_open < 0:
        # 收在当日 9:30 之前（盘前时段）——已有仓位应已在昨日收盘平掉；
        # 兜底再平一次，不开新仓。
        if position:
            return {"action": "CLOSE_QUICK_EXIT", "price": round(price, 6),
                    "reason": "已进入盘前时段，不留夜", "bar_time": bar_time}
        return None

    session_bars = [b for b in bars if int(b["t"]) >= today_anchor]
    vwap = _vwap(session_bars)

    # ── 持仓：收盘平仓 / 跌破 VWAP 认输 ──────────────────────────────
    if position:
        side = str(position.get("side") or "").upper()
        if mins_from_open >= float(p["session_end_min"]):
            return {"action": "CLOSE_QUICK_EXIT", "price": round(price, 6),
                    "reason": "RTH 收盘前强制平仓(日内策略不留夜)", "bar_time": bar_time}
        if bool(p["vwap_fail_exit"]) and vwap is not None:
            if side == "LONG" and price < vwap:
                return {"action": "CLOSE_QUICK_EXIT", "price": round(price, 6),
                        "reason": f"收盘跌回当日VWAP({vwap:.4f})下方，突破失败", "bar_time": bar_time}
            if side == "SHORT" and price > vwap:
                return {"action": "CLOSE_QUICK_EXIT", "price": round(price, 6),
                        "reason": f"收盘拉回当日VWAP({vwap:.4f})上方，突破失败", "bar_time": bar_time}
        return None

    # ── 空仓：昨日 RTH 区间突破 + VWAP 同向 + 当日首次 ───────────────
    if not (float(p["entry_start_min"]) <= mins_from_open <= float(p["entry_end_min"])):
        return None
    if vwap is None or len(session_bars) < 2:
        return None

    prev_anchor = _prev_session_anchor(today_anchor)
    if prev_anchor is None:
        return None
    prev_win = [b for b in bars if prev_anchor <= int(b["t"]) < prev_anchor + int(p["rth_minutes"]) * _MIN_MS]
    if len(prev_win) < 4:
        return None
    prior_high = max(float(b["h"]) for b in prev_win)
    prior_low = min(float(b["l"]) for b in prev_win)

    earlier = session_bars[:-1]
    atr = indicators.wilder_atr(bars, atr_len)
    if atr <= 0:
        return None
    sess_high = max(float(b["h"]) for b in session_bars)
    sess_low = min(float(b["l"]) for b in session_bars)

    long_ok = (price > prior_high and price > vwap
               and not any(float(b["c"]) > prior_high for b in earlier))
    short_ok = (price < prior_low and price < vwap
                and not any(float(b["c"]) < prior_low for b in earlier))

    if long_ok:
        stop = min(sess_low, price - float(p["stop_atr_mult"]) * atr)
        if stop >= price:
            return None
        return {"action": "LONG", "price": round(price, 6), "atr": round(atr, 6),
                "stop_loss": round(stop, 6), "tier": 1, "bar_time": bar_time,
                "reason": f"突破昨日RTH高{prior_high:.4f}+站上当日VWAP{vwap:.4f}"}
    if short_ok:
        stop = max(sess_high, price + float(p["stop_atr_mult"]) * atr)
        if stop <= price:
            return None
        return {"action": "SHORT", "price": round(price, 6), "atr": round(atr, 6),
                "stop_loss": round(stop, 6), "tier": 1, "bar_time": bar_time,
                "reason": f"跌破昨日RTH低{prior_low:.4f}+跌破当日VWAP{vwap:.4f}"}
    return None
