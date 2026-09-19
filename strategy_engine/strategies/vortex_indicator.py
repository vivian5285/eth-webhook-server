#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Vortex Indicator——Etienne Botes与Douglas Siepman公开发表于《Technical
Analysis of Stocks & Commodities》杂志2010年1月刊(公开出版物，不是网红
私有指标)，受Wilder的DMI/ADX启发，公式确定、可手算复现。

公式(period默认14，跟原始论文一致)：
  VM+ = |当根最高价 - 上一根最低价|
  VM- = |当根最低价 - 上一根最高价|
  TR  = Wilder真实波幅(跟ATR用的是同一个TR定义)
  VI+ = period根VM+之和 / period根TR之和
  VI- = period根VM-之和 / period根TR之和

VI+代表"上涨推力"，VI-代表"下跌推力"，两条线的相对强弱衡量多空拉扯。

交易规则(原始论文)：VI+上穿VI-做多，VI-上穿VI+做空——论文原文建议在
穿越当根的最高/最低价挂GTC单进场，这里跟本仓库其余所有战法统一用
"整仓在收盘价入场/离场"的简化模型(见strategy_engine/README.md"整仓
入场出场"的既定简化)，不是这套战法独有的偏差。离场：反向穿越，另配
ATR止损安全网。

周期：4h，跟擂台里adx_regime_switch等同为"Wilder体系衍生指标"的战法
同一档位对比。
"""
from __future__ import annotations

from typing import Dict, List, Optional

from strategy_engine import indicators

DEFAULT_PARAMS = {
    "period": 14,
    "atr_len": 14,
    "atr_stop_mult": 2.0,
}


def _vortex_series(bars: List[dict], period: int):
    n = len(bars)
    if n < period + 2:
        return [], []
    vm_plus, vm_minus, trs = [], [], []
    for i in range(1, n):
        h, l = float(bars[i]["h"]), float(bars[i]["l"])
        ph, pl = float(bars[i - 1]["h"]), float(bars[i - 1]["l"])
        pc = float(bars[i - 1]["c"])
        vm_plus.append(abs(h - pl))
        vm_minus.append(abs(l - ph))
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    vi_plus, vi_minus = [], []
    for i in range(period - 1, len(trs)):
        tr_sum = sum(trs[i - period + 1:i + 1])
        if tr_sum <= 0:
            vi_plus.append(1.0)
            vi_minus.append(1.0)
            continue
        vi_plus.append(sum(vm_plus[i - period + 1:i + 1]) / tr_sum)
        vi_minus.append(sum(vm_minus[i - period + 1:i + 1]) / tr_sum)
    return vi_plus, vi_minus


def generate_signal(bars_by_tf: Dict[str, List[dict]], params: Optional[dict] = None, position: Optional[dict] = None) -> Optional[dict]:
    p = {**DEFAULT_PARAMS, **(params or {})}
    bars = bars_by_tf.get("base") or []
    period = int(p["period"])
    atr_len = int(p["atr_len"])
    need = max(period * 2, atr_len + 2) + 5
    if len(bars) < need:
        return None

    vi_plus, vi_minus = _vortex_series(bars, period)
    if len(vi_plus) < 2:
        return None

    last = bars[-1]
    price = float(last["c"])
    bar_time = int(last["t"])

    p_now, p_prev = vi_plus[-1], vi_plus[-2]
    m_now, m_prev = vi_minus[-1], vi_minus[-2]
    cross_up = p_prev <= m_prev and p_now > m_now
    cross_dn = p_prev >= m_prev and p_now < m_now

    if position:
        side = str(position.get("side") or "").upper()
        if side == "LONG" and cross_dn:
            return {"action": "CLOSE_QUICK_EXIT", "price": round(price, 6),
                    "reason": f"VI-上穿VI+(VI+={p_now:.3f} VI-={m_now:.3f})，上涨推力转弱", "bar_time": bar_time}
        if side == "SHORT" and cross_up:
            return {"action": "CLOSE_QUICK_EXIT", "price": round(price, 6),
                    "reason": f"VI+上穿VI-(VI+={p_now:.3f} VI-={m_now:.3f})，下跌推力转弱", "bar_time": bar_time}
        return None

    if cross_up:
        action, d = "LONG", 1
    elif cross_dn:
        action, d = "SHORT", -1
    else:
        return None

    atr = indicators.wilder_atr(bars, atr_len)
    if atr <= 0:
        return None

    return {
        "action": action,
        "price": round(price, 6),
        "atr": round(atr, 6),
        "stop_loss": round(price - d * atr * float(p["atr_stop_mult"]), 6),
        "tier": 1,
        "bar_time": bar_time,
        "reason": f"Vortex({period}) VI+({p_now:.3f}) {'上穿' if d == 1 else '被'}VI-({m_now:.3f})",
    }
