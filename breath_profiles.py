#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
按品种呼吸参数档（ETH / XAU）。执行引擎共用，只在配置层区分。

v15.8.0：
  - 雷达启动改为递进阈值（见 reentry_profiles），本文件仅存阶段一/二系数基线（tier0）
  - ETH/XAU phase2 trail 统一 1.2~2.5
  - XAU tier0: early_be=0.65 / step_trigger=0.70 / step_advance=0.45
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

# 共用边界
RATIO_FLOOR = 0.6
RATIO_CEILING = 2.2

# ETH：追踪距离 1.2~2.5×ATR；tier0 系数
BREATH_ETH: Dict[str, Any] = {
    "name": "ETH",
    "initial_sl_atr": 1.5,
    "stop_exec_buffer": 0.3,
    "early_be_atr": 0.5,
    "step_trigger_atr": 0.75,
    "step_advance_atr": 0.4,
    "phase_switch_atr": 3.0,
    "tp1_atr": 1.35,
    "tp1_floor_atr": 0.5,
    "tp2_atr": 2.5,
    "tp2_floor_atr": 1.5,
    "phase2_trail_mult": 1.0,
    "min_mult": 1.2,
    "max_mult": 2.5,
    "ratio_floor": RATIO_FLOOR,
    "ratio_ceiling": RATIO_CEILING,
    "tick_size": 0.01,
    "entry_score": 3,
    "exit_score": 2,
}

# XAU：tier0（v15.8.0 递进表首档）；trail 与 ETH 对齐 1.2~2.5
BREATH_XAU: Dict[str, Any] = {
    "name": "XAU",
    "initial_sl_atr": 1.5,
    "stop_exec_buffer": 0.5,
    "early_be_atr": 0.65,
    "step_trigger_atr": 0.70,
    "step_advance_atr": 0.45,
    "phase_switch_atr": 3.0,
    "tp1_atr": 1.35,
    "tp1_floor_atr": 0.5,
    "tp2_atr": 2.5,
    "tp2_floor_atr": 1.5,
    "phase2_trail_mult": 1.0,
    "min_mult": 1.2,
    "max_mult": 2.5,
    "ratio_floor": RATIO_FLOOR,
    "ratio_ceiling": RATIO_CEILING,
    "tick_size": 0.01,
    "entry_score": 1,
    "exit_score": 1,
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


def trail_distance_multiplier(ratio: float, profile: Optional[Dict[str, Any]] = None) -> float:
    """
    连续线性插值：
      ratio<=floor → minMult
      ratio>=ceiling → maxMult
      否则线性
    """
    p = profile if isinstance(profile, dict) and profile else BREATH_ETH
    lo = float(p.get("ratio_floor") if p.get("ratio_floor") is not None else RATIO_FLOOR)
    hi = float(p.get("ratio_ceiling") if p.get("ratio_ceiling") is not None else RATIO_CEILING)
    mn = float(p.get("min_mult") if p.get("min_mult") is not None else 1.2)
    mx = float(p.get("max_mult") if p.get("max_mult") is not None else 2.5)
    r = float(ratio or 0.0)
    if r <= lo:
        return mn
    if r >= hi:
        return mx
    if hi <= lo:
        return mx
    t = (r - lo) / (hi - lo)
    return mn + (mx - mn) * t


def cold_start_multiplier(profile: Optional[Dict[str, Any]] = None) -> float:
    """0 次采样：ratio=1.0 代入公式。"""
    return trail_distance_multiplier(1.0, profile)


def map_coeff_from_tiers(smooth_ratio: float, tiers: Optional[List] = None) -> float:
    """兼容旧名：现改为连续插值。"""
    profile = None
    if isinstance(tiers, dict):
        profile = tiers
    return trail_distance_multiplier(float(smooth_ratio or 0), profile)


def default_breath_profile() -> Dict[str, Any]:
    return dict(BREATH_ETH)


class LockedInitialAtr:
    """
    initial_atr 开仓写入后锁定；仅 clear_on_flat 可清零。
    非开仓路径赋值 raise / 忽略（strict 模式 raise）。
    """

    def __init__(self, strict: bool = True):
        self._value = 0.0
        self._locked = False
        self._strict = bool(strict)

    @property
    def value(self) -> float:
        return float(self._value or 0.0)

    @property
    def locked(self) -> bool:
        return bool(self._locked)

    def set_on_open(self, atr: float) -> float:
        v = float(atr or 0)
        if v <= 0:
            raise ValueError("set_on_open requires atr>0")
        self._value = v
        self._locked = True
        return self._value

    def clear_on_flat(self) -> None:
        self._value = 0.0
        self._locked = False

    def try_set(self, atr: float, *, allow_while_locked: bool = False) -> float:
        """持仓期禁止写入；allow_while_locked 仅测试/迁移用。"""
        if self._locked and not allow_while_locked:
            if self._strict:
                raise RuntimeError(
                    f"initial_atr locked at {self._value}; refuse write {atr}"
                )
            return self._value
        v = float(atr or 0)
        if v > 0:
            self._value = v
        return self._value

    def upgrade_to_vps(self, atr: float) -> float:
        """
        两场景定稿：允许场景二(TV atr) → 场景一(VPS 真实 1h ATR) 覆盖锁定值。
        """
        v = float(atr or 0)
        if v <= 0:
            raise ValueError("upgrade_to_vps requires atr>0")
        self._value = v
        self._locked = True
        return self._value
