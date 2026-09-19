#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Clenow动量排名——Andreas Clenow《Stocks on the Move: Beating the Market
with Hedge Fund Momentum Strategies》(2015年公开出版)。作者是真实基金
经理(ACIES Asset Management)，这套排名公式是他书里"动量分数"的原版
定义，公开可查，不是网红私有指标：

    动量分 = 年化(指数回归斜率) × R²

把过去lookback_bars根K线的ln(close)做线性回归：
  - 回归斜率年化后 ≈ "如果按最近这段的轨迹一直走下去，一年能涨/跌百分之几"
  - R²(拟合优度，0~1) 衡量这段走势沿趋势线走得干不干净——同样的斜率，
    走得越平滑(噪音越小)R²越高
  - 两者相乘：既要涨得快，也要涨得干净，单纯暴涨但天天大幅震荡的品种
    R²低，分数会被打下来

跟本擂台已有的'cross_momentum'(v2版本用ATR%做波动率标准化)是同一个
"给动量排名加波动率修正"思路的另一种公开实现——cross_momentum_v2的
ATR标准化是我们自己按论文思路简化拼的，这套是Clenow原书的完整定义
(回归斜率+拟合优度，比单纯除以ATR%更严谨)，两者并排跑用真实数据看
谁的排名质量更高，不是主观替换。

规则：
  - 全篮子按动量分排序，top_frac(默认25%)做多，bottom_frac(默认25%)
    做空，中间不操作(跟cross_momentum同款分桶逻辑，含进出场缓冲带)。
  - 离场：排名跌出候选池，另配ATR止损安全网。
  - Clenow原书还有"低于200日均线不做多、高于200日均线不做空"的大盘
    过滤(用SPY指数)，本仓库没有加密货币的"大盘指数"概念，诚实地不做
    这道过滤——如实说明局限，不是漏做。

周期/回看窗口：Clenow原书用日线+90个交易日。本仓库注册在4h上(见
comparison_roster.py)、lookback_bars=90(直接用原书数字，不是重新拍)，
年化倍数按4h的真实每年根数换算(365×6)，不是套股票的252天惯例。
"""
from __future__ import annotations

from typing import Dict, List, Optional

from strategy_engine import indicators

NEEDS_UNIVERSE = True  # 供多策略并行runner识别：调用前需要准备好params["universe_returns"]

DEFAULT_PARAMS = {
    "lookback_bars": 90,
    "top_frac": 0.25,
    "bottom_frac": 0.25,
    "exit_top_frac": 0.45,
    "exit_bottom_frac": 0.45,
    "atr_len": 14,
    "atr_stop_mult": 2.5,
    "min_universe": 6,
    "use_fixed_tp": True,
}


def _rank_bucket(
    symbol: str, universe_returns: Dict[str, float], top_frac: float, bottom_frac: float,
    exit_top_frac: Optional[float] = None, exit_bottom_frac: Optional[float] = None,
    currently: Optional[str] = None,
):
    if symbol not in universe_returns or len(universe_returns) < 2:
        return None
    ranked = sorted(universe_returns.items(), key=lambda kv: kv[1], reverse=True)
    n = len(ranked)
    eff_top = top_frac
    eff_bottom = bottom_frac
    if currently == "LONG" and exit_top_frac is not None:
        eff_top = max(top_frac, float(exit_top_frac))
    if currently == "SHORT" and exit_bottom_frac is not None:
        eff_bottom = max(bottom_frac, float(exit_bottom_frac))
    top_n = max(1, int(round(n * eff_top)))
    bottom_n = max(1, int(round(n * eff_bottom)))
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
        return None

    atr_len = int(p["atr_len"])
    if len(bars) < atr_len + 2:
        return None

    last = bars[-1]
    price = float(last["c"])
    bar_time = int(last["t"])

    if position:
        side = str(position.get("side") or "").upper()
        bucket = _rank_bucket(
            symbol, universe_returns, float(p["top_frac"]), float(p["bottom_frac"]),
            exit_top_frac=p.get("exit_top_frac"), exit_bottom_frac=p.get("exit_bottom_frac"),
            currently=side,
        )
        if bucket is None:
            return None
        if side == "LONG" and bucket != "top":
            return {"action": "CLOSE_QUICK_EXIT", "price": round(price, 6),
                    "reason": "Clenow动量分排名跌出候选池", "bar_time": bar_time}
        if side == "SHORT" and bucket != "bottom":
            return {"action": "CLOSE_QUICK_EXIT", "price": round(price, 6),
                    "reason": "Clenow动量分排名回升出候选池", "bar_time": bar_time}
        return None

    bucket = _rank_bucket(symbol, universe_returns, float(p["top_frac"]), float(p["bottom_frac"]))
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
    score = universe_returns.get(symbol, 0.0)

    use_tp = bool(p.get("use_fixed_tp", True))
    sig = {
        "action": action,
        "price": round(price, 6),
        "atr": round(atr, 6),
        "stop_loss": round(price - direction * atr * float(p["atr_stop_mult"]), 6),
        "tier": 1,
        "bar_time": bar_time,
        "reason": f"Clenow动量分={bucket}({score:+.3f}=年化回归斜率×R2)"
                  + ("" if use_tp else "(无固定止盈,持有到跌出候选池)"),
    }
    if use_tp:
        sig["tp1"] = round(price + direction * atr * 1.2, 6)
        sig["tp2"] = round(price + direction * atr * 2.2, 6)
        sig["tp3"] = round(price + direction * atr * 3.5, 6)
    return sig
