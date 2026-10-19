#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
日线EMA8/30大级别顺势，**用正股日线算信号、在永续上执行**——2026-10-16新增，只跑美股。
跟 daily_ema8_30_long / daily_ema8_30_ls 的唯一区别：均线方向用正股(Yahoo，只算已收盘交易日)算，
不用永续自己的K线。开仓价、4×ATR灾难止损、position_fraction 全部照旧按永续价格计算。
没有上市正股的品种(OPENAI/ANTHROPIC)或 Yahoo 拉取失败时，退回用永续自己的K线(跟原版行为一样)。

起因：宝贝拿SNDK质疑收益太低。SNDK 永续04-07才上线，EMA要攒够天数才有信号，错过了690→1189；
永续24/7的周末波动也让日线均线来回打脸。回测(/root/research/stock_warm_signal_20261016.py，12只，EMA8/30)：
只做多 -5%→+11%，多空 -7%→+4%；SNDK EMA7/25 只做多 +25%→+67%。纸面实验。
"""
from __future__ import annotations

import time
from typing import Dict, List, Optional

from strategy_engine import indicators, yahoo_daily
from strategy_engine.strategies import daily_ema_trend

DEFAULT_PARAMS = dict(daily_ema_trend.DEFAULT_PARAMS)


def generate_signal(bars_by_tf: Dict[str, List[dict]], params: Optional[dict] = None, position: Optional[dict] = None) -> Optional[dict]:
    perp = bars_by_tf.get("base") or []
    p = {**DEFAULT_PARAMS, **(params or {})}
    stock = yahoo_daily.daily_for_perp(p.get("symbol"))
    if len(stock) < int(p["slow_period"]) + 2 or len(perp) < 16:
        return daily_ema_trend.generate_signal(bars_by_tf, p, position)   # 没有正股数据：跟原版一样
    closes = indicators.closes(stock)
    fast = indicators.ema(closes, int(p["fast_period"]))
    slow = indicators.ema(closes, int(p["slow_period"]))
    if not fast or not slow:
        return None
    up = fast[-1] > slow[-1]
    last = perp[-1]
    price, bar_time = float(last["c"]), int(last["t"])
    day = time.strftime("%Y-%m-%d", time.gmtime(stock[-1]["t"] / 1000))
    tag = f"正股{day}收盘 EMA{p['fast_period']}{'>' if up else '<'}EMA{p['slow_period']}"

    if position:
        side = str(position.get("side") or "").upper()
        if (side == "LONG" and not up) or (side == "SHORT" and up):
            return {"action": "CLOSE_QUICK_EXIT", "price": round(price, 6), "reason": f"{tag}，离场", "bar_time": bar_time}
        return None
    if up:
        action = "LONG"
    elif str(p.get("mode")) == "both":
        action = "SHORT"
    else:
        return None
    atr = indicators.wilder_atr(perp, int(p["atr_len"]))
    if atr <= 0:
        return None
    d = 1 if action == "LONG" else -1
    return {
        "action": action, "price": round(price, 6), "atr": round(atr, 6),
        "stop_loss": round(price - d * atr * float(p["atr_stop_mult"]), 6),
        "tier": 1, "position_fraction": float(p["position_fraction"]), "bar_time": bar_time,
        "reason": f"{tag}，按状态持仓(信号用正股)",
    }
