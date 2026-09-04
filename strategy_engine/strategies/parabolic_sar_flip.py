#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
抛物线转向指标(Parabolic SAR)——J. Welles Wilder Jr. 在《New Concepts in
Technical Trading Systems》(1978)公开发表，跟本仓库已经在用的RSI、ATR、
ADX是同一本书、同一位作者的公开发明，规则透明、可手算复现。

核心思想：SAR(Stop And Reverse)本身就是一条不断逼近价格的"抛物线"止损/
反手线——趋势刚开始时贴得松，随着趋势延续、加速因子(AF)每创一次新高/
新低就累加一点(上限0.2)，SAR线追得越来越紧；一旦价格反向碰到SAR线，
立刻反手(不是平仓观望，是直接反向开仓)。这是Wilder自己发表时就明确
设计成"永远在场内、多空来回换手"的系统，没有额外的确认门槛。

规则(Wilder原始参数：初始加速因子0.02，每次新高/新低递增0.02，
上限0.2)：
  - 价格站上/跌破SAR线 → 反手信号(SAR从空翻多/从多翻空的那一刻)
  - SAR线本身就是止损位，不需要另配ATR止损——这是这套系统的设计初衷，
    止损止盈都内建在指标本身里
  - 离场即反手：跟supertrend_adx/ema_cross_7_30一样是"反向信号出现就
    主动平仓"，不设固定止盈

跟本仓库其余"翻转型"趋势战法的关键区别：**刻意不加任何确认门槛**，
直接对照supertrend_adx——那套结构上几乎一样(指标翻转定方向+指标本身
当止损)，但特意保留了"ADX≥20才允许开仓"这一道确认门槛；这套完全遵照
Wilder原始设计，翻转就换手，不问当前趋势强不强。两套用同一个4H周期跑，
能直接回答"去掉确认门槛，纯反转系统打不打得过加了确认门槛的
supertrend_adx"这个问题，是继turtle_breakout vs breakout_retest、
supertrend_adx vs adx_regime_switch之后本擂台第三组"控制变量对照"。

周期选择理由：4H，为了跟supertrend_adx严格同周期，上面这层对照才成立。

数据要求：SAR是逐根递推算出来的，理论上从第2根就有值，但为了让加速
因子有机会走完至少一轮完整的加速-反手周期、不是刚初始化的粗糙值，
本模块要求至少min_bars(默认30)根热身。
"""
from __future__ import annotations

from typing import Dict, List, Optional

from strategy_engine import indicators

DEFAULT_PARAMS = {
    "af_init": 0.02,
    "af_step": 0.02,
    "af_max": 0.2,
    "min_bars": 30,
    "atr_len": 14,
}


def generate_signal(bars_by_tf: Dict[str, List[dict]], params: Optional[dict] = None, position: Optional[dict] = None) -> Optional[dict]:
    bars = bars_by_tf.get("base") or []
    p = {**DEFAULT_PARAMS, **(params or {})}
    min_bars = int(p["min_bars"])
    atr_len = int(p["atr_len"])
    need = max(min_bars, atr_len + 2) + 2
    if len(bars) < need:
        return None

    sar_series = indicators.parabolic_sar(bars, float(p["af_init"]), float(p["af_step"]), float(p["af_max"]))
    if len(sar_series) < 2:
        return None
    (_, dir_prev), (sar_now, dir_now) = sar_series[-2], sar_series[-1]

    last = bars[-1]
    price = float(last["c"])
    bar_time = int(last["t"])
    flipped_up = dir_prev < 0 and dir_now > 0
    flipped_down = dir_prev > 0 and dir_now < 0

    if position:
        side = str(position.get("side") or "").upper()
        if side == "LONG" and flipped_down:
            return {
                "action": "CLOSE_QUICK_EXIT", "price": round(price, 6),
                "reason": f"SAR翻转向下(线={sar_now:.6f})，反手离场", "bar_time": bar_time,
            }
        if side == "SHORT" and flipped_up:
            return {
                "action": "CLOSE_QUICK_EXIT", "price": round(price, 6),
                "reason": f"SAR翻转向上(线={sar_now:.6f})，反手离场", "bar_time": bar_time,
            }
        return None

    if not flipped_up and not flipped_down:
        return None

    atr = indicators.wilder_atr(bars, atr_len)  # 仅用于pnl归一(atr0)，止损用SAR线本身
    if atr <= 0:
        return None

    action = "LONG" if flipped_up else "SHORT"
    return {
        "action": action,
        "price": round(price, 6),
        "atr": round(atr, 6),
        "stop_loss": round(sar_now, 6),
        "tier": 1,
        "bar_time": bar_time,
        "reason": f"SAR翻转{'向上' if action == 'LONG' else '向下'}(线={sar_now:.6f})，AF从{p['af_init']}起步",
    }
