#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
三层防线 + 两场景 ATR（白皮书定稿纯函数）：

硬止损（永久，唯一公式 · 白皮书 v3.0）：
  tv_stop_distance = |TV.price − TV.stop_loss|
  actual_stop_distance = tv_stop_distance × 1.15（统一系数，不分档）
  多：硬止损 = 成交价 − actual_stop_distance
  空：硬止损 = 成交价 + actual_stop_distance
  禁止再用 1.5×ATR 地板 / 滑点×2 旧路径；缺 stop_loss → 距离=0（上层拒开）

雷达止损（独立）：场景一用 VPS 原生 ATR；场景二用 TV.atr；可持续恢复场景一
TP1/TP2/TP3 三级限价常挂（10%/20%/70%）；TP3 与雷达互斥
"""
from __future__ import annotations

from typing import Dict, Optional, Tuple

HARD_SL_BUFFER_MULT = 1.15
# 兼容旧常量名（已废弃语义，保留 import 不崩；不再参与计算）
HARD_SL_RADAR_ATR_MULT = 0.0
HARD_SL_RADAR_PAD = 0.0
HARD_SL_SLIPPAGE_MULT = 0.0
TEMP_STOP_BUFFER_MULT = HARD_SL_BUFFER_MULT
SCENARIO_VPS = 1
SCENARIO_TV = 2


def compute_hard_stop_distance(
    tv_entry: float,
    tv_stop_loss: float,
    fill_entry: float = 0.0,
    initial_atr: float = 0.0,
    *,
    tv_mult: float = HARD_SL_BUFFER_MULT,
    radar_atr_mult: float = 0.0,
    radar_pad: float = 0.0,
    slip_mult: float = 0.0,
) -> Dict[str, float]:
    """
    硬止损距离拆解（不含方向）· v15.9.0。
    仅 |TV价−TV.SL|×buffer；radar_floor/slip 恒为 0（旧字段保留兼容日志）。
    """
    tv_e = float(tv_entry or 0)
    tv_sl = float(tv_stop_loss or 0)
    tv_m = float(tv_mult or HARD_SL_BUFFER_MULT)

    tv_implied = abs(tv_e - tv_sl) * tv_m if tv_e > 0 and tv_sl > 0 and tv_m > 0 else 0.0
    radar_floor = 0.0  # 已废除 1.5×ATR 地板
    slip = 0.0         # 已废除 |fill−TV|×2；距离锚定成交价即可
    base = float(tv_implied)
    final = base if base > 0 else 0.0
    return {
        "tv_implied": float(tv_implied),
        "radar_floor": float(radar_floor),
        "base": float(base),
        "slip": float(slip),
        "final": float(final),
    }


def hard_stop_price(
    side: str,
    entry: float,
    tv_stop_loss: float,
    buffer_mult: float = HARD_SL_BUFFER_MULT,
    *,
    tv_entry: Optional[float] = None,
    initial_atr: float = 0.0,
    fill_entry: Optional[float] = None,
    slip_mult: float = 0.0,
) -> float:
    """
    永久硬止损价（唯一路径 · v15.9.0）。

    - fill = fill_entry 或 entry（交易所成交价）
    - 距离 = |tv_entry − tv_stop_loss| × buffer（tv_entry 缺省=fill）
    - 无有效 stop_loss → 0（上层必须拒开，禁止 1.5×ATR 兜底）
    """
    side_u = str(side or "").strip().upper()
    fill = float(fill_entry if fill_entry is not None else (entry or 0))
    sl = float(tv_stop_loss or 0)
    mult = float(buffer_mult or HARD_SL_BUFFER_MULT)
    tv_e = float(tv_entry) if tv_entry is not None else fill
    if tv_e <= 0:
        tv_e = fill

    if fill <= 0 or side_u not in ("LONG", "SHORT"):
        return 0.0
    if sl <= 0:
        return 0.0

    parts = compute_hard_stop_distance(
        tv_e, sl, fill, 0.0, tv_mult=mult, slip_mult=0.0,
    )
    dist = float(parts["final"])
    if dist <= 0:
        return 0.0
    if side_u == "LONG":
        return round(fill - dist, 2)
    return round(fill + dist, 2)


def temp_hard_stop_price(side: str, entry: float, tv_stop_loss: float,
                         buffer_mult: float = HARD_SL_BUFFER_MULT, **kwargs) -> float:
    """兼容旧调用名 → 永久硬止损（同一公式）。"""
    return hard_stop_price(
        side, entry, tv_stop_loss, buffer_mult=buffer_mult, **kwargs
    )


def resolve_atr_scenario(vps_atr: float, tv_atr: float) -> Tuple[int, float, str]:
    """
    返回 (scenario, radar_initial_atr, source)。
    场景一优先：vps_atr>0；否则场景二要求 tv_atr>0。
    仅决定雷达 ATR；TP3 已常挂，不再由场景决定是否挂限价。
    """
    vps = float(vps_atr or 0)
    tv = float(tv_atr or 0)
    if vps > 0:
        return SCENARIO_VPS, vps, "vps"
    if tv > 0:
        return SCENARIO_TV, tv, "tv"
    return 0, 0.0, "reject"


def place_tp_levels_for_scenario(scenario: int) -> int:
    """v15.9.0：无论场景一/二，始终挂 TP1+TP2+TP3。"""
    return 3


def scenario_notice(scenario: int, vps_atr: float = 0.0, tv_atr: float = 0.0,
                    recovered: bool = False) -> Optional[str]:
    """钉钉/日志文案；场景一无通知；场景二/恢复有记录。"""
    sc = int(scenario or 0)
    if recovered and sc == SCENARIO_VPS:
        return (
            f"VPS真实ATR已恢复接管 atr={float(vps_atr or 0):.4f}，"
            f"切回场景一雷达（硬止损未动；TP3 限价保留与雷达互斥）"
        )
    if sc == SCENARIO_TV:
        return (
            "本次VPS真实ATR获取失败，已用TV理论ATR继续运作雷达，"
            f"TP123 限价保持挂出（tv_atr={float(tv_atr or 0):.4f}）；"
            "硬止损保持永久挂出"
        )
    return None
