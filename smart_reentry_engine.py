#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
智能再入场状态机辅助（无交易所 IO；由 PositionSupervisor / mixin 驱动）。
"""
from __future__ import annotations

import time
from typing import Any, Dict, Optional, Tuple

from reentry_profiles import (
    LIMIT_TTL_SEC,
    MAX_UNFILLED_REFRESHES,
    activation_frac_for_attempt,
    activation_price,
    apply_tier_to_breath_profile,
    can_smart_reenter,
    compute_reentry_limit_px,
    get_reentry_profile,
    next_activation_frac,
    parse_kline_extreme,
    tier_coeffs,
)


REENTRY_STATE_KEYS = (
    "reentry_attempt",
    "radar_tier",
    "radar_activation_frac",
    "cycle_tv_price",
    "cycle_tv_side",
    "cycle_open_atr",
    "cycle_entry",
    "reentry_active",
    "reentry_limit_order_id",
    "reentry_limit_px",
    "reentry_limit_deadline_ts",
    "reentry_unfilled_refreshes",
    "last_exit_source",
    "last_exit_px",
    "radar_pending_arm",
)


def blank_reentry_state() -> Dict[str, Any]:
    return {
        "reentry_attempt": 0,
        "radar_tier": 0,
        "radar_activation_frac": 0.50,
        "cycle_tv_price": 0.0,
        "cycle_tv_side": None,
        "cycle_open_atr": 0.0,
        "cycle_entry": 0.0,
        "reentry_active": False,
        "reentry_limit_order_id": None,
        "reentry_limit_px": 0.0,
        "reentry_limit_deadline_ts": 0.0,
        "reentry_unfilled_refreshes": 0,
        "last_exit_source": "",
        "last_exit_px": 0.0,
        "radar_pending_arm": True,
    }


def init_cycle_on_open(
    *,
    side: str,
    tv_price: float,
    entry: float,
    open_atr: float,
    reentry_attempt: int = 0,
    symbol: str = "ETHUSDT",
) -> Dict[str, Any]:
    rp = get_reentry_profile(symbol)
    attempt = int(reentry_attempt or 0)
    frac = activation_frac_for_attempt(attempt, rp)
    return {
        "reentry_attempt": attempt,
        "radar_tier": attempt,
        "radar_activation_frac": frac,
        "cycle_tv_price": float(tv_price or 0),
        "cycle_tv_side": str(side or "").upper() or None,
        "cycle_open_atr": float(open_atr or 0),
        "cycle_entry": float(entry or 0),
        "reentry_active": False,
        "reentry_limit_order_id": None,
        "reentry_limit_px": 0.0,
        "reentry_limit_deadline_ts": 0.0,
        "reentry_unfilled_refreshes": 0,
        "radar_pending_arm": True,
    }


def compute_activation_px(side: str, entry: float, atr: float, frac: float) -> float:
    return activation_price(side, entry, atr, frac)


def build_tier_breath(breath_profile: Dict[str, Any], attempt: int, symbol: str) -> Dict[str, Any]:
    return apply_tier_to_breath_profile(
        breath_profile, attempt, get_reentry_profile(symbol),
    )


def plan_reentry_limit(
    *,
    side: str,
    tv_price: float,
    symbol: str,
    klines_5m: Any = None,
    klines_3m: Any = None,
    now: Optional[float] = None,
) -> Tuple[Optional[Dict[str, Any]], str]:
    """
    双保险：极值(5m→3m) 与 TV 折扣取更优；必须优于 TV。
    返回 (plan, reason)。
    """
    rp = get_reentry_profile(symbol)
    tick = float(rp.get("tick_size") or 0.01)
    d = float(rp.get("limit_discount") or 0.003)
    lo5, hi5 = parse_kline_extreme(klines_5m)
    lo3, hi3 = parse_kline_extreme(klines_3m)
    lim, src = compute_reentry_limit_px(
        side=side,
        tv_price=tv_price,
        low5=lo5,
        high5=hi5,
        low3=lo3,
        high3=hi3,
        tick=tick,
        discount=d,
    )
    if lim <= 0:
        return None, src or "bad_limit_px"
    ttl = float(rp.get("limit_ttl_sec") or LIMIT_TTL_SEC)
    ts = float(now if now is not None else time.time())
    return {
        "limit_px": lim,
        "deadline_ts": ts + ttl,
        "source": src,
        "tick": tick,
        "low5": lo5,
        "high5": hi5,
        "low3": lo3,
        "high3": hi3,
    }, "ok"


def max_unfilled_refreshes(symbol: str) -> int:
    rp = get_reentry_profile(symbol)
    return int(rp.get("max_unfilled_refreshes") or MAX_UNFILLED_REFRESHES)


def bump_after_reentry_fill(prev_attempt: int, prev_frac: float, symbol: str) -> Dict[str, Any]:
    """成交后再入：attempt+1，frac 单调抬升，radar 重新待激活。"""
    rp = get_reentry_profile(symbol)
    nxt = int(prev_attempt or 0) + 1
    frac = next_activation_frac(prev_frac, nxt, rp)
    return {
        "reentry_attempt": nxt,
        "radar_tier": nxt,
        "radar_activation_frac": frac,
        "reentry_active": False,
        "reentry_limit_order_id": None,
        "reentry_limit_px": 0.0,
        "reentry_limit_deadline_ts": 0.0,
        "reentry_unfilled_refreshes": 0,
        "radar_pending_arm": True,
        "tier_coeffs": tier_coeffs(nxt, rp),
    }


def evaluate_flat_for_reentry(
    *,
    exit_source: str,
    side: str,
    entry: float,
    exit_px: float,
    atr: float,
    reentry_attempt: int,
    symbol: str,
) -> Tuple[bool, str]:
    return can_smart_reenter(
        exit_source=exit_source,
        side=side,
        entry=entry,
        exit_px=exit_px,
        initial_atr=atr,
        reentry_attempt=reentry_attempt,
        profile=get_reentry_profile(symbol),
    )
