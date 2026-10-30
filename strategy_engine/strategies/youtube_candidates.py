#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
油管博主回测里两条'低频、与方案F低相关的小正'候选(2026-10-30)，宝贝同意放擂台纸面观察，不上实盘。
规则与回测脚本完全一致(纯Python重写，擂台venv没有pandas/numpy)：
  research_scripts/speculation_channel/batch19.py::ribbon      (币哥 双均线'均线密集'突破，4h)
  research_scripts/youtube_b2/tv_combos.py::twin_range           (Trader小Q Twin Range Filter+Supertrend+放量，日线)

ribbon_dense_4h：MA20/60/120 + EMA20/60/120 共6条线，最高-最低 < w×ATR14 叫'密集'；连续密集≥m根时记下这片区间上下沿，
  之后20根内收盘向上(下)穿出区间上(下)沿、且当根不再密集 → 做多(空)；止损区间另一侧∓0.1ATR；
  收盘跌破(升破)20线(MA20/EMA20 较低/较高的那条)离场。
twin_range_st_1d：Twin Range Filter(27×1.6 与 55×2.0 平均)第一次转多/空，同时 Supertrend(10,3) 同向、成交量>vol_k×20日均量 → 进场；
  止损=当时 Supertrend 线；Twin Range 反向第一次信号或 Supertrend 翻向离场。

回测(币安永续主流5币 2022-01..2026-09，吃单5bp+滑点1.5bp/边)，逐日盯市叠加方案F(每笔1%风险，同窗口方案F 5.26)：
  均线密集 4h：宽度1.5~2.5×ATR整片小正，6组平均 → 5.58(前半3.86→4.04、后半10.02→11.39)，单组 w2.0 5.43 / w2.5 5.58。
  Twin Range 日线：放量1.2~2.0整片正(+0.18~+0.71R)，5档平均 → 5.60(4.16/10.22)，vol_k1.5 单组 5.73。
  两条都是每档几十到一百多笔、参数间差别不小，所以只做纸面观察。
