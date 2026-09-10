#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
美股系列专属：开盘跳空策略（Gap & Go / Gap Fill）—— 2026-09-10 应宝贝
转发的「跨资产量化战法大全」新增。只挂 10 个代币化美股。

为什么美股要单独做跳空：
  美股个股在 9:30 ET 开盘经常相对昨日 RTH 收盘跳空（财报/新闻/隔夜情绪），
  代币化外壳在币安永续上把这个跳空如实映射出来。跳空之后两种典型走法：
    · 大跳空 + 首段顺跳空方向延续  →  Gap & Go（追跳空）
    · 中等跳空 + 开盘后开始往昨收方向回补  →  Gap Fill（赌回补，目标昨收）
  币圈没有"开盘"、黄金也没有个股式跳空，所以是美股专属。

复用 opening_range_breakout._session_anchor_ms 做 9:30 ET 锚点（含夏令时）。
跟 us_stock_rth_momentum（突破昨日 RTH 区间 + VWAP）互补：这套专门吃
"开盘那一下的价格断层"，一个看区间突破、一个看跳空。

规则：
  · prior_close = 昨日 RTH session 最后一根 15m K线收盘。
  · today_open = 今日 9:30 ET 锚点后第一根 15m K线开盘。
  · gap = today_open / prior_close - 1。|gap| < min_gap_pct(1.5%) 不做。
  · Gap & Go：|gap| ≥ go_gap_pct(4%)，且进场时价格仍在 today_open 的
    跳空方向那侧、且站在当日 session VWAP 正确一侧 → 顺跳空方向进。
    进场窗口 开盘后 15–90 分钟。止损 = 当日 session 反向极值 或 入场
    ∓ stop_atr×ATR 取更近。无固定止盈（吊灯/VWAP 失守/收盘平）。
  · Gap Fill：min_gap_pct ≤ |gap| < go_gap_pct，且价格已从 today_open
    往 prior_close 方向回补了至少 fill_start_frac(0.25) 的跳空幅度 →
    反跳空方向进，tp1 = prior_close（回补目标）。进场窗口 15–120 分钟。
    止损 = 跳空方向的当日 session 极值 ∓ 小缓冲。
  · 每个 session 每方向只进一次。离场：session 收盘前(session_end_min)
    强平；周末不开新仓、清仓。

