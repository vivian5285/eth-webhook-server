#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Bollinger+RSI逆势均值回归——2026-09-01根据宝贝从QuantConnect克隆下来的
真实项目源码复现(Indicators.py::CustomBollingerBands/RSIIndicator/
TrendFilter + AssetStrategy.py::AssetArbitrageStrategy + main.py::
VolatilityArbitrage，四个文件构成完整可跑的QCAlgorithm)。

**诚实说明**：这份源码本身是真实、完整、可复现的QuantConnect项目代码，
规则精确(下面列的每个数字都是源码原样抄的，不是猜的)；但回测区间写的
是2023-10-01~2023-12-31，跟"Open Quant League 2025 Q3夺冠(Lake Forest
College, 夏普3.93)"那次公告对不上——大概率是宝贝在QuantConnect社区
浏览时找到的一个概念高度相似(布林带+RSI+趋势过滤，覆盖加密货币+股票)
但不是同一个项目的公开策略，或者是同一作者更早的版本。不影响这份代码
本身的真实性/可复现性——依然是"有真实源码、非黑箱"这条准入门槛要求的
东西，只是不能标榜成"就是那个冠军策略"。

规则(源码原样抄，未改动任何参数)：
  - 布林带：20周期(日线)，2倍标准差(CustomBollingerBands)
  - RSI：14周期(日线)(RSIIndicator)
  - 趋势过滤：50周期SMA(日线)(TrendFilter)
  - 做多：无持仓 + 收盘价<下轨 + RSI<30 + 收盘价>SMA50(上升趋势)
  - 做空：无持仓 + 收盘价>上轨 + RSI>70 + 收盘价<SMA50(下降趋势)
  - 止损：多单固定5%、空单固定3%(源码里long_stop_loss=0.05/
    short_stop_loss=0.03，两个方向故意不对称，源码没解释为什么，原样保留)
  - 离场：价格回到中轨(20周期SMA)就平仓，不管盈亏

跟本仓库其余策略的对照：跟connors_rsi2同属"逆势均值回归"但机制不同——
connors_rsi2用RSI(2)极短周期超卖超买+SMA200更长期趋势过滤；这套用
RSI(14)常规周期+布林带极值双重确认+SMA50中期趋势过滤，进场条件更严格
(两个指标都要在极端区间)，退场逻辑也不同(中轨 vs SMA5)。跟bollinger_
squeeze同样用布林带但方向相反——squeeze是"缩量后放量突破"的趋势延续
逻辑，这套是"触及极值后均值回归"的逆势逻辑，同一个指标两种反着用的
经典流派对照组。

数据要求：SMA50+RSI14+BB20需要至少51根bars_by_tf["base"]历史，源码用
日线(Resolution.Daily)，本模块同样用1d周期。
"""
from __future__ import annotations

from typing import Dict, List, Optional

from strategy_engine import indicators

DEFAULT_PARAMS = {
    "bb_len": 20,
    "bb_mult": 2.0,
    "rsi_len": 14,
    "rsi_entry_long": 30.0,
    "rsi_entry_short": 70.0,
    "trend_sma_len": 50,
    "long_stop_pct": 0.05,
    "short_stop_pct": 0.03,
}


def generate_signal(bars_by_tf: Dict[str, List[dict]], params: Optional[dict] = None, position: Optional[dict] = None) -> Optional[dict]:
    bars = bars_by_tf.get("base") or []
    p = {**DEFAULT_PARAMS, **(params or {})}
    bb_len = int(p["bb_len"])
    bb_mult = float(p["bb_mult"])
    rsi_len = int(p["rsi_len"])
    trend_len = int(p["trend_sma_len"])
    need = max(bb_len, rsi_len + 1, trend_len) + 2
    if len(bars) < need:
        return None

    cs = indicators.closes(bars)
    mid = indicators.sma(cs, bb_len)
    sd = indicators.stdev(cs, bb_len)
    rsi_series = indicators.rsi(cs, rsi_len)
    trend_sma = indicators.sma(cs, trend_len)
    if not mid or not sd or not rsi_series or not trend_sma:
        return None

    last = bars[-1]
    price = float(last["c"])
    bar_time = int(last["t"])
    mid_now = mid[-1]
    upper_now = mid_now + bb_mult * sd[-1]
    lower_now = mid_now - bb_mult * sd[-1]
    rsi_now = rsi_series[-1]
    trend_now = trend_sma[-1]

    if position:
        side = str(position.get("side") or "").upper()
        # 源码原样：离场只看中轨，不管当时是不是盈利
        if side == "LONG" and price >= mid_now:
            return {
                "action": "CLOSE_QUICK_EXIT",
                "price": round(price, 6),
                "reason": f"回归完成·收盘价回到中轨SMA{bb_len}({mid_now:.6f})以上",
                "bar_time": bar_time,
            }
        if side == "SHORT" and price <= mid_now:
            return {
                "action": "CLOSE_QUICK_EXIT",
                "price": round(price, 6),
                "reason": f"回归完成·收盘价回到中轨SMA{bb_len}({mid_now:.6f})以下",
                "bar_time": bar_time,
            }
        return None

    if price < lower_now and rsi_now < float(p["rsi_entry_long"]) and price > trend_now:
        action = "LONG"
    elif price > upper_now and rsi_now > float(p["rsi_entry_short"]) and price < trend_now:
        action = "SHORT"
    else:
        return None

    atr = indicators.wilder_atr(bars, 14)
    if atr <= 0:
        return None
    direction = 1 if action == "LONG" else -1
    # 源码止损是固定百分比(不是ATR)，多空故意不对称(5%/3%)，原样保留
    stop_pct = float(p["long_stop_pct"]) if action == "LONG" else float(p["short_stop_pct"])
    stop_loss = round(price * (1 - direction * stop_pct), 6)

    return {
        "action": action,
        "price": round(price, 6),
        "atr": round(atr, 6),
        "stop_loss": stop_loss,
        "tp1": round(mid_now, 6),
        "tp2": round(mid_now, 6),
        "tp3": round(mid_now, 6),
        "tier": 1,
        "bar_time": bar_time,
        "reason": (
            f"{action} 触及布林带极值(上/下轨={upper_now:.4f}/{lower_now:.4f}) "
            f"RSI{rsi_len}={rsi_now:.1f} SMA{trend_len}={trend_now:.4f}确认趋势"
        ),
    }
