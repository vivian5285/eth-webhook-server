#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
TD Sequential（Tom DeMark，TD Setup 阶段 9 计数）—— 2026-09-10 应宝贝
"加著名指标战法"要求新增。DeMark 的 TD Sequential 是真机构在用的确定性
趋势衰竭系统（数 K 线，无参数拟合空间），本模块只做最核心、最广为人知
的 **TD Setup 9 计数**（不做后续 TD Countdown 13 计数）。

规则（TD Setup）：
  · 买入 Setup：连续 9 根，每根收盘 < 4 根之前那根的收盘。第 9 根 =
    下跌趋势可能衰竭 → 做多（逆势/反转）。
  · 卖出 Setup：连续 9 根，每根收盘 > 4 根之前那根的收盘。第 9 根 →
    做空。
  · 中断：任何一根不满足 </>（相对 4 根前）条件，计数清零重来。
  · "完美化(perfection)"：买入 Setup 第 8 或第 9 根的最低价 ≤ 第 6、7 根
    的最低价（卖出对称用最高价）——满足记 tier2（更高确信）。
  · 止损：买入 Setup 放 9 根里的最低最低价 − 0.5×ATR；卖出对称。
  · 离场：反向 Setup 完成、或持有超过 max_hold(13) 根（衰竭反转是快照式
    的，对应 DeMark Countdown 的长度）、或 ATR 止损。不设固定止盈。

跟擂台已有的关键区别：这是**唯一一套纯"趋势衰竭反转"计数系统**。
wyckoff_spring / livermore_pivotal_point 是结构位反转，chanlun/obv/cvd
是背离反转，都要看形态/量；TD Setup 只数收盘价相对 4 根前的大小关系，
最干净、最不可能过拟合。周期 1d（DeMark 原始就是日线级别）。
"""
from __future__ import annotations

from typing import Dict, List, Optional

from strategy_engine import indicators

DEFAULT_PARAMS = {
    "setup_len": 9,
    "lookback_offset": 4,
    "atr_len": 14,
    "stop_pad_atr": 0.5,
    "max_hold": 13,
}


def _setup_count(bars: List[dict], offset: int, need: int, direction: int) -> int:
    """从最后一根往回数：连续满足 (direction=+1: close < close[-1-offset];
    -1: close > close[-1-offset]) 的根数，最多数到 need。"""
    cnt = 0
    n = len(bars)
    for k in range(n - 1, offset - 1, -1):
        c = float(bars[k]["c"])
        cref = float(bars[k - offset]["c"])
        ok = (c < cref) if direction == 1 else (c > cref)
        if not ok:
            break
        cnt += 1
        if cnt >= need:
            break
    return cnt


def generate_signal(bars_by_tf: Dict[str, List[dict]], params: Optional[dict] = None, position: Optional[dict] = None) -> Optional[dict]:
    p = {**DEFAULT_PARAMS, **(params or {})}
    bars = bars_by_tf.get("base") or []
    sl = int(p["setup_len"])
    off = int(p["lookback_offset"])
    atr_len = int(p["atr_len"])
    if len(bars) < sl + off + atr_len + 5:
        return None

    last = bars[-1]
    price = float(last["c"])
    bar_time = int(last["t"])
    atr = indicators.wilder_atr(bars, atr_len)
    if atr <= 0:
        return None

    buy_cnt = _setup_count(bars, off, sl, direction=1)
    sell_cnt = _setup_count(bars, off, sl, direction=-1)
    buy_done = buy_cnt >= sl
    sell_done = sell_cnt >= sl

    if position:
        side = str(position.get("side") or "").upper()
        entry_bt = int(position.get("entry_bar_time") or 0)
        held = sum(1 for b in bars if int(b["t"]) > entry_bt)
        if side == "LONG" and sell_done:
            return {"action": "CLOSE_QUICK_EXIT", "price": round(price, 6),
                    "reason": "反向：卖出 Setup 9 完成", "bar_time": bar_time}
        if side == "SHORT" and buy_done:
            return {"action": "CLOSE_QUICK_EXIT", "price": round(price, 6),
                    "reason": "反向：买入 Setup 9 完成", "bar_time": bar_time}
        if held >= int(p["max_hold"]):
            return {"action": "CLOSE_QUICK_EXIT", "price": round(price, 6),
                    "reason": f"持有超过 {p['max_hold']} 根（衰竭反转窗口关闭）", "bar_time": bar_time}
        return None

    seg = bars[-sl:]
    if buy_done:
        lo9 = min(float(b["l"]) for b in seg)
        perfect = (min(float(seg[-1]["l"]), float(seg[-2]["l"]))
                   <= min(float(seg[-4]["l"]), float(seg[-3]["l"])))
        stop = lo9 - float(p["stop_pad_atr"]) * atr
        if stop < price:
            return {"action": "LONG", "price": round(price, 6), "atr": round(atr, 6),
                    "stop_loss": round(stop, 6), "tier": 2 if perfect else 1, "bar_time": bar_time,
                    "reason": f"买入 Setup 9 完成{'(完美化)' if perfect else ''}，下跌衰竭"}
    if sell_done:
        hi9 = max(float(b["h"]) for b in seg)
        perfect = (max(float(seg[-1]["h"]), float(seg[-2]["h"]))
                   >= max(float(seg[-4]["h"]), float(seg[-3]["h"])))
        stop = hi9 + float(p["stop_pad_atr"]) * atr
        if stop > price:
            return {"action": "SHORT", "price": round(price, 6), "atr": round(atr, 6),
                    "stop_loss": round(stop, 6), "tier": 2 if perfect else 1, "bar_time": bar_time,
                    "reason": f"卖出 Setup 9 完成{'(完美化)' if perfect else ''}，上涨衰竭"}
    return None
