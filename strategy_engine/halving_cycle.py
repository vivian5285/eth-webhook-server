#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
BTC 减半周期阶段——2026-10-17新增(宝贝："btc还有4年减半的牛市和牛市后的熊市，这都是可以提前抓的行情预判")。

减半日期是事先公开的，所以阶段划分不用未来数据。阶段按"距上一次减半的月数"：
  0–18个月 = 牛市阶段 → 只做多
  18–30个月 = 熊市阶段 → 只做空
  30–48个月 = 筑底阶段 → 多空都做
历史上：2017-12、2021-11、2025-10 见顶都在减半后约18个月；2018-12、2022-11 见底在约30个月。
回测(/root/research/halving_cycle_20261017.py，主流5个日线EMA8/30+波动率3%+BTC方向，2017起)：
收益/回撤 2.12 → 3.11，胜率 36%→45%。只有约2.5个周期、阶段边界参照历史顶底定的，样本内，要持续验证。
下一次减半日期是估计值(约2028-04)，真正发生后要在 HALVINGS 里补上实际日期。
"""
from __future__ import annotations

import calendar
import time
from typing import Optional

HALVINGS = [
    calendar.timegm((2012, 11, 28, 0, 0, 0)),
    calendar.timegm((2016, 7, 9, 0, 0, 0)),
    calendar.timegm((2020, 5, 11, 0, 0, 0)),
    calendar.timegm((2024, 4, 19, 0, 0, 0)),
]
BULL_MONTHS = 18
BEAR_MONTHS = 30


def months_since_halving(ts_sec: Optional[float] = None) -> float:
    t = time.time() if ts_sec is None else float(ts_sec)
    last = max(h for h in HALVINGS if h <= t)
    return (t - last) / (86400 * 30.44)


def phase(ts_sec: Optional[float] = None) -> str:
    """'bull' / 'bear' / 'base'(筑底)"""
    m = months_since_halving(ts_sec)
    if m < BULL_MONTHS:
        return "bull"
    if m < BEAR_MONTHS:
        return "bear"
    return "base"


def allows(action: str, ts_sec: Optional[float] = None) -> bool:
    """牛市阶段不开空、熊市阶段不开多；筑底阶段都允许。"""
    ph = phase(ts_sec)
    if ph == "bull" and action == "SHORT":
        return False
    if ph == "bear" and action == "LONG":
        return False
    return True
