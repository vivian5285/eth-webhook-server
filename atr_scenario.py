#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
硬止损纯函数（白皮书）：

  tv_stop_distance = |TV.price − TV.stop_loss|
  actual_stop_distance = tv_stop_distance × 1.15（统一系数，不分档）
  多：硬止损 = 成交价 − actual_stop_distance
  空：硬止损 = 成交价 + actual_stop_distance

v16.4.0：已删除 VPS 独立拉 ATR / 场景一二。雷达 ATR 一律用 TV webhook.atr。
TP 限价仅 TP1+TP2；TP3 不挂限价。
"""
from __future__ import annotations

from typing import Dict, Optional

HARD_SL_BUFFER_MULT = 1.15
# 兼容旧常量名（已废弃语义，保留 import 不崩；不再参与计算）
HARD_SL_RADAR_ATR_MULT = 0.0
HARD_SL_RADAR_PAD = 0.0
HARD_SL_SLIPPAGE_MULT = 0.0
TEMP_STOP_BUFFER_MULT = HARD_SL_BUFFER_MULT


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
    硬止损距离拆解（不含方向）。
    仅 |TV价−TV.SL|×buffer；radar_floor/slip 恒为 0（旧字段保留兼容日志）。
    """
    tv_e = float(tv_entry or 0)
    tv_sl = float(tv_stop_loss or 0)
    tv_m = float(tv_mult or HARD_SL_BUFFER_MULT)

    tv_implied = abs(tv_e - tv_sl) * tv_m if tv_e > 0 and tv_sl > 0 and tv_m > 0 else 0.0
    radar_floor = 0.0
    slip = 0.0
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
    永久硬止损价（唯一路径）。

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


def reanchor_tp_prices_to_fill(
    side: str,
    tv_entry: float,
    fill_entry: float,
    tp_prices,
) -> list:
    """
    TP1/2/3 按"空间"重新锚定到真实成交价——2026-09-05新增，跟本模块顶部
    硬止损公式(距离锚TV、价格锚成交价)同一个道理，此前只有硬止损做了这层，
    TP123一直直接照搬TV绝对价位挂单：PAXGUSDT实盘复现，TV信号到我们真正
    下单之间有滑点(市场剧烈波动/webhook处理耗时)，成交价比TV参考价差了
    近一整根ATR，TP绝对价位没跟着挪，等于"到TP1还有多远"这件事被滑点悄悄
    改变了，而且方向还是缩窄可用利润空间(entry变差、TP位置不变→距离变
    小)。硬止损那边已经在这么做(hard_stop_price)，TP这边只是此前一直
    没补——不是新决定，是补齐同一套已经生效的原则。

    dist_i = |TP_i − TV.entry|（每一档单独算，不假设三档等距）
    多：新TP_i = 成交价 + dist_i；空：新TP_i = 成交价 − dist_i

    tv_entry<=0 或 tv_entry 跟 fill_entry 几乎相等(没有实质滑点)时原样
    返回，避免无意义的浮点误差改写；0 值档位（该档本来就没有）保持 0。
    """
    side_u = str(side or "").strip().upper()
    tv_e = float(tv_entry or 0)
    fill = float(fill_entry or 0)
    prices = list(tp_prices or [])
    if side_u not in ("LONG", "SHORT") or tv_e <= 0 or fill <= 0:
        return prices
    if abs(tv_e - fill) < 1e-9:
        return prices
    out = []
    for tp in prices:
        tp = float(tp or 0)
        if tp <= 0:
            out.append(0.0)
            continue
        dist = abs(tp - tv_e)
        out.append(round(fill + dist, 2) if side_u == "LONG" else round(fill - dist, 2))
    return out


def temp_hard_stop_price(side: str, entry: float, tv_stop_loss: float,
                         buffer_mult: float = HARD_SL_BUFFER_MULT, **kwargs) -> float:
    """兼容旧调用名 → 永久硬止损（同一公式）。"""
    return hard_stop_price(
        side, entry, tv_stop_loss, buffer_mult=buffer_mult, **kwargs
    )


def place_tp_levels_for_scenario(scenario: int = 0) -> int:
    """兼容旧调用：恒挂 TP1+TP2（2 档）；场景参数已废弃。"""
    return 2
