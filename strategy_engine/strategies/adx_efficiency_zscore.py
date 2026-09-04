#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ADX+效率比+Z-score回调(固定1:3盈亏比)——2026-09-05应宝贝转发DeepSeek
建议"两阶段AI过滤(Binance Futures AI Bot)"而新增。诚实说明：DeepSeek
原方案是"规则过滤器先筛出高分候选，再送给Claude AI做最终确认"，这里
**去掉了AI确认那一步**，只留规则过滤层——AI临场判断没法被记录成确定性
函数、没法回测复现，过不了本擂台准入线。规则层本身用的三个指标(ADX、
Kaufman效率比、Z-score)全部是本仓库/公开领域已经在用的经典公开指标
(Wilder发表ADX、Perry Kaufman发表效率比、Z-score是统计学标准工具)，
只是换了一种新组合方式，不是某个具名交易员的可考证实盘战绩，跟
vegas_tunnel/keltner_channel同一类"公开成体系、没有个人战绩背书"。

规则：
  - 方向：+DI>-DI判多头偏向，-DI>+DI判空头偏向(跟raschke_adx_pullback
    同一个方向判断口径)
  - 趋势确认：ADX(14) > adx_threshold(默认20，Wilder自己书里给的"有无
    趋势"经典分界线，比raschke_adx_pullback的30门槛更宽松——那套要求
    "非常强"的趋势，这套只要求"确实有趋势"就行，两套形成一组"门槛松紧"
    的对照)
  - 效率过滤：Kaufman效率比(净变化/波动路径总长，跟kaufman_ama用的是
    同一个公式) ≥ min_efficiency_ratio(默认0.20)——ADX有时对刚启动的
    趋势反应滞后，效率比是价格路径本身"走得直不直"的直接度量，两个
    一起用能比单用ADX更早排除"看着有趋势、实际来回震荡"的假趋势
  - 回调入场：价格相对zscore_period(默认20)根滚动均值/标准差算出的
    Z-score，最近pullback_window(默认4)根内跌到过multiplier=-zscore_
    entry(默认1.0)以下(做多方向)，当前这根重新收回到-zscore_entry之上
    ——"在确认有效率的趋势里，等一次真实回调，回调修复了再进"，
    不是追涨追跌
  - 离场：**固定1:3盈亏比**——止损1.5×ATR，止盈4.5×ATR，都在开仓那一刻
    锁定，交给runner通用止损止盈机制执行，本模块不设额外的主动离场
    判断。这是刻意的纪律选择：DeepSeek这批建议里最有价值的洞察是"胜率
    不重要，盈亏比才重要"(Gate.io六模型对决里，DeepSeek以41%的胜率、
    但6.71的盈亏比拿到了最高收益)——本战法把这个原则做成了硬约束，
    不臨場加判断、不该止损就止损、不该止盈就止盈，赌的就是"控制频率、
    放大盈亏比"这条路径本身，不是判断力。

跟本仓库其余ADX类战法的关键区别：raschke_adx_pullback回踩的是EMA(20)
均线本身；这套回踩的是Z-score(统计意义上的"偏离均值多少个标准差")，
且多了一层效率比过滤(raschke没有)，止盈止损是**固定**的(raschke没有
设固定止盈，止损用ATR倍数但没有配对的固定止盈)。跟kaufman_ama用效率比
调整均线速度(自适应)不同，这套把效率比当成一道**门槛**(regime filter)，
不是拿来调速度。

周期选择理由：4h，跟adx_regime_switch/raschke_adx_pullback/
supertrend_adx同一批"ADX主导判断"战法用同一周期，方便横向比较。

