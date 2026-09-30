#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
EMA7/30 纯裸K微结构突破——2026-09-30照搬宝贝转发的DeepSeek TradingView
Pine脚本("EMA7 & EMA30 纯裸K微结构突破 (极简VPS对接版)")逐字复刻，用来
先在Python这边把因子回测一遍再考虑要不要接实盘/擂台。

跟仓库里已有的两套"EMA7"相关战法都不是一回事，注意区分：
  - ema_cross_7_30.py：纯金叉死叉(EMA7上穿/下穿EMA30才动作)，反向交叉
    离场，没有K线形态/量能/突破过滤，也没有"持仓期间用反向信号同根
    反手"这个机制(是等下一根收盘价确认后才反向开新仓)。
  - 擂台里的dual_ema_band_7_25(2026-09-29新增)：价格同时站上/跌破两条
    EMA(7/25)，不要求K线方向、不要求实体大小、不要求突破前高前低。

这个Pine脚本比前两个都更严格——四个条件全部同时成立才开仓：
  1. EMA快线方向(emaFastUp/Down，EMA7自己在往上/往下走，不是横盘)
  2. K线颜色跟方向一致(isBullish/isBearish，阳线开多、阴线开空)
  3. 收盘价同时站上/跌破两条EMA(跟dual_ema_band共享这一条)
  4. 实体够大(bodySize >= ATR*0.2，过滤十字星/无效小实体)
  5. 突破前5根K线的最高高点/最低低点(经典微结构突破确认，不是纯均线
     信号，要"真的破了新高/新低"才算数)

宝贝原话提到"双均线7和25"，但转发的这份Pine源码里 slowLen 的input默认
值实际是30(不是25)——这里严格照抄源码的30，不代入宝贝口头说的25，避免
"我以为是25"跟"代码实际跑的是30"这种口径不一致的坑；如果宝贝后续确认
想测25，再另外加一组参数对比。

平仓+反手用同一根K线原子完成，跟Pine脚本第5节的alert打包逻辑一致：
持有多头时若shortCondition成立，直接返回SHORT信号(不是CLOSE)，交给
调用方(backtest_runner.py/多策略引擎)在同一步里"平多、开空"；只有
"没有反向信号，但站不住双均线了"这种弱离场，才返回纯CLOSE。
"""
from __future__ import annotations

from typing import Dict, List, Optional

from strategy_engine import indicators

DEFAULT_PARAMS = {
    "fast_period": 7,
    "slow_period": 30,
    "atr_len": 14,
    "atr_stop_mult": 2.5,
    "breakout_bars": 5,
    "body_atr_mult": 0.2,
}


def generate_signal(bars_by_tf: Dict[str, List[dict]], params: Optional[dict] = None, position: Optional[dict] = None) -> Optional[dict]:
    bars = bars_by_tf.get("base") or []
    p = {**DEFAULT_PARAMS, **(params or {})}
    fast_n = int(p["fast_period"])
    slow_n = int(p["slow_period"])
    atr_len = int(p["atr_len"])
    breakout_n = int(p["breakout_bars"])
    body_mult = float(p["body_atr_mult"])

    need = max(slow_n + 2, atr_len + 2, breakout_n + 2)
    if len(bars) < need:
        return None

    closes = indicators.closes(bars)
    fast = indicators.ema(closes, fast_n)
    slow = indicators.ema(closes, slow_n)
    if len(fast) < 2 or len(slow) < 2:
        return None

    donchian = indicators.donchian_high_low(bars, breakout_n)
    if not donchian:
        return None

    atr_ser = indicators.atr_series(bars, atr_len)
    if not atr_ser:
        return None

    last = bars[-1]
    price = float(last["c"])
    o = float(last["o"])
    bar_time = int(last["t"])
    atr_now = float(atr_ser[-1])
    if atr_now <= 0:
        return None

    fast_curr, fast_prev = fast[-1], fast[-2]
    slow_curr = slow[-1]
    hh, ll = donchian[-1]

    ema_fast_up = fast_curr > fast_prev
    ema_fast_down = fast_curr < fast_prev
    is_bullish = price > o
    is_bearish = price < o
    body_ok = abs(price - o) >= atr_now * body_mult

    long_condition = (
        ema_fast_up and is_bullish and price > fast_curr and price > slow_curr
        and body_ok and price > hh
    )
    short_condition = (
        ema_fast_down and is_bearish and price < fast_curr and price < slow_curr
        and body_ok and price < ll
    )
    close_long_condition = price < fast_curr and price < slow_curr
    close_short_condition = price > fast_curr and price > slow_curr

    if position:
        side = str(position.get("side") or "").upper()
        direction = 1 if side == "SHORT" else -1  # 反手时新仓的方向
        if side == "LONG":
            if short_condition:
                return {
                    "action": "SHORT",
                    "price": round(price, 6),
                    "atr": round(atr_now, 6),
                    "stop_loss": round(price + atr_now * float(p["atr_stop_mult"]), 6),
                    "reason": "反转平多开空(微结构突破)",
                    "tier": 1,
                    "bar_time": bar_time,
                }
            if close_long_condition:
                return {
                    "action": "CLOSE_QUICK_EXIT",
                    "price": round(price, 6),
                    "reason": "跌破双均线平多",
                    "bar_time": bar_time,
                }
            return None
        if side == "SHORT":
            if long_condition:
                return {
                    "action": "LONG",
                    "price": round(price, 6),
                    "atr": round(atr_now, 6),
                    "stop_loss": round(price - atr_now * float(p["atr_stop_mult"]), 6),
                    "reason": "反转平空开多(微结构突破)",
                    "tier": 1,
                    "bar_time": bar_time,
                }
            if close_short_condition:
                return {
                    "action": "CLOSE_QUICK_EXIT",
                    "price": round(price, 6),
                    "reason": "站上双均线平空",
                    "bar_time": bar_time,
                }
            return None
        return None

    if not long_condition and not short_condition:
        return None

    action = "LONG" if long_condition else "SHORT"
    direction = 1 if action == "LONG" else -1
    return {
        "action": action,
        "price": round(price, 6),
        "atr": round(atr_now, 6),
        "stop_loss": round(price - direction * atr_now * float(p["atr_stop_mult"]), 6),
        "tier": 1,
        "bar_time": bar_time,
    }
