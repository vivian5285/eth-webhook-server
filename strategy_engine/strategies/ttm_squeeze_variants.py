#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ttm_squeeze的出场逻辑/加仓节奏对照实验——2026-09-29应宝贝要求，测试
"要不要开仓"之外的仓位管理维度(止盈规则、加仓节奏)，跟止损宽度(纯参数
覆盖，不需要新代码，见comparison_roster.py里的params覆盖)分开处理。
基础版ttm_squeeze的"不设固定止盈、动量转向才离场"是刻意设计(见其
docstring)，这几个变体反着测一遍，看数据支不支持。

不改动base ttm_squeeze.py本身，只是包一层——保证实盘那条sleeve(直接
import ttm_squeeze.generate_signal)完全不受影响。
"""
from __future__ import annotations

from typing import Dict, List, Optional

from strategy_engine.strategies import ttm_squeeze

DEFAULT_TP_ATR_MULT = 3.0
DEFAULT_PROBE_FRACTION = 0.5


def generate_signal_tp(
    bars_by_tf: Dict[str, List[dict]],
    params: Optional[dict] = None,
    position: Optional[dict] = None,
) -> Optional[dict]:
    """止盈梯度对照版：入场时按ATR×tp_atr_mult(默认3.0，比止损2.0x更宽，
    验证"提前落袋"划不划算)算一个固定tp1，交给引擎的_check_stop_tp机制
    自动执行(不用改动力矩反转离场逻辑，两条腿都保留——谁先触发算谁的)。
    """
    p = dict(params or {})
    tp_mult = float(p.pop("tp_atr_mult", DEFAULT_TP_ATR_MULT))
    sig = ttm_squeeze.generate_signal(bars_by_tf, p, position)
    if not sig or position:
        return sig
    action = str(sig.get("action") or "").upper()
    if action not in ("LONG", "SHORT"):
        return sig
    d = 1.0 if action == "LONG" else -1.0
    atr = float(sig.get("atr") or 0)
    price = float(sig.get("price") or 0)
    if atr > 0 and price > 0:
        sig = dict(sig)
        sig["tp1"] = round(price + d * atr * tp_mult, 6)
    return sig


def generate_signal_pyramid(
    bars_by_tf: Dict[str, List[dict]],
    params: Optional[dict] = None,
    position: Optional[dict] = None,
) -> Optional[dict]:
    """金字塔加仓对照版：首次入场只开probe_fraction(默认50%)大小的"试探仓"
    (entry_stage="probe")，下一根K线只要动量还没反转(没出现CLOSE信号)，
    就用ADD补满——依赖引擎已有的probe/confirm机制(multi_strategy_runner.
    py::_add_to_position，本来就在，只是没有战法用过)。跟一次性满仓的
    base版对照：同样的总仓位，分两步进场值不值。
    """
    p = dict(params or {})
    probe_frac = float(p.pop("probe_fraction", DEFAULT_PROBE_FRACTION))

    if position and str(position.get("entry_stage") or "") == "probe":
        # 处于试探仓阶段：先看基础版会不会判定反转离场
        close_sig = ttm_squeeze.generate_signal(bars_by_tf, p, position)
        if close_sig and str(close_sig.get("action") or "").startswith("CLOSE"):
            return close_sig
        # 没反转 → 补仓确认(只会被引擎调用一次，probe->confirmed后不再进这支)
        bars = bars_by_tf.get("base") or []
        if not bars:
            return None
        last = bars[-1]
        price = float(last["c"])
        side = str(position.get("side") or "").upper()
        entry = float(position.get("entry_price") or price)
        stop = float(position.get("stop_loss") or 0)
        return {
            "action": "ADD", "side": side, "price": round(price, 6),
            "stop_loss": stop, "atr": float(position.get("atr0") or 0),
            "bar_time": int(last["t"]),
            "reason": "probe确认，动量未反转，补满仓位",
        }

    sig = ttm_squeeze.generate_signal(bars_by_tf, p, position)
    if not sig or position:
        return sig
    action = str(sig.get("action") or "").upper()
    if action not in ("LONG", "SHORT"):
        return sig
    sig = dict(sig)
    sig["entry_stage"] = "probe"
    sig["position_fraction"] = probe_frac
    return sig
