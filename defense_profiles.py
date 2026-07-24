#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
执行层防御配置（v15.9.0）：硬止损缓冲 + TP 分腿比例。

与 TV 策略对齐：
  - 硬止损距离 = |TV.price − TV.stop_loss| × buffer_multiplier，锚定成交价
  - TP 仓位 10% / 20% / 70%，三级限价常挂；TP3 与雷达互斥
  - 禁止 VPS 再维护独立的 1.5×ATR 硬止损倍数

参数可按品种调整，无需改逻辑代码。
"""
from __future__ import annotations

from typing import Any, Dict, List

# 默认起步值（规格 §4）
DEFAULT_BUFFER_MULT = 1.2
DEFAULT_TP1_PCT = 0.10
DEFAULT_TP2_PCT = 0.20
DEFAULT_MIN_STOP_TICKS = 5
DEFAULT_TICK = 0.01

DEFENSE_ETH: Dict[str, Any] = {
    "name": "ETH",
    "buffer_multiplier": DEFAULT_BUFFER_MULT,
    "tp1_pct": DEFAULT_TP1_PCT,
    "tp2_pct": DEFAULT_TP2_PCT,
    "min_stop_ticks": DEFAULT_MIN_STOP_TICKS,
    "tick_size": DEFAULT_TICK,
}
DEFENSE_XAU: Dict[str, Any] = {
    "name": "XAU",
    "buffer_multiplier": DEFAULT_BUFFER_MULT,
    "tp1_pct": DEFAULT_TP1_PCT,
    "tp2_pct": DEFAULT_TP2_PCT,
    "min_stop_ticks": DEFAULT_MIN_STOP_TICKS,
    "tick_size": DEFAULT_TICK,
}

_BY_SYMBOL = {
    "ETHUSDT": DEFENSE_ETH,
    "XAUUSDT": DEFENSE_XAU,
    "ETH-USDT-SWAP": DEFENSE_ETH,
    "XAU-USDT-SWAP": DEFENSE_XAU,
}


def get_defense_profile(symbol: str) -> Dict[str, Any]:
    sym = str(symbol or "").strip().upper()
    return dict(_BY_SYMBOL.get(sym) or DEFENSE_ETH)


def buffer_multiplier(symbol: str) -> float:
    p = get_defense_profile(symbol)
    return float(p.get("buffer_multiplier") or DEFAULT_BUFFER_MULT)


def tp_leg_ratios(symbol: str) -> List[float]:
    """返回 [tp1, tp2, tp3]，tp3 = 1 − tp1 − tp2。"""
    p = get_defense_profile(symbol)
    t1 = float(p.get("tp1_pct") if p.get("tp1_pct") is not None else DEFAULT_TP1_PCT)
    t2 = float(p.get("tp2_pct") if p.get("tp2_pct") is not None else DEFAULT_TP2_PCT)
    t1 = max(0.0, min(1.0, t1))
    t2 = max(0.0, min(1.0 - t1, t2))
    t3 = max(0.0, 1.0 - t1 - t2)
    return [t1, t2, t3]


def min_stop_distance(symbol: str) -> float:
    """止损距离下限：min_stop_ticks × tick_size（过小视为 TV 异常拒开）。"""
    p = get_defense_profile(symbol)
    ticks = int(p.get("min_stop_ticks") or DEFAULT_MIN_STOP_TICKS)
    tick = float(p.get("tick_size") or DEFAULT_TICK)
    return max(tick, ticks * tick)


def validate_tv_stop_loss(
    symbol: str, tv_price: float, tv_stop_loss: float,
) -> tuple:
    """
    开仓前校验 TV stop_loss。
    返回 (ok, reason, tv_stop_distance)。
    """
    px = float(tv_price or 0)
    sl = float(tv_stop_loss or 0)
    if sl <= 0:
        return False, "missing_or_invalid_stop_loss", 0.0
    if px <= 0:
        return False, "missing_tv_price", 0.0
    dist = abs(px - sl)
    floor = min_stop_distance(symbol)
    if dist < floor:
        return False, f"tv_stop_distance_too_small({dist:.6f}<{floor:.6f})", dist
    return True, "ok", dist