数据要求：至少max(adx_len*2+2, zscore_period+pullback_window, er_period)
+atr_len+4根bars_by_tf["base"]。
"""
from __future__ import annotations

from typing import Dict, List, Optional

from strategy_engine import indicators

DEFAULT_PARAMS = {
    "adx_len": 14,
    "adx_threshold": 20.0,
    "er_period": 10,
    "min_efficiency_ratio": 0.20,
    "zscore_period": 20,
    "zscore_entry": 1.0,
    "pullback_window": 4,
    "atr_len": 14,
    "atr_stop_mult": 1.5,
    "atr_tp_mult": 4.5,
}


def _efficiency_ratio(closes: List[float], period: int) -> Optional[float]:
    """Kaufman效率比：净变化的绝对值 / 波动路径总长，跟kama()内部用的
    同一个公式，这里单独暴露成标量(取序列最后一个点)，供本模块当regime
    过滤门槛用(不是拿来调均线速度)。"""
    n = len(closes)
    if n <= period:
        return None
    change = abs(closes[-1] - closes[-1 - period])
    volatility = sum(abs(closes[i] - closes[i - 1]) for i in range(n - period, n))
    if volatility <= 0:
        return 0.0
    return change / volatility


def generate_signal(bars_by_tf: Dict[str, List[dict]], params: Optional[dict] = None, position: Optional[dict] = None) -> Optional[dict]:
    bars = bars_by_tf.get("base") or []
    p = {**DEFAULT_PARAMS, **(params or {})}
    adx_len = int(p["adx_len"])
    er_period = int(p["er_period"])
    zscore_period = int(p["zscore_period"])
    pb_window = int(p["pullback_window"])
    atr_len = int(p["atr_len"])
    need = max(adx_len * 2 + 2, zscore_period + pb_window + 2, er_period + 2) + atr_len + 4
    if len(bars) < need:
        return None

    if position:
        # 固定1:3盈亏比止损止盈交给runner通用机制处理，本模块不设额外
        # 主动离场判断(刻意的纪律选择，见docstring)。
        return None

    cs = indicators.closes(bars)
    adx_now, plus_di, minus_di = indicators.wilder_adx_di(bars, adx_len)
    if adx_now <= float(p["adx_threshold"]):
        return None

    er = _efficiency_ratio(cs, er_period)
    if er is None or er < float(p["min_efficiency_ratio"]):
        return None

    ma = indicators.sma(cs, zscore_period)
    sd = indicators.stdev(cs, zscore_period)
    if len(ma) < pb_window + 1 or len(sd) < pb_window + 1:
        return None

    def _z(i_from_end: int) -> Optional[float]:
        # i_from_end=0 是最新一个点
        idx = -1 - i_from_end
        if -idx > len(ma) or -idx > len(sd):
            return None
        std = sd[idx]
        if std <= 0:
            return None
        return (cs[-1 - i_from_end] - ma[idx]) / std

    z_now = _z(0)
    if z_now is None:
        return None

    entry_thresh = float(p["zscore_entry"])
    last = bars[-1]
    price = float(last["c"])
    bar_time = int(last["t"])

    if plus_di > minus_di:
        touched = any((_z(k) or 0) <= -entry_thresh for k in range(1, pb_window + 1))
        reclaimed = z_now > -entry_thresh
        if not (touched and reclaimed):
            return None
        action, d = "LONG", 1
    elif minus_di > plus_di:
        touched = any((_z(k) or 0) >= entry_thresh for k in range(1, pb_window + 1))
        reclaimed = z_now < entry_thresh
        if not (touched and reclaimed):
            return None
        action, d = "SHORT", -1
    else:
        return None

    atr = indicators.wilder_atr(bars, atr_len)
    if atr <= 0:
        return None

    stop_mult = float(p["atr_stop_mult"])
    tp_mult = float(p["atr_tp_mult"])

    return {
        "action": action,
        "price": round(price, 6),
        "atr": round(atr, 6),
        "stop_loss": round(price - d * atr * stop_mult, 6),
        "tp1": round(price + d * atr * tp_mult, 6),
        "tier": 1,
        "bar_time": bar_time,
        "reason": f"ADX={adx_now:.1f}>{p['adx_threshold']} + 效率比={er:.2f}≥{p['min_efficiency_ratio']} "
                  f"+ Z-score回调修复(现{z_now:+.2f})，固定1:{tp_mult/stop_mult:.1f}盈亏比",
    }
