#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Raschke ADX回踩系统("Holy Grail")——Linda Raschke与Larry Connors合著
《Street Smarts: High Probability Short-Term Trading Strategies》(1996)
公开发表的战法之一，Raschke本人是真实注册过的商品交易顾问(CTA)、公开
可查战绩的职业交易员，不是网红自创。

核心思想：ADX很高说明当前是罕见的强趋势行情，强趋势里价格回踩到短期
均线附近往往是介入良机而不是反转信号——"强趋势 + 回踩到20均线 + 重新
往趋势方向走"三件事同时发生，才是这套战法真正要等的入场点，不是
"ADX高就一直做"。

规则：
  - 方向：用+DI/-DI判断(ADX本身只讲强度不讲方向)，+DI>-DI偏多，反之偏空
  - 强度门槛：ADX(14) > adx_threshold(默认30，比adx_regime_switch的
    trend_adx=25更严格——Raschke原文用的门槛本来就比教科书标准的25更高，
    要求"非常强"的趋势才做)
  - 回踩：最近pullback_window(默认4)根内，最低价(做多时)触碰或跌破
    EMA(20)——确认真的发生了一次短线回调，不是一路直奔没有回调过
  - 触发：价格收盘重新站上EMA(20)(这一根站上、上一根还在下方——"夺回"
    这个事件，不是"站上就一直持有")
  - 离场：价格重新跌破EMA(20)(回踩支撑丢了，Raschke原文的退出规则)

跟本仓库其余ADX类战法的关键区别：adx_regime_switch用ADX做"趋势市/
震荡市"二分状态开关，趋势市里走的是EMA(10/30)金叉死叉；这套只在ADX
**非常高**的单一区间里工作(不管震荡市，也不像supertrend_adx那样只要
过了一个较低的门槛(20)就行)，且不是均线交叉触发、是"强趋势+回踩+夺回
短均线"的组合事件触发，是本擂台里唯一一套"趋势确认+回踩确认"双重门槛
都用ADX/DI体系(而不是像mtf_ema_pullback那样用多周期结构)实现的战法。

周期选择理由：4H，跟adx_regime_switch同周期，两者都是"ADX主导判断"的
战法，同周期跑更方便横向比较"用ADX的不同方式，谁更好"。

数据要求：ADX(14)至少需要period*2+2根起算才有值，EMA(20)+
pullback_window也要够，取较大者+atr_len缓冲。
"""
from __future__ import annotations

from typing import Dict, List, Optional

from strategy_engine import indicators

DEFAULT_PARAMS = {
    "adx_len": 14,
    "adx_threshold": 30.0,
    "ema_len": 20,
    "pullback_window": 4,
    "atr_len": 14,
    "atr_stop_mult": 2.0,
}


def generate_signal(bars_by_tf: Dict[str, List[dict]], params: Optional[dict] = None, position: Optional[dict] = None) -> Optional[dict]:
    bars = bars_by_tf.get("base") or []
    p = {**DEFAULT_PARAMS, **(params or {})}
    adx_len = int(p["adx_len"])
    ema_len = int(p["ema_len"])
    pb_window = int(p["pullback_window"])
    atr_len = int(p["atr_len"])
    need = max(adx_len * 2 + 2, ema_len + pb_window) + atr_len + 4
    if len(bars) < need:
        return None

    cs = indicators.closes(bars)
    ema20 = indicators.ema(cs, ema_len)
    if len(ema20) < pb_window + 2:
        return None
    adx_now, plus_di, minus_di = indicators.wilder_adx_di(bars, adx_len)

    last = bars[-1]
    price = float(last["c"])
    bar_time = int(last["t"])
    ema_now, ema_prev = ema20[-1], ema20[-2]

    if position:
        side = str(position.get("side") or "").upper()
        if side == "LONG" and price < ema_now:
            return {
                "action": "CLOSE_QUICK_EXIT", "price": round(price, 6),
                "reason": f"重新跌破EMA{ema_len}({ema_now:.6f})，回踩支撑丢失", "bar_time": bar_time,
            }
        if side == "SHORT" and price > ema_now:
            return {
                "action": "CLOSE_QUICK_EXIT", "price": round(price, 6),
                "reason": f"重新站上EMA{ema_len}({ema_now:.6f})，回踩压力丢失", "bar_time": bar_time,
            }
        return None

    if adx_now <= float(p["adx_threshold"]):
        return None

    recent_bars = bars[-1 - pb_window:-1]
    recent_ema = ema20[-1 - pb_window:-1]
    prev_price = cs[-2]

    if plus_di > minus_di:
        touched = any(float(b["l"]) <= e for b, e in zip(recent_bars, recent_ema))
        reclaim = price >= ema_now and prev_price < ema_prev
        if not (touched and reclaim):
            return None
        action, d = "LONG", 1
    elif minus_di > plus_di:
        touched = any(float(b["h"]) >= e for b, e in zip(recent_bars, recent_ema))
        reclaim = price <= ema_now and prev_price > ema_prev
        if not (touched and reclaim):
            return None
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
        "reason": f"ADX={adx_now:.1f}>{p['adx_threshold']}强趋势+回踩EMA{ema_len}后夺回"
                  f"(+DI={plus_di:.1f}/-DI={minus_di:.1f})",
    }
