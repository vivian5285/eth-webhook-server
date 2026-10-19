#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
残差动量·截面多空对冲(Residual Momentum, cross-sectional long/short) —— 2026-10-06 新增(纸面)。

背景(2026-10-05样本外研究，43个币/日线/2020-08~2026-10，前半选参、后半验证)：
  · 现行"逐币残差动量"(每个币各自阈值开仓)在大币池上几乎没有优势：夏普前半-0.04/后半+0.12；
  · 全品种按"残差动量分数"排名、做多最强一批+做空最弱一批、每H天再平衡的截面多空版，整个参数
    网格几乎全部为正：前半选参→后半夏普 +1.41→+1.37(BTC因子)、+1.45→+1.28(BTC+ETH因子)；
  · 保留意见：①赚钱主要在多头腿，空头腿单独看亏钱(主要起对冲大盘作用)；②2022熊市与2026年偏弱；
    ③有一部分优势来自较新的币(幸存者偏差)；④研究没算资金费率——擂台PnL里含资金费，正好补这个缺口。
本策略就是把它放进擂台做纸面验证，不上实盘。

规则(每个币各自一条"身份"，由runner按UNIVERSE_ROSTER逐币调用)：
  · 1d K线。只在"再平衡日"(日序号 % H == 0)评估；其它日子返回None(持仓不动)。
  · 全篮子每个币算残差动量分数：对最近reg(120)日对数收益回归市场因子(BTC，或BTC+ETH)，取最近mom日残差和
    ÷(残差标准差×√mom)；篮子里可评估品种<8个则不评估。BTC自己是因子，不参与。
  · 分数最高的 q(0.33)比例做多，最低的 q 比例做空；已持仓的币：仍在对应一侧→继续持有；掉出→平仓。
  · 只带灾难性止损(5×ATR，封顶30%)当安全网，不做任何其它离场(研究里就是这样回放的)。
