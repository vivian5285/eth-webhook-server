#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Connors RSI-2均值回归——Larry Connors在《Short Term Trading Strategies
That Work》公开发表、有大量公开回测记录的短线均值回归系统。核心思想跟
本仓库其余"趋势跟随/动量确认"类策略完全相反：只在大趋势方向(200均线)
顺着做，但入场时机专挑短线超卖/超买的"深度回调"，赌的是"大趋势没变、
短线情绪出现极端后会均值回归"。

规则(经典版)：
  - 大方向过滤：close > SMA(200) 只做多；close < SMA(200) 只做空
  - 入场：RSI(2) 跌破entry_rsi(默认10，Connors原文测试过5/10两档，10更
    保守) → 做多；RSI(2) 涨破100-entry_rsi(默认90) → 做空
  - 离场：close重新站上SMA(exit_sma_len,默认5) → 平多；跌破SMA5 → 平空
    (Connors原文常用的退出规则之一，不是唯一，但最简单最常引用)
  - 止损：原始系统不强制止损(靠均值回归本身快速兑现)，这里加一道
    atr_stop_mult(默认3，故意给得比趋势类策略宽，避免均值回归策略常见的
    "还没回归就先被止损扫掉"这个已知弱点)×ATR的安全网，不是系统本身
    要求的止损位置。

品种覆盖：这套系统在权益类资产上验证最多，本账户里TSLA/META/ASML/GS/
MU/SNDK这些代币化股票品种(本质是股票，不是加密货币)是最贴近原始验证
场景的候选，纯加密货币品种上是否依然有效需要靠影子引擎实测数据说话，
不能想当然照搬股票市场的历史有效性。

数据要求：SMA(200)需要至少201根bars_by_tf["base"]历史，本模块设计给
较慢的周期用(4h/1d)。
"""
from __future__ import annotations

from typing import Dict, List, Optional

from strategy_engine import indicators

DEFAULT_PARAMS = {
    "trend_sma_len": 200,
    "rsi_len": 2,
    "entry_rsi_long": 10.0,
    "entry_rsi_short": 90.0,
    "exit_sma_len": 5,
    "atr_len": 14,
    "atr_stop_mult": 3.0,
}


def generate_signal(bars_by_tf: Dict[str, List[dict]], params: Optional[dict] = None, position: Optional[dict] = None) -> Optional[dict]:
    bars = bars_by_tf.get("base") or []
    p = {**DEFAULT_PARAMS, **(params or {})}
    trend_len = int(p["trend_sma_len"])
    rsi_len = int(p["rsi_len"])
    exit_len = int(p["exit_sma_len"])
    atr_len = int(p["atr_len"])
    need = max(trend_len, rsi_len + 1, exit_len, atr_len) + 2
    if len(bars) < need:
        return None

    cs = indicators.closes(bars)
    last = bars[-1]
    price = float(last["c"])
    bar_time = int(last["t"])

    exit_sma = indicators.sma(cs, exit_len)
    if not exit_sma:
        return None
    exit_sma_now = exit_sma[-1]

    if position:
        side = str(position.get("side") or "").upper()
        if side == "LONG" and price >= exit_sma_now:
            return {
                "action": "CLOSE_QUICK_EXIT",
                "price": round(price, 6),
                "reason": f"均值回归完成·重新站上SMA{exit_len}({exit_sma_now:.6f})",
                "bar_time": bar_time,
            }
        if side == "SHORT" and price <= exit_sma_now:
            return {
                "action": "CLOSE_QUICK_EXIT",
                "price": round(price, 6),
                "reason": f"均值回归完成·跌破SMA{exit_len}({exit_sma_now:.6f})",
                "bar_time": bar_time,
            }
        return None

    trend_sma = indicators.sma(cs, trend_len)
    rsi2 = indicators.rsi(cs, rsi_len)
    if not trend_sma or not rsi2:
        return None
    trend_now = trend_sma[-1]
    rsi_now = rsi2[-1]

    if price > trend_now and rsi_now < float(p["entry_rsi_long"]):
        action = "LONG"
    elif price < trend_now and rsi_now > float(p["entry_rsi_short"]):
        action = "SHORT"
    else:
        return None

    atr = indicators.wilder_atr(bars, atr_len)
    if atr <= 0:
        return None
    direction = 1 if action == "LONG" else -1

    return {
        "action": action,
        "price": round(price, 6),
        "atr": round(atr, 6),
        "stop_loss": round(price - direction * atr * float(p["atr_stop_mult"]), 6),
        "tp1": round(price + direction * atr * 0.8, 6),
        "tp2": round(price + direction * atr * 1.5, 6),
        "tp3": round(price + direction * atr * 2.5, 6),
        "tier": 1,
        "bar_time": bar_time,
        "reason": f"RSI({rsi_len})={rsi_now:.1f} vs SMA{trend_len}={trend_now:.6f}",
    }
