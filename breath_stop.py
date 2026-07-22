#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
呼吸止损引擎（开仓即工作 · TV initial_atr 基准 · 1h ATR 呼吸系数）

两阶段：
  阶段一：initial_stop 基准阶梯（步长/跟进 × breathing_coefficient）
  阶段二：追踪距离 = initial_atr × breathing_coefficient

调用方持久化：entry_price, initial_atr, initial_stop, current_stop,
highest/lowest, phase(breakeven_phase), breathing_coefficient, step_count,
remaining_qty_pct
"""
from __future__ import annotations

from typing import List, Optional, Tuple

INITIAL_SL_ATR = 1.5
STEP_TRIGGER_ATR = 0.75
STEP_ADVANCE_ATR = 0.4
BREAKEVEN_TRIGGER_ATR = 3.0
TP1_ATR = 1.35
TP1_FLOOR_ATR = 0.5
TP2_ATR = 2.5
TP2_FLOOR_ATR = 1.5
# 挂单执行缓冲（覆盖滑点/延迟）：多单再向外减，空单再向外加
STOP_EXEC_BUFFER_USD = 0.3

# 兼容旧 import（阶段二已改呼吸系数，不再用 ADX 追踪）
ADX_WEAK_BOUND = 15.0
ADX_STRONG_BOUND = 35.0
TRAIL_DIST_WEAK_ATR = 1.2
TRAIL_DIST_STRONG_ATR = 2.5
ADX_FALLBACK = 25.0


def trail_distance_by_adx(adx_val: float) -> float:
    """已废弃：阶段二改用呼吸系数。保留空壳供旧测试/静态检查。"""
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


def get_breathing_coefficient(current_atr_1h: float, initial_atr: float,
                              ratio_history: Optional[List[float]] = None
                              ) -> Tuple[float, float, List[float]]:
    """
    呼吸系数档位（对 ratio=current_1h/initial_atr 做最近3次平滑）：
      <0.7 → 0.7
      0.7~1.0 → 0.85
      1.0~1.4 → 1.0
      1.4~2.0 → 1.2~1.4 线性
      ≥2.0 → 1.5
    返回 (coefficient, smooth_ratio, updated_history)
    """
    init = float(initial_atr or 0)
    cur = float(current_atr_1h or 0)
    hist = list(ratio_history or [])
    if init <= 0 or cur <= 0:
        return 1.0, 0.0, hist
    ratio = cur / init
    hist.append(ratio)
    if len(hist) > 3:
        hist = hist[-3:]
    smooth = sum(hist) / len(hist)
    if smooth < 0.7:
        coeff = 0.7
    elif smooth < 1.0:
        coeff = 0.85
    elif smooth < 1.4:
        coeff = 1.0
    elif smooth < 2.0:
        coeff = 1.2 + (smooth - 1.4) / 0.6 * 0.2
    else:
        coeff = 1.5
    return float(coeff), float(smooth), hist


def initial_stop_price(side: str, entry_price: float, initial_atr: float) -> float:
    """理论 initialStop：多=entry-1.5ATR，空=entry+1.5ATR（不含执行缓冲）。"""
    entry = float(entry_price or 0)
    atr = float(initial_atr or 0)
    if entry <= 0 or atr <= 0:
        return 0.0
    side = str(side or "").strip().upper()
    if side == "SHORT":
        return round(entry + INITIAL_SL_ATR * atr, 2)
    return round(entry - INITIAL_SL_ATR * atr, 2)


def order_stop_price(side: str, initial_stop: float,
                     buffer_usd: float = STOP_EXEC_BUFFER_USD) -> float:
    """
    盘口挂单止损 = initialStop ± 0.3 USDT 执行缓冲（向外扩，更难被扫）。
    多单再减；空单再加。
    """
    stop = float(initial_stop or 0)
    buf = abs(float(buffer_usd or 0))
    if stop <= 0:
        return 0.0
    side = str(side or "").strip().upper()
    if side == "SHORT":
        return round(stop + buf, 2)
    return round(stop - buf, 2)


def calculate_stop_long(
    price: float,
    entry_price: float,
    initial_atr: float,
    initial_stop: float,
    current_stop: float,
    highest_price: float,
    breakeven_phase: bool,
    breathing_coefficient: float = 1.0,
    adx_val: float = ADX_FALLBACK,  # 兼容旧调用签名，忽略
) -> Tuple[float, float, bool, int]:
    """多单。返回：(新止损, 新最高, 新阶段, step_count)"""
    price = float(price or 0)
    entry_price = float(entry_price or 0)
    initial_atr = float(initial_atr or 0)
    initial_stop = float(initial_stop or 0)
    current_stop = float(current_stop or 0)
    highest_price = float(highest_price or entry_price or 0)
    breakeven_phase = bool(breakeven_phase)
    coeff = float(breathing_coefficient or 1.0)
    if coeff <= 0:
        coeff = 1.0

    new_highest = max(highest_price, price) if price > 0 else highest_price
    new_stop = current_stop
    new_phase = breakeven_phase
    step_count = 0

    if entry_price <= 0 or initial_atr <= 0 or price <= 0:
        return new_stop, new_highest, new_phase, step_count

    trail_dist = initial_atr * coeff

    if not breakeven_phase:
        step_trigger = STEP_TRIGGER_ATR * initial_atr * coeff
        step_count = max(0, int((price - entry_price) / step_trigger)) if step_trigger > 0 else 0
        step_stop = initial_stop + step_count * STEP_ADVANCE_ATR * initial_atr * coeff
        candidate = max(current_stop, step_stop)

        if price >= entry_price + TP1_ATR * initial_atr:
            candidate = max(candidate, entry_price + TP1_FLOOR_ATR * initial_atr)
        if price >= entry_price + TP2_ATR * initial_atr:
            candidate = max(candidate, entry_price + TP2_FLOOR_ATR * initial_atr)

        new_stop = candidate

        if price >= entry_price + BREAKEVEN_TRIGGER_ATR * initial_atr:
            new_phase = True
            new_stop = max(new_stop, new_highest - trail_dist)
    else:
        candidate = new_highest - trail_dist
        new_stop = max(current_stop, candidate)

    return round(float(new_stop), 2), round(float(new_highest), 2), bool(new_phase), int(step_count)


def calculate_stop_short(
    price: float,
    entry_price: float,
    initial_atr: float,
    initial_stop: float,
    current_stop: float,
    lowest_price: float,
    breakeven_phase: bool,
    breathing_coefficient: float = 1.0,
    adx_val: float = ADX_FALLBACK,
) -> Tuple[float, float, bool, int]:
    """空单对称。返回：(新止损, 新最低, 新阶段, step_count)"""
    price = float(price or 0)
    entry_price = float(entry_price or 0)
    initial_atr = float(initial_atr or 0)
    initial_stop = float(initial_stop or 0)
    current_stop = float(current_stop or 0)
    lowest_price = float(lowest_price or entry_price or 0)
    breakeven_phase = bool(breakeven_phase)
    coeff = float(breathing_coefficient or 1.0)
    if coeff <= 0:
        coeff = 1.0

    new_lowest = min(lowest_price, price) if (lowest_price > 0 and price > 0) else (
        price if price > 0 else lowest_price
    )
    if lowest_price <= 0 and price > 0:
        new_lowest = price
    new_stop = current_stop
    new_phase = breakeven_phase
    step_count = 0

    if entry_price <= 0 or initial_atr <= 0 or price <= 0:
        return new_stop, new_lowest, new_phase, step_count

    trail_dist = initial_atr * coeff

    if not breakeven_phase:
        step_trigger = STEP_TRIGGER_ATR * initial_atr * coeff
        step_count = max(0, int((entry_price - price) / step_trigger)) if step_trigger > 0 else 0
        step_stop = initial_stop - step_count * STEP_ADVANCE_ATR * initial_atr * coeff
        candidate = min(current_stop, step_stop) if current_stop > 0 else step_stop

        if price <= entry_price - TP1_ATR * initial_atr:
            candidate = min(candidate, entry_price - TP1_FLOOR_ATR * initial_atr)
        if price <= entry_price - TP2_ATR * initial_atr:
            candidate = min(candidate, entry_price - TP2_FLOOR_ATR * initial_atr)

        new_stop = candidate

        if price <= entry_price - BREAKEVEN_TRIGGER_ATR * initial_atr:
            new_phase = True
            new_stop = min(new_stop, new_lowest + trail_dist)
    else:
        candidate = new_lowest + trail_dist
        new_stop = min(current_stop, candidate) if current_stop > 0 else candidate

    return round(float(new_stop), 2), round(float(new_lowest), 2), bool(new_phase), int(step_count)


def calculate_breath_stop(
    side: str,
    price: float,
    entry_price: float,
    initial_atr: float,
    initial_stop: float,
    current_stop: float,
    best_price: float,
    breakeven_phase: bool,
    breathing_coefficient: float = 1.0,
    adx_val: float = ADX_FALLBACK,  # 兼容旧调用；忽略
    **_kw,
):
    """
    统一入口。best_price = 多单 highest / 空单 lowest。
    返回 dict: stop, best, breakeven_phase, meta
    """
    side = str(side or "").strip().upper()
    atr = float(initial_atr or 0)
    entry = float(entry_price or 0)
    px = float(price or 0)
    coeff = float(breathing_coefficient or 1.0)
    if coeff <= 0:
        coeff = 1.0
    meta = {
        "trail_atr": coeff,  # 阶段二距离倍数 = 呼吸系数
        "breathing_coefficient": coeff,
        "adx": float(adx_val or 0),  # 仅日志兼容
        "phase": "breakeven" if breakeven_phase else "ladder",
        "step_count": 0,
    }
    if side == "SHORT":
        stop, best, phase, step_count = calculate_stop_short(
            px, entry, atr, initial_stop, current_stop, best_price,
            breakeven_phase, breathing_coefficient=coeff,
        )
    else:
        stop, best, phase, step_count = calculate_stop_long(
            px, entry, atr, initial_stop, current_stop, best_price,
            breakeven_phase, breathing_coefficient=coeff,
        )
    meta["step_count"] = int(step_count)
    meta["phase"] = "breakeven" if phase else "ladder"
    meta["trail_distance"] = round(atr * coeff, 4) if atr > 0 else 0.0
    return {
        "stop": stop,
        "best": best,
        "breakeven_phase": phase,
        "meta": meta,
    }
