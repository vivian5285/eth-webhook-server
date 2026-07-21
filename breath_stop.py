#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
平衡版·移动保本止损·趋势强度自适应（硬止损 + 雷达合并）

两阶段状态机：
  阶段一(保本前)：以 initial_stop 为基准的阶梯快速锁本
  阶段二(保本后)：ADX 驱动连续追踪

调用方必须持久化：entryPrice, initialAtr, initialStop, currentStop,
highestPrice/lowestPrice, breakevenPhase, remainingQtyPct, lastAdx
"""
from __future__ import annotations

from typing import Tuple

INITIAL_SL_ATR = 1.5
STEP_TRIGGER_ATR = 0.75
STEP_ADVANCE_ATR = 0.4
BREAKEVEN_TRIGGER_ATR = 3.0
TP1_ATR = 1.35
TP1_FLOOR_ATR = 0.5
TP2_ATR = 2.5
TP2_FLOOR_ATR = 1.5

ADX_WEAK_BOUND = 15.0
ADX_STRONG_BOUND = 35.0
TRAIL_DIST_WEAK_ATR = 1.2
TRAIL_DIST_STRONG_ATR = 2.5
ADX_FALLBACK = 25.0  # 行情引擎尚未产出 ADX 时用中间值（非 webhook）


def trail_distance_by_adx(adx_val: float) -> float:
    """ADX → 追踪距离（×ATR），弱紧强宽，线性插值防抖。"""
    try:
        adx = float(adx_val)
    except (TypeError, ValueError):
        adx = ADX_FALLBACK
    if adx <= ADX_WEAK_BOUND:
        return TRAIL_DIST_WEAK_ATR
    if adx >= ADX_STRONG_BOUND:
        return TRAIL_DIST_STRONG_ATR
    ratio = (adx - ADX_WEAK_BOUND) / (ADX_STRONG_BOUND - ADX_WEAK_BOUND)
    return TRAIL_DIST_WEAK_ATR + ratio * (TRAIL_DIST_STRONG_ATR - TRAIL_DIST_WEAK_ATR)


def initial_stop_price(side: str, entry_price: float, initial_atr: float) -> float:
    """开仓初始止损：多=entry-1.5ATR，空=entry+1.5ATR。"""
    entry = float(entry_price or 0)
    atr = float(initial_atr or 0)
    if entry <= 0 or atr <= 0:
        return 0.0
    side = str(side or "").strip().upper()
    if side == "SHORT":
        return round(entry + INITIAL_SL_ATR * atr, 2)
    return round(entry - INITIAL_SL_ATR * atr, 2)


def calculate_stop_long(
    price: float,
    entry_price: float,
    initial_atr: float,
    initial_stop: float,
    current_stop: float,
    highest_price: float,
    breakeven_phase: bool,
    adx_val: float,
) -> Tuple[float, float, bool]:
    """
    多单呼吸止损。每个 tick 调用。
    返回：(新止损价, 新历史最高价, 新阶段标志)
    """
    price = float(price or 0)
    entry_price = float(entry_price or 0)
    initial_atr = float(initial_atr or 0)
    initial_stop = float(initial_stop or 0)
    current_stop = float(current_stop or 0)
    highest_price = float(highest_price or entry_price or 0)
    breakeven_phase = bool(breakeven_phase)

    new_highest = max(highest_price, price) if price > 0 else highest_price
    new_stop = current_stop
    new_phase = breakeven_phase

    if entry_price <= 0 or initial_atr <= 0 or price <= 0:
        return new_stop, new_highest, new_phase

    if not breakeven_phase:
        # 阶段一：阶梯基准必须是 initial_stop（禁止 step0 跳到入场价）
        step_count = max(
            0, int((price - entry_price) / (STEP_TRIGGER_ATR * initial_atr))
        )
        step_stop = initial_stop + step_count * STEP_ADVANCE_ATR * initial_atr
        candidate = max(current_stop, step_stop)

        if price >= entry_price + TP1_ATR * initial_atr:
            candidate = max(candidate, entry_price + TP1_FLOOR_ATR * initial_atr)
        if price >= entry_price + TP2_ATR * initial_atr:
            candidate = max(candidate, entry_price + TP2_FLOOR_ATR * initial_atr)

        new_stop = candidate

        if price >= entry_price + BREAKEVEN_TRIGGER_ATR * initial_atr:
            new_phase = True
            trail_dist = trail_distance_by_adx(adx_val) * initial_atr
            new_stop = max(new_stop, new_highest - trail_dist)
    else:
        trail_dist = trail_distance_by_adx(adx_val) * initial_atr
        candidate = new_highest - trail_dist
        new_stop = max(current_stop, candidate)

    return round(float(new_stop), 2), round(float(new_highest), 2), bool(new_phase)


def calculate_stop_short(
    price: float,
    entry_price: float,
    initial_atr: float,
    initial_stop: float,
    current_stop: float,
    lowest_price: float,
    breakeven_phase: bool,
    adx_val: float,
) -> Tuple[float, float, bool]:
    """空单对称逻辑。返回：(新止损价, 新历史最低价, 新阶段标志)"""
    price = float(price or 0)
    entry_price = float(entry_price or 0)
    initial_atr = float(initial_atr or 0)
    initial_stop = float(initial_stop or 0)
    current_stop = float(current_stop or 0)
    lowest_price = float(lowest_price or entry_price or 0)
    breakeven_phase = bool(breakeven_phase)

    new_lowest = min(lowest_price, price) if (lowest_price > 0 and price > 0) else (
        price if price > 0 else lowest_price
    )
    if lowest_price <= 0 and price > 0:
        new_lowest = price
    new_stop = current_stop
    new_phase = breakeven_phase

    if entry_price <= 0 or initial_atr <= 0 or price <= 0:
        return new_stop, new_lowest, new_phase

    if not breakeven_phase:
        step_count = max(
            0, int((entry_price - price) / (STEP_TRIGGER_ATR * initial_atr))
        )
        step_stop = initial_stop - step_count * STEP_ADVANCE_ATR * initial_atr
        candidate = min(current_stop, step_stop) if current_stop > 0 else step_stop

        if price <= entry_price - TP1_ATR * initial_atr:
            candidate = min(candidate, entry_price - TP1_FLOOR_ATR * initial_atr)
        if price <= entry_price - TP2_ATR * initial_atr:
            candidate = min(candidate, entry_price - TP2_FLOOR_ATR * initial_atr)

        new_stop = candidate

        if price <= entry_price - BREAKEVEN_TRIGGER_ATR * initial_atr:
            new_phase = True
            trail_dist = trail_distance_by_adx(adx_val) * initial_atr
            new_stop = min(new_stop, new_lowest + trail_dist)
    else:
        trail_dist = trail_distance_by_adx(adx_val) * initial_atr
        candidate = new_lowest + trail_dist
        new_stop = min(current_stop, candidate) if current_stop > 0 else candidate

    return round(float(new_stop), 2), round(float(new_lowest), 2), bool(new_phase)


def calculate_breath_stop(
    side: str,
    price: float,
    entry_price: float,
    initial_atr: float,
    initial_stop: float,
    current_stop: float,
    best_price: float,
    breakeven_phase: bool,
    adx_val: float = ADX_FALLBACK,
):
    """
    统一入口。best_price = 多单 highest / 空单 lowest。
    返回 dict: stop, best, phase, trail_atr, step_count(阶段一估算)
    """
    side = str(side or "").strip().upper()
    atr = float(initial_atr or 0)
    entry = float(entry_price or 0)
    px = float(price or 0)
    meta = {
        "trail_atr": trail_distance_by_adx(adx_val),
        "adx": float(adx_val or ADX_FALLBACK),
        "phase": "breakeven" if breakeven_phase else "ladder",
    }
    if side == "SHORT":
        stop, best, phase = calculate_stop_short(
            px, entry, atr, initial_stop, current_stop, best_price,
            breakeven_phase, adx_val,
        )
        if atr > 0 and entry > 0 and px > 0 and not phase:
            meta["step_count"] = max(
                0, int((entry - px) / (STEP_TRIGGER_ATR * atr))
            )
        else:
            meta["step_count"] = 0
    else:
        stop, best, phase = calculate_stop_long(
            px, entry, atr, initial_stop, current_stop, best_price,
            breakeven_phase, adx_val,
        )
        if atr > 0 and entry > 0 and px > 0 and not phase:
            meta["step_count"] = max(
                0, int((px - entry) / (STEP_TRIGGER_ATR * atr))
            )
        else:
            meta["step_count"] = 0
    meta["phase"] = "breakeven" if phase else "ladder"
    return {
        "stop": stop,
        "best": best,
        "breakeven_phase": phase,
        "meta": meta,
    }
