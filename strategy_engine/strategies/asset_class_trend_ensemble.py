#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Asset-aware trend ensemble for paper comparison only.

HA provides the entry/exit event. HMA and the MTF indicator stack are used as
regime confirmations, so independent event signals do not need to occur on the
same bar. Crypto accepts either confirmation; tokenized stocks require MTF and
do not use HMA. Gold is deliberately excluded by the roster until a positive
gold-specific edge exists.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from strategy_engine import indicators
from strategy_engine.strategies import heikin_ashi_trend


DEFAULT_PARAMS = {
    "asset_class": "crypto",
    "ema_fast": 7,
    "ema_slow": 30,
    "macd_fast": 12,
    "macd_slow": 26,
    "macd_signal": 9,
    "cci_len": 20,
    "hma_period": 20,
    "adx_len": 14,
    "adx_gate": 16.0,
    "hma_min_slope_atr_frac": 0.02,
}


def _mtf_direction(bars: List[dict], p: dict) -> int:
    need = max(int(p["ema_slow"]), int(p["macd_slow"]) + int(p["macd_signal"]), int(p["cci_len"])) + 5
    if len(bars) < need:
        return 0
    closes = indicators.closes(bars)
    slow = indicators.ema(closes, int(p["ema_slow"]))
    _, _, hist = indicators.macd(
        closes, int(p["macd_fast"]), int(p["macd_slow"]), int(p["macd_signal"]),
    )
    cci = indicators.cci(bars, int(p["cci_len"]))
    if not slow or not hist or not cci:
        return 0
    price = float(bars[-1]["c"])
    if price > slow[-1] and hist[-1] >= 0 and cci[-1] > 0:
        return 1
    if price < slow[-1] and hist[-1] <= 0 and cci[-1] < 0:
        return -1
    return 0


def _hma_direction(bars: List[dict], p: dict) -> int:
    period = int(p["hma_period"])
    atr_len = int(p["adx_len"])
    if len(bars) < period * 3 + atr_len + 10:
        return 0
    closes = indicators.closes(bars)
    hma = indicators.hma(closes, period)
    if len(hma) < 2:
        return 0
    atr = indicators.wilder_atr(bars, atr_len)
    adx = indicators.wilder_adx(bars, atr_len)
    slope = hma[-1] - hma[-2]
    if atr <= 0 or adx < float(p["adx_gate"]):
        return 0
    if abs(slope) < float(p["hma_min_slope_atr_frac"]) * atr:
        return 0
    return 1 if slope > 0 else -1


def _daily_direction(daily: List[dict], p: dict) -> int:
    slow_len = int(p["ema_slow"])
    if len(daily) < slow_len + 2:
        return 0
    closes = indicators.closes(daily)
    fast = indicators.ema(closes, int(p["ema_fast"]))
    slow = indicators.ema(closes, slow_len)
    if not fast or not slow:
        return 0
    if fast[-1] > slow[-1]:
        return 1
    if fast[-1] < slow[-1]:
        return -1
    return 0


def _regime_votes(bars_by_tf: Dict[str, List[dict]], p: dict) -> Tuple[int, int, int]:
    base = bars_by_tf.get("base") or []
    daily = bars_by_tf.get("1d") or []
    return _mtf_direction(base, p), _hma_direction(base, p), _daily_direction(daily, p)


def generate_signal(
    bars_by_tf: Dict[str, List[dict]], params: Optional[dict] = None,
    position: Optional[dict] = None,
) -> Optional[dict]:
    p = {**DEFAULT_PARAMS, **(params or {})}
    asset_class = str(p.get("asset_class") or "crypto").lower()
    if asset_class not in ("crypto", "stocks"):
        return None

    ha_params = {
        "use_ema_direction_filter": True,
        "ema_require_price_side": True,
    }
    ha_signal = heikin_ashi_trend.generate_signal(
        bars_by_tf, params=ha_params, position=position,
    )
    if not ha_signal:
        return None

    mtf_dir, hma_dir, daily_dir = _regime_votes(bars_by_tf, p)

    if position:
        if ha_signal.get("action") != "CLOSE_QUICK_EXIT":
            return None
        side = str(position.get("side") or "").upper()
        held_dir = 1 if side == "LONG" else -1
        reason = str(ha_signal.get("reason") or "")
        if "影" in reason:
            return ha_signal
        confirmations = [mtf_dir]
        if asset_class == "crypto":
            confirmations.append(hma_dir)
        else:
            confirmations.append(daily_dir)
        aligned = any(v == held_dir for v in confirmations)
        opposed = any(v == -held_dir for v in confirmations)
        if aligned and not opposed:
            return None
        ha_signal["reason"] = f"{reason} | ensemble确认退出 MTF={mtf_dir:+d} HMA={hma_dir:+d} D1={daily_dir:+d}"
        return ha_signal

    action = str(ha_signal.get("action") or "").upper()
    direction = 1 if action == "LONG" else -1 if action == "SHORT" else 0
    if direction == 0:
        return None

    if asset_class == "crypto":
        if mtf_dir != direction and hma_dir != direction:
            return None
        confirmations = int(mtf_dir == direction) + int(hma_dir == direction)
    else:
        if mtf_dir != direction:
            return None
        confirmations = 1 + int(daily_dir == direction)

    ha_signal["tier"] = 2 if confirmations >= 2 else 1
    ha_signal["reason"] = (
        f"严格HA触发 + {asset_class} ensemble确认 "
        f"MTF={mtf_dir:+d} HMA={hma_dir:+d} D1={daily_dir:+d}"
    )
    return ha_signal
