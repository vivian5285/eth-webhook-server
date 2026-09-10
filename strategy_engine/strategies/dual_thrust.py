#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DualThrust——Michael Chalek 在 1980 年代提出、公开发表的日内区间突破系统，
后来在中国期货量化圈被大量文章/回测复现讨论，规则完全透明、任何人拿
K线都能手算复现，符合本擂台准入线。

规则：
  - Range = max(HH - LC, HC - LL)，其中 HH/LL/HC/LC 是最近 n_days 根**已
    收盘日线**的最高价/最低价/最高收盘/最低收盘。
  - 当日基准 = 当前 UTC 自然日第一根 base K线的开盘价。
  - 买线 BuyLine = 当日开盘 + K1 × Range
    卖线 SellLine = 当日开盘 - K2 × Range
    (K1=K2=0.5 是最常见的对称设置)
  - base K线收盘价 > BuyLine → 做多；< SellLine → 做空。
  - 反手：DualThrust 经典就是 stop-and-reverse——持多头时价格跌破 SellLine
    直接反手做空，持空头时突破 BuyLine 反手做多。跟 donchian_reversal /
    parabolic_sar_flip 一样是"永远持仓反手"类型，反向线本身就是止损；
    本仓库另外统一加一道 ATR 止损兜底(不是原始规则，是对"反向线迟迟
    不破、单向敞口失控"的统一处理)。
  - one_entry_per_day(默认 True)：同一个 UTC 日、同一个方向只进一次——
    如果当天更早的 base K线已经收盘越过同一侧的线，就不再重复进，避免
    在线附近反复抽刷时刷出一堆交易(跟 opening_range_breakout 的"只认
    当日第一次突破"同一个思路)。

⚠️ "当日开盘"锚点的诚实说明：DualThrust 原本是有真实开盘收盘的期货
日内系统。加密货币 24/7 没有"开盘"，这里用 UTC 00:00 作当日起点是
人为约定(跟 opening_range_breakout / vwap_mean_reversion 的 UTC 锚点
同一个局限)，写在这里不藏着。

跟本仓库其余突破类战法的关键区别：
  - volatility_breakout(Larry Williams)：突破位 = 今日开盘 ± k×**昨日
    单日振幅**(一个数)；DualThrust 的 Range 是"n 日里 HH-LC 和 HC-LL
    两个跨度取大的那个"，且 K1/K2 可以不对称，是不同的具名公式。
  - opening_range_breakout(Toby Crabel)：区间 = 开盘后前 30 分钟自己的
    高低点(只用今天开盘后的信息)；DualThrust 用的是过去 n 天的信息。
  - turtle_breakout：滚动 Donchian 通道、不锚定"当日开盘"。

周期选择理由：base=1h(要在日内足够多的判定点上抓到价格穿线的那一根，
1h 比 4h 更贴 DualThrust 的日内属性、又不像 15m 那样噪音太大)，
mtf=["1d"] 提供 n 日 Range。

数据要求：1d 至少 n_days+2 根；base 至少 atr_len+一整天的 K线数+4。
"""
from __future__ import annotations

from typing import Dict, List, Optional

from strategy_engine import indicators

_DAY_MS = 86_400_000

DEFAULT_PARAMS = {
    "n_days": 4,
    "k1": 0.5,
    "k2": 0.5,
    "one_entry_per_day": True,
    "atr_len": 14,
    "atr_stop_mult": 2.5,
}


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def generate_signal(bars_by_tf: Dict[str, List[dict]], params: Optional[dict] = None, position: Optional[dict] = None) -> Optional[dict]:
    p = {**DEFAULT_PARAMS, **(params or {})}
    base = bars_by_tf.get("base") or []
    daily = bars_by_tf.get("1d") or []
    n = int(p["n_days"])
    atr_len = int(p["atr_len"])

    if len(daily) < n + 2 or len(base) < atr_len + 30:
        return None

    win = daily[-n:]
    hh = max(_f(b["h"]) for b in win)
    ll = min(_f(b["l"]) for b in win)
    hc = max(_f(b["c"]) for b in win)
    lc = min(_f(b["c"]) for b in win)
    rng = max(hh - lc, hc - ll)
    if rng <= 0:
        return None

    last = base[-1]
    price = _f(last["c"])
    bar_time = int(last["t"])
    day_start = (bar_time // _DAY_MS) * _DAY_MS

    today_open = None
    for b in base:
        if int(b["t"]) >= day_start:
            today_open = _f(b["o"])
            break
    if today_open is None or today_open <= 0:
        return None

    buy_line = today_open + float(p["k1"]) * rng
    sell_line = today_open - float(p["k2"]) * rng

    if position:
        side = str(position.get("side") or "").upper()
        if side == "LONG" and price < sell_line:
            return {"action": "CLOSE_QUICK_EXIT", "price": round(price, 6),
                    "reason": f"跌破DualThrust卖线({sell_line:.6f})，反手", "bar_time": bar_time}
        if side == "SHORT" and price > buy_line:
            return {"action": "CLOSE_QUICK_EXIT", "price": round(price, 6),
                    "reason": f"突破DualThrust买线({buy_line:.6f})，反手", "bar_time": bar_time}
        return None

    if price > buy_line:
        action, d, line = "LONG", 1, buy_line
    elif price < sell_line:
        action, d, line = "SHORT", -1, sell_line
    else:
        return None

    if p.get("one_entry_per_day", True):
        earlier_today = [b for b in base if day_start <= int(b["t"]) < bar_time]
        if action == "LONG" and any(_f(b["c"]) > buy_line for b in earlier_today):
            return None  # 今天已经有K线收盘越过买线了，不重复进
        if action == "SHORT" and any(_f(b["c"]) < sell_line for b in earlier_today):
            return None

    atr = indicators.wilder_atr(base, atr_len)
    if atr <= 0:
        return None

    return {
        "action": action,
        "price": round(price, 6),
        "atr": round(atr, 6),
        "stop_loss": round(price - d * atr * float(p["atr_stop_mult"]), 6),
        "tier": 1,
        "bar_time": bar_time,
        "reason": (f"DualThrust {'突破买线' if d > 0 else '跌破卖线'}({line:.6f}) "
                   f"开{today_open:.6f} Range{rng:.6f}(K1={p['k1']}/K2={p['k2']},{n}日)"),
    }
