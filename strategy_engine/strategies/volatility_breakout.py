#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Volatility Breakout(波动率突破开盘区间)——Larry Williams公开发表的经典
日内突破系统，1987年就是靠这套战法赢下Robbins World Cup Trading
Championship(实盘年化+11376%，公开可查的真实交易冠军战绩，不是回测),
后来在《Long-Term Secrets to Short-Term Trading》里完整公开过规则。

规则(经典版)：
  - 今日突破位 = 今日开盘价 ± k×昨日振幅(昨日最高-昨日最低)
  - 价格突破上沿(收盘价 ≥ 多头突破位) → 做多；跌破下沿 → 做空
  - k(默认0.6)是Larry Williams自己反复测试过的取值区间(0.25~1.0)里的
    中位数，不是随便取的，越大越保守(触发更少但更可靠)
  - 离场：原始系统是纯日内单，隔日/下一根收盘就平——本模块用"进场后
    只要有一根新K线收盘就主动离场"复刻这个"只留一根K线"的极短持仓
    特征，止损止盈本身的触碰仍由通用runner逻辑处理(可能在离场判断
    之前就先触发)

跟本仓库其余突破类策略(海龟)的关键区别：海龟用的是"20日通道"这种慢
节奏的结构性突破，波动率突破是"单根K线内、基于前一根振幅"的极快
节奏突破——两者是"结构突破"与"波动率突破"两种不同流派的对照组。

品种覆盖：Larry Williams原始验证场景是S&P期货/商品期货这类流动性好、
日内波动足够大的品种，不特别挑资产类别，本模块跟全篮子品种一起跑，
用真实数据看这套"极快进出"的风格在加密货币上是否依然有效。

数据要求：至少需要2根bars_by_tf["base"](今天+昨天)算突破位，另加
atr_len根计算ATR安全网。本模块设计给"日线"级别用(1d)，对应经典系统
里"昨日振幅"的原始定义；本仓库24/7无休市概念，用日线K线的open/high/
low/close天然对应"开盘区间"的原始设计意图。
"""
from __future__ import annotations

from typing import Dict, List, Optional

from strategy_engine import indicators

DEFAULT_PARAMS = {
    "k": 0.6,
    "atr_len": 14,
    "atr_stop_mult": 1.5,
}


def generate_signal(bars_by_tf: Dict[str, List[dict]], params: Optional[dict] = None, position: Optional[dict] = None) -> Optional[dict]:
    bars = bars_by_tf.get("base") or []
    p = {**DEFAULT_PARAMS, **(params or {})}
    atr_len = int(p["atr_len"])
    need = atr_len + 3
    if len(bars) < need:
        return None

    last = bars[-1]
    price = float(last["c"])
    bar_time = int(last["t"])

    if position:
        # 经典系统是极短持仓(隔日就平)——只要进场那根K线已经收盘、
        # 又收出了一根新K线，就主动离场，不等结构性反转信号。
        entry_bar_time = int(position.get("entry_bar_time") or 0)
        if bar_time != entry_bar_time:
            return {
                "action": "CLOSE_QUICK_EXIT",
                "price": round(price, 6),
                "reason": "次根K线到期离场(经典系统只留一根K线的持仓周期)",
                "bar_time": bar_time,
            }
        return None

    prev = bars[-2]
    prev_range = float(prev["h"]) - float(prev["l"])
    if prev_range <= 0:
        return None
    open_now = float(last["o"])
    k = float(p["k"])
    long_level = open_now + k * prev_range
    short_level = open_now - k * prev_range

    if price >= long_level:
        action = "LONG"
    elif price <= short_level:
        action = "SHORT"
    else:
        return None

    atr = indicators.wilder_atr(bars, atr_len)
    if atr <= 0:
        return None
    direction = 1 if action == "LONG" else -1

    return {
        "action": action,
        "price": round(price, 6),
        "atr": round(atr, 6),
        "stop_loss": round(price - direction * atr * float(p["atr_stop_mult"]), 6),
        "tp1": round(price + direction * atr * 1.0, 6),
        "tp2": round(price + direction * atr * 2.0, 6),
        "tp3": round(price + direction * atr * 3.5, 6),
        "tier": 1,
        "bar_time": bar_time,
        "reason": f"开盘{open_now:.6f} k={k}×昨日振幅{prev_range:.6f} 突破位={long_level:.6f}/{short_level:.6f}",
    }
