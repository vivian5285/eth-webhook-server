#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Heikin-Ashi adaptive probe entry for arena-only comparison.

Two completed 4h HA bars establish context. A completed 1h breakout may open a
one-third probe. The next completed 4h bar must confirm the original three-bar
HA signal; confirmation adds the remaining two-thirds, otherwise the probe is
closed. No unfinished candle is treated as final data.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from strategy_engine import funding, indicators
from strategy_engine.strategies import heikin_ashi_trend


FOUR_HOURS_MS = 4 * 60 * 60 * 1000
DEFAULT_PARAMS = {
    "ema_fast_len": 7,
    "ema_slow_len": 25,
    "fast_atr_len": 14,
    "min_fast_body_atr": 0.35,
    "max_fast_body_atr": 1.50,
    "max_extension_atr": 1.20,
    "close_location_frac": 0.25,
    "atr_stop_mult": 2.0,
    "funding_extreme_pct": 0.90,
}


def _green(bar: dict) -> bool:
    return float(bar["c"]) > float(bar["o"])


def _body(bar: dict) -> float:
    return abs(float(bar["c"]) - float(bar["o"]))


def _funding_allows(symbol: str, side: str, extreme: float) -> bool:
    percentile = funding.funding_percentile(symbol)
    if percentile is None:
        return True
    if side == "LONG":
        return percentile < extreme
    return percentile > (1.0 - extreme)


def _confirmed_signal(base: List[dict], params: dict, side: str, context_bar_time: int) -> Optional[dict]:
    signal = heikin_ashi_trend.generate_signal({"base": base}, params, None)
    if not signal or signal.get("action") != side:
        return None
    if int(signal.get("bar_time") or 0) <= context_bar_time:
        return None
    return signal


def generate_signal(
    bars_by_tf: Dict[str, List[dict]],
    params: Optional[dict] = None,
    position: Optional[dict] = None,
) -> Optional[dict]:
    p = {**DEFAULT_PARAMS, **(params or {})}
    base = bars_by_tf.get("base") or []
    fast = bars_by_tf.get("1h") or []
    if len(base) < 40:
        return None

    if position:
        if str(position.get("entry_stage") or "") != "probe":
            return heikin_ashi_trend.generate_signal({"base": base}, p, position)
        context_time = int(position.get("context_bar_time") or 0)
        latest_base_time = int(base[-1]["t"])
        if latest_base_time <= context_time:
            return None
        side = str(position.get("side") or "").upper()
        confirmed = _confirmed_signal(base, p, side, context_time)
        decision_time = latest_base_time + FOUR_HOURS_MS
        if confirmed:
            return {
                **confirmed,
                "action": "ADD",
                "side": side,
                "position_fraction": 2.0 / 3.0,
                "entry_stage": "confirmed",
                "bar_time": decision_time,
                "reason": "4h HA third-bar confirmation; add remaining two-thirds",
            }
        return {
            "action": "CLOSE_PROBE_TIMEOUT",
            "price": round(float(base[-1]["c"]), 6),
            "bar_time": decision_time,
            "reason": "probe not confirmed by next completed 4h bar",
        }

    if len(fast) < 40:
        return None
    ha = heikin_ashi_trend._ha(base)
    last_two = ha[-2:]
    all_green = all(_green(x) for x in last_two)
    all_red = all(not _green(x) for x in last_two)
    if not (all_green or all_red):
        return None
    bodies = [_body(x) for x in last_two]
    if bodies[1] < bodies[0]:
        return None

    closes = indicators.closes(base)
    ema_fast = indicators.ema(closes, int(p["ema_fast_len"]))
    ema_slow = indicators.ema(closes, int(p["ema_slow_len"]))
    if not ema_fast or not ema_slow:
        return None
    side = "LONG" if all_green else "SHORT"
    if side == "LONG" and not (ema_fast[-1] > ema_slow[-1]):
        return None
    if side == "SHORT" and not (ema_fast[-1] < ema_slow[-1]):
        return None

    context = base[-1]
    trigger = fast[-1]
    context_time = int(context["t"])
    if int(trigger["t"]) < context_time + FOUR_HOURS_MS:
        return None
    price = float(trigger["c"])
    if side == "LONG" and price <= float(context["h"]):
        return None
    if side == "SHORT" and price >= float(context["l"]):
        return None

    fast_atr = indicators.wilder_atr(fast, int(p["fast_atr_len"]))
    base_atr = indicators.wilder_atr(base, 14)
    if fast_atr <= 0 or base_atr <= 0:
        return None
    body = abs(float(trigger["c"]) - float(trigger["o"]))
    body_atr = body / fast_atr
    if not (float(p["min_fast_body_atr"]) <= body_atr <= float(p["max_fast_body_atr"])):
        return None
    bar_range = max(float(trigger["h"]) - float(trigger["l"]), 1e-12)
    close_loc = (price - float(trigger["l"])) / bar_range
    close_frac = float(p["close_location_frac"])
    if side == "LONG" and close_loc < 1.0 - close_frac:
        return None
    if side == "SHORT" and close_loc > close_frac:
        return None
    if abs(price - ema_fast[-1]) > float(p["max_extension_atr"]) * base_atr:
        return None
    symbol = str(p.get("symbol") or "")
    if symbol and not _funding_allows(symbol, side, float(p["funding_extreme_pct"])):
        return None

    direction = 1.0 if side == "LONG" else -1.0
    return {
        "action": side,
        "price": round(price, 6),
        "atr": round(base_atr, 6),
        "stop_loss": round(price - direction * float(p["atr_stop_mult"]) * base_atr, 6),
        "tier": 1,
        "bar_time": int(trigger["t"]),
        "context_bar_time": context_time,
        "entry_stage": "probe",
        "position_fraction": 1.0 / 3.0,
        "reason": "two closed 4h HA bars + closed 1h breakout probe",
    }
