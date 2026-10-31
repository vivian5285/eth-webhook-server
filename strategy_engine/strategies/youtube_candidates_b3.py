#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
第三批油管博主回测(2026-11-01)留下的两个候选，宝贝同意放擂台纸面观察，不上实盘。
规则与回测脚本一致(纯Python重写，擂台venv没有pandas/numpy)：
  research_scripts/youtube_b3/b3.py::neo_box   (卫央Neo 趋势七步①区间突破，exit_mode=trail)
  research_scripts/youtube_b3/b3.py::stiff     (交易研究院 T3+Trendilo+Stiffness，exit_mode=trail, event=True)

neo_box：过去 n 根(不含当根)最高/最低 = 箱体，宽 ≤ wmax×ATR14 才算收敛箱体；收盘第一次站上箱顶 → 多(破箱底 → 空)；
  初始止损箱体中线；之后 3×ATR 追踪(引擎 multi_strategy_runner._maybe_bar_trail，与回测逐根同口径)。
stiff：T3(t3n, 0.7) 上行且收盘在 T3 上方 + Trendilo(涨跌幅% 的 ALMA(50,0.85,6) 第一次上穿其 RMS 带) + Stiffness(60,100,3) > thr → 多(空单镜像，Stiffness 同一阈值)；
  初始止损近10根最低/最高 ∓0.1ATR；之后 3×ATR 追踪。

回测(币安永续主流5币 2022-01..2026-09，吃单5bp+滑点1.5bp/边)，逐日盯市叠加方案F(同窗口 5.26，前半3.86/后半10.02)：
  区间突破 4h/6h × N40/60/80 × 宽6/7/8ATR 18组平均：+1% → 5.66(4.43/9.83)；4h N60 宽7 单组 5.33。
  Stiffness 6h/8h/12h × 阈值40/50/60 × T3 50/100/150 27组平均：+1% → 5.46(3.92/11.70)；8h 阈值50 T3100 单组 5.36。
  两者合并 +1~1.5% → 5.58~5.70。从约280组里挑出来、提升和上一批候选同量级，所以只做纸面。
