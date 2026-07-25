#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
智能再入场状态机辅助（无交易所 IO；由 PositionSupervisor / mixin 驱动）。
白皮书 v3.0：最多 1 次重入；窗口按 K 线根数；首次激活 0.85、重入激活 1.00。
"""
from __future__ import annotations

import time
from typing import Any, Dict, Optional, Tuple

from reentry_profiles import (
    ACTIVATION_TP1_FRAC,
    LIMIT_TTL_SEC,
    MAX_UNFILLED_REFRESHES,
    activation_frac_for_attempt,
    activation_price,
    apply_tier_to_breath_profile,
    can_smart_reenter,
    compute_reentry_limit_px,
    get_reentry_profile,
    looser_tier,
    parse_kline_extreme,
    reentry_window_deadline,
    tier_coeffs,
)


REENTRY_STATE_KEYS = (
    "reentry_attempt",
    "radar_tier",
    "adx_tier",
    "radar_activation_frac",
    "cycle_tv_price",
    "cycle_tv_side",
    "cycle_open_atr",
    "cycle_entry",
    "reentry_active",
    "reentry_limit_order_id",
    "reentry_limit_px",
    "reentry_limit_deadline_ts",
    "reentry_window_deadline_ts",
    "reentry_unfilled_refreshes",
    "last_exit_source",
    "last_exit_px",
    "radar_pending_arm",
)


def blank_reentry_state() -> Dict[str, Any]:
    return {
        "reentry_attempt": 0,
        "radar_tier": 0,
        "adx_tier": 1,
        "radar_activation_frac": float(ACTIVATION_TP1_FRAC),
        "cycle_tv_price": 0.0,
        "cycle_tv_side": None,
        "cycle_open_atr": 0.0,
        "cycle_entry": 0.0,
        "reentry_active": False,
        "reentry_limit_order_id": None,
        "reentry_limit_px": 0.0,
        "reentry_limit_deadline_ts": 0.0,
        "reentry_window_deadline_ts": 0.0,
        "reentry_unfilled_refreshes": 0,
        "reentry_order_tag": None,
        "reentry_sterile_fail_count": 0,
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
    adx_tier: int = 1,
    radar_tier: Optional[int] = None,
) -> Dict[str, Any]:
    rp = get_reentry_profile(symbol)
    attempt = int(reentry_attempt or 0)
    frac = activation_frac_for_attempt(attempt, rp)
    base_tier = int(adx_tier if adx_tier is not None else 1)
    # 首次开仓：雷达档=ADX档；重入成交后由 bump 写入放宽档
    r_tier = int(radar_tier if radar_tier is not None else base_tier)
    return {
        "reentry_attempt": attempt,
        "adx_tier": base_tier,
        "radar_tier": r_tier,
        "radar_activation_frac": frac,
        "cycle_tv_price": float(tv_price or 0),
        "cycle_tv_side": str(side or "").upper() or None,
        "cycle_open_atr": float(open_atr or 0),
        "cycle_entry": float(entry or 0),
        "reentry_active": False,
        "reentry_limit_order_id": None,
        "reentry_limit_px": 0.0,
        "reentry_limit_deadline_ts": 0.0,
        "reentry_window_deadline_ts": 0.0,
        "reentry_unfilled_refreshes": 0,
        "reentry_order_tag": None,
        "reentry_sterile_fail_count": 0,
        "radar_pending_arm": True,
    }


def compute_activation_px(side: str, entry: float, atr: float, frac: float) -> float:
    return activation_price(side, entry, atr, frac)


def build_tier_breath(breath_profile: Dict[str, Any], tier: int, symbol: str) -> Dict[str, Any]:
    return apply_tier_to_breath_profile(
        breath_profile, tier, get_reentry_profile(symbol),
    )


def plan_reentry_limit(
    *,
    side: str,
    tv_price: float,
    symbol: str,
    klines_5m: Any = None,
    klines_3m: Any = None,
    now: Optional[float] = None,
    prev_entry: float = 0.0,
) -> Tuple[Optional[Dict[str, Any]], str]:
    """
    双保险：极值(5m→3m) 与 TV 折扣取更优；必须优于 TV 与上次开仓价。
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
        prev_entry=prev_entry,
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


def bump_after_reentry_fill(
    prev_attempt: int,
    prev_frac: float,
    symbol: str,
    *,
    adx_tier: int = 1,
) -> Dict[str, Any]:
    """
    成交后再入：attempt+1（封顶1），雷达系数放宽一档，激活线改为 1.00。
    """
    rp = get_reentry_profile(symbol)
    nxt = int(prev_attempt or 0) + 1
    base = int(adx_tier if adx_tier is not None else 1)
    loose = looser_tier(base)
    frac = activation_frac_for_attempt(nxt, rp)
    return {
        "reentry_attempt": nxt,
        "adx_tier": base,
        "radar_tier": loose,
        "radar_activation_frac": frac,
        "reentry_active": False,
        "reentry_limit_order_id": None,
        "reentry_limit_px": 0.0,
        "reentry_limit_deadline_ts": 0.0,
        "reentry_window_deadline_ts": 0.0,
        "reentry_unfilled_refreshes": 0,
        "radar_pending_arm": True,
        "tier_coeffs": tier_coeffs(loose, rp),
    }


def open_reentry_window(symbol: str, from_ts: Optional[float] = None) -> float:
    return reentry_window_deadline(symbol, from_ts)


def evaluate_flat_for_reentry(
    *,
    exit_source: str,
    side: str,
    entry: float,
    exit_px: float,
    atr: float,
    reentry_attempt: int,
    symbol: str,
    window_deadline_ts: float = 0.0,
    now: Optional[float] = None,
    tp1_ever_filled: bool = False,
    adx_tier: Optional[int] = None,
) -> Tuple[bool, str]:
    return can_smart_reenter(
        exit_source=exit_source,
        side=side,
        entry=entry,
        exit_px=exit_px,
        initial_atr=atr,
        reentry_attempt=reentry_attempt,
        profile=get_reentry_profile(symbol),
        window_deadline_ts=window_deadline_ts,
        now=now,
        tp1_ever_filled=tp1_ever_filled,
        adx_tier=adx_tier,
    )
