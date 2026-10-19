#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MACD × RSI 极值反转(宝贝要求：RSI 底部20、顶部80，再用MACD确认转向) —— 2026-10-06 新增(纸面)。

擂台里原有的相关战法都是"单指标"：macd_histogram(只看MACD柱)、rsi_mean_reversion(只看RSI，含2080变体)、
mtf_ema_macd_cci(MACD+EMA+CCI，没有RSI)。没有一条是"RSI极值 + MACD转向"联合触发的，这条补上。

规则：
  做多 = 最近 lookback(6)根内 RSI(14) 曾跌到 ≤20(极端超卖)  且  当前这根 MACD柱(12,26,9)由≤0翻成>0(即MACD线上穿信号线)
  做空 = 最近 lookback 根内 RSI 曾涨到 ≥80(极端超买)       且  当前这根 MACD柱由≥0翻成<0
  离场 = RSI回到中性(exit_level，默认50)  或  MACD柱反向翻转(确认丢失)  或  ATR止损(默认2×ATR)兜底
  RSI极端程度越深(≤15/≥85)记tier2，否则tier1。
为什么要两个条件：RSI 20/80 本身是"已经跌/涨过头"，但极端值可以继续极端(单边行情)；MACD柱翻向是"动能开始掉头"
的确认，两者同时才进，换来更少但更干净的信号(代价是信号很稀少，所以同时开4h与1h两个周期对照)。
"""
from __future__ import annotations

from typing import Dict, List, Optional

from strategy_engine import indicators

DEFAULT_PARAMS = {
    "rsi_period": 14,
    "os": 20.0,
    "ob": 80.0,
    "os_tier2": 15.0,
    "ob_tier2": 85.0,
    "lookback": 6,
    "macd_fast": 12,
    "macd_slow": 26,
    "macd_signal": 9,
    "exit_level": 50.0,
    "atr_len": 14,
    "atr_stop_mult": 2.0,
    # 2026-10-06 优化(4h/43品种/2.7年网格+前半选参后半验证)新增，默认值=原版行为不变：
    "macd_mode": "cross",     # cross=MACD柱过零；turn=柱子连续2根向零收缩(更早)
    "trend_ema": 0,           # >0=顺势过滤：多头只在收盘>EMA(n)、空头只在收盘<EMA(n)时做(优化版用100)
    "exit_mode": "rsi_or_macd",   # rsi_or_macd=RSI回50或MACD反向；macd_only=只看MACD柱反向翻转
}


def generate_signal(bars_by_tf: Dict[str, List[dict]], params: Optional[dict] = None, position: Optional[dict] = None) -> Optional[dict]:
    bars = bars_by_tf.get("base") or []
    p = {**DEFAULT_PARAMS, **(params or {})}
    need = int(p["macd_slow"]) + int(p["macd_signal"]) + int(p["lookback"]) + 5
    if len(bars) < max(need, int(p["rsi_period"]) + int(p["atr_len"]) + 10):
        return None
    closes = indicators.closes(bars)
    rsi = indicators.rsi(closes, int(p["rsi_period"]))
    _, _, hist = indicators.macd(closes, int(p["macd_fast"]), int(p["macd_slow"]), int(p["macd_signal"]))
    lb = int(p["lookback"])
    if len(rsi) < lb + 2 or len(hist) < 3:
        return None
    last = bars[-1]
    price = float(last["c"])
    bar_time = int(last["t"])
    h0, h1 = hist[-2], hist[-1]
    cross_up = h0 <= 0 < h1
    cross_dn = h0 >= 0 > h1
    hm1 = hist[-3]
    if str(p["macd_mode"]) == "turn":
        open_up = h1 < 0 and h1 > h0 > hm1
        open_dn = h1 > 0 and h1 < h0 < hm1
    else:
        open_up, open_dn = cross_up, cross_dn
    rsi_now = rsi[-1]

    if position:
        side = str(position.get("side") or "").upper()
        exit_level = float(p["exit_level"])
        macd_only = str(p["exit_mode"]) == "macd_only"
        if side == "LONG" and ((not macd_only and rsi_now >= exit_level) or cross_dn):
            return {"action": "CLOSE_QUICK_EXIT", "price": round(price, 6),
                    "reason": "RSI回到中性(%.1f)或MACD柱转负，反弹离场" % rsi_now, "bar_time": bar_time}
        if side == "SHORT" and ((not macd_only and rsi_now <= exit_level) or cross_up):
            return {"action": "CLOSE_QUICK_EXIT", "price": round(price, 6),
                    "reason": "RSI回到中性(%.1f)或MACD柱转正，回落离场" % rsi_now, "bar_time": bar_time}
        return None

    recent = rsi[-lb:]
    rmin, rmax = min(recent), max(recent)
    d = 0
    tema = float(p["trend_ema"] or 0)
    ema_v = None
    if tema > 0:
        _e = indicators.ema(closes, int(tema))
        if not _e:
            return None          # K线不足以算顺势均线(新上市品种)：不出信号，不崩
        ema_v = _e[-1]
    if rmin <= float(p["os"]) and open_up and (ema_v is None or price > ema_v):
        d = 1
    elif rmax >= float(p["ob"]) and open_dn and (ema_v is None or price < ema_v):
        d = -1
    if d == 0:
        return None
    atr = indicators.wilder_atr(bars, int(p["atr_len"]))
    if atr <= 0:
        return None
    deep = (rmin <= float(p["os_tier2"])) if d == 1 else (rmax >= float(p["ob_tier2"]))
    return {
        "action": "LONG" if d == 1 else "SHORT", "price": round(price, 6), "atr": round(atr, 6),
        "stop_loss": round(price - d * atr * float(p["atr_stop_mult"]), 6),
        "tier": 2 if deep else 1, "bar_time": bar_time,
        "reason": "RSI极值(近%d根%s=%.1f)+MACD柱%s" % (lb, "最低" if d == 1 else "最高", rmin if d == 1 else rmax, "翻正" if d == 1 else "翻负"),
    }
