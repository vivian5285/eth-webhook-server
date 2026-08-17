#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
占位示例策略：双EMA交叉 + ATR止损/止盈。

这不是任何品种的真实策略——真实策略要等用户提供每个品种在 TradingView 上
实际跑的 Pine 脚本逻辑后，逐个品种替换成真实复刻。这份模板存在的意义是
证明"拉K线→算指标→出信号"整条链路是通的，同时给后续真实策略一个统一的
接口范式照抄。

接口约定（所有策略模块必须实现）：
    generate_signal(bars: list[dict], params: dict) -> dict | None

- bars：strategy_engine.klines.get_bars() 的输出，按时间升序、全部已收盘，
  最后一根 bars[-1] 就是"当前最新已收盘K线"。
- params：symbol_registry.py 里给这个品种配的参数字典。
- 无状态设计：策略每次都拿完整历史重新判断"最新收盘的这根K线是否触发信号"，
  不依赖外部保存的状态——重启/回测/实盘用同一份逻辑，行为不会因为"有没有
  记住上次状态"而分裂成两套。
- 返回 None 表示这根K线没有新信号；返回 dict 时字段形状故意跟 TV webhook
  payload 同构（action/price/atr/tp1/tp2/tp3/stop_loss/tier/bar_time），
  方便复用现有的信号展示/去重/对比逻辑。
"""
from __future__ import annotations

from typing import List, Optional

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


def generate_signal(bars: List[dict], params: Optional[dict] = None) -> Optional[dict]:
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
