#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
能量潮背离(On-Balance Volume Divergence)——Joseph Granville 1963年公开
发表OBV指标，"用OBV背离判断趋势即将反转"是几十年公开教材里最经典的
OBV用法之一，不是网红自创。

核心思想：价格创新高本身不代表这波上涨"健康"——如果创新高的同时，
累计成交量能量潮(OBV)没有跟着创新高，说明这波上涨背后的买盘力度
其实在减弱(可能是量缩上涨、或者上涨主要靠少数几根大阳线堆出来的)，
是趋势见顶的早期警示信号；底部背离对称。

跟本仓库其余"背离"类战法的关键区别：chanlun_pivot的背驰比较的是
MACD柱状图面积(动量维度，价格变化速度)；这套比较的是**累计成交量**
本身(资金流维度，量在先、价在后)——是本擂台里唯一一套纯粹从"成交量"
这个数据源构造背离信号的战法，跟看"成交量随价格分布"的volume_
profile_reversion、看"单根K线放量确认"的wyckoff_spring都不是同一个
维度(那两个是空间/瞬时视角，这个是时间序列的累计视角)。

规则：
  - 用轻量级3根K线局部极值识别法找最近两个摆动高点(顶背离)/摆动低点
    (底背离)，不做K线合并处理(比chanlun_pivot的正统缠论分型识别简单
    得多，是刻意的简化——这套只需要"两个连续摆动点"，不需要完整的
    笔/中枢结构)
  - 顶背离：价格的后一个摆动高点比前一个更高，但对应时刻的OBV比前一个
    更低 → 做空
  - 底背离：价格的后一个摆动低点比前一个更低，但对应时刻的OBV比前一个
    更高 → 做多
  - 离场：价格突破触发背离信号的那个摆动点、朝不利方向再创极值(说明
    背离判断错了，趋势并没有反转)，或ATR止损兜底

周期选择理由：4h，摆动点识别需要一定的历史深度积累，4h是本仓库"日线
合理代理"的既定选择，跟chanlun_pivot(同属结构/背离类)保持同周期方便
对照"用哪种背离信号源更好"。

数据要求：至少lookback_bars(默认60，够找到至少2个摆动高点+2个摆动
低点的经验值)+atr_len+4根bars_by_tf["base"]。
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
    obv_series = indicators.obv(bars)

    highs = indicators.swing_points(window, "high")
    lows = indicators.swing_points(window, "low")

    last = bars[-1]
    price = _f(last["c"])
    bar_time = int(last["t"])

    if position:
        side = str(position.get("side") or "").upper()
        # 用当前窗口里最新的同类型摆动点做结构失效判断(自然重放，无需
        # 额外持久化状态)
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
        obv1, obv2 = obv_series[offset + i1], obv_series[offset + i2]
        if p2 > p1 and obv2 < obv1:
            action, d = "SHORT", -1
            reason = f"顶背离：价格新高{p2:.6f}>{p1:.6f}，OBV反而{obv2:.0f}<{obv1:.0f}"
    if action is None and len(lows) >= 2:
        (i1, p1), (i2, p2) = lows[-2], lows[-1]
        obv1, obv2 = obv_series[offset + i1], obv_series[offset + i2]
        if p2 < p1 and obv2 > obv1:
            action, d = "LONG", 1
            reason = f"底背离：价格新低{p2:.6f}<{p1:.6f}，OBV反而{obv2:.0f}>{obv1:.0f}"

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
