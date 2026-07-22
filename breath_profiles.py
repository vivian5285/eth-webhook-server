#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
按品种呼吸参数档（ETH / XAU）。执行引擎共用，只在配置层区分。

档位表：对 smooth_ratio = sma(current_1h_atr / initial_atr, 3) 映射呼吸系数。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

# ETH：与 v15.5.23 实盘一致
BREATH_ETH: Dict[str, Any] = {
    "name": "ETH",
    "initial_sl_atr": 1.5,
    "stop_exec_buffer": 0.3,
    "early_be_atr": 0.5,          # 价达 entry±0.5ATR → stop→entry±1tick
    "step_trigger_atr": 0.75,
    "step_advance_atr": 0.4,
    "phase_switch_atr": 3.0,
    "tp1_atr": 1.35,
    "tp1_floor_atr": 0.5,
    "tp2_atr": 2.5,
    "tp2_floor_atr": 1.5,
    "phase2_trail_mult": 1.0,     # trail = atr × coeff × mult
    "tick_size": 0.01,
    # (ratio_hi_exclusive, coeff_or_tuple_for_linear)
    # <0.7→0.7; 0.7~1.0→0.85; 1.0~1.4→1.0; 1.4~2.0→1.2~1.4; ≥2.0→1.5
    "coeff_tiers": [
        (0.7, 0.7),
        (1.0, 0.85),
        (1.4, 1.0),
        (2.0, (1.2, 1.4, 1.4, 2.0)),  # (lo_c, hi_c, lo_r, hi_r)
        (None, 1.5),
    ],
}

# XAU：更紧、更早保本、更密阶梯
BREATH_XAU: Dict[str, Any] = {
    "name": "XAU",
    "initial_sl_atr": 1.5,
    "stop_exec_buffer": 0.5,
    "early_be_atr": 0.3,
    "step_trigger_atr": 0.4,
    "step_advance_atr": 0.35,
    "phase_switch_atr": 3.0,
    "tp1_atr": 1.35,
    "tp1_floor_atr": 0.5,
    "tp2_atr": 2.5,
    "tp2_floor_atr": 1.5,
    "phase2_trail_mult": 0.8,
    "tick_size": 0.01,
    # 0.5 / 0.7 / 0.9 / 1.0~1.2 / 1.3
    "coeff_tiers": [
        (0.7, 0.5),
        (1.0, 0.7),
        (1.4, 0.9),
        (2.0, (1.0, 1.2, 1.4, 2.0)),
        (None, 1.3),
    ],
}

_BY_BINANCE = {
    "ETHUSDT": BREATH_ETH,
    "XAUUSDT": BREATH_XAU,
}

_BY_DEEPCOIN = {
    "ETH-USDT-SWAP": BREATH_ETH,
    "XAU-USDT-SWAP": BREATH_XAU,
}


def get_breath_profile(symbol: str, exchange: str = "binance") -> Dict[str, Any]:
    sym = str(symbol or "").strip().upper()
    if exchange == "deepcoin":
        return dict(_BY_DEEPCOIN.get(sym) or BREATH_ETH)
    return dict(_BY_BINANCE.get(sym) or BREATH_ETH)


def map_coeff_from_tiers(smooth_ratio: float, tiers: Optional[List] = None) -> float:
    """按档位表映射呼吸系数。"""
    smooth = float(smooth_ratio or 0)
    tiers = tiers if tiers is not None else BREATH_ETH["coeff_tiers"]
    for bound, val in tiers:
        if bound is None:
            return float(val)
        if smooth < float(bound):
            if isinstance(val, (tuple, list)) and len(val) == 4:
                lo_c, hi_c, lo_r, hi_r = (float(x) for x in val)
                if hi_r <= lo_r:
                    return hi_c
                t = (smooth - lo_r) / (hi_r - lo_r)
                t = max(0.0, min(1.0, t))
                return lo_c + t * (hi_c - lo_c)
            return float(val)
    return 1.0


def default_breath_profile() -> Dict[str, Any]:
    return dict(BREATH_ETH)
