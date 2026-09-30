#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Crypto组合协同实验——2026-09-30应宝贝要求。目的：验证"策略间协同、分清
主次"这个设计假设是否真的比现有实盘的纯加权blend更好，而不是凭直觉直接
上实盘。

背景：实盘asset_class_combo_strategy.py的CRYPTO_SLEEVES(hma_trend15%+
ttm_squeeze50%+keltner_channel20%+turtle_breakout15%)是纯加权blend——
entry_signals()收集每个sleeve各自的信号，combine_entries()要求所有开火的
sleeve方向一致才合成一笔，权重直接相加(封顶1.0)。residual_momentum没有
参与，因为它成交频率极低(9-26上线到9-30，20多天只有1笔已平仓交易)，混
进纯加权blend里权重会小到几乎不起作用。

宝贝的想法：residual_momentum这种"低频高确信"的战法，一旦真的开火，应该
让其他常规sleeve"让路"，而不是被稀释成一个小权重分量。这个文件提供两个
可以直接对照的战法身份，跑在同一批品种、同一套风控上：

  · generate_signal_blend()      —— 对照组，逐字复刻实盘CRYPTO_SLEEVES的
    纯加权blend逻辑(不含residual_momentum)，作为"现状"基线。
  · generate_signal_coordinated() —— 实验组，同样4个sleeve的blend之上，
    叠加residual_momentum的"高确信让权"机制(见下方COORDINATION RULES)。

COORDINATION RULES（保守设计，residual_momentum沉默时跟对照组行为完全
一致，不引入任何新风险）：
  1. 4个常规sleeve先按原有逻辑做一次"方向一致才合成"的blend，得到
     (base_direction, base_weight)或None。
  2. residual_momentum没有信号 → 直接返回base blend的结果，跟对照组
     逐字一致。
  3. residual_momentum有信号，且方向跟base blend一致(或base blend为
     None即没有常规sleeve开火) → 在base_weight基础上加
     RESIDUAL_MOMENTUM_AGREEMENT_BONUS(或者base为空时单独按
     RESIDUAL_MOMENTUM_SOLO_WEIGHT开仓)，方向选residual_momentum/base
     一致的那个方向。
  4. residual_momentum有信号但方向跟base blend相反 → "让权"，不是简单
     取消：base blend的weight打YIELD_DISCOUNT_FRAC折扣，residual_
     momentum自己按RESIDUAL_MOMENTUM_SOLO_WEIGHT计权，净方向由谁的
     net weight更大决定；如果net weight <= 0(打折后还是不够压过对方)，
     视为双方低确信度冲突，不开仓——不是无脑让residual_momentum完全
     override，是让它有更大但不是绝对的话语权。

