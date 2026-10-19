#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
日线双均线大级别顺势(EMA8/30)——2026-10-16应宝贝要求新增两个对照：只做多 vs 多空双向。

按"状态"持仓，不是只在交叉那根开仓(跟回测一致)：
  - 空仓时：EMA快>慢 → 开多；mode="both" 且 EMA快<慢 → 开空
  - 持多时 EMA快<慢 → 离场；持空时 EMA快>慢 → 离场(多空版下一轮按新状态反向开仓)
  - 止损：回测里没有止损。擂台仓位计算需要止损价，这里给 4×ATR(14) 日线灾难止损，正常情况下碰不到。
    被打掉后如果状态没变，下一轮会重新进场(更接近回测里一直持有的行为)。

回测(/root/research/daily_ema_ls_20261016.py，次日开盘成交、扣费、等权)：
  币圈16个币 2019起：只做多 年化≈62–68% 回撤50.6%；多空 年化≈28–40% 回撤57%；持有 61.6%/79%。
  美股研究池52只 2017起：只做多 9.3%/15.1%；多空 -1.7%/29.3%；样本外39只：只做多 6.2%，多空 -0.4%。
  做空腿只在2022年真熊市赚钱；"EMA200下方才做空"也没改善。纸面实验，不上实盘。
"""
from __future__ import annotations

from typing import Dict, List, Optional

from strategy_engine import indicators

DEFAULT_PARAMS = {
    "fast_period": 8,
    "slow_period": 30,
    "atr_len": 14,
    "atr_stop_mult": 4.0,
    "mode": "long",  # "long" | "both"
    # 回测是全部品种等权分散持有。擂台默认每单名义约 245美元(净值×20%×5×0.245)，
    # 乘1/8≈31美元≈净值/32个品种，让风控的止损热度上限能同时容纳多个品种。
    "position_fraction": 0.125,
}


def generate_signal(bars_by_tf: Dict[str, List[dict]], params: Optional[dict] = None, position: Optional[dict] = None) -> Optional[dict]:
    bars = bars_by_tf.get("base") or []
    p = {**DEFAULT_PARAMS, **(params or {})}
    fast_n, slow_n = int(p["fast_period"]), int(p["slow_period"])
    if len(bars) < slow_n + 2:
        return None
    closes = indicators.closes(bars)
    fast = indicators.ema(closes, fast_n)
    slow = indicators.ema(closes, slow_n)
    if not fast or not slow:
        return None
    up = fast[-1] > slow[-1]
    last = bars[-1]
    price = float(last["c"])
    bar_time = int(last["t"])

    if position:
        side = str(position.get("side") or "").upper()
        if side == "LONG" and not up:
            return {"action": "CLOSE_QUICK_EXIT", "price": round(price, 6),
                    "reason": f"日线EMA{fast_n}跌到EMA{slow_n}下方，离场", "bar_time": bar_time}
        if side == "SHORT" and up:
            return {"action": "CLOSE_QUICK_EXIT", "price": round(price, 6),
                    "reason": f"日线EMA{fast_n}回到EMA{slow_n}上方，平空", "bar_time": bar_time}
        return None

    if up:
        action = "LONG"
    elif str(p.get("mode")) == "both":
        action = "SHORT"
    else:
        return None
    atr = indicators.wilder_atr(bars, int(p["atr_len"]))
    if atr <= 0:
        return None
    d = 1 if action == "LONG" else -1
    return {
        "action": action,
        "price": round(price, 6),
        "atr": round(atr, 6),
        "stop_loss": round(price - d * atr * float(p["atr_stop_mult"]), 6),
        "tier": 1,
        "position_fraction": float(p["position_fraction"]),
        "bar_time": bar_time,
        "reason": f"日线EMA{fast_n}{'>' if up else '<'}EMA{slow_n}，按状态持仓",
    }
