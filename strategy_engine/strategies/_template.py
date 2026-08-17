#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
占位示例策略：双EMA交叉 + ATR止损/止盈。

这不是任何品种的真实策略——真实策略要等用户提供每个品种在 TradingView 上
实际跑的 Pine 脚本逻辑后，逐个品种替换成真实复刻。这份模板存在的意义是
证明"拉K线→算指标→出信号"整条链路是通的，同时给后续真实策略一个统一的
接口范式照抄。

接口约定见 strategies/__init__.py 顶部注释：
    generate_signal(bars_by_tf: dict[str, list[dict]], params: dict, position: dict | None) -> dict | None

这个占位策略不需要多周期数据、也不需要持仓状态（纯粹看最新收盘K线的双EMA
是否刚穿越），只用 bars_by_tf["base"]，position 参数接收但不使用。
"""
from __future__ import annotations

from typing import Dict, List, Optional

from strategy_engine import indicators

DEFAULT_PARAMS = {
    "fast_len": 12,
    "slow_len": 26,
    "atr_len": 14,
    "atr_stop_mult": 1.5,
    "atr_tp1_mult": 1.0,
    "atr_tp2_mult": 2.0,
    "atr_tp3_mult": 3.5,
}


def generate_signal(bars_by_tf: Dict[str, List[dict]], params: Optional[dict] = None, position: Optional[dict] = None) -> Optional[dict]:
    bars = bars_by_tf.get("base") or []
    p = {**DEFAULT_PARAMS, **(params or {})}
    fast_len, slow_len, atr_len = int(p["fast_len"]), int(p["slow_len"]), int(p["atr_len"])
    need = max(fast_len, slow_len, atr_len) + 2
    if len(bars) < need:
        return None

    cs = indicators.closes(bars)
    fast = indicators.ema(cs, fast_len)
    slow = indicators.ema(cs, slow_len)
    if len(fast) < 2 or len(slow) < 2:
        return None

    # ema() 输出比输入短（少了热身段），对齐到最后两个点做"是否刚穿越"判断
    f_prev, f_curr = fast[-2], fast[-1]
    s_prev, s_curr = slow[-2], slow[-1]

    crossed_up = f_prev <= s_prev and f_curr > s_curr
    crossed_down = f_prev >= s_prev and f_curr < s_curr
    if not crossed_up and not crossed_down:
        return None

    atr = indicators.wilder_atr(bars, atr_len)
    if atr <= 0:
        return None

    last = bars[-1]
    price = float(last["c"])
    action = "LONG" if crossed_up else "SHORT"
    direction = 1 if action == "LONG" else -1

    return {
        "action": action,
        "price": round(price, 6),
        "atr": round(atr, 6),
        "stop_loss": round(price - direction * atr * p["atr_stop_mult"], 6),
        "tp1": round(price + direction * atr * p["atr_tp1_mult"], 6),
        "tp2": round(price + direction * atr * p["atr_tp2_mult"], 6),
        "tp3": round(price + direction * atr * p["atr_tp3_mult"], 6),
        "tier": 1,
        "bar_time": int(last["t"]),
    }
