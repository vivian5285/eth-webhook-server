#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
52周新高锚定效应（52-Week High Anchor）—— 2026-09-10 应宝贝转发的千问
《超越经典动量/均值回归》整理稿新增。学术来源：George & Hwang (2004)
《The 52-Week High and Momentum Investing》(Journal of Finance)——投资者
存在"锚定偏差"：价格接近 52 周高点时不愿追涨，导致正面信息未被充分
定价、随后继续上行。这个**单因子**在多项研究里预测力超过传统 12-1 月
动量，且参数极少、逻辑清晰，天然不容易过拟合。

信号：
  · ratio_hi = 现价 / 近 hl_window(252根≈52周) 最高价；越接近 1 越贴前高
  · ratio_lo = 现价 / 近 hl_window 最低价；越接近 1 越贴前低
规则：
  · 做多：ratio_hi ≥ near_high_frac(0.95，即离 52 周高不到 5%) 且当根收盘
    创近 breakout_lookback(20) 日新高（确认还在动，不是贴着高点阴跌）。
  · 做空：ratio_lo ≤ near_low_frac(1.05) 且创近 20 日新低。
  · 离场：ratio_hi 跌破 exit_high_frac(0.85，离 52 周高已 15%+，锚定失效)；
    做空对称 ratio_lo 涨破 1.15。ATR 止损兜底，不设固定止盈（让它顺着
    锚定效应慢慢跑）。
  · tier：几乎贴在极值（ratio_hi ≥ 0.99 / ratio_lo ≤ 1.01）记 2 档。

跟擂台已有突破类的区别：turtle_breakout / donchian_reversal 看的是 20 日
级别的通道突破；这套的锚是**52 周**级别的极值，而且允许"仅仅接近"极值
就进场（不强求当根刚突破），离场也慢（靠 ratio 衰减而非反向通道）。跟
weinstein_stage（30 周 SMA 位置分四阶段）也不同——这套只认与历史极值的
相对距离这一个量。单品种，1d 周期，跑全 _ALL_SYMBOLS。
"""
from __future__ import annotations

from typing import Dict, List, Optional

from strategy_engine import indicators

DEFAULT_PARAMS = {
    "hl_window": 252,
    "near_high_frac": 0.95,
    "near_low_frac": 1.05,
    "exit_high_frac": 0.85,
    "exit_low_frac": 1.15,
    "breakout_lookback": 20,
    "atr_len": 14,
    "atr_stop_mult": 3.0,
}


def generate_signal(bars_by_tf: Dict[str, List[dict]], params: Optional[dict] = None, position: Optional[dict] = None) -> Optional[dict]:
    p = {**DEFAULT_PARAMS, **(params or {})}
    bars = bars_by_tf.get("base") or []
    hlw = int(p["hl_window"])
    blb = int(p["breakout_lookback"])
    atr_len = int(p["atr_len"])
    if len(bars) < hlw + 5:
        return None

    last = bars[-1]
    price = float(last["c"])
    bar_time = int(last["t"])
    window = bars[-hlw:]
    hh = max(float(b["h"]) for b in window)
    ll = min(float(b["l"]) for b in window)
    if hh <= 0 or ll <= 0:
        return None
    ratio_hi = price / hh
    ratio_lo = price / ll

    if position:
        side = str(position.get("side") or "").upper()
        if side == "LONG" and ratio_hi <= float(p["exit_high_frac"]):
            return {"action": "CLOSE_QUICK_EXIT", "price": round(price, 6),
                    "reason": f"离52周高已{(1-ratio_hi)*100:.0f}%，锚定效应失效", "bar_time": bar_time}
        if side == "SHORT" and ratio_lo >= float(p["exit_low_frac"]):
            return {"action": "CLOSE_QUICK_EXIT", "price": round(price, 6),
                    "reason": f"离52周低已{(ratio_lo-1)*100:.0f}%，锚定效应失效", "bar_time": bar_time}
        return None

    prior = bars[-1 - blb:-1]
    if len(prior) < blb:
        return None
    new_high = price > max(float(b["h"]) for b in prior)
    new_low = price < min(float(b["l"]) for b in prior)
    atr = indicators.wilder_atr(bars, atr_len)
    if atr <= 0:
        return None

    if ratio_hi >= float(p["near_high_frac"]) and new_high:
        return {"action": "LONG", "price": round(price, 6), "atr": round(atr, 6),
                "stop_loss": round(price - atr * float(p["atr_stop_mult"]), 6),
                "tier": 2 if ratio_hi >= 0.99 else 1, "bar_time": bar_time,
                "reason": f"贴52周高(现价={ratio_hi*100:.1f}%×52wk高)+创{blb}日新高"}
    if ratio_lo <= float(p["near_low_frac"]) and new_low:
        return {"action": "SHORT", "price": round(price, 6), "atr": round(atr, 6),
                "stop_loss": round(price + atr * float(p["atr_stop_mult"]), 6),
                "tier": 2 if ratio_lo <= 1.01 else 1, "bar_time": bar_time,
                "reason": f"贴52周低(现价={ratio_lo*100:.1f}%×52wk低)+创{blb}日新低"}
    return None