"""
from __future__ import annotations

import math
from typing import Dict, List, Optional

DEFAULT_PARAMS = {
    "mode": "ribbon",        # ribbon | twin
    "w": 2.0, "m": 10, "look": 20,             # ribbon
    "vol_k": 1.5,                              # twin
}


def _f(x) -> float:
    return float(x or 0.0)


# ---------- 指标(与 research_scripts/speculation_channel/lib.py 同口径) ----------
def _ema(x: List[float], n: int) -> List[float]:
    """pandas ewm(span=n, adjust=False)：首值=x[0]。"""
    a = 2.0 / (n + 1); out = []; prev = None
    for v in x:
        prev = v if prev is None else a * v + (1 - a) * prev
        out.append(prev)
    return out


def _rma(x: List[float], n: int) -> List[float]:
    a = 1.0 / n; out = []; prev = None
    for v in x:
        prev = v if prev is None else a * v + (1 - a) * prev
        out.append(prev)
    return out


def _sma(x: List[float], n: int) -> List[float]:
    out = [math.nan] * len(x); s = 0.0
    for i, v in enumerate(x):
        s += v
        if i >= n: s -= x[i - n]
        if i >= n - 1: out[i] = s / n
    return out


def _atr(h, l, c, n=14) -> List[float]:
    tr = [h[0] - l[0]] + [max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1])) for i in range(1, len(c))]
    return _rma(tr, n)


def _supertrend(h, l, c, n=10, m=3.0):
    a = _atr(h, l, c, n); N = len(c)
    up = [(h[i] + l[i]) / 2 - m * a[i] for i in range(N)]; dn = [(h[i] + l[i]) / 2 + m * a[i] for i in range(N)]
    fu, fd = up[:], dn[:]; d = [1] * N; line = [math.nan] * N
    for i in range(1, N):
        fu[i] = max(up[i], fu[i - 1]) if c[i - 1] > fu[i - 1] else up[i]
        fd[i] = min(dn[i], fd[i - 1]) if c[i - 1] < fd[i - 1] else dn[i]
        d[i] = 1 if c[i] > fd[i - 1] else (-1 if c[i] < fu[i - 1] else d[i - 1])
        line[i] = fu[i] if d[i] == 1 else fd[i]
    return d, line


# ---------- 币哥 均线密集 ----------
def _ribbon_state(bars: List[dict], p: dict):
    h = [_f(b["h"]) for b in bars]; l = [_f(b["l"]) for b in bars]; c = [_f(b["c"]) for b in bars]; N = len(c)
    L = [_sma(c, 20), _sma(c, 60), _sma(c, 120), _ema(c, 20), _ema(c, 60), _ema(c, 120)]
    a = _atr(h, l, c, 14); w, m, look = float(p["w"]), int(p["m"]), int(p["look"])
    top = [max(x[i] for x in L) if not any(math.isnan(x[i]) for x in L) else math.nan for i in range(N)]
    bot = [min(x[i] for x in L) if not any(math.isnan(x[i]) for x in L) else math.nan for i in range(N)]
    dense = [(not math.isnan(top[i])) and (top[i] - bot[i]) < w * a[i] for i in range(N)]
    zt = [math.nan] * N; zb = [math.nan] * N; run = 0; last = -10 ** 9; zt_ = zb_ = math.nan
    for i in range(N):
        run = run + 1 if dense[i] else 0
        if dense[i] and run >= m: last = i; zt_, zb_ = top[i], bot[i]
        if i - last <= look: zt[i], zb[i] = zt_, zb_
    m20lo = [min(L[0][i], L[3][i]) for i in range(N)]; m20hi = [max(L[0][i], L[3][i]) for i in range(N)]
    return c, a, dense, zt, zb, m20lo, m20hi


def _ribbon(bars: List[dict], p: dict, position: Optional[dict]) -> Optional[dict]:
    if len(bars) < 200:
        return None
    c, a, dense, zt, zb, m20lo, m20hi = _ribbon_state(bars, p); i = len(c) - 1; last = bars[-1]
    if position:
        side = str(position.get("side") or "").upper()
        if (side == "LONG" and c[i] < m20lo[i]) or (side == "SHORT" and c[i] > m20hi[i]):
            return {"action": "CLOSE_QUICK_EXIT", "price": round(c[i], 6), "reason": "收盘破20线离场", "bar_time": int(last["t"])}
        return None
    if dense[i] or math.isnan(zt[i]) or math.isnan(zt[i - 1]):
        return None
    if c[i] > zt[i] and c[i - 1] <= zt[i - 1]:
        side, stop = "LONG", zb[i] - 0.1 * a[i]
    elif c[i] < zb[i] and c[i - 1] >= zb[i - 1]:
        side, stop = "SHORT", zt[i] + 0.1 * a[i]
    else:
        return None
    if (side == "LONG" and stop >= c[i]) or (side == "SHORT" and stop <= c[i]):
        return None
    return {"action": side, "price": round(c[i], 6), "atr": round(a[i], 6), "stop_loss": round(stop, 6), "tier": 1,
            "bar_time": int(last["t"]), "reason": f"均线密集区间{'上' if side == 'LONG' else '下'}破(宽<{p['w']}×ATR)"}


# ---------- Trader小Q Twin Range + Supertrend + 放量 ----------
def _smrng(x: List[float], t: int, m: float) -> List[float]:
    d = [0.0] + [abs(x[i] - x[i - 1]) for i in range(1, len(x))]
    return [v * m for v in _ema(_ema(d, t), 2 * t - 1)]


def _twin_state(bars: List[dict], p: dict):
    h = [_f(b["h"]) for b in bars]; l = [_f(b["l"]) for b in bars]; c = [_f(b["c"]) for b in bars]; v = [_f(b.get("v")) for b in bars]; N = len(c)
    r1, r2 = _smrng(c, 27, 1.6), _smrng(c, 55, 2.0); r = [(r1[i] + r2[i]) / 2 for i in range(N)]
    f = c[:]; up = [0.0] * N; dn = [0.0] * N
    for i in range(1, N):
        pv = f[i - 1]
        f[i] = (pv if c[i] - r[i] < pv else c[i] - r[i]) if c[i] > pv else (pv if c[i] + r[i] > pv else c[i] + r[i])
        up[i] = up[i - 1] + 1 if f[i] > pv else (0 if f[i] < pv else up[i - 1])
        dn[i] = dn[i - 1] + 1 if f[i] < pv else (0 if f[i] > pv else dn[i - 1])
    lc = [c[i] > f[i] and up[i] > 0 for i in range(N)]; sc = [c[i] < f[i] and dn[i] > 0 for i in range(N)]
    lf = [lc[i] and not (lc[i - 1] if i else False) for i in range(N)]; sf = [sc[i] and not (sc[i - 1] if i else False) for i in range(N)]
    d, line = _supertrend(h, l, c, 10, 3.0); vm = _sma(v, 20)
    return c, v, vm, lf, sf, d, line


def _twin(bars: List[dict], p: dict, position: Optional[dict]) -> Optional[dict]:
    if len(bars) < 120:
        return None
    c, v, vm, lf, sf, d, line = _twin_state(bars, p); i = len(c) - 1; last = bars[-1]
    if position:
        side = str(position.get("side") or "").upper()
        if (side == "LONG" and (sf[i] or d[i] == -1)) or (side == "SHORT" and (lf[i] or d[i] == 1)):
            return {"action": "CLOSE_QUICK_EXIT", "price": round(c[i], 6), "reason": "Twin Range反向或Supertrend翻向", "bar_time": int(last["t"])}
        return None
    if math.isnan(vm[i]) or not v[i] > float(p["vol_k"]) * vm[i]:
        return None
    if lf[i] and d[i] == 1:
        side = "LONG"
    elif sf[i] and d[i] == -1:
        side = "SHORT"
    else:
        return None
    stop = line[i]
    if (side == "LONG" and stop >= c[i]) or (side == "SHORT" and stop <= c[i]):
        return None
    a = _atr([_f(b["h"]) for b in bars], [_f(b["l"]) for b in bars], c, 14)[-1]
    return {"action": side, "price": round(c[i], 6), "atr": round(a, 6), "stop_loss": round(stop, 6), "tier": 1,
            "bar_time": int(last["t"]), "reason": f"Twin Range转{'多' if side == 'LONG' else '空'}+Supertrend同向+{v[i] / vm[i]:.1f}倍量"}


def generate_signal(bars_by_tf: Dict[str, List[dict]], params: Optional[dict] = None, position: Optional[dict] = None) -> Optional[dict]:
    bars = bars_by_tf.get("base") or []
    if not bars:
        return None
    p = {**DEFAULT_PARAMS, **(params or {})}
    return _ribbon(bars, p, position) if str(p["mode"]) == "ribbon" else _twin(bars, p, position)
