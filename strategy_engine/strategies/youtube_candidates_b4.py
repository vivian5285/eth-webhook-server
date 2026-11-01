#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
第四批油管博主回测(2026-11-01)留下的两个候选，按惯例只放擂台纸面观察，不上实盘。
规则与回测脚本一致(纯Python重写，擂台venv没有pandas/numpy)：
  research_scripts/youtube_b4/b4c.py::brk     (JiaSheng nowxuPXru3Q 判断真假突破)
  research_scripts/youtube_b4/b4d.py::mio_bb  (mio来了 4QE8YfsrWzA 布林带擠壓突破)

brk：过去 n_box 根(不含当根)最高/最低 = 箱体，宽 ≤ wmax×ATR14 才算形态；收盘破箱 且 当根是"大K"(实体 ≥ big×箱内平均实体)
  或 连续3根同向K累计涨跌(收盘-两根前开盘) ≥ big×平均实体 → 真突破进场；止损箱体中线。
  他说"一半 1.5R 止盈、另一半吊灯止损追踪"——擂台拆成两条各自记账：brk_tp(1.5R 止盈) / brk_trail(2×ATR14 逐根追踪，引擎 _maybe_bar_trail)。
mio_bb：布林(20,2) 带宽 (上轨-下轨)/中轨 收到近 sq_days 天最低 = 擠壓；最近 sq_win 根内出现过擠壓、且收盘收在带外(放量≥1.5×20根均量)才进场；
  止损 中轨∓0.5ATR；持有到连续两根收盘都在中轨另一侧才离场(CLOSE)。多空都做。

回测(币安永续主流5币 2022-01..2026-09，吃单5bp+滑点1.5bp/边)，逐日盯市叠加方案F(同窗口 5.26，前半3.86/后半10.02)：
  真假突破 4h 箱长30/40/50 × 宽3.5/4/4.5ATR × 大K1.5/2.5 共18组：16组>5.26，平均序列 +1% → 6.11(4.90/11.97)，每年都赚，和方案F相关0.15。
  布林擠壓 8h 擠壓窗口63/126天 × 最近5/10/20根 × 放量与否 × 多空/只多 共24组：20组>5.26，平均 +1% → 5.99(4.03/15.31)，
    但收益集中在2024、2026两年(2023小亏)，笔数少(每组40~160笔)。
  两者合并 +1% → 6.55(4.81/14.04)。本批共测约700组，有选择偏差，所以只做纸面。
