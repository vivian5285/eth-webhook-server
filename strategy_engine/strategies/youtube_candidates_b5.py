#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
第五批油管博主回测(2026-11-01)留下的候选：指標與策略 ifWUYXnWJtY 布林/KDJ 逆势。宝贝同意先放擂台纸面观察(2026年这一腿是亏的)，不上实盘。
规则与回测脚本一致(纯Python重写)：research_scripts/youtube_b5/b6.py::zb_sqz(need_sqz=False，多空)
  收盘碰/破布林(20,2)下轨 且 KD(n) 的 K<20 → 之后 look 根内，K 上穿20 且这根收阳 → 收盘做多，止损近5根最低-0.1ATR；
  K 从80以上跌回80下 → 平多(CLOSE)。空单对称(碰上轨且K>80 → K 下穿80且收阴做空；K 从20下回到20上平空)。
  原版还要求"布林缩进肯特纳(擠壓)"，回测里不加擠壓过滤更稳，所以纸面用不加的版本。

回测(主流5币 2022-01..2026-09 含费)：4h/6h/8h × KD周期14~40 × 回看10/20/30 × 擠壓与否 × 多空/只多 180组平均，
接到"方案F+突破叠加2%"(比值7.75)上 +1% → 9.59(前6.97/后18.83)，回撤20.0→17.8%，与方案F相关-0.10；
但单组只有约一半能改善(收益靠多参数平均)，且2026年各版本都亏 → 擂台跑多组参数集成观察。
"""
from __future__ import annotations

import math
from typing import Dict, List, Optional

DEFAULT_PARAMS = {"kn": 25, "look": 20}


def _f(x) -> float:
    return float(x or 0.0)


def _atr(h, l, c, n=14) -> List[float]:
    tr = [h[0] - l[0]] + [max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1])) for i in range(1, len(c))]
    out = []; prev = None
    for v in tr:
        prev = v if prev is None else v / n + (1 - 1 / n) * prev
        out.append(prev)
    return out


def _k(h, l, c, n):
    """KD 的 K 线(同 b6.kd：RSV 无效时取50，K = 2/3·K前 + 1/3·RSV，起点50)。"""
    K = []; prev = 50.0
    for i in range(len(c)):
        if i >= n - 1:
            hh, ll = max(h[i - n + 1:i + 1]), min(l[i - n + 1:i + 1])
            rsv = 100 * (c[i] - ll) / (hh - ll) if hh - ll > 0 else 50.0
        else:
            rsv = 50.0
        prev = 2 / 3 * prev + rsv / 3 if i else 2 / 3 * 50.0 + rsv / 3
        K.append(prev)
    return K


def _bb(c, i, n=20, k=2.0):
    w = c[i - n + 1:i + 1]; m = sum(w) / n; sd = math.sqrt(sum((v - m) ** 2 for v in w) / (n - 1))
    return m - k * sd, m + k * sd


def generate_signal(bars_by_tf: Dict[str, List[dict]], params: Optional[dict] = None, position: Optional[dict] = None) -> Optional[dict]:
    bars = bars_by_tf.get("base") or []
    p = {**DEFAULT_PARAMS, **(params or {})}
    kn, look = int(p["kn"]), int(p["look"])
    if len(bars) < max(60, kn + look + 30):
        return None
    o = [_f(b["o"]) for b in bars]; h = [_f(b["h"]) for b in bars]; l = [_f(b["l"]) for b in bars]; c = [_f(b["c"]) for b in bars]
    K = _k(h, l, c, kn); i = len(c) - 1
    if position:
        side = str(position.get("side") or "").upper()
        if side == "LONG" and K[i] < 80 <= K[i - 1]:
            return {"action": "CLOSE_LONG", "price": round(c[i], 6), "bar_time": int(bars[-1]["t"]), "reason": "KD的K从80上方跌回80下"}
        if side == "SHORT" and K[i] > 20 >= K[i - 1]:
            return {"action": "CLOSE_SHORT", "price": round(c[i], 6), "bar_time": int(bars[-1]["t"]), "reason": "KD的K从20下方回到20上"}
        return None
    # 回测是逐根状态机：触发条件出现记一次"待命"，look 根内出现进场K就用掉。这里在最近 look+40 根上重放同一个状态机，只看当根是否进场。
    a = _atr(h, l, c, 14); armL = armS = -10**9; start = max(30, i - look - 40)
    for j in range(start, i + 1):
        lo, up = _bb(c, j)
        if c[j] <= lo and K[j] < 20: armL = j
        if c[j] >= up and K[j] > 80: armS = j
        goL = 0 < j - armL <= look and K[j] > 20 >= K[j - 1] and c[j] > o[j]
        goS = (not goL) and 0 < j - armS <= look and K[j] < 80 <= K[j - 1] and c[j] < o[j]
        if goL:
            stop = min(l[j - 4:j + 1]) - 0.1 * a[j]; armL = -10**9
            if j == i and stop < c[i]:
                return {"action": "LONG", "price": round(c[i], 6), "atr": round(a[i], 6), "stop_loss": round(stop, 6), "tier": 1,
                        "bar_time": int(bars[-1]["t"]), "reason": f"碰布林下轨+K<20后，K上穿20收阳(KD{kn})"}
        elif goS:
            stop = max(h[j - 4:j + 1]) + 0.1 * a[j]; armS = -10**9
            if j == i and stop > c[i]:
                return {"action": "SHORT", "price": round(c[i], 6), "atr": round(a[i], 6), "stop_loss": round(stop, 6), "tier": 1,
                        "bar_time": int(bars[-1]["t"]), "reason": f"碰布林上轨+K>80后，K下穿80收阴(KD{kn})"}
    return None