周期 15m，bars_limit 960（同 us_stock_rth_momentum，稳覆盖前一交易日 session）。
"""
from __future__ import annotations

from typing import Dict, List, Optional

from strategy_engine import indicators
from strategy_engine.strategies import opening_range_breakout as _orb
from strategy_engine.strategies.us_stock_rth_momentum import _prev_session_anchor, _vwap

_DAY_MS = 24 * 60 * 60 * 1000
_MIN_MS = 60 * 1000

DEFAULT_PARAMS = {
    "rth_minutes": 390,
    "min_gap_pct": 0.015,
    "go_gap_pct": 0.04,
    "fill_start_frac": 0.25,
    "go_entry_start_min": 15,
    "go_entry_end_min": 90,
    "fill_entry_start_min": 15,
    "fill_entry_end_min": 120,
    "session_end_min": 385,
    "atr_len": 14,
    "stop_atr_mult": 1.5,
    "stop_pad_atr": 0.3,
}


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

    if today_anchor is None:  # 周末
        if position:
            return {"action": "CLOSE_QUICK_EXIT", "price": round(price, 6),
                    "reason": "美股休市(周末)，清仓", "bar_time": bar_time}
        return None

    mins_from_open = (bar_time - today_anchor) / _MIN_MS
    if mins_from_open < 0:
        if position:
            return {"action": "CLOSE_QUICK_EXIT", "price": round(price, 6),
                    "reason": "已进入盘前时段，不留夜", "bar_time": bar_time}
        return None

    session_bars = [b for b in bars if int(b["t"]) >= today_anchor]
    if not session_bars:
        return None
    today_open = float(session_bars[0]["o"])
    vwap = _vwap(session_bars)

    # 跳空幅度（开平仓两条路径都要用，从 K 线现算，可重放）
    prev_anchor = _prev_session_anchor(today_anchor)
    prev_win = ([b for b in bars if prev_anchor <= int(b["t"]) < prev_anchor + int(p["rth_minutes"]) * _MIN_MS]
                if prev_anchor is not None else [])
    prior_close = float(prev_win[-1]["c"]) if len(prev_win) >= 4 else 0.0
    gap = (today_open / prior_close - 1.0) if prior_close > 0 else 0.0
    ag = abs(gap)
    up_gap = gap > 0
    is_go = ag >= float(p["go_gap_pct"])

    # ── 持仓：收盘平仓 / (仅 Gap&Go) VWAP 失守 ─────────────────────
    if position:
        side = str(position.get("side") or "").upper()
        if mins_from_open >= float(p["session_end_min"]):
            return {"action": "CLOSE_QUICK_EXIT", "price": round(price, 6),
                    "reason": "RTH 收盘前强制平仓(日内不留夜)", "bar_time": bar_time}
        # Gap&Go（大跳空追单）跌破/升破 VWAP 认输；Gap Fill（逆跳空）不看 VWAP
        if is_go and vwap is not None:
            if side == "LONG" and price < vwap:
                return {"action": "CLOSE_QUICK_EXIT", "price": round(price, 6),
                        "reason": f"追跳空失败，跌破当日VWAP({vwap:.4f})", "bar_time": bar_time}
            if side == "SHORT" and price > vwap:
                return {"action": "CLOSE_QUICK_EXIT", "price": round(price, 6),
                        "reason": f"追跳空失败，升破当日VWAP({vwap:.4f})", "bar_time": bar_time}
        return None

    # ── 空仓 ──────────────────────────────────────────────────────
    if prior_close <= 0 or ag < float(p["min_gap_pct"]):
        return None
    atr = indicators.wilder_atr(bars, atr_len)
    if atr <= 0:
        return None
    sess_hi = max(float(b["h"]) for b in session_bars)
    sess_lo = min(float(b["l"]) for b in session_bars)

    # ── Gap & Go：大跳空 + 顺跳空延续 ─────────────────────────────
    if is_go:
        if not (float(p["go_entry_start_min"]) <= mins_from_open <= float(p["go_entry_end_min"])):
            return None
        if vwap is None:
            return None
        if up_gap and price > today_open and price > vwap:
            stop = max(sess_lo, price - float(p["stop_atr_mult"]) * atr)
            if stop >= price:
                return None
            return {"action": "LONG", "price": round(price, 6), "atr": round(atr, 6),
                    "stop_loss": round(stop, 6), "tier": 1, "bar_time": bar_time,
                    "reason": f"追跳空↑{gap*100:.1f}% 站上开盘价+VWAP"}
        if (not up_gap) and price < today_open and price < vwap:
            stop = min(sess_hi, price + float(p["stop_atr_mult"]) * atr)
            if stop <= price:
                return None
            return {"action": "SHORT", "price": round(price, 6), "atr": round(atr, 6),
                    "stop_loss": round(stop, 6), "tier": 1, "bar_time": bar_time,
                    "reason": f"追跳空↓{gap*100:.1f}% 跌破开盘价+VWAP"}
        return None

    # ── Gap Fill：中等跳空 + 已开始回补 → 赌回补到昨收 ───────────
    if not (float(p["fill_entry_start_min"]) <= mins_from_open <= float(p["fill_entry_end_min"])):
        return None
    gap_px = today_open - prior_close
    retraced = (today_open - price) / gap_px if gap_px != 0 else 0.0  # >0 = 往昨收方向回补
    if retraced < float(p["fill_start_frac"]):
        return None
    pad = float(p["stop_pad_atr"]) * atr
    if up_gap:  # 向上跳空回补 = 做空，目标昨收
        stop = max(sess_hi, today_open) + pad
        if stop <= price or price <= prior_close:
            return None
        return {"action": "SHORT", "price": round(price, 6), "atr": round(atr, 6),
                "stop_loss": round(stop, 6), "tp1": round(prior_close, 6), "tier": 1,
                "bar_time": bar_time, "reason": f"跳空↑{gap*100:.1f}% 回补中，目标昨收{prior_close:.4f}"}
    else:       # 向下跳空回补 = 做多
        stop = min(sess_lo, today_open) - pad
        if stop >= price or price >= prior_close:
            return None
        return {"action": "LONG", "price": round(price, 6), "atr": round(atr, 6),
                "stop_loss": round(stop, 6), "tp1": round(prior_close, 6), "tier": 1,
                "bar_time": bar_time, "reason": f"跳空↓{gap*100:.1f}% 回补中，目标昨收{prior_close:.4f}"}
