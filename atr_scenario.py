#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
三层防线 + 两场景 ATR（白皮书定稿纯函数）：

硬止损（永久）：|entry − TV.stop_loss| × 1.2 — 挂出后禁止改价/撤单，直至仓位归零
雷达止损（独立）：场景一用 VPS 原生 1h ATR；场景二用 TV.atr；可持续恢复场景一
TP1/TP2 始终挂；TP3 仅场景二挂
"""
from __future__ import annotations

from typing import Optional, Tuple

HARD_SL_BUFFER_MULT = 1.2
TEMP_STOP_BUFFER_MULT = HARD_SL_BUFFER_MULT  # 兼容旧名
SCENARIO_VPS = 1
SCENARIO_TV = 2


def hard_stop_price(side: str, entry: float, tv_stop_loss: float,
                    buffer_mult: float = HARD_SL_BUFFER_MULT) -> float:
    """永久硬止损价：距离 = |entry−TV.stop_loss|×buffer_mult。"""
    side_u = str(side or "").strip().upper()
    entry_f = float(entry or 0)
    sl = float(tv_stop_loss or 0)
    mult = float(buffer_mult or HARD_SL_BUFFER_MULT)
    if entry_f <= 0 or sl <= 0 or mult <= 0:
        return 0.0
    dist = abs(entry_f - sl) * mult
    if dist <= 0:
        return 0.0
    if side_u == "LONG":
        return round(entry_f - dist, 2)
    if side_u == "SHORT":
        return round(entry_f + dist, 2)
    return 0.0


def temp_hard_stop_price(side: str, entry: float, tv_stop_loss: float,
                         buffer_mult: float = HARD_SL_BUFFER_MULT) -> float:
    """兼容旧调用名 → 永久硬止损。"""
    return hard_stop_price(side, entry, tv_stop_loss, buffer_mult=buffer_mult)


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
