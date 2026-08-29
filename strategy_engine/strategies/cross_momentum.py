#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
跨品种动量因子——Jegadeesh & Titman(1993)学术动量因子的经典公开研究，
几十年跨市场、跨资产类别复现过的真实异象，不是技术分析流派的经验之谈。
核心思想跟本仓库其余所有策略都不一样：其余策略清一色是"看这一个品种自己
的K线形态/指标"，这个策略反过来看"这个品种相对于整个持仓篮子里其它
品种，涨跌快慢排第几"——本账户本来就有18个品种的篮子(加密+黄金系+
代币化股票)，天然适合做相对强弱排序，单品种技术流派做不到这一点。

规则：
  - 每个品种算lookback_bars(默认按小时算，20根4h=约3.3天)动量 =
    (close_now / close_lookback_bars_ago - 1)
  - 全篮子按动量排序，top_frac(默认最强25%)做多，bottom_frac(默认最弱
    25%)做空，中间部分不操作
  - 离场：排名跌出多头前top_frac区间(平多)/涨出空头后bottom_frac区间
    (平空)，即"相对强弱关系变了就离场"，不是看这一个品种自己的止损/
    止盈价——不过为了适配通用runner框架，仍然提供ATR止损作为安全网。

跟本仓库其余策略的接口差异：本策略需要"篮子里所有品种此刻的动量排名"，
单品种的bars_by_tf信息不够用。约定：调用方(多策略并行runner)每个tick
统一算一次全篮子动量，通过params["universe_returns"] = {symbol: 动量值}
整体喂进来；本模块用全局NEEDS_UNIVERSE=True标记这个需求，供runner识别
要不要做这一步预处理。如果调用方没有提供universe_returns(比如被单品种
的backtest_runner.py直接调用)，本策略无法评估，直接返回None——这是
诚实的局限，不是bug，这个策略天生不是单品种回测框架能独立跑通的。
"""
from __future__ import annotations

from typing import Dict, List, Optional

from strategy_engine import indicators

NEEDS_UNIVERSE = True  # 供多策略并行runner识别：调用前需要准备好params["universe_returns"]

DEFAULT_PARAMS = {
    "lookback_bars": 20,
    "top_frac": 0.25,
    "bottom_frac": 0.25,
    "atr_len": 14,
    "atr_stop_mult": 2.5,
    "min_universe": 6,  # 篮子里参与排名的品种数太少，排名意义不大，直接不评估
}


def _rank_bucket(symbol: str, universe_returns: Dict[str, float], top_frac: float, bottom_frac: float):
    """返回 'top' / 'bottom' / 'mid' / None(数据不足或symbol不在榜里)。"""
    if symbol not in universe_returns or len(universe_returns) < 2:
        return None
    ranked = sorted(universe_returns.items(), key=lambda kv: kv[1], reverse=True)
    n = len(ranked)
    top_n = max(1, int(round(n * top_frac)))
    bottom_n = max(1, int(round(n * bottom_frac)))
    top_symbols = {s for s, _ in ranked[:top_n]}
    bottom_symbols = {s for s, _ in ranked[-bottom_n:]}
    if symbol in top_symbols:
        return "top"
    if symbol in bottom_symbols:
        return "bottom"
    return "mid"


def generate_signal(bars_by_tf: Dict[str, List[dict]], params: Optional[dict] = None, position: Optional[dict] = None) -> Optional[dict]:
    bars = bars_by_tf.get("base") or []
    p = {**DEFAULT_PARAMS, **(params or {})}
    universe_returns = (params or {}).get("universe_returns") or {}
    symbol = (params or {}).get("symbol") or ""
    if not symbol or len(universe_returns) < int(p["min_universe"]):
        return None  # 榜单没喂进来，或篮子太小，诚实放弃评估

    atr_len = int(p["atr_len"])
    if len(bars) < atr_len + 2:
        return None

    last = bars[-1]
    price = float(last["c"])
    bar_time = int(last["t"])
    bucket = _rank_bucket(symbol, universe_returns, float(p["top_frac"]), float(p["bottom_frac"]))
    if bucket is None:
        return None

    if position:
        side = str(position.get("side") or "").upper()
        if side == "LONG" and bucket != "top":
            return {
                "action": "CLOSE_QUICK_EXIT",
                "price": round(price, 6),
                "reason": f"动量排名跌出榜单前{p['top_frac']*100:.0f}%",
                "bar_time": bar_time,
            }
        if side == "SHORT" and bucket != "bottom":
            return {
                "action": "CLOSE_QUICK_EXIT",
                "price": round(price, 6),
                "reason": f"动量排名回升出榜单后{p['bottom_frac']*100:.0f}%",
                "bar_time": bar_time,
            }
        return None

    if bucket == "top":
        action = "LONG"
    elif bucket == "bottom":
        action = "SHORT"
    else:
        return None

    atr = indicators.wilder_atr(bars, atr_len)
    if atr <= 0:
        return None
    direction = 1 if action == "LONG" else -1
    own_ret = universe_returns.get(symbol, 0.0)

    return {
        "action": action,
        "price": round(price, 6),
        "atr": round(atr, 6),
        "stop_loss": round(price - direction * atr * float(p["atr_stop_mult"]), 6),
        "tp1": round(price + direction * atr * 1.2, 6),
        "tp2": round(price + direction * atr * 2.2, 6),
        "tp3": round(price + direction * atr * 3.5, 6),
        "tier": 1,
        "bar_time": bar_time,
        "reason": f"篮子动量排名={bucket} 自身{p['lookback_bars']}根动量={own_ret:+.4f}",
    }
