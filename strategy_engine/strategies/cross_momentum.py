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
    # 2026-09-10 新增，默认 True = 原行为不变。True 发 tp1/tp2/tp3(1.2/2.2/3.5
    # 倍 ATR 固定止盈)，runner 摸到 tp1 就平——但这会把赢家封在 +1.2R、亏家
    # 却能跑到 -2.5R 止损，结构性倒挂(跟 turtle_breakout 2026-09-02 修的是
    # 同一个病)。False 则不发 tp，只靠"排名跌出榜单"+ATR 止损离场，让利润跑。
    # cross_momentum_runwin 用 params 传 False 做单变量对照。
    "use_fixed_tp": True,
    # 2026-09-19新增(cross_momentum_v2对照实验，宝贝要求)：
    # exit_top_frac/exit_bottom_frac —— 原版进场阈值(top_frac=25%)跟离场
    # 阈值是同一条线，排名在25%边界附近来回抖一格就反复开平仓，产生一批
    # 接近零盈亏的噪音交易，拖累胜率。None=沿用旧行为(离场阈值=进场阈值)；
    # 传一个比top_frac更宽的值(比如0.45)，进场要求前25%强，但只要还留在
    # 前45%就不平仓，给排名噪音留缓冲带，参考指数编制"缓冲区"惯例。
    "exit_top_frac": None,
    "exit_bottom_frac": None,
    # use_ema_direction_filter：宝贝要求——additionally require EMA(ema_fast_len)
    # 相对 EMA(ema_slow_len) 的站上/跌破方向，跟动量排名方向一致才真正开仓。
    # 排名进前25%只代表"篮子内相对最强"，不保证这个品种自己的均线结构是
    # 多头排列——加一道自身趋势方向确认，过滤"篮子里矮子拔将军"式的入场。
    "use_ema_direction_filter": False,
    "ema_fast_len": 7,
    "ema_slow_len": 25,
}


def _rank_bucket(
    symbol: str, universe_returns: Dict[str, float], top_frac: float, bottom_frac: float,
    exit_top_frac: Optional[float] = None, exit_bottom_frac: Optional[float] = None,
    currently: Optional[str] = None,
):
    """返回 'top' / 'bottom' / 'mid' / None(数据不足或symbol不在榜里)。

    currently传"LONG"/"SHORT"时，用更宽的exit_top_frac/exit_bottom_frac
    (缓冲带)判断是否还留在榜单里，不传则用原版的进出同阈值行为(逐字
    兼容旧版本)——两个新参数都不传时，这个函数跟旧版本完全等价。"""
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
        return None  # 榜单没喂进来，或篮子太小，诚实放弃评估

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
            exit_frac = p.get("exit_top_frac") or p["top_frac"]
            return {
                "action": "CLOSE_QUICK_EXIT",
                "price": round(price, 6),
                "reason": f"动量排名跌出榜单前{exit_frac*100:.0f}%",
                "bar_time": bar_time,
            }
        if side == "SHORT" and bucket != "bottom":
            exit_frac = p.get("exit_bottom_frac") or p["bottom_frac"]
            return {
                "action": "CLOSE_QUICK_EXIT",
                "price": round(price, 6),
                "reason": f"动量排名回升出榜单后{exit_frac*100:.0f}%",
                "bar_time": bar_time,
            }
        return None

    bucket = _rank_bucket(symbol, universe_returns, float(p["top_frac"]), float(p["bottom_frac"]))
    if bucket == "top":
        action = "LONG"
    elif bucket == "bottom":
        action = "SHORT"
    else:
        return None

    # 2026-09-19新增(宝贝要求)：额外要求EMA(7)相对EMA(25)的站上/跌破方向
    # 跟动量排名方向一致——排名前25%只代表"篮子内相对最强"，不保证这个
    # 品种自己的均线结构是多头排列，加一道自身趋势确认过滤"矮子里拔将军"
    # 式的入场。
    if bool(p.get("use_ema_direction_filter")):
        closes = indicators.closes(bars)
        ema_f = indicators.ema(closes, int(p["ema_fast_len"]))
        ema_s = indicators.ema(closes, int(p["ema_slow_len"]))
        if not ema_f or not ema_s:
            return None
        if action == "LONG" and not (ema_f[-1] > ema_s[-1]):
            return None
        if action == "SHORT" and not (ema_f[-1] < ema_s[-1]):
            return None

    atr = indicators.wilder_atr(bars, atr_len)
    if atr <= 0:
        return None
    direction = 1 if action == "LONG" else -1
    own_ret = universe_returns.get(symbol, 0.0)

    use_tp = bool(p.get("use_fixed_tp", True))
    sig = {
        "action": action,
        "price": round(price, 6),
        "atr": round(atr, 6),
        "stop_loss": round(price - direction * atr * float(p["atr_stop_mult"]), 6),
        "tier": 1,
        "bar_time": bar_time,
        "reason": f"篮子动量排名={bucket} 自身{p['lookback_bars']}根动量={own_ret:+.4f}"
                  + ("" if use_tp else "(无固定止盈,持有到跌出榜单)"),
    }
    if use_tp:
        sig["tp1"] = round(price + direction * atr * 1.2, 6)
        sig["tp2"] = round(price + direction * atr * 2.2, 6)
        sig["tp3"] = round(price + direction * atr * 3.5, 6)
    return sig
