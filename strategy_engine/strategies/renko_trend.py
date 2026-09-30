#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Renko砖形图趋势系统——2026-09-29应宝贝要求新增，来源是公开、超过百年历史
的日式"炼瓦"图表法(西方引入后长期用于趋势过滤)，不是网红自创指标。

核心思想：不按时间画K线，按价格走了多少"砖块"画线——价格没走够一个
砖块的幅度就不产生新数据点，天然把達不到有效幅度的震荡噪音过滤掉，
只在价格真的朝一个方向走出足够距离时才推进/反转。

规则(教科书标准构造，不做改动)：
  · 砖块大小 = brick_atr_mult(默认1.5) × ATR(atr_len，默认14)
  · 顺势推进：价格朝现有方向再走满1个砖块 → 补一块同色砖，不产生新信号
    (已经在场上的仓位继续持有)
  · 反转：价格朝反方向走满2个砖块(不是1个——反转必须先把当前这块的
    整个身位吃掉，再多走一块确认，防止贴着边缘来回抖动的假反转) →
    新方向的第一块砖形成时，做多/做空
  · 离场：反向新砖出现即反手，止损另设ATR×atr_stop_mult保底

跟本仓库现有策略的关键区别：所有其余策略都是"同一批K线上算指标"，
这是本仓库第一个"先把K线重新构造成另一种时间无关的图表，再在新图表
上找信号"的思路——数据表示方式本身不同，不是同一套K线换个指标公式。

周期：4h(跟仓库其余中速趋势战法同一批"日线合理代理"选择)，砖块大小
按ATR动态算，不是固定死的绝对价格，能跨品种直接复用同一套参数。
"""
from __future__ import annotations

from typing import Dict, List, Optional

from strategy_engine import indicators

DEFAULT_PARAMS = {
    "atr_len": 14,
    "brick_atr_mult": 1.5,
    "atr_stop_mult": 2.0,
}


def _build_renko(closes: List[float], brick_size: float) -> List[int]:
    """标准close-based renko构造：返回按时间顺序排列的砖块方向列表
    (+1=涨砖, -1=跌砖)。顺势只需1个砖块幅度，反转需要2个砖块幅度
    (先吃掉当前砖块的整个身位，再多走一块才算真反转)。"""
    bricks: List[int] = []
    if not closes or brick_size <= 0:
        return bricks
    anchor = closes[0]
    direction = 0
    for c in closes[1:]:
        diff = c - anchor
        if direction == 0:
            if abs(diff) >= brick_size:
                n = int(abs(diff) // brick_size)
                d = 1 if diff > 0 else -1
                bricks.extend([d] * n)
                anchor += d * brick_size * n
                direction = d
        elif direction == 1:
            if diff >= brick_size:
                n = int(diff // brick_size)
                bricks.extend([1] * n)
                anchor += brick_size * n
            elif diff <= -2 * brick_size:
                n = int((-diff) // brick_size)
                bricks.extend([-1] * n)
                anchor += -brick_size * n
                direction = -1
        else:
            if diff <= -brick_size:
                n = int((-diff) // brick_size)
                bricks.extend([-1] * n)
                anchor += -brick_size * n
            elif diff >= 2 * brick_size:
                n = int(diff // brick_size)
                bricks.extend([1] * n)
                anchor += brick_size * n
                direction = 1
    return bricks


def generate_signal(bars_by_tf: Dict[str, List[dict]], params: Optional[dict] = None, position: Optional[dict] = None) -> Optional[dict]:
    p = {**DEFAULT_PARAMS, **(params or {})}
    bars = bars_by_tf.get("base") or []
    atr_len = int(p["atr_len"])
    need = atr_len + 150
    if len(bars) < need:
        return None

    cs = indicators.closes(bars)
    atr_ser = indicators.atr_series(bars, atr_len)
    if len(atr_ser) < 2:
        return None
    atr = float(atr_ser[-1])
    brick_size = atr * float(p["brick_atr_mult"])
    if brick_size <= 0:
        return None

    established = _build_renko(cs[:-1], brick_size)
    last_established_dir = established[-1] if established else 0

    full = _build_renko(cs, brick_size)
    if len(full) <= len(established):
        return None  # 这根K线没有让价格走完一整个新砖块
    new_dir = full[len(established)]

    last = bars[-1]
    price = float(last["c"])
    bar_time = int(last["t"])

    if position:
        side = str(position.get("side") or "").upper()
        if side == "LONG" and new_dir == -1:
            return {"action": "CLOSE_QUICK_EXIT", "price": round(price, 6),
                    "reason": "renko新砖转跌", "bar_time": bar_time}
        if side == "SHORT" and new_dir == 1:
            return {"action": "CLOSE_QUICK_EXIT", "price": round(price, 6),
                    "reason": "renko新砖转涨", "bar_time": bar_time}
        return None

    if last_established_dir != 0 and new_dir == last_established_dir:
        return None  # 顺势延续砖，已经在场上的仓位继续持有，不重复开新仓
    if new_dir == 0:
        return None

    d = new_dir
    return {
        "action": "LONG" if d == 1 else "SHORT",
        "price": round(price, 6), "atr": round(atr, 6),
        "stop_loss": round(price - d * atr * float(p["atr_stop_mult"]), 6),
        "tier": 1, "bar_time": bar_time,
        "reason": f"renko砖块反转({'转涨' if d == 1 else '转跌'}) brick_size={brick_size:.6g}",
    }
