#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Time Series Momentum(时间序列动量)——Moskowitz、Ooi、Pedersen三人2012年
发表在Journal of Financial Economics的学术论文《Time Series Momentum》，
跨58个流动性最好的期货/远期市场(股指/债券/货币/商品)、25年数据验证过
的真实异象，是动量类因子里被复现次数最多的学术研究之一。

跟本仓库`cross_momentum`/`dual_momentum`的关键区别：那两套都是"横截面"
(cross-sectional)动量——只看这个品种相对篮子里其它品种排第几；TSMOM
是"时间序列"(time-series)动量——只看这个品种自己过去这段时间涨了还是
跌了，完全不跟篮子里其它品种比较，单独一个品种自己就能独立跑通(这也是
原始论文的核心论点：动量效应在"品种相对自己的历史"这个维度上依然显著
存在，不需要跨品种比较这个前提)。

跟本仓库`turtle_breakout`的关键区别：两个都是"纯趋势跟随、不用震荡指标
确认"，但turtle看的是"价格结构"(有没有创新高/新低突破通道)，TSMOM看的
是"收益率本身"(这段时间累计涨跌了多少)——同一个趋势跟随大类下，两种
完全不同的原始信号来源，可以互相印证或分化。

规则(原始论文核心版，未加论文里另外提出的"波动率目标仓位"，那部分是
仓位管理不是入场信号，本仓库仓位大小统一由position_sizing.py处理)：
  - 动量 = close_now / close_lookback_bars_ago - 1
  - 动量>0 → 做多；动量<0 → 做空(纯粹按符号，论文原版就是这么简单，
    没有额外的震荡指标确认)
  - 离场：动量符号反转(连续重新评估，不是论文原版"固定持有到下个月末
    再评估"的日历再平衡节奏，这里适配成更贴近本仓库其余策略的连续
    监控风格)

品种覆盖：原始论文横跨股指/债券/货币/商品几乎所有主流资产类别验证过，
不挑资产类型，本模块跟全篮子品种一起跑。

数据要求：至少需要lookback_bars+1根bars_by_tf["base"]算动量，另加
atr_len根计算ATR安全网。本模块用1d周期，lookback_bars默认20(约20天)，
是论文原版12个月周期针对加密货币更快节奏的有意识压缩(跟本仓库
cross_momentum/bollinger_squeeze_fast同样的"不猜哪个周期更好，用真实
数据说话"的一贯做法)。
"""
from __future__ import annotations

from typing import Dict, List, Optional

from strategy_engine import indicators

DEFAULT_PARAMS = {
    "lookback_bars": 20,
    "atr_len": 14,
    "atr_stop_mult": 2.0,
}


def generate_signal(bars_by_tf: Dict[str, List[dict]], params: Optional[dict] = None, position: Optional[dict] = None) -> Optional[dict]:
    bars = bars_by_tf.get("base") or []
    p = {**DEFAULT_PARAMS, **(params or {})}
    lookback = int(p["lookback_bars"])
    atr_len = int(p["atr_len"])
    need = max(lookback + 1, atr_len + 2) + 1
    if len(bars) < need:
        return None

    last = bars[-1]
    price = float(last["c"])
    bar_time = int(last["t"])
    c_then = float(bars[-1 - lookback]["c"])
    if c_then <= 0:
        return None
    momentum = price / c_then - 1.0

    if position:
        side = str(position.get("side") or "").upper()
        if side == "LONG" and momentum <= 0:
            return {
                "action": "CLOSE_QUICK_EXIT",
                "price": round(price, 6),
                "reason": f"{lookback}根动量转负({momentum:+.4f})离场",
                "bar_time": bar_time,
            }
        if side == "SHORT" and momentum >= 0:
            return {
                "action": "CLOSE_QUICK_EXIT",
                "price": round(price, 6),
                "reason": f"{lookback}根动量转正({momentum:+.4f})离场",
                "bar_time": bar_time,
            }
        return None

    if momentum > 0:
        action = "LONG"
    elif momentum < 0:
        action = "SHORT"
    else:
        return None

    atr = indicators.wilder_atr(bars, atr_len)
    if atr <= 0:
        return None
    direction = 1 if action == "LONG" else -1

    return {
        "action": action,
        "price": round(price, 6),
        "atr": round(atr, 6),
        "stop_loss": round(price - direction * atr * float(p["atr_stop_mult"]), 6),
        "tp1": round(price + direction * atr * 1.5, 6),
        "tp2": round(price + direction * atr * 3.0, 6),
        "tp3": round(price + direction * atr * 5.0, 6),
        "tier": 1,
        "bar_time": bar_time,
        "reason": f"自身{lookback}根时间序列动量={momentum:+.4f}",
    }
