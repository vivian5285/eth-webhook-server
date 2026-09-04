#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
达瓦斯箱形突破(Darvas Box)——Nicolas Darvas公开出版《我如何在股市赚了200万》
(How I Made $2,000,000 in the Stock Market，1960)记录的真实交易战绩：
18个月靠这套战法把$36,000做到$2,000,000，是公开可考证的真实实盘案例，
不是回测截图。

核心思想：股价创出新高后，如果接下来若干根K线都没有再创新高、也没有
跌破某个低点，说明多空在这个价格区间"箱体"里达成了短期共识；一旦价格
再次向上突破箱体上沿，说明共识被打破、新一轮上涨启动，止损放在箱体下沿
——"新高不追、等箱体确认、突破箱体再进"。

规则(本模块的可复现实现，简化自 Darvas 原始的手工箱体识别法)：
  - 箱体上沿 = box_period 根K线内的最高高点，且这个高点在其后
    confirm_bars 根收盘价里都没有被突破(说明这个高点已经"稳定"了，
    不是刚创出来的假突破)
  - 箱体下沿 = 同一段窗口内的最低低点
  - 箱体宽度(上沿-下沿)/上沿 必须 <= max_box_width_pct，太宽不算真正的
    箱体整理，只是普通波动
  - 突破：当前收盘价 > 箱体上沿 → 做多，止损放箱体下沿；做空对称
    (跌破箱体下沿)
  - 离场：Darvas 原始做法是"新箱体在更高处形成后，把止损上移到新箱体
    下沿，箱体越堆越高、止损跟着走"——本模块按同样思路实现：持仓期间
    如果又出现了一个下沿更高(多头)/上沿更低(空头)的新箱体，一旦价格
    跌破这个新箱体的边界就主动离场；最初入场时的箱体边界(=初始止损)
    交给通用runner的止损逻辑处理。

跟本仓库其余突破类战法的关键区别：turtle_breakout 用 Donchian 通道
(单纯"N日最高/最低")判断突破，没有"箱体要够窄、要稳定住"这个额外要求；
breakout_retest 是"突破后回踩到突破位附近再进"；这套是"必须先有一个
稳定收窄的箱体、突破箱体本身"——箱体宽度过滤本身就是一种波动率压缩
确认，介于两者之间但机制不同。

周期选择理由：4h，跟 turtle_breakout/ema_cross_7_30 同一批"4h是加密货币
日线合理代理"的既定选择——箱体整理在Darvas原始的股票日线上通常持续
数天到数周，4h上 box_period(20)+confirm_bars(3) ≈ 23根×4h ≈ 3.8天，
是这个"整理后突破"节奏在更快加密市场里的合理压缩。

数据要求：至少 box_period+confirm_bars+atr_len+4 根 bars_by_tf["base"]。
"""
from __future__ import annotations

from typing import Dict, List, Optional

from strategy_engine import indicators

DEFAULT_PARAMS = {
    "box_period": 20,
    "confirm_bars": 3,
    "max_box_width_pct": 15.0,
    "atr_len": 14,
}


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def _latest_box(bars: List[dict], box_period: int, confirm_bars: int, max_width: float):
    """在最近的历史里找一个"已经稳定住"的箱体：box_period根之前那个窗口
    的最高/最低点，且随后confirm_bars根(不含当前这根)收盘价都没有突破
    这个高点或跌破这个低点。找不到合格箱体返回(None, None)。"""
    n = len(bars)
    window_end = n - 1 - confirm_bars
    window_start = window_end - box_period
    if window_start < 0:
        return None, None
    window = bars[window_start:window_end]
    top = max(_f(b["h"]) for b in window)
    bottom = min(_f(b["l"]) for b in window)
    if top <= 0 or top <= bottom:
        return None, None
    if (top - bottom) / top > max_width:
        return None, None
    hold = bars[window_end:n - 1]
    for b in hold:
        c = _f(b["c"])
        if c > top or c < bottom:
            return None, None
    return top, bottom


def generate_signal(bars_by_tf: Dict[str, List[dict]], params: Optional[dict] = None, position: Optional[dict] = None) -> Optional[dict]:
    bars = bars_by_tf.get("base") or []
    p = {**DEFAULT_PARAMS, **(params or {})}
    box_period = int(p["box_period"])
    confirm_bars = int(p["confirm_bars"])
    max_width = float(p["max_box_width_pct"]) / 100.0
    atr_len = int(p["atr_len"])
    need = box_period + confirm_bars + atr_len + 4
    if len(bars) < need:
        return None

    last = bars[-1]
    price = _f(last["c"])
    bar_time = int(last["t"])

    if position:
        side = str(position.get("side") or "").upper()
        new_top, new_bottom = _latest_box(bars, box_period, confirm_bars, max_width)
        if new_top is not None:
            entry_price = _f(position.get("entry_price"))
            if side == "LONG" and new_bottom > entry_price and price < new_bottom:
                return {
                    "action": "CLOSE_QUICK_EXIT", "price": round(price, 6),
                    "reason": f"跌破更高处新箱体下沿({new_bottom:.6f})，箱体阶梯止损上移后触发",
                    "bar_time": bar_time,
                }
            if side == "SHORT" and new_top < entry_price and price > new_top:
                return {
                    "action": "CLOSE_QUICK_EXIT", "price": round(price, 6),
                    "reason": f"突破更低处新箱体上沿({new_top:.6f})，箱体阶梯止损下移后触发",
                    "bar_time": bar_time,
                }
        return None

    top, bottom = _latest_box(bars, box_period, confirm_bars, max_width)
    if top is None:
        return None

    if price > top:
        action, d = "LONG", 1
        stop = bottom
    elif price < bottom:
        action, d = "SHORT", -1
        stop = top
    else:
        return None

    atr = indicators.wilder_atr(bars, atr_len)
    if atr <= 0:
        return None

    return {
        "action": action,
        "price": round(price, 6),
        "atr": round(atr, 6),
        "stop_loss": round(stop, 6),
        "tier": 1,
        "bar_time": bar_time,
        "reason": f"突破箱体[{bottom:.6f},{top:.6f}]",
    }