止损：跟实盘combine_entries()同一个做法——LONG取最宽松(最低)的止损，
SHORT取最宽松(最高)的止损，谁的信号入选就用谁的止损，residual_momentum
自己的止损公式(ATR止损，封顶12%距离)沿用它自己模块内的计算。
"""
from __future__ import annotations

from typing import Dict, List, Optional

from strategy_engine.strategies import (
    hma_trend, ttm_squeeze, keltner_channel, turtle_breakout, residual_momentum,
)

CRYPTO_SLEEVES = (
    ("hma_trend", 0.15),
    ("ttm_squeeze", 0.50),
    ("keltner_channel", 0.20),
    ("turtle_breakout", 0.15),
)

RESIDUAL_MOMENTUM_SOLO_WEIGHT = 0.40
RESIDUAL_MOMENTUM_AGREEMENT_BONUS = 0.15
YIELD_DISCOUNT_FRAC = 0.5


def _call_sleeve(name: str, bars_by_tf: Dict[str, List[dict]], position: Optional[dict]) -> Optional[dict]:
    if name == "hma_trend":
        return hma_trend.generate_signal(bars_by_tf, {}, position)
    if name == "ttm_squeeze":
        return ttm_squeeze.generate_signal(bars_by_tf, {}, position)
    if name == "keltner_channel":
        return keltner_channel.generate_signal(bars_by_tf, {}, position)
    if name == "turtle_breakout":
        return turtle_breakout.generate_signal(bars_by_tf, {}, position)
    raise ValueError(f"unknown sleeve: {name}")


def _base_entries(bars_by_tf: Dict[str, List[dict]]) -> List[dict]:
    out = []
    for name, weight in CRYPTO_SLEEVES:
        signal = _call_sleeve(name, bars_by_tf, None)
        if not signal or str(signal.get("action") or "").upper() not in {"LONG", "SHORT"}:
            continue
        out.append({"name": name, "weight": float(weight), "signal": dict(signal)})
    return out


def _combine_base(entries: List[dict]) -> Optional[dict]:
    """逐字照抄实盘combine_entries()的"方向一致才合成"逻辑。"""
    if not entries:
        return None
    directions = {str(item["signal"].get("action") or "").upper() for item in entries}
    if len(directions) != 1:
        return None
    direction = directions.pop()
    weight = sum(float(item["weight"]) for item in entries)
    if weight <= 0:
        return None
    stops = [float(item["signal"]["stop_loss"]) for item in entries]
    stop_loss = max(stops) if direction == "LONG" else min(stops)
    return {
        "action": direction, "weight": min(1.0, weight), "stop_loss": stop_loss,
        "entries": entries,
        "price": entries[0]["signal"]["price"], "atr": entries[0]["signal"].get("atr"),
        "bar_time": entries[0]["signal"]["bar_time"],
    }


def _finalize(base_signal_source: dict, action: str, weight: float, reason: str) -> Optional[dict]:
    if weight <= 0:
        return None
    return {
        "action": action,
        "price": round(float(base_signal_source["price"]), 6),
        "atr": base_signal_source.get("atr"),
        "stop_loss": round(float(base_signal_source["stop_loss"]), 6),
        "tier": 1,
        "bar_time": int(base_signal_source["bar_time"]),
        "reason": reason,
        "position_fraction": min(1.0, weight),
    }


def generate_signal_blend(
    bars_by_tf: Dict[str, List[dict]], params: Optional[dict] = None, position: Optional[dict] = None,
) -> Optional[dict]:
    """对照组：纯加权blend，逐字对齐实盘现状(不含residual_momentum)。"""
    if position:
        # 离场：任一sleeve报CLOSE即离场，跟实盘exit_signal()同一哲学。
        for name, _ in CRYPTO_SLEEVES:
            sig = _call_sleeve(name, bars_by_tf, position)
            if sig and str(sig.get("action") or "").upper().startswith("CLOSE"):
                return sig
        return None
    combined = _combine_base(_base_entries(bars_by_tf))
    if not combined:
        return None
    return _finalize(combined, combined["action"], combined["weight"], "crypto_combo_blend(对照组)")


def generate_signal_coordinated(
    bars_by_tf: Dict[str, List[dict]], params: Optional[dict] = None, position: Optional[dict] = None,
) -> Optional[dict]:
    """实验组：4-sleeve blend + residual_momentum高确信让权。"""
    p = params or {}
    symbol = str(p.get("symbol") or "").upper()
    # residual_momentum走1d周期，自己的bars_by_tf.get("base")要指向1d序列，
    # 不能跟其余4个sleeve共用4h的"base"——靠roster的"mtf":["1d"]拿到。
    rm_bars_by_tf = {"base": bars_by_tf.get("1d") or []}
    rm_params = {"symbol": symbol}

    if position:
        # 离场：4个常规sleeve任一报CLOSE，或residual_momentum自己报CLOSE
        # (仅当当前持仓其实是residual_momentum主导开的仓，用同一份position
        # 传给它做离场判断，逻辑跟实盘exit_signal()一致，只是多问一个人)。
        for name, _ in CRYPTO_SLEEVES:
            sig = _call_sleeve(name, bars_by_tf, position)
            if sig and str(sig.get("action") or "").upper().startswith("CLOSE"):
                return sig
        rm_sig = residual_momentum.generate_signal(rm_bars_by_tf, rm_params, position)
        if rm_sig and str(rm_sig.get("action") or "").upper().startswith("CLOSE"):
            return rm_sig
        return None

    base_entries = _base_entries(bars_by_tf)
    base_combined = _combine_base(base_entries)
    rm_signal = residual_momentum.generate_signal(rm_bars_by_tf, rm_params, None)
    rm_action = None
    if rm_signal and str(rm_signal.get("action") or "").upper() in {"LONG", "SHORT"}:
        rm_action = str(rm_signal["action"]).upper()

    if rm_action is None:
        # residual_momentum沉默 -> 跟对照组行为逐字一致。
        if not base_combined:
            return None
        return _finalize(
            base_combined, base_combined["action"], base_combined["weight"],
            "crypto_combo_coordinated(常规sleeve独立开仓，residual_momentum沉默)",
        )

    if base_combined is None:
        # 只有residual_momentum开火，常规sleeve集体沉默或方向打架。
        return _finalize(
            rm_signal, rm_action, RESIDUAL_MOMENTUM_SOLO_WEIGHT,
            "crypto_combo_coordinated(仅residual_momentum开火)",
        )

    if base_combined["action"] == rm_action:
        weight = min(1.0, base_combined["weight"] + RESIDUAL_MOMENTUM_AGREEMENT_BONUS)
        return _finalize(
            base_combined, rm_action, weight,
            "crypto_combo_coordinated(常规sleeve与residual_momentum同向，加权确认)",
        )

    # 方向冲突：让权，不是无脑override——常规sleeve打折但residual_momentum
    # 也不是白拿满权重，net weight由两边打完折/给权重之后的差值决定。
    discounted_base_weight = base_combined["weight"] * YIELD_DISCOUNT_FRAC
    net_weight = RESIDUAL_MOMENTUM_SOLO_WEIGHT - discounted_base_weight
    if net_weight <= 0:
        return None
    return _finalize(
        rm_signal, rm_action, net_weight,
        "crypto_combo_coordinated(residual_momentum与常规sleeve反向，让权后仍按residual_momentum方向)",
    )
