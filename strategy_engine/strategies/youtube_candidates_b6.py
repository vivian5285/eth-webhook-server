#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
第六批油管博主回测(2026-11-02)留下的候选：交易研究院 JwDvZxSK1W0 转述的 BNF(小手川隆) 25日均线负乖离抄底。按惯例只放擂台纸面观察，不上实盘。
规则与回测脚本一致(纯Python重写)：research_scripts/youtube_b6/b10.py::bnf
  收盘低于 SMA(25天，按K线周期折算根数) 超过 thr，且 MACD(12,26,9) 柱子在这根拐头向上(本根>前根 且 前根<=前前根) → 收盘做多(回测=下一根开盘)；
  止损 入场-3×ATR14；乖离收窄到 ex 以内(收盘/均线-1 >= -ex) 平仓，或持满 hold 天平仓。只做多。

回测(主流5币 2022-01..2026-09 含费)：4h × 均线20/25/30天 × 乖离10~30% × 回收0/5/10% × 持有5/10/20天 135组里129组让"方案F+突破叠加2%"(7.75)变好，
平均 +1% → 8.99(前6.24/后20.00)，回撤20.0→17.7%，与方案F相关-0.14(逆势抄底，真分散)；4h/6h/8h/日线540组全部平均 +1% 8.53。
弱点：笔数少(4h 中位60笔/4.5年)、每年只贡献2~5%、2022/2026小亏。擂台跑 4h 乖离10/15/20% 三组集成观察。
"""
from __future__ import annotations

from typing import Dict, List, Optional

DEFAULT_PARAMS = {"thr": 0.15, "ex": 0.05, "hold_days": 10, "n_days": 25}


def _f(x) -> float:
    return float(x or 0.0)


def _ema(x: List[float], n: int) -> List[float]:
    a = 2.0 / (n + 1); out = []; prev = None
    for v in x:
        prev = v if prev is None else a * v + (1 - a) * prev
        out.append(prev)
    return out


def _atr(h, l, c, n=14) -> List[float]:
    tr = [h[0] - l[0]] + [max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1])) for i in range(1, len(c))]
    out = []; prev = None
    for v in tr:
        prev = v if prev is None else v / n + (1 - 1 / n) * prev
        out.append(prev)
    return out


def generate_signal(bars_by_tf: Dict[str, List[dict]], params: Optional[dict] = None, position: Optional[dict] = None) -> Optional[dict]:
    bars = bars_by_tf.get("base") or []
    p = {**DEFAULT_PARAMS, **(params or {})}
    if len(bars) < 250:
        return None
    step = (int(bars[-1]["t"]) - int(bars[-201]["t"])) / 200
    bpd = 86400e3 / step; n = max(5, int(round(float(p["n_days"]) * bpd)))
    h = [_f(b["h"]) for b in bars]; l = [_f(b["l"]) for b in bars]; c = [_f(b["c"]) for b in bars]; i = len(c) - 1
    m = sum(c[i - n + 1:i + 1]) / n; dev = c[i] / m - 1
    if position:
        if str(position.get("side") or "").upper() != "LONG":
            return None
        held_bars = sum(1 for b in bars if int(b["t"]) > int(position.get("entry_bar_time") or 0))
        if dev >= -float(p["ex"]):
            return {"action": "CLOSE_LONG", "price": round(c[i], 6), "bar_time": int(bars[-1]["t"]), "reason": f"乖离收窄到 {dev:.1%}(>= -{float(p['ex']):.0%})"}
        if held_bars >= int(float(p["hold_days"]) * bpd):
            return {"action": "CLOSE_LONG", "price": round(c[i], 6), "bar_time": int(bars[-1]["t"]), "reason": f"持满 {p['hold_days']} 天"}
        return None
    e12, e26 = _ema(c, 12), _ema(c, 26); md = [x - y for x, y in zip(e12, e26)]; sg = _ema(md, 9); hist = [x - y for x, y in zip(md, sg)]
    if not (dev <= -float(p["thr"]) and hist[i] > hist[i - 1] and hist[i - 1] <= hist[i - 2]):
        return None
    a = _atr(h, l, c, 14)
    return {"action": "LONG", "price": round(c[i], 6), "atr": round(a[i], 6), "stop_loss": round(c[i] - 3 * a[i], 6), "tier": 1,
            "bar_time": int(bars[-1]["t"]), "reason": f"低于{p['n_days']}日均线 {dev:.1%}(<= -{float(p['thr']):.0%})且MACD柱拐头向上(BNF抄底)"}
