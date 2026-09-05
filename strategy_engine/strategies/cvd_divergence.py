#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
累计成交量差值背离(CVD/Delta Divergence)——2026-09-05应宝贝转发的
"永续合约主流战法大全"整理稿新增。Delta/CVD是订单流分析里的标准公开
概念，各大交易所/图表软件(Binance自己的期货界面、TradingView等)都有
现成CVD指标可看，规则透明公开、不是黑箱。

**这套用的是真实数据，不是代理指标**——币安K线接口本身就带
taker_buy_base_asset_volume(主动买入量)这个字段，是交易所自己对每一笔
成交按主动方分类后汇总的真实结果；本擂台已有的obv_divergence用的OBV
是"看收盘价比上一根涨还是跌"来猜多空力量对比，是一种代理近似，这套
CVD直接用交易所自己的分类结果，理论上比OBV更准。klines.py 2026-09-05
新增了"tb"字段专门给这套用。

规则(跟obv_divergence同一套背离检测框架，只换了成交量数据源，方便
直接对照"哪种成交量信号源更好用")：
  - 用轻量级3根K线局部极值找最近两个摆动高点/低点
  - 顶背离：价格新高，但CVD(累计主动买卖差值)反而走低 → 做空(上涨
    主要是空头回补/被动成交撑起来的，不是真主动买盘推动)
  - 底背离：价格新低，但CVD反而走高 → 做多(下跌途中主动买盘其实在
    增加，可能是主动买盘在低位吸筹)
  - 离场：价格突破触发背离信号的摆动点、朝不利方向再创极值(背离判断
    失效)，或ATR止损兜底

跟obv_divergence的关键区别：OBV是"价格方向×总成交量"的代理指标，CVD
是"交易所自己分类的主动买卖盘差值"，本擂台第一套、也是唯一一套用
**真实主动买卖盘分类数据**(而不是从OHLCV自己推算)构造信号的战法。
两套用完全相同的背离检测框架、同一周期跑，是本擂台"控制变量对照实验"
的又一组：变量只有成交量数据源本身。

周期选择理由：4h，跟obv_divergence同周期，保证对照实验只有数据源这一个
变量。

数据要求：至少lookback_bars(默认60)+atr_len+4根bars_by_tf["base"]，
且K线必须带"tb"字段(get_bars/to_ohlcv_dicts 2026-09-05起默认自带)。
"""
from __future__ import annotations

from typing import Dict, List, Optional

from strategy_engine import indicators

DEFAULT_PARAMS = {
    "lookback_bars": 60,
    "atr_len": 14,
    "atr_stop_mult": 2.0,
}


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def generate_signal(bars_by_tf: Dict[str, List[dict]], params: Optional[dict] = None, position: Optional[dict] = None) -> Optional[dict]:
    bars = bars_by_tf.get("base") or []
    p = {**DEFAULT_PARAMS, **(params or {})}
    lookback = int(p["lookback_bars"])
    atr_len = int(p["atr_len"])
    need = lookback + atr_len + 4
    if len(bars) < need:
        return None

    window = bars[-lookback:]
    offset = len(bars) - len(window)  # window下标 -> bars下标的偏移
    cvd_series = indicators.cvd(bars)

    highs = indicators.swing_points(window, "high")
    lows = indicators.swing_points(window, "low")

    last = bars[-1]
    price = _f(last["c"])
    bar_time = int(last["t"])

    if position:
        side = str(position.get("side") or "").upper()
        if side == "LONG" and lows:
            _, last_low_price = lows[-1]
            if price < last_low_price:
                return {
                    "action": "CLOSE_QUICK_EXIT", "price": round(price, 6),
                    "reason": f"跌破最近摆动低点({last_low_price:.6f})，底背离判断失效", "bar_time": bar_time,
                }
        if side == "SHORT" and highs:
            _, last_high_price = highs[-1]
            if price > last_high_price:
                return {
                    "action": "CLOSE_QUICK_EXIT", "price": round(price, 6),
                    "reason": f"突破最近摆动高点({last_high_price:.6f})，顶背离判断失效", "bar_time": bar_time,
                }
        return None

    action = None
    reason = ""
    if len(highs) >= 2:
        (i1, p1), (i2, p2) = highs[-2], highs[-1]
        c1, c2 = cvd_series[offset + i1], cvd_series[offset + i2]
        if p2 > p1 and c2 < c1:
            action, d = "SHORT", -1
            reason = f"顶背离：价格新高{p2:.6f}>{p1:.6f}，CVD反而{c2:.0f}<{c1:.0f}"
    if action is None and len(lows) >= 2:
        (i1, p1), (i2, p2) = lows[-2], lows[-1]
        c1, c2 = cvd_series[offset + i1], cvd_series[offset + i2]
        if p2 < p1 and c2 > c1:
            action, d = "LONG", 1
            reason = f"底背离：价格新低{p2:.6f}<{p1:.6f}，CVD反而{c2:.0f}>{c1:.0f}"

    if action is None:
        return None

    atr = indicators.wilder_atr(bars, atr_len)
    if atr <= 0:
        return None

    return {
        "action": action,
        "price": round(price, 6),
        "atr": round(atr, 6),
        "stop_loss": round(price - d * atr * float(p["atr_stop_mult"]), 6),
        "tier": 1,
        "bar_time": bar_time,
        "reason": reason,
    }
