#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
三层防线 + 两场景 ATR（白皮书定稿纯函数）：

硬止损（永久，唯一公式 · v15.7.8+）：
  基础距离 = max(|TV理论开仓价 − TV.stop_loss| × 1.2, 1.5 × initial_atr × 1.05)
  滑点缓冲 = |交易所成交价 − TV理论开仓价| × 2
  最终距离 = 基础距离 + 滑点缓冲
  挂单价 = 成交价 ± 最终距离
  挂出后禁止改价/撤单，直至仓位归零（仅公式升级允许一次性重挂）

已删除：单独的「|成交价−TV.SL|×1.2」旧路径（与上式在 atr=0、tv_entry=fill 时等价，不再分叉）。

雷达止损（独立）：场景一用 VPS 原生 ATR；场景二用 TV.atr；可持续恢复场景一
TP1/TP2 始终挂；TP3 仅场景二挂
"""
from __future__ import annotations

from typing import Dict, Optional, Tuple

HARD_SL_BUFFER_MULT = 1.2
HARD_SL_RADAR_ATR_MULT = 1.5
HARD_SL_RADAR_PAD = 1.05  # 雷达初始距 ×1.05 作地板
HARD_SL_SLIPPAGE_MULT = 2.0
TEMP_STOP_BUFFER_MULT = HARD_SL_BUFFER_MULT  # 兼容旧名
SCENARIO_VPS = 1
SCENARIO_TV = 2


def compute_hard_stop_distance(
    tv_entry: float,
    tv_stop_loss: float,
    fill_entry: float,
    initial_atr: float = 0.0,
    *,
    tv_mult: float = HARD_SL_BUFFER_MULT,
    radar_atr_mult: float = HARD_SL_RADAR_ATR_MULT,
    radar_pad: float = HARD_SL_RADAR_PAD,
    slip_mult: float = HARD_SL_SLIPPAGE_MULT,
) -> Dict[str, float]:
    """
    返回硬止损距离拆解（不含方向）。
    tv_entry/tv_stop_loss 为 TV 理论价；fill_entry 为交易所成交价。
    """
    tv_e = float(tv_entry or 0)
    tv_sl = float(tv_stop_loss or 0)
    fill = float(fill_entry or 0)
    atr = float(initial_atr or 0)
    tv_m = float(tv_mult or HARD_SL_BUFFER_MULT)
    r_m = float(radar_atr_mult or HARD_SL_RADAR_ATR_MULT)
    r_pad = float(radar_pad or HARD_SL_RADAR_PAD)
    s_m = float(slip_mult if slip_mult is not None else HARD_SL_SLIPPAGE_MULT)

    tv_implied = abs(tv_e - tv_sl) * tv_m if tv_e > 0 and tv_sl > 0 and tv_m > 0 else 0.0
    radar_floor = atr * r_m * r_pad if atr > 0 and r_m > 0 and r_pad > 0 else 0.0
    base = max(tv_implied, radar_floor)
    slip = abs(fill - tv_e) * s_m if fill > 0 and tv_e > 0 and s_m > 0 else 0.0
    final = base + slip if base > 0 else 0.0
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
    slip_mult: float = HARD_SL_SLIPPAGE_MULT,
) -> float:
    """
    永久硬止损价（唯一路径）。

    - fill = fill_entry 或 entry（交易所成交价）
    - tv_entry 缺省 = fill（无滑点项）
    - atr=0 时雷达地板为 0，退化为 max(TV隐含×1.2, 0)+滑点
    """
    side_u = str(side or "").strip().upper()
    fill = float(fill_entry if fill_entry is not None else (entry or 0))
    sl = float(tv_stop_loss or 0)
    atr = float(initial_atr or 0)
    mult = float(buffer_mult or HARD_SL_BUFFER_MULT)
    tv_e = float(tv_entry) if tv_entry is not None else fill
    if tv_e <= 0:
        tv_e = fill

    if fill <= 0 or side_u not in ("LONG", "SHORT"):
        return 0.0
    if sl <= 0 and atr <= 0:
        return 0.0

    parts = compute_hard_stop_distance(
        tv_e,
        sl,
        fill,
        atr,
        tv_mult=mult if sl > 0 else 0.0,
        slip_mult=slip_mult,
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
    仅决定雷达 ATR / 是否挂 TP3；绝不改写硬止损价。
    """
    vps = float(vps_atr or 0)
    tv = float(tv_atr or 0)
    if vps > 0:
        return SCENARIO_VPS, vps, "vps"
    if tv > 0:
        return SCENARIO_TV, tv, "tv"
    return 0, 0.0, "reject"


def place_tp_levels_for_scenario(scenario: int) -> int:
    """场景一=2（不挂TP3）；场景二=3（挂TP3兜底）。"""
    return 3 if int(scenario or 0) == SCENARIO_TV else 2


def scenario_notice(scenario: int, vps_atr: float = 0.0, tv_atr: float = 0.0,
                    recovered: bool = False) -> Optional[str]:
    """钉钉/日志文案；场景一无通知；场景二/恢复有记录。"""
    sc = int(scenario or 0)
    if recovered and sc == SCENARIO_VPS:
        return (
            f"VPS真实ATR已恢复接管 atr={float(vps_atr or 0):.4f}，"
            f"已撤销TP3兜底，切回场景一雷达（硬止损未动）"
        )
    if sc == SCENARIO_TV:
        return (
            "本次VPS真实ATR获取失败，已用TV理论ATR继续运作雷达，"
            f"TP3已按TV价位挂出兜底（tv_atr={float(tv_atr or 0):.4f}）；"
            "硬止损保持永久挂出"
        )
    return None
