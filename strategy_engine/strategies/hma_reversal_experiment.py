#!/usr/bin/env python3
"""Closed-bar HMA reversal experiments; the original HMA is unchanged."""
from __future__ import annotations

from typing import Dict, List, Optional

from strategy_engine import indicators
from strategy_engine.strategies import hma_trend


STRONG_SLOPE_ATR = 0.10
STRONG_ADX = 20.0
MEDIUM_SLOPE_ATR = 0.05
MEDIUM_ADX = 15.0


def _state(bars: List[dict]) -> Optional[dict]:
    period = int(hma_trend.DEFAULT_PARAMS["period"])
    atr_len = int(hma_trend.DEFAULT_PARAMS["atr_len"])
    if len(bars) < period * 3 + atr_len + 10:
        return None
    h = indicators.hma(indicators.closes(bars), period)
    if len(h) < 3:
        return None
    atr = indicators.wilder_atr(bars, atr_len)
    if atr <= 0:
        return None
    direction = 1 if h[-1] > h[-2] else -1
    previous = 1 if h[-2] > h[-3] else -1
    adx = indicators.wilder_adx(bars, atr_len)
    slope_atr = abs(h[-1] - h[-2]) / atr
    strong = slope_atr >= STRONG_SLOPE_ATR and adx >= STRONG_ADX
    medium = slope_atr >= MEDIUM_SLOPE_ATR and adx >= MEDIUM_ADX
    return {
        "direction": direction,
        "flipped": direction != previous,
        "strong": strong,
        "medium": medium,
        "slope_atr": slope_atr,
        "adx": adx,
        "atr": atr,
    }


def _delayed_entry(bars: List[dict], state: dict) -> Optional[dict]:
    if len(bars) < 2 or state["flipped"] or not state["medium"]:
        return None
    prior = _state(bars[:-1])
    if not prior or not prior["flipped"] or prior["strong"] or not prior["medium"]:
        return None
    if prior["direction"] != state["direction"]:
        return None
    direction = state["direction"]
    price = float(bars[-1]["c"])
    action = "LONG" if direction > 0 else "SHORT"
    return {
        "action": action,
        "price": round(price, 6),
        "atr": round(state["atr"], 6),
        "stop_loss": round(
            price - direction * state["atr"]
            * float(hma_trend.DEFAULT_PARAMS["atr_stop_mult"]), 6,
        ),
        "tier": 1,
        "bar_time": int(bars[-1]["t"]),
        "reason": "HMA中强度拐头后一根4h收盘确认",
        "reverse_now": False,
    }


def generate_signal(
    bars_by_tf: Dict[str, List[dict]],
    params: Optional[dict] = None,
    position: Optional[dict] = None,
) -> Optional[dict]:
    bars = bars_by_tf.get("base") or []
    state = _state(bars)
    if not state:
        return None
    base = hma_trend.generate_signal(bars_by_tf, {}, position)
    if position:
        return base

    mode = str((params or {}).get("reversal_mode") or "strong_immediate")
    if mode == "strong_immediate":
        if base:
            return {**base, "reverse_now": state["strong"]}
        return None
    if mode != "tiered_confirm":
        raise ValueError(f"unknown HMA reversal mode: {mode}")
    if base and state["strong"]:
        return {**base, "reverse_now": True}
    return _delayed_entry(bars, state)
