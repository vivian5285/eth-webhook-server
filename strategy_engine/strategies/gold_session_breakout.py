#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
黄金系列专属：亚盘区间 → 伦敦/纽约开盘突破 —— 2026-09-10 应宝贝转发的
「跨资产量化战法大全」新增。只挂 XAUUSDT / PAXGUSDT。

为什么黄金要单独做时段突破：
  黄金有非常明显的三段式时段结构——亚盘(00:00–07:00 UTC)通常窄幅盘整
  憋出一个区间，伦敦盘(07:00 UTC 起)和纽约盘(13:30 UTC 起)开盘后波动
  率显著扩张，经常就是从亚盘区间的某一侧放量突破出去。这是外汇/贵金属
  几十年的公开时段套路(London Breakout / NY Breakout)，币圈没有、美股
  时段结构也不一样，所以是黄金专属。

跟擂台已有 opening_range_breakout / us_stock_rth_momentum 的区别：
  - ORB：取当日某个锚点后**前 30 分钟**自己的高低点作区间；
  - us_stock_rth_momentum：取**昨日整个 RTH**区间 + 当日 VWAP；
  - 这套：取**当日亚盘 00–07 UTC**区间，在**伦敦/纽约两个开盘窗口**里
    等第一次放量突破，突破后用吊灯止损跨时段持有到纽约盘收(21:00 UTC)。
  三套的"区间盒子"和"触发时钟"都不同。

规则：
  · 亚盘区间：当日 00:00–07:00 UTC 所有 15m K线的最高/最低 = AR_high/AR_low。
    区间宽度 > max_range_atr(6)×ATR 视为"根本不是区间"，当天不做。
  · 触发窗口：伦敦 [07:00, 07:00+entry_window_min) 或 纽约 [13:30, 13:30+
    entry_window_min)。窗口内收盘价突破 AR_high 且当根放量(量 > vol_mult
    ×vwma20) → 做多，止损 = max(AR_low, 收盘 - stop_atr×ATR)；跌破 AR_low
    对称做空。每个 UTC 自然日、每个方向只认第一次(避免边缘反复抽)。
  · 离场：吊灯止损(入场以来极值 -/+ chand_mult(3.0)×ATR 收盘触发)；或
    当日纽约盘收 21:00 UTC 强制平仓；周末(周六周日 UTC)不开新仓、已有
    仓位平掉(黄金现货周末休市，币安永续周末是薄成交漂移)。