"""
from __future__ import annotations

import math
from typing import Dict, List, Optional

DEFAULT_PARAMS = {
    "mode": "brk_tp",                                  # brk_tp | brk_trail | mio_bb
    "n_box": 40, "wmax": 4.0, "big": 1.5, "rr": 1.5,   # brk
    "sq_days": 126, "sq_win": 10, "vol": True,         # mio_bb
}

_NAN = math.nan


def _f(x) -> float:
    return float(x or 0.0)


def _isnan(x) -> bool:
    return x != x


def _rma(x: List[float], n: int) -> List[float]:
    a = 1.0 / n; out = []; prev = None
    for v in x:
        prev = v if prev is None else a * v + (1 - a) * prev
        out.append(prev)
    return out


def _atr(h, l, c, n=14) -> List[float]:
    tr = [h[0] - l[0]] + [max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1])) for i in range(1, len(c))]
    return _rma(tr, n)


def _mean(w):
    return sum(w) / len(w)


def _std(w):  # pandas rolling std, ddof=1
    m = _mean(w); return math.sqrt(sum((v - m) ** 2 for v in w) / (len(w) - 1))


# ---------- JiaSheng 真假突破 ----------
def _brk(bars: List[dict], p: dict, position: Optional[dict]) -> Optional[dict]:
    nb, wmax, big = int(p["n_box"]), float(p["wmax"]), float(p["big"])
    if position or len(bars) < nb + 40:
        return None
    o = [_f(b["o"]) for b in bars]; h = [_f(b["h"]) for b in bars]; l = [_f(b["l"]) for b in bars]; c = [_f(b["c"]) for b in bars]
    a = _atr(h, l, c, 14); i = len(c) - 1
    hi, lo = max(h[i - nb:i]), min(l[i - nb:i])
    if hi - lo > wmax * a[i]:
        return None
    avgb = _mean([abs(c[j] - o[j]) for j in range(i - nb, i)])
    body = abs(c[i] - o[i]); mv3 = c[i] - o[i - 2]
    up3 = all(c[j] > o[j] for j in (i, i - 1, i - 2)); dn3 = all(c[j] < o[j] for j in (i, i - 1, i - 2))
    if c[i] > hi and ((body >= big * avgb and c[i] > o[i]) or (up3 and mv3 >= big * avgb)):
        side = "LONG"
    elif c[i] < lo and ((body >= big * avgb and c[i] < o[i]) or (dn3 and -mv3 >= big * avgb)):
        side = "SHORT"
    else:
        return None
    stop = (hi + lo) / 2
    sig = {"action": side, "price": round(c[i], 6), "atr": round(a[i], 6), "stop_loss": round(stop, 6), "tier": 1,
           "bar_time": int(bars[-1]["t"]),
           "reason": f"箱体{'上' if side == 'LONG' else '下'}破(近{nb}根宽{(hi - lo) / a[i]:.1f}ATR≤{wmax})+大K/三连K确认"}
    if str(p["mode"]) == "brk_tp":
        sig["tp1"] = round(c[i] + (1 if side == "LONG" else -1) * float(p["rr"]) * abs(c[i] - stop), 6)
    return sig


# ---------- mio 布林擠壓突破 ----------
def _mio_bb(bars: List[dict], p: dict, position: Optional[dict]) -> Optional[dict]:
    n, k = 20, 2.0
    if len(bars) < 60:
        return None
    h = [_f(b["h"]) for b in bars]; l = [_f(b["l"]) for b in bars]; c = [_f(b["c"]) for b in bars]; v = [_f(b.get("v")) for b in bars]
    i = len(c) - 1
    mid = [_NAN] * len(c); up = [_NAN] * len(c); dn = [_NAN] * len(c); bw = [_NAN] * len(c)
    for j in range(n - 1, len(c)):
        w = c[j - n + 1:j + 1]; m = _mean(w); s = _std(w)
        mid[j], up[j], dn[j] = m, m + k * s, m - k * s; bw[j] = (up[j] - dn[j]) / m
    if position:   # 连续两根收盘在中轨另一侧 → 离场
        side = str(position.get("side") or "").upper()
        if side == "LONG" and c[i] < mid[i] and c[i - 1] < mid[i - 1]:
            return {"action": "CLOSE_LONG", "price": round(c[i], 6), "bar_time": int(bars[-1]["t"]), "reason": "连续两根收盘跌破布林中轨"}
        if side == "SHORT" and c[i] > mid[i] and c[i - 1] > mid[i - 1]:
            return {"action": "CLOSE_SHORT", "price": round(c[i], 6), "bar_time": int(bars[-1]["t"]), "reason": "连续两根收盘站上布林中轨"}
        return None
    step = (int(bars[-1]["t"]) - int(bars[-201]["t"])) / 200 if len(bars) > 201 else 86400e3
    L = int(float(p["sq_days"]) * 86400e3 / step)
    if len(bars) < L + n + 5:
        return None
    sq_win = int(p["sq_win"])

    def squeeze(j):   # pandas rolling(L, min_periods=L//2).min()，满窗口时就是近 L 根最小
        lo_ = min(x for x in bw[max(0, j - L + 1):j + 1] if not _isnan(x))
        return bw[j] <= lo_ * 1.0001
    if not any(squeeze(j) for j in range(i - sq_win + 1, i + 1)):
        return None
    if p.get("vol", True) and not v[i] > 1.5 * _mean(v[i - 19:i + 1]):
        return None
    a = _atr(h, l, c, 14)
    if c[i] > up[i]:
        side, stop = "LONG", mid[i] - 0.5 * a[i]
    elif c[i] < dn[i]:
        side, stop = "SHORT", mid[i] + 0.5 * a[i]
    else:
        return None
    if (side == "LONG" and stop >= c[i]) or (side == "SHORT" and stop <= c[i]):
        return None
    return {"action": side, "price": round(c[i], 6), "atr": round(a[i], 6), "stop_loss": round(stop, 6), "tier": 1,
            "bar_time": int(bars[-1]["t"]), "reason": f"布林擠壓后放量收{'上' if side == 'LONG' else '下'}轨外(带宽近{p['sq_days']}天最低)"}


def generate_signal(bars_by_tf: Dict[str, List[dict]], params: Optional[dict] = None, position: Optional[dict] = None) -> Optional[dict]:
    bars = bars_by_tf.get("base") or []
    if not bars:
        return None
    p = {**DEFAULT_PARAMS, **(params or {})}
    return _mio_bb(bars, p, position) if str(p["mode"]) == "mio_bb" else _brk(bars, p, position)
