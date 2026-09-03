#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
EMA快慢线金叉死叉——2026-09-04应宝贝要求新增。宝贝原话："其实我观察这么多
指标，em和ema最有效，7和30，的快慢线，金叉死叉"。

双EMA交叉是技术分析里最古老、最公开的趋势跟随系统之一，没有单一发明人可
考证归属(不像turtle_breakout/connors_rsi2那样有明确的公开发表人/文献)，
但规则本身极简、几十年来在各类图表软件/教材里都是标准配置，符合本擂台
"公开、可复现，不是网红自创黑箱指标"这条准入门槛——规则本身透明到任何人
拿收盘价都能手算复现，这正是它被选入的理由。

规则：
  - EMA(7) 上穿 EMA(30) = 金叉 → 开多
  - EMA(7) 下穿 EMA(30) = 死叉 → 开空
  - 持仓中，出现反向交叉 → 主动离场(不等止损/止盈)，下一轮再判断是否
    立即反向开仓——这是多空交替系统的标准做法，也是"7/30"这种慢速交叉
    组合公开教材里的标准离场规则(用反向信号离场，不额外发明退出逻辑)。
  - 止损：加一条ATR止损纯粹是执行层的风险管理需要(整仓模型必须有点数字
    防止极端行情爆仓)，不是"7/30金叉死叉"本身规则的一部分——原始系统
    完全靠反向交叉离场，本身没有止损；这里用ATR(14)×2.5是本仓库其它
    趋势类策略(turtle_breakout用2×ATR(20))同一数量级的保守取值，交叉本身
    没有像海龟通道那样天然的止损位可用。

周期：4H——同turtle_breakout/bollinger_squeeze同一批"4H在加密货币上是
日线合理代理"的既定选择(见comparison_roster.py顶部注释)，7/30根K线的
EMA在4H上大约对应1.2天/5天的平滑窗口，是"快慢线"这个概念在中周期上
该有的尺度；日线级别(1d)会让7/30显得太钝(约1周/1个月才交叉一次，样本
积累太慢)，1H又太碎(噪音多、假交叉频繁)，4H是折中。

数据要求：EMA(30)至少需要30根K线才能算出种子值，为了能判断"这一根是
不是新出现的交叉"(不是每根都在同一侧，需要对比上一根)，至少要有31根
才能取到两个连续的EMA点。
"""
from __future__ import annotations

from typing import Dict, List, Optional

from strategy_engine import indicators

DEFAULT_PARAMS = {
    "fast_period": 7,
    "slow_period": 30,
    "atr_len": 14,
    "atr_stop_mult": 2.5,
}


def generate_signal(bars_by_tf: Dict[str, List[dict]], params: Optional[dict] = None, position: Optional[dict] = None) -> Optional[dict]:
    bars = bars_by_tf.get("base") or []
    p = {**DEFAULT_PARAMS, **(params or {})}
    fast_n = int(p["fast_period"])
    slow_n = int(p["slow_period"])
    atr_len = int(p["atr_len"])
    need = slow_n + 2
    if len(bars) < need:
        return None

    closes = indicators.closes(bars)
    fast = indicators.ema(closes, fast_n)
    slow = indicators.ema(closes, slow_n)
    # ema()返回的序列长度取决于各自period，起点不同，只有两条序列都至少
    # 有2个点才能对比"这一根 vs 上一根"来判定是不是新出现的交叉。
    if len(fast) < 2 or len(slow) < 2:
        return None

    # 两条序列长度不同(fast序列比slow序列长slow_n-fast_n个点，因为EMA从
    # period-1开始才有值)，但两者末尾都对齐到同一根最新K线，取负索引即可，
    # 不需要额外算偏移量。
    fast_prev, fast_curr = fast[-2], fast[-1]
    slow_prev, slow_curr = slow[-2], slow[-1]

    last = bars[-1]
    price = float(last["c"])
    bar_time = int(last["t"])

    golden_cross = fast_prev <= slow_prev and fast_curr > slow_curr
    death_cross = fast_prev >= slow_prev and fast_curr < slow_curr

    if position:
        side = str(position.get("side") or "").upper()
        if side == "LONG" and death_cross:
            return {
                "action": "CLOSE_QUICK_EXIT",
                "price": round(price, 6),
                "reason": f"EMA{fast_n}下穿EMA{slow_n}死叉离场",
                "bar_time": bar_time,
            }
        if side == "SHORT" and golden_cross:
            return {
                "action": "CLOSE_QUICK_EXIT",
                "price": round(price, 6),
                "reason": f"EMA{fast_n}上穿EMA{slow_n}金叉离场",
                "bar_time": bar_time,
            }
        return None

    if not golden_cross and not death_cross:
        return None

    action = "LONG" if golden_cross else "SHORT"
    atr = indicators.wilder_atr(bars, atr_len)
    if atr <= 0:
        return None
    direction = 1 if action == "LONG" else -1

    return {
        "action": action,
        "price": round(price, 6),
        "atr": round(atr, 6),
        "stop_loss": round(price - direction * atr * float(p["atr_stop_mult"]), 6),
        # 刻意不返回tp1/tp2/tp3：原始系统靠反向交叉离场，不设固定止盈，
        # 让趋势充分展开——跟turtle_breakout同一个理由(见该模块2026-09-02
        # 修正注释：固定止盈会把每个赢家在小目标位就封顶，反而伤了系统
        # 本该吃到的大趋势尾部)。
        "tier": 1,
        "bar_time": bar_time,
    }