"""
from __future__ import annotations

import math
from typing import Dict, List, Optional

DEFAULT_PARAMS = {
    "mode": "neo_box",              # neo_box | stiff
    "n": 60, "wmax": 7.0,           # neo_box
    "thr": 50.0, "t3n": 100,        # stiff
}
TRAIL_ATR = 3.0  # 引擎侧追踪倍数(见 multi_strategy_runner.BAR_TRAIL_STRATEGIES)

_NAN = math.nan


def _f(x) -> float:
    return float(x or 0.0)


def _isnan(x) -> bool:
    return x != x


# ---------- 指标(与 research_scripts/speculation_channel/lib.py 同口径，pandas ewm adjust=False / rolling) ----------
def _ema(x: List[float], n: int) -> List[float]:
    """跳过开头的 NaN，从第一个有效值起算(同 pandas ewm(span=n, adjust=False))。"""
    a = 2.0 / (n + 1); out = []; prev = None
    for v in x:
        if _isnan(v):
            out.append(prev if prev is not None else _NAN); continue
        prev = v if prev is None else a * v + (1 - a) * prev
        out.append(prev)
    return out


def _rma(x: List[float], n: int) -> List[float]:
    a = 1.0 / n; out = []; prev = None
    for v in x:
        prev = v if prev is None else a * v + (1 - a) * prev
        out.append(prev)
    return out


def _atr(h, l, c, n=14) -> List[float]:
    tr = [h[0] - l[0]] + [max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1])) for i in range(1, len(c))]
    return _rma(tr, n)


def _roll(x: List[float], n: int, fn) -> List[float]:
    out = [_NAN] * len(x)
    for i in range(n - 1, len(x)):
        w = x[i - n + 1:i + 1]
        out[i] = _NAN if any(_isnan(v) for v in w) else fn(w)
    return out


def _std(w):  # pandas rolling std, ddof=1
    m = sum(w) / len(w); return math.sqrt(sum((v - m) ** 2 for v in w) / (len(w) - 1))


# ---------- 卫央 区间突破 ----------
def _neo_box(bars: List[dict], p: dict, position: Optional[dict]) -> Optional[dict]:
    n, wmax = int(p["n"]), float(p["wmax"])
    if position or len(bars) < n + 30:
        return None
    h = [_f(b["h"]) for b in bars]; l = [_f(b["l"]) for b in bars]; c = [_f(b["c"]) for b in bars]
    a = _atr(h, l, c, 14); i = len(c) - 1
    hi, lo = max(h[i - n:i]), min(l[i - n:i]); hi0, lo0 = max(h[i - 1 - n:i - 1]), min(l[i - 1 - n:i - 1])
    w = hi - lo
    if w > wmax * a[i]:
        return None
    if c[i] > hi and c[i - 1] <= hi0:
        side = "LONG"
    elif c[i] < lo and c[i - 1] >= lo0:
        side = "SHORT"
    else:
        return None
    stop = (hi + lo) / 2
    return {"action": side, "price": round(c[i], 6), "atr": round(a[i], 6), "stop_loss": round(stop, 6), "tier": 1,
            "bar_time": int(bars[-1]["t"]), "reason": f"收敛箱体{'上' if side == 'LONG' else '下'}破(近{n}根宽{w / a[i]:.1f}ATR≤{wmax})"}


# ---------- 交易研究院 T3 + Trendilo + Stiffness ----------
def _stiff_state(c: List[float], t3n: int):
    N = len(c); av_ = 0.7
    e = [c]
    for _ in range(6):
        e.append(_ema(e[-1], t3n))
    c1 = -av_ ** 3; c2 = 3 * av_ ** 2 + 3 * av_ ** 3; c3 = -6 * av_ ** 2 - 3 * av_ - 3 * av_ ** 3; c4 = 1 + 3 * av_ + av_ ** 3 + 3 * av_ ** 2
    T = [c1 * e[6][i] + c2 * e[5][i] + c3 * e[4][i] + c4 * e[3][i] for i in range(N)]
    pch = [0.0] + [(c[i] - c[i - 1]) / c[i] * 100 for i in range(1, N)]
    n = 50; m = 0.85 * (n - 1); s = n / 6.0
    wts = [math.exp(-((k - m) ** 2) / (2 * s * s)) for k in range(n)]; sw = sum(wts); wts = [x / sw for x in wts]
    av = [_NAN] * N
    for i in range(n - 1, N):
        av[i] = sum(wts[k] * pch[i - n + 1 + k] for k in range(n))  # wts[0]=最老
    sq = [v * v if not _isnan(v) else _NAN for v in av]
    rms = [math.sqrt(x / n) if not _isnan(x) else _NAN for x in _roll(sq, n, sum)]
    ma = _roll(c, 100, lambda w: sum(w) / len(w)); sd = _roll(c, 100, _std)
    above = [1.0 if (not _isnan(ma[i]) and c[i] > ma[i] - 0.2 * sd[i]) else 0.0 for i in range(N)]
    raw = [x * 100 / 60 if not _isnan(x) else _NAN for x in _roll(above, 60, sum)]
    stf = _ema(raw, 3)
    return T, av, rms, stf


def _stiff(bars: List[dict], p: dict, position: Optional[dict]) -> Optional[dict]:
    t3n, thr = int(p["t3n"]), float(p["thr"])
    if position or len(bars) < 300:
        return None
    h = [_f(b["h"]) for b in bars]; l = [_f(b["l"]) for b in bars]; c = [_f(b["c"]) for b in bars]
    T, av, rms, stf = _stiff_state(c, t3n); a = _atr(h, l, c, 14); i = len(c) - 1
    if any(_isnan(x) for x in (av[i], rms[i], av[i - 1], rms[i - 1], stf[i])) or not stf[i] > thr:
        return None
    green = T[i] > T[i - 1]
    if green and c[i] > T[i] and av[i] > rms[i] and not av[i - 1] > rms[i - 1]:
        side, stop = "LONG", min(l[i - 9:i + 1]) - 0.1 * a[i]
    elif (not green) and c[i] < T[i] and av[i] < -rms[i] and not av[i - 1] < -rms[i - 1]:
        side, stop = "SHORT", max(h[i - 9:i + 1]) + 0.1 * a[i]
    else:
        return None
    if (side == "LONG" and stop >= c[i]) or (side == "SHORT" and stop <= c[i]):
        return None
    return {"action": side, "price": round(c[i], 6), "atr": round(a[i], 6), "stop_loss": round(stop, 6), "tier": 1,
            "bar_time": int(bars[-1]["t"]), "reason": f"T3{'上' if side == 'LONG' else '下'}行+Trendilo转{'多' if side == 'LONG' else '空'}+Stiffness{stf[i]:.0f}>{thr:.0f}"}


def trail_stop(bars: List[dict], position: dict, mult: float = TRAIL_ATR) -> Optional[float]:
    """回测 lib._run 的 trail_atr 口径：入场后每根K线收盘，best=入场以来最高(空单最低)，候选止损 best∓mult×ATR14(当根)，只收紧。
    只用入场K线之后已收盘的K线。返回新止损或 None。"""
    side = str(position.get("side") or "").upper(); e = _f(position.get("entry")); st = _f(position.get("stop_loss"))
    t0 = int(position.get("entry_bar_time") or 0)
    if side not in ("LONG", "SHORT") or e <= 0 or st <= 0 or len(bars) < 30:
        return None
    h = [_f(b["h"]) for b in bars]; l = [_f(b["l"]) for b in bars]; c = [_f(b["c"]) for b in bars]; a = _atr(h, l, c, 14)
    best = e; new = st
    for j, b in enumerate(bars):
        if int(b["t"]) <= t0:
            continue
        if side == "LONG":
            best = max(best, h[j]); new = max(new, best - mult * a[j])
        else:
            best = min(best, l[j]); new = min(new, best + mult * a[j])
    return new if abs(new - st) > 1e-12 else None


def generate_signal(bars_by_tf: Dict[str, List[dict]], params: Optional[dict] = None, position: Optional[dict] = None) -> Optional[dict]:
    bars = bars_by_tf.get("base") or []
    if not bars:
        return None
    p = {**DEFAULT_PARAMS, **(params or {})}
    return _neo_box(bars, p, position) if str(p["mode"]) == "neo_box" else _stiff(bars, p, position)
