#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tsmom_agile —— time_series_momentum 的"手术版"。2026-09-10 宝贝诊断出原版
四个结构性毛病，逐条对症下药，新开一个对照选手（原版 time_series_momentum
不动，留作基准）。

原版（1d / 20 根收益符号）的病 → 本版的手术：
  1. **滞后**：只看"现价 vs 20 天前"，UNI 那种短周期早翻空、20 天维度还在涨
     的品种会死扛反向仓。
     → **双周期动量必须同向**：20 根方向(mom_window) 与 5 根方向(fast_window)
       一致才允许持有；持仓期间 5 根动量一反向就立刻走，不等 20 根翻符号。
  2. **全品种梭哈 25 仓、相关敞口爆炸**。
     → **趋势强度门**：只在 |20根收益| / (日ATR% × √20) ≥ strength_min(1.0)
       时进场——这是把累计收益按"随机游走该有的漂移幅度"标准化后的 t 值，
       弱趋势/纯震荡的品种直接不碰，自然只留几个最干净的趋势。
  3. **硬止损 10~20%，比 5x 强平线(~19.6%)还远，真跌先爆仓**。
     → **止损硬性封顶**：stop_dist = min(ATR×倍数, 入场价 × max_stop_frac(0.08))，
       永远够在强平前触发。
  4. **翻转慢**（等 20 根累计收益变号才离场）。
     → **快出场**：5 根动量反向 或 收盘穿越 EMA(ema_len=10) 逆势即离场；
       20 根符号反转只作为兜底。
  另：沿用 time_series_momentum_v2 的教训——**不设固定止盈**，让利润跑到
      上面的快出场信号。

目标是"敏捷 + 风控形状"（更少的仓、更近的止损、更快的翻转），胜率提高是
筛掉模糊信号的顺带结果，不是把它当调参目标。

单品种，1d 周期，跑全 _ALL_SYMBOLS。接口同 strategies/__init__.py 约定。
"""
from __future__ import annotations

import math
from typing import Dict, List, Optional

from strategy_engine import indicators

DEFAULT_PARAMS = {
    "mom_window": 20,
    "fast_window": 5,
    "ema_len": 10,
    "strength_min": 1.0,
    "atr_len": 14,
    "atr_stop_mult": 2.0,
    "max_stop_frac": 0.08,   # 止损距离上限（占入场价比例），必须 < 5x 强平线 ~0.196
}


def generate_signal(bars_by_tf: Dict[str, List[dict]], params: Optional[dict] = None, position: Optional[dict] = None) -> Optional[dict]:
    p = {**DEFAULT_PARAMS, **(params or {})}
    bars = bars_by_tf.get("base") or []
    mw, fw = int(p["mom_window"]), int(p["fast_window"])
    atr_len = int(p["atr_len"])
    ema_len = int(p["ema_len"])
    if len(bars) < mw + atr_len + max(ema_len, 5) + 5:
        return None

    last = bars[-1]
    price = float(last["c"])
    bar_time = int(last["t"])
    c_mom = float(bars[-1 - mw]["c"])
    c_fast = float(bars[-1 - fw]["c"])
    if c_mom <= 0 or c_fast <= 0:
        return None
    ret_mom = price / c_mom - 1.0
    ret_fast = price / c_fast - 1.0

    atr = indicators.wilder_atr(bars, atr_len)
    if atr <= 0 or price <= 0:
        return None
    atr_pct = atr / price
    cs = indicators.closes(bars)
    ema = indicators.ema(cs, ema_len)
    if not ema:
        return None
    ema_now = ema[-1]

    dir_mom = 1 if ret_mom > 0 else (-1 if ret_mom < 0 else 0)
    dir_fast = 1 if ret_fast > 0 else (-1 if ret_fast < 0 else 0)
    strength = abs(ret_mom) / (atr_pct * math.sqrt(mw)) if atr_pct > 0 else 0.0

    # ── 持仓：快出场（5根动量反向 / 穿越EMA10）+ 20根符号反转兜底 ──
    if position:
        side = str(position.get("side") or "").upper()
        if side == "LONG":
            if ret_fast < 0 or price < ema_now:
                return {"action": "CLOSE_QUICK_EXIT", "price": round(price, 6),
                        "reason": f"快出场:5根动量{ret_fast:+.2%}转负/跌破EMA{ema_len}", "bar_time": bar_time}
            if ret_mom <= 0:
                return {"action": "CLOSE_QUICK_EXIT", "price": round(price, 6),
                        "reason": f"20根动量翻空({ret_mom:+.2%})", "bar_time": bar_time}
        elif side == "SHORT":
            if ret_fast > 0 or price > ema_now:
                return {"action": "CLOSE_QUICK_EXIT", "price": round(price, 6),
                        "reason": f"快出场:5根动量{ret_fast:+.2%}转正/升破EMA{ema_len}", "bar_time": bar_time}
            if ret_mom >= 0:
                return {"action": "CLOSE_QUICK_EXIT", "price": round(price, 6),
                        "reason": f"20根动量翻多({ret_mom:+.2%})", "bar_time": bar_time}
        return None

    # ── 空仓：双周期同向 + 强度门 + EMA 站位 ──────────────────────────
    if dir_mom == 0 or dir_mom != dir_fast or strength < float(p["strength_min"]):
        return None
    d = dir_mom
    if d == 1 and price <= ema_now:
        return None
    if d == -1 and price >= ema_now:
        return None

    stop_dist = min(atr * float(p["atr_stop_mult"]), price * float(p["max_stop_frac"]))
    if stop_dist <= 0:
        return None
    stop = price - d * stop_dist
    return {
        "action": "LONG" if d == 1 else "SHORT",
        "price": round(price, 6),
        "atr": round(atr, 6),
        "stop_loss": round(stop, 6),
        "tier": 2 if strength >= 2 * float(p["strength_min"]) else 1,
        "bar_time": bar_time,
        "reason": (f"20根{ret_mom:+.1%}&5根{ret_fast:+.1%}同向 强度{strength:.2f} "
                   f"止损{stop_dist / price * 100:.1f}%(封顶{p['max_stop_frac']*100:.0f}%)"),
    }