周期 15m，bars_limit 672×15m≈7 天(够覆盖前几日 + 跨周末缓冲)。
唯一信号由绝对时钟驱动的黄金战法。
"""
from __future__ import annotations

import datetime
from typing import Dict, List, Optional

from strategy_engine import indicators

_DAY_MS = 24 * 60 * 60 * 1000
_MIN_MS = 60 * 1000

DEFAULT_PARAMS = {
    "asia_start_min": 0,           # 00:00 UTC
    "asia_end_min": 7 * 60,        # 07:00 UTC
    "london_open_min": 7 * 60,     # 07:00 UTC
    "ny_open_min": 13 * 60 + 30,   # 13:30 UTC
    "session_end_min": 21 * 60,    # 21:00 UTC 纽约盘收
    "entry_window_min": 240,       # 每个开盘后 4h 内允许突破进场
    "atr_len": 14,
    "max_range_atr": 18.0,         # 亚盘 7h 区间 vs 15m ATR 常态就有 10~13 倍，
                                   # 这道门只挡"已经在单边狂奔"的病态日
    "stop_atr_mult": 1.2,
    "chand_mult": 3.0,
    "vol_len": 20,
    "vol_mult": 1.1,
}


def _day_start_ms(t_ms: int) -> int:
    return (t_ms // _DAY_MS) * _DAY_MS


def _is_weekend(day_start_ms: int) -> bool:
    return datetime.datetime.utcfromtimestamp(day_start_ms / 1000.0).weekday() >= 5


def generate_signal(bars_by_tf: Dict[str, List[dict]], params: Optional[dict] = None, position: Optional[dict] = None) -> Optional[dict]:
    p = {**DEFAULT_PARAMS, **(params or {})}
    bars = bars_by_tf.get("base") or []
    atr_len = int(p["atr_len"])
    if len(bars) < atr_len + 40:
        return None

    last = bars[-1]
    price = float(last["c"])
    bar_time = int(last["t"])
    day0 = _day_start_ms(bar_time)
    mins_of_day = (bar_time - day0) / _MIN_MS
    atr = indicators.wilder_atr(bars, atr_len)
    if atr <= 0:
        return None

    # ── 周末：不开新仓，已有仓位清掉 ────────────────────────────────
    if _is_weekend(day0):
        if position:
            return {"action": "CLOSE_QUICK_EXIT", "price": round(price, 6),
                    "reason": "黄金周末休市，清仓", "bar_time": bar_time}
        return None

    seg_today = [b for b in bars if int(b["t"]) >= day0]

    # ── 持仓：吊灯止损 / 纽约盘收强平 ───────────────────────────────
    if position:
        side = str(position.get("side") or "").upper()
        if mins_of_day >= float(p["session_end_min"]):
            return {"action": "CLOSE_QUICK_EXIT", "price": round(price, 6),
                    "reason": "纽约盘收(21:00 UTC)强制平仓", "bar_time": bar_time}
        entry_bt = int(position.get("entry_bar_time") or 0)
        seg = [b for b in bars if int(b["t"]) >= entry_bt] or [last]
        if side == "LONG":
            hh = max(float(b["h"]) for b in seg)
            if price < hh - float(p["chand_mult"]) * atr:
                return {"action": "CLOSE_QUICK_EXIT", "price": round(price, 6),
                        "reason": f"吊灯止损(极值{hh:.3f}-{p['chand_mult']}ATR)", "bar_time": bar_time}
        elif side == "SHORT":
            ll = min(float(b["l"]) for b in seg)
            if price > ll + float(p["chand_mult"]) * atr:
                return {"action": "CLOSE_QUICK_EXIT", "price": round(price, 6),
                        "reason": f"吊灯止损(极值{ll:.3f}+{p['chand_mult']}ATR)", "bar_time": bar_time}
        return None

    # ── 空仓：亚盘区间 + 伦敦/纽约开盘窗口首次放量突破 ─────────────
    asia = [b for b in seg_today
            if float(p["asia_start_min"]) <= (int(b["t"]) - day0) / _MIN_MS < float(p["asia_end_min"])]
    if len(asia) < 6:
        return None
    ar_hi = max(float(b["h"]) for b in asia)
    ar_lo = min(float(b["l"]) for b in asia)
    if ar_hi - ar_lo > float(p["max_range_atr"]) * atr or ar_hi <= ar_lo:
        return None

    win = float(p["entry_window_min"])
    in_london = float(p["london_open_min"]) <= mins_of_day < float(p["london_open_min"]) + win
    in_ny = float(p["ny_open_min"]) <= mins_of_day < float(p["ny_open_min"]) + win
    if not (in_london or in_ny):
        return None

    vv = indicators.vwma_of_volume(bars, int(p["vol_len"]))
    if not vv or vv[-1] <= 0:
        return None
    vol_ok = float(last.get("v") or 0.0) > float(p["vol_mult"]) * vv[-1]
    if not vol_ok:
        return None

    # 当日该方向是否已突破过(只认第一次)
    post_asia = [b for b in seg_today if (int(b["t"]) - day0) / _MIN_MS >= float(p["asia_end_min"])]
    earlier = post_asia[:-1]
    sess = in_london and "伦敦" or "纽约"

    if price > ar_hi and not any(float(b["c"]) > ar_hi for b in earlier):
        stop = max(ar_lo, price - float(p["stop_atr_mult"]) * atr)
        if stop >= price:
            return None
        return {"action": "LONG", "price": round(price, 6), "atr": round(atr, 6),
                "stop_loss": round(stop, 6), "tier": 1, "bar_time": bar_time,
                "reason": f"{sess}开盘突破亚盘区间高{ar_hi:.3f}+放量"}
    if price < ar_lo and not any(float(b["c"]) < ar_lo for b in earlier):
        stop = min(ar_hi, price + float(p["stop_atr_mult"]) * atr)
        if stop <= price:
            return None
        return {"action": "SHORT", "price": round(price, 6), "atr": round(atr, 6),
                "stop_loss": round(stop, 6), "tier": 1, "bar_time": bar_time,
                "reason": f"{sess}开盘跌破亚盘区间低{ar_lo:.3f}+放量"}
    return None
