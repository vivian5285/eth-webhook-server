#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
急跌反弹(只做多，15m)——2026-10-30 投机实验室全频道回测里唯一和方案F不相关的方向，
宝贝同意放擂台纸面观察。两条独立写法，规则与回测脚本完全一致：
  research_scripts/speculation_channel/batch07.py::knife    (Flight 接刀)
  research_scripts/speculation_channel/batch09.py::chuanmu  (川沐 V字针底)

crash_knife_15m：近32根最高点到本根最低点跌幅≥12%，本根成交量≥前50根均量×4，
  收盘收回本根振幅30%以上(长下影) → 收盘做多；止损本根低点-0.3×ATR14，目标2R，最长持有48根(12小时)。
crash_vbottom_15m：倒数第3根相对倒数第8根累计跌≥4%且倒数第3/4根是阴线，最近两根连续收阳且
  两根累计涨≥2% → 收盘做多；止损近8根最低点-0.2×ATR14，目标3R，最长持有96根(24小时)。

回测(币安永续主流5币 2022-01..2026-09，吃单5bp+滑点1.5bp/边)：
  接刀  n149 每笔+0.26R(样本内+0.32/样本外+0.07)；V底 n148 每笔+0.35R(内+0.29/外+0.57)。
  逐日盯市叠加在方案F上(每笔1%风险)：与F日相关0.04，比值5.26→5.28基本持平，
  2022-24段改善、2025起变差。所以只做纸面观察，不上实盘。
"""
from __future__ import annotations

from typing import Dict, List, Optional

from strategy_engine import indicators

BAR_MS = 15 * 60 * 1000

DEFAULT_PARAMS = {
    "mode": "knife",        # knife | vbottom
    # knife
    "look": 32, "drop": 0.12, "vol_k": 4.0, "vol_n": 50, "knife_rr": 2.0, "knife_stop_atr": 0.3, "knife_max_bars": 48,
    # vbottom
    "d5": 0.04, "u2": 0.02, "v_rr": 3.0, "v_stop_atr": 0.2, "v_max_bars": 96,
}


def _f(x) -> float:
    return float(x or 0.0)


def _time_exit(bars: List[dict], position: dict, max_bars: int) -> Optional[dict]:
    last = bars[-1]
    entry_t = int(position.get("entry_bar_time") or last["t"])
    held = (int(last["t"]) - entry_t) // BAR_MS
    if held >= max_bars:
        return {"action": "CLOSE_QUICK_EXIT", "price": round(_f(last["c"]), 6),
                "reason": f"持有满{max_bars}根(15m)时间离场", "bar_time": int(last["t"])}
    return None


def _knife(bars: List[dict], p: dict) -> Optional[dict]:
    look, vol_n = int(p["look"]), int(p["vol_n"])
    if len(bars) < max(look, vol_n) + 20:
        return None
    last = bars[-1]
    h, l, c = _f(last["h"]), _f(last["l"]), _f(last["c"])
    hi = max(_f(b["h"]) for b in bars[-look:])
    if hi <= 0 or (hi - l) / hi < float(p["drop"]):
        return None
    prev = bars[-1 - vol_n:-1]
    vm = sum(_f(b.get("v")) for b in prev) / len(prev)
    if vm <= 0 or _f(last.get("v")) < float(p["vol_k"]) * vm:
        return None
    if not c > l + 0.3 * (h - l):
        return None
    atr = indicators.wilder_atr(bars, 14)
    stop = l - float(p["knife_stop_atr"]) * atr
    if atr <= 0 or stop >= c:
        return None
    return {"action": "LONG", "price": round(c, 6), "atr": round(atr, 6), "stop_loss": round(stop, 6),
            "tp1": round(c + float(p["knife_rr"]) * (c - stop), 6), "tier": 1, "bar_time": int(last["t"]),
            "reason": f"急跌{(hi - l) / hi:.1%}+{_f(last.get('v')) / vm:.1f}倍量+长下影"}


def _vbottom(bars: List[dict], p: dict) -> Optional[dict]:
    if len(bars) < 40:
        return None
    o = [_f(b["o"]) for b in bars]; c = [_f(b["c"]) for b in bars]; l = [_f(b["l"]) for b in bars]
    dn5 = c[-3] / c[-8] - 1 <= -float(p["d5"]) and c[-3] < o[-3] and c[-4] < o[-4]
    up2 = c[-1] / c[-3] - 1 >= float(p["u2"]) and c[-1] > o[-1] and c[-2] > o[-2]
    if not (dn5 and up2):
        return None
    atr = indicators.wilder_atr(bars, 14)
    stop = min(l[-8:]) - float(p["v_stop_atr"]) * atr
    if atr <= 0 or stop >= c[-1]:
        return None
    return {"action": "LONG", "price": round(c[-1], 6), "atr": round(atr, 6), "stop_loss": round(stop, 6),
            "tp1": round(c[-1] + float(p["v_rr"]) * (c[-1] - stop), 6), "tier": 1, "bar_time": int(bars[-1]["t"]),
            "reason": f"5根跌{c[-3] / c[-8] - 1:.1%}后2根涨{c[-1] / c[-3] - 1:.1%}"}


def generate_signal(bars_by_tf: Dict[str, List[dict]], params: Optional[dict] = None, position: Optional[dict] = None) -> Optional[dict]:
    bars = bars_by_tf.get("base") or []
    if not bars:
        return None
    p = {**DEFAULT_PARAMS, **(params or {})}
    knife = str(p["mode"]) == "knife"
    if position:
        return _time_exit(bars, position, int(p["knife_max_bars"] if knife else p["v_max_bars"]))
    return _knife(bars, p) if knife else _vbottom(bars, p)
