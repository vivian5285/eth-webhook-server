#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Heikin Ashi 趋势 —— 2026-09-23 从CoinW实盘版(/home/coinw/coinw-hft-server/
heikin_ashi_strategy.py，本身又是从擂台系统187.53.133.188
strategy_engine/strategies/heikin_ashi_trend.py原样搬过去的)逐字节复制到
币安账户C。138笔纸面交易、38.5%胜率、盈亏比2.25、最大回撤8.85(ATR加权
单位)，是宝贝选中要在真实账户复刻的策略。

跨仓库复制而不是import：币安这边是完全独立的代码库/VPS，没有
strategy_engine这个包。为了保证"验证的是什么，实盘跑的就是什么"，
DEFAULT_PARAMS跟擂台/CoinW两份逐字节一致，不做任何参数调整。

HA 计算：
  HA_close = (o + h + l + c) / 4
  HA_open  = (前一根 HA_open + 前一根 HA_close) / 2   （首根用 (o+c)/2）
  HA_high  = max(h, HA_open, HA_close)
  HA_low   = min(l, HA_open, HA_close)

规则：
  · 连续 streak_len(3) 根同色 HA K 线 → 第 streak_len 根顺势进场，且要求
    这几根 HA 实体递增放大(趋势在加速)。
  · 止损：ATR × 2.0（初始值，实盘引擎里会按真实成交价重锚）。
  · 离场：出现反色 HA K 线，或出现明显反向影线，不设固定止盈——让利润
    奔跑直到颜色变。
"""
from __future__ import annotations

from typing import Dict, List, Optional

DEFAULT_PARAMS = {
    "streak_len": 3,
    "require_growing_body": True,
    "wick_frac": 1.0,
    "atr_len": 14,
    "atr_stop_mult": 2.0,
    "require_clean_entry_bar": False,
    "wick_exit_atr_floor_frac": 0.0,
    "use_ema_direction_filter": False,
    "ema_fast_len": 7,
    "ema_slow_len": 25,
    "exit_confirm_bars": 1,
}


def wilder_atr(bars: List[dict], period: int = 14) -> float:
    """跟strategy_engine/indicators.py::wilder_atr完全同一套公式(Wilder
    平滑的真实波幅均值)，独立复制一份避免跨仓库依赖。"""
    if len(bars) < period + 1:
        return 0.0
    trs = []
    prev_close = float(bars[0]["c"])
    for b in bars[1:]:
        h, l, c = float(b["h"]), float(b["l"]), float(b["c"])
        tr = max(h - l, abs(h - prev_close), abs(l - prev_close))
        trs.append(tr)
        prev_close = c
    if len(trs) < period:
        return 0.0
    atr = sum(trs[:period]) / period
    for tr in trs[period:]:
        atr = (atr * (period - 1) + tr) / period
    return float(atr)


def _ha(bars: List[dict]) -> List[dict]:
    out = []
    prev_o = prev_c = None
    for b in bars:
        o, h, l, c = float(b["o"]), float(b["h"]), float(b["l"]), float(b["c"])
        hc = (o + h + l + c) / 4.0
        ho = (o + c) / 2.0 if prev_o is None else (prev_o + prev_c) / 2.0
        hh = max(h, ho, hc)
        hl = min(l, ho, hc)
        out.append({"t": int(b["t"]), "o": ho, "h": hh, "l": hl, "c": hc})
        prev_o, prev_c = ho, hc
    return out


def generate_signal(bars_by_tf: Dict[str, List[dict]], params: Optional[dict] = None, position: Optional[dict] = None) -> Optional[dict]:
    p = {**DEFAULT_PARAMS, **(params or {})}
    bars = bars_by_tf.get("base") or []
    sk = int(p["streak_len"])
    atr_len = int(p["atr_len"])
    if len(bars) < sk + atr_len + 10:
        return None

    ha = _ha(bars)
    last = bars[-1]
    price = float(last["c"])
    bar_time = int(last["t"])

    def _green(x):
        return x["c"] > x["o"]

    def _body(x):
        return abs(x["c"] - x["o"])

    cur = ha[-1]
    atr = wilder_atr(bars, atr_len)
    floor_frac = float(p.get("wick_exit_atr_floor_frac") or 0.0)
    wick_floor = max(floor_frac * atr, 1e-9) if atr > 0 else 1e-9

    if position:
        side = str(position.get("side") or "").upper()
        confirm_n = max(1, int(p.get("exit_confirm_bars") or 1))
        recent = ha[-confirm_n:] if len(ha) >= confirm_n else ha
        if side == "LONG":
            if len(recent) >= confirm_n and all(not _green(x) for x in recent):
                return {"action": "CLOSE_QUICK_EXIT", "price": round(price, 6),
                        "reason": f"连续{confirm_n}根HA转阴，趋势转弱", "bar_time": bar_time}
            if (cur["o"] - cur["l"]) > float(p["wick_frac"]) * max(_body(cur), wick_floor):
                return {"action": "CLOSE_QUICK_EXIT", "price": round(price, 6),
                        "reason": "HA 阳线现明显下影，上涨承压", "bar_time": bar_time}
        elif side == "SHORT":
            if len(recent) >= confirm_n and all(_green(x) for x in recent):
                return {"action": "CLOSE_QUICK_EXIT", "price": round(price, 6),
                        "reason": f"连续{confirm_n}根HA转阳，趋势转弱", "bar_time": bar_time}
            if (cur["h"] - cur["o"]) > float(p["wick_frac"]) * max(_body(cur), wick_floor):
                return {"action": "CLOSE_QUICK_EXIT", "price": round(price, 6),
                        "reason": "HA 阴线现明显上影，下跌承压", "bar_time": bar_time}
        return None

    seg = ha[-sk:]
    all_green = all(_green(x) for x in seg)
    all_red = all(not _green(x) for x in seg)
    growing = True
    if bool(p["require_growing_body"]):
        bodies = [_body(x) for x in seg]
        growing = all(bodies[i] >= bodies[i - 1] for i in range(1, len(bodies)))
    if not ((all_green or all_red) and growing):
        return None

    if bool(p.get("require_clean_entry_bar")):
        last_ha = seg[-1]
        if all_green and (last_ha["o"] - last_ha["l"]) > float(p["wick_frac"]) * max(_body(last_ha), wick_floor):
            return None
        if all_red and (last_ha["h"] - last_ha["o"]) > float(p["wick_frac"]) * max(_body(last_ha), wick_floor):
            return None

    if atr <= 0:
        return None

    d = 1 if all_green else -1
    return {
        "action": "LONG" if d == 1 else "SHORT",
        "price": round(price, 6), "atr": round(atr, 6),
        "stop_loss": round(price - d * atr * float(p["atr_stop_mult"]), 6),
        "tier": 1, "bar_time": bar_time,
        "reason": f"连续 {sk} 根 HA {'阳' if d == 1 else '阴'}线(实体放大)",
    }
