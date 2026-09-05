#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
利弗莫尔关键点突破(Livermore's Pivotal Point Breakout)——Jesse Livermore，
史上最具名的真实交易员之一，1929年美股大崩盘真实做空获利上亿美元(公开
可考证的历史记录)，本人口述出版《股票作手回忆录》(Reminiscences of a
Stock Operator, 1923，后世公认的交易经典)，"关键点"(Pivotal Point)是
他书里反复强调的核心概念。

核心思想：Livermore反复讲的一个观察——大行情很少是一条直线拉到底，
中途会有"自然反应"(natural reaction)：价格经过一段明确的大动作之后，
会先停下来在窄幅区间里整理消化，这个停顿本身不说明趋势结束，往往是
"关键点"——如果价格随后朝着原来的方向重新突破这个整理区间，说明大
行情还没走完，是加仓/进场的信号；他本人的真实交易记录里(公开出版的
1907年及1929年做空案例)反复用的就是这套"等停顿、等确认突破"的方法，
不是追第一时间的突破。

规则(本模块的可复现实现)：
  - 前期大动作：trend_lookback(默认20)根内，涨跌幅绝对值 ≥ trend_
    min_pct(默认8%)，方向决定后续只允许同向突破(这是Livermore方法
    区别于普通突破戦法的关键——不追反转，只追**趋势延续**)
  - 停顿：紧接着pause_period(默认8)根内，价格区间宽度 ≤ pause_max_
    width_pct(默认6%)，确认这是真的"停下来"不是趋势已经逆转
  - 突破：收盘价朝原趋势方向突破这段停顿区间的边界 → 进场(同向做多/
    做空，反趋势方向的假设不成立时直接跳过，不做)
  - 离场：价格跌回/涨回停顿区间内部(结构失效)，或ATR止损兜底

跟本仓库其余突破/结构类战法的关键区别：breakout_retest是"突破后回踩
再确认"，不管突破之前是不是已经有大动作；darvas_box只要求箱体本身
够窄、不要求箱体之前有一段"够猛"的前置行情；这套**同时要求两件事**
——前面必须先有一段真正的大动作(不是随便一个小区间突破都算)，后面
必须先有一段真正安静的停顿(不是随便一根阳线就追)，只在"大动作+真
停顿+同向突破"三件事叠加时才进场，是本擂台里唯一一套明确要求"入场
前必须已经存在明确前置趋势"的突破类战法。

周期选择理由：4h，跟同批结构类战法(darvas_box/breakout_retest)同
周期，trend_lookback+pause_period合计约28根×4h≈4.7天，是"大动作+
消化"这个节奏在加密市场的合理压缩(原始设计是股票日线/周线上的月度
量级节奏)。

数据要求：至少trend_lookback+pause_period+atr_len+4根bars_by_tf["base"]。
"""
from __future__ import annotations

from typing import Dict, List, Optional

from strategy_engine import indicators

DEFAULT_PARAMS = {
    "trend_lookback": 20,
    "trend_min_pct": 8.0,
    "pause_period": 8,
    "pause_max_width_pct": 6.0,
    "atr_len": 14,
    "atr_stop_mult": 2.0,
}


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def generate_signal(bars_by_tf: Dict[str, List[dict]], params: Optional[dict] = None, position: Optional[dict] = None) -> Optional[dict]:
    bars = bars_by_tf.get("base") or []
    p = {**DEFAULT_PARAMS, **(params or {})}
    trend_lb = int(p["trend_lookback"])
    pause_period = int(p["pause_period"])
    atr_len = int(p["atr_len"])
    need = trend_lb + pause_period + atr_len + 4
    if len(bars) < need:
        return None

    n = len(bars)
    # 停顿窗口：排除当前这根，最近pause_period根
    pause_end = n - 1
    pause_start = pause_end - pause_period
    pause_window = bars[pause_start:pause_end]
    pause_top = max(_f(b["h"]) for b in pause_window)
    pause_bottom = min(_f(b["l"]) for b in pause_window)
    if pause_top <= 0:
        return None
    pause_width_pct = (pause_top - pause_bottom) / pause_top * 100.0

    last = bars[-1]
    price = _f(last["c"])
    bar_time = int(last["t"])

    if position:
        side = str(position.get("side") or "").upper()
        if side == "LONG" and pause_bottom <= price <= pause_top:
            return {
                "action": "CLOSE_QUICK_EXIT", "price": round(price, 6),
                "reason": f"跌回停顿区间[{pause_bottom:.6f},{pause_top:.6f}]，结构失效", "bar_time": bar_time,
            }
        if side == "SHORT" and pause_bottom <= price <= pause_top:
            return {
                "action": "CLOSE_QUICK_EXIT", "price": round(price, 6),
                "reason": f"涨回停顿区间[{pause_bottom:.6f},{pause_top:.6f}]，结构失效", "bar_time": bar_time,
            }
        return None

    if pause_width_pct > float(p["pause_max_width_pct"]):
        return None  # 停顿不够窄，不算真的"停下来"

    # 前置大动作：停顿窗口之前的trend_lookback根，涨跌幅
    trend_end = pause_start
    trend_start = trend_end - trend_lb
    if trend_start < 0:
        return None
    trend_open_price = _f(bars[trend_start]["c"])
    trend_close_price = _f(bars[trend_end - 1]["c"]) if trend_end > trend_start else trend_open_price
    if trend_open_price <= 0:
        return None
    trend_pct = (trend_close_price - trend_open_price) / trend_open_price * 100.0
    min_pct = float(p["trend_min_pct"])

    if trend_pct >= min_pct:
        prior_direction = 1
    elif trend_pct <= -min_pct:
        prior_direction = -1
    else:
        return None  # 前面没有明确的大动作，不是Livermore说的"关键点"场景

    if prior_direction == 1 and price > pause_top:
        action, d = "LONG", 1
    elif prior_direction == -1 and price < pause_bottom:
        action, d = "SHORT", -1
    else:
        return None  # 只做同向延续突破，不追反趋势方向

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
        "reason": f"前置大动作{trend_pct:+.1f}%+停顿[{pause_bottom:.6f},{pause_top:.6f}]"
                  f"(宽度{pause_width_pct:.1f}%)后同向突破",
    }
