#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Heikin Ashi 趋势 —— 2026-09-10 应宝贝"加著名指标"要求新增。平均K线
(Heikin-Ashi) 是把 OHLC 做递归平滑的经典 K 线变换，"骑趋势"最常用的
可视化方法之一，规则完全确定。

HA 计算：
  HA_close = (o + h + l + c) / 4
  HA_open  = (前一根 HA_open + 前一根 HA_close) / 2   （首根用 (o+c)/2）
  HA_high  = max(h, HA_open, HA_close)
  HA_low   = min(l, HA_open, HA_close)

规则：
  · 连续 streak_len(3) 根同色 HA K 线（阳：HA_close>HA_open）→ 第 streak_len
    根顺势进场。可选加强：要求这几根 HA 实体在放大（趋势在加速）。
  · 止损：ATR × atr_stop_mult。
  · 离场：出现第一根反色 HA K 线（趋势转弱信号），或出现明显反向影线
    （阳线里 HA_open−HA_low > 实体的 wick_frac 倍 = 下方承压），或 ATR 止损。
    不设固定止盈——HA 的用途就是"一直待在趋势里直到颜色变"。

跟擂台已有趋势跟随的区别：turtle/ema_cross/supertrend/hma 都是在**原始
价格**上算指标；HA 是先把 K 线本身平滑掉再看颜色，对单根插针不敏感，
连续同色 = 趋势的"视觉确认"。是这一簇里唯一"改造 K 线本身"的做法。
周期 4h。
"""
from __future__ import annotations

from typing import Dict, List, Optional

from strategy_engine import indicators

DEFAULT_PARAMS = {
    "streak_len": 3,
    "require_growing_body": True,
    "wick_frac": 1.0,
    "atr_len": 14,
    "atr_stop_mult": 2.0,
    # 2026-09-19新增(heikin_ashi_trend_v2对照实验，宝贝要求)：
    # require_growing_body原版要求streak里"实体一根比一根大"——这个条件
    # 恰好把入场点锁定在这波HA同色行情里实体最夸张、最延伸的那一根，是
    # 系统性的偏晚入场，不是随机噪音。HA本身的经典读法是"强势K线下影极短"，
    # 但这套代码只把这条读法用在离场(wick_frac)上，入场反而没用——等于
    # 拿HA自己的强弱标准去晚出场，却不用来挑好入场点。
    # require_clean_entry_bar(默认False=原行为不变)：True时不再要求实体
    # 递增，改成要求streak最后一根(触发进场那一根)本身下影(多)/上影(空)
    # 够短——用HA自身已有的定义去"评分入场质量"，而不是新造一个参数。
    "require_clean_entry_bar": False,
    # wick_exit_atr_floor_frac(默认0.0=原行为不变，分母floor仍是1e-9)：
    # 原版离场判断 (cur.o - cur.l) > wick_frac × max(实体, 1e-9) —— HA实体
    # 收缩趋近0(十字星，趋势中段很常见的犹豫K线)时分母趋近1e-9，任何一点
    # 下影线都会触发离场，等于对十字星极度敏感、经常提前把仓位震出去。
    # 传大于0的值(比如0.15)后分母floor改成max(实体, floor_frac×ATR)，
    # 用ATR兜住十字星场景，不再对分母趋零敏感——这是数值稳定性修正，
    # 不是新的择时逻辑。
    "wick_exit_atr_floor_frac": 0.0,
    # use_ema_direction_filter：额外要求EMA(7)相对EMA(25)的站上/跌破方向
    # 跟HA颜色方向一致，过滤"HA刚好连续同色但大周期其实还在盘整"的情况。
    "use_ema_direction_filter": False,
    "ema_fast_len": 7,
    "ema_slow_len": 25,
}


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
    atr = indicators.wilder_atr(bars, atr_len)
    floor_frac = float(p.get("wick_exit_atr_floor_frac") or 0.0)
    wick_floor = max(floor_frac * atr, 1e-9) if atr > 0 else 1e-9

    if position:
        side = str(position.get("side") or "").upper()
        if side == "LONG":
            if not _green(cur):
                return {"action": "CLOSE_QUICK_EXIT", "price": round(price, 6),
                        "reason": "HA 转阴，趋势转弱", "bar_time": bar_time}
            if (cur["o"] - cur["l"]) > float(p["wick_frac"]) * max(_body(cur), wick_floor):
                return {"action": "CLOSE_QUICK_EXIT", "price": round(price, 6),
                        "reason": "HA 阳线现明显下影，上涨承压", "bar_time": bar_time}
        elif side == "SHORT":
            if _green(cur):
                return {"action": "CLOSE_QUICK_EXIT", "price": round(price, 6),
                        "reason": "HA 转阳，趋势转弱", "bar_time": bar_time}
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

    if bool(p.get("use_ema_direction_filter")):
        closes = indicators.closes(bars)
        ema_f = indicators.ema(closes, int(p["ema_fast_len"]))
        ema_s = indicators.ema(closes, int(p["ema_slow_len"]))
        if not ema_f or not ema_s:
            return None
        if all_green and not (ema_f[-1] > ema_s[-1]):
            return None
        if all_red and not (ema_f[-1] < ema_s[-1]):
            return None

    d = 1 if all_green else -1
    return {
        "action": "LONG" if d == 1 else "SHORT",
        "price": round(price, 6), "atr": round(atr, 6),
        "stop_loss": round(price - d * atr * float(p["atr_stop_mult"]), 6),
        "tier": 1, "bar_time": bar_time,
        "reason": f"连续 {sk} 根 HA {'阳' if d == 1 else '阴'}线{'(实体放大)' if p['require_growing_body'] else ''}",
    }