"""
from __future__ import annotations

import math
import time
from typing import Dict, List, Optional

from strategy_engine import indicators

NEEDS_UNIVERSE = True

DEFAULT_PARAMS = {
    "factor": "BTC",        # BTC / BTCETH
    "mom": 20,
    "reg": 120,
    "H": 10,                # 每H天再平衡
    "q": 0.33,
    "min_universe": 8,
    "atr_len": 14,
    "atr_stop_mult": 5.0,
    "max_stop_frac": 0.30,
    "factor_symbols": ("BTCUSDT", "ETHUSDT"),
}

_BARS_TTL = 1800.0
_bars_cache: dict = {}      # symbol -> (ts, bars)
_score_cache: dict = {}     # (factor, mom, reg, cutoff) -> (ts, {symbol: score})


def _daily_bars(symbol: str, need: int) -> List[dict]:
    hit = _bars_cache.get(symbol)
    now = time.time()
    if hit and now - hit[0] < _BARS_TTL and len(hit[1]) >= need:
        return hit[1]
    try:
        from strategy_engine import klines
        bars = klines.get_bars(symbol, "1d", max(need + 20, 300))
    except Exception:
        return hit[1] if hit else []
    if bars:
        _bars_cache[symbol] = (now, bars)
        return bars
    return hit[1] if hit else []


def _logret(bars: List[dict], cutoff: int) -> Dict[int, float]:
    out = {}
    for i in range(1, len(bars)):
        t = int(bars[i]["t"])
        if t > cutoff:
            break
        p0, p1 = float(bars[i - 1]["c"]), float(bars[i]["c"])
        if p0 > 0 and p1 > 0:
            out[t] = math.log(p1 / p0)
    return out


def _solve(a: List[List[float]], b: List[float]) -> Optional[List[float]]:
    n = len(b)
    m = [row[:] + [b[i]] for i, row in enumerate(a)]
    for c in range(n):
        piv = max(range(c, n), key=lambda r: abs(m[r][c]))
        if abs(m[piv][c]) < 1e-18:
            return None
        m[c], m[piv] = m[piv], m[c]
        for r in range(n):
            if r != c:
                f = m[r][c] / m[c][c]
                for k in range(c, n + 1):
                    m[r][k] -= f * m[c][k]
    return [m[i][n] / m[i][i] for i in range(n)]


def _score(ra: Dict[int, float], facs: List[Dict[int, float]], reg: int, mom: int) -> Optional[float]:
    common = sorted(t for t in ra if all(t in f for f in facs))
    if len(common) < reg:
        return None
    common = common[-reg:]
    y = [ra[t] for t in common]
    xs = [[f[t] for t in common] for f in facs]
    n = len(y)
    k = len(xs)
    # 带截距的OLS(正规方程)
    cols = [[1.0] * n] + xs
    a = [[sum(cols[i][r] * cols[j][r] for r in range(n)) for j in range(k + 1)] for i in range(k + 1)]
    b = [sum(cols[i][r] * y[r] for r in range(n)) for i in range(k + 1)]
    beta = _solve(a, b)
    if beta is None:
        return None
    eps = [y[r] - sum(beta[i] * cols[i][r] for i in range(k + 1)) for r in range(n)]
    mu = sum(eps) / n
    sd = (sum((e - mu) ** 2 for e in eps) / n) ** 0.5
    if sd <= 0:
        return None
    return sum(eps[-mom:]) / (sd * math.sqrt(mom))


def _scores(symbols: List[str], cutoff: int, p: dict) -> Dict[str, float]:
    key = (p["factor"], int(p["mom"]), int(p["reg"]), tuple(sorted(symbols)), cutoff)
    hit = _score_cache.get(key)
    now = time.time()
    if hit and now - hit[0] < 3600:
        return hit[1]
    reg, mom = int(p["reg"]), int(p["mom"])
    need = reg + 40
    fsyms = list(p["factor_symbols"])
    fbars = {s: _daily_bars(s, need) for s in fsyms}
    flr = {s: _logret(fbars[s], cutoff) for s in fsyms}
    out: Dict[str, float] = {}
    for s in symbols:
        if s in fsyms[:1]:
            continue
        bars = _daily_bars(s, need)
        if len(bars) < reg + 5:
            continue
        ra = _logret(bars, cutoff)
        if p["factor"] == "BTCETH" and s != fsyms[1]:
            facs = [flr[fsyms[0]], flr[fsyms[1]]]
        else:   # BTC单因子；ETH自己在多因子里退化为BTC单因子
            facs = [flr[fsyms[0]]]
        sc = _score(ra, facs, reg, mom)
        if sc is not None and math.isfinite(sc):
            out[s] = sc
    if len(_score_cache) > 20:
        _score_cache.clear()
    _score_cache[key] = (now, out)
    return out


def generate_signal(bars_by_tf: Dict[str, List[dict]], params: Optional[dict] = None, position: Optional[dict] = None) -> Optional[dict]:
    pr = params or {}
    p = {**DEFAULT_PARAMS, **{k: v for k, v in pr.items() if k in DEFAULT_PARAMS}}
    bars = bars_by_tf.get("base") or []
    symbol = str(pr.get("symbol") or "").upper()
    universe = [str(s).upper() for s in (pr.get("universe_returns") or {}).keys()]
    if not symbol or len(bars) < int(p["reg"]) + 5 or len(universe) < int(p["min_universe"]):
        return None
    last = bars[-1]
    bar_time = int(last["t"])
    day_idx = bar_time // 86_400_000
    if day_idx % int(p["H"]) != 0:
        return None                      # 非再平衡日：持仓不动
    sc = _scores(universe, bar_time, p)
    if len(sc) < int(p["min_universe"]) or symbol not in sc:
        # 本品种没有分数(新币/缺数据)：已持仓则平仓，空仓则不动
        if position:
            return {"action": "CLOSE_QUICK_EXIT", "price": round(float(last["c"]), 8), "reason": "本品种本期无残差分数", "bar_time": bar_time}
        return None
    order = sorted(sc, key=lambda s: sc[s])
    k = max(2, int(round(float(p["q"]) * len(order))))
    short_set, long_set = set(order[:k]), set(order[-k:])
    price = float(last["c"])
    rank_txt = "排名%d/%d" % (order.index(symbol) + 1, len(order))
    if position:
        side = str(position.get("side") or "").upper()
        if (side == "LONG" and symbol in long_set) or (side == "SHORT" and symbol in short_set):
            return None
        return {"action": "CLOSE_QUICK_EXIT", "price": round(price, 8),
                "reason": "残差动量排名掉出%s侧(%s, 分数%+.2f)" % ("多头" if side == "LONG" else "空头", rank_txt, sc[symbol]), "bar_time": bar_time}
    if symbol not in long_set and symbol not in short_set:
        return None
    atr = indicators.wilder_atr(bars, int(p["atr_len"]))
    if atr <= 0:
        return None
    d = 1 if symbol in long_set else -1
    stop_dist = min(atr * float(p["atr_stop_mult"]), price * float(p["max_stop_frac"]))
    return {
        "action": "LONG" if d == 1 else "SHORT", "price": round(price, 8), "atr": round(atr, 8),
        "stop_loss": round(price - d * stop_dist, 8), "tier": 1, "bar_time": bar_time,
        "reason": "截面残差动量%s(%s 分数%+.2f, 因子%s/mom%d/reg%d/H%d/q%.2f)" % ("多头" if d == 1 else "空头", rank_txt, sc[symbol], p["factor"], int(p["mom"]), int(p["reg"]), int(p["H"]), float(p["q"])),
    }
