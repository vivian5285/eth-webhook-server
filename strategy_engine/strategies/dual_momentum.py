#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Dual Momentum(双重动量)——Gary Antonacci在《Dual Momentum Investing》
(2014年出版，公开发表)提出，把"相对动量"(篮子里谁涨得快)和"绝对动量"
(这个品种自己是不是真的在涨)结合起来的经典配置系统，是本仓库里
`cross_momentum`(纯相对动量)的直接对照组——两者用完全相同的排名机制，
唯一差异就是要不要多加"自身趋势方向"这道过滤。

规则：
  - 相对动量：跟cross_momentum同一套——篮子里全部品种按lookback_bars
    动量排名，最强top_frac(默认25%)进候选多头池，最弱bottom_frac
    (默认25%)进候选空头池
  - 绝对动量过滤(Dual Momentum真正的核心贡献，Antonacci原书里对应
    "跟无风险利率比较"，本仓库没有无风险资产可比，adapt成"品种自己
    这段lookback的收益率是不是真的同号")：候选多头池里，只有自身
    动量>0(真的在涨，不是"篮子更烂的衬托")才真正做多；候选空头池
    同理，只有自身动量<0才真正做空。候选池里但自身动量不同号的，
    按Antonacci原书的逻辑应该是"空仓/换避险资产"，本仓库没有避险
    资产可换，直接不开仓。
  - 离场：排名跌出候选池，或者自身动量符号反转(两个条件任一触发都
    离场，跟入场的双重门槛对称)

跟cross_momentum预期会怎么分化：cross_momentum在普遍下跌的篮子里，
也会去"矮子里拔将军"做多那个跌得最少的（哪怕它自己其实也在跌）；
Dual Momentum会拒绝这种仓位，情愿空仓等真正双向确认的信号——用真实
数据看这道额外过滤到底是"减少了假信号"还是"错过了真实机会"，这正是
这套战法存在的意义(Antonacci原书的核心论点是"多一道绝对动量过滤，
能显著降低大回撤")。

跟本仓库其余策略的接口差异：跟cross_momentum同款，需要"篮子里所有
品种此刻的动量排名"，用全局NEEDS_UNIVERSE=True标记。
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
    "min_universe": 6,
}


def _rank_bucket(symbol: str, universe_returns: Dict[str, float], top_frac: float, bottom_frac: float):
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
        return None

    atr_len = int(p["atr_len"])
    if len(bars) < atr_len + 2:
        return None

    last = bars[-1]
    price = float(last["c"])
    bar_time = int(last["t"])
    bucket = _rank_bucket(symbol, universe_returns, float(p["top_frac"]), float(p["bottom_frac"]))
    own_ret = universe_returns.get(symbol, 0.0)
    if bucket is None:
        return None

    # 双重动量的核心：候选池(相对排名)之外，还要求自身动量同号(绝对动量)
    if position:
        side = str(position.get("side") or "").upper()
        if side == "LONG" and (bucket != "top" or own_ret <= 0):
            return {
                "action": "CLOSE_QUICK_EXIT",
                "price": round(price, 6),
                "reason": f"跌出候选池或自身动量转负(排名={bucket} 自身动量={own_ret:+.4f})",
                "bar_time": bar_time,
            }
        if side == "SHORT" and (bucket != "bottom" or own_ret >= 0):
            return {
                "action": "CLOSE_QUICK_EXIT",
                "price": round(price, 6),
                "reason": f"回升出候选池或自身动量转正(排名={bucket} 自身动量={own_ret:+.4f})",
                "bar_time": bar_time,
            }
        return None

    if bucket == "top" and own_ret > 0:
        action = "LONG"
    elif bucket == "bottom" and own_ret < 0:
        action = "SHORT"
    else:
        return None  # 候选池里但自身动量不同号——Dual Momentum拒绝这种"矮子里拔将军"的信号

    atr = indicators.wilder_atr(bars, atr_len)
    if atr <= 0:
        return None
    direction = 1 if action == "LONG" else -1

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
        "reason": f"相对动量={bucket} 且自身{p['lookback_bars']}根动量同号={own_ret:+.4f}(双重确认)",
    }
