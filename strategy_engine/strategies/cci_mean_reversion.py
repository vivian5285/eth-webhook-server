#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
CCI均值回归——2026-09-20新增，宝贝要求。Donald Lambert 1980年公开发表
CCI(Commodity Channel Index)，±100是他原始定义的"常规波动边界"。本仓库
另一套用法(mtf_ema_macd_cci)是把±100当"冲量确认"(动量够猛才敢追)；这套
反过来用——CCI冲出±100代表短期已经打到极端，赌它会往回收敛，是CCI在
零售交易圈更常见的均值回归读法，跟RSI超买超卖同一个哲学，规则同样确定、
公开可查，不是黑箱。

规则(跟kdj_cross.py同一副"只在极值区反向操作+回归中位离场"的骨架——那套
39笔0次碰到止损、64.1%胜率，是擂台目前机制最干净的均值回归类战法，这里
把同一个思路搬到CCI+更慢的周期上验证)：
  - CCI(period=20，Lambert原始默认)
  - CCI从超买区(≥100)回落穿破100 → 做空(赌见顶回落)
  - CCI从超卖区(≤-100)回升穿破-100 → 做多(赌止跌反弹)
  - 离场：CCI回到中性区(exit_level，默认0)，或再次反向触及另一端极值
    (反转比预期更快更猛，提前认错离场)，或ATR止损兜底
  - tier：|CCI|超过150算"极端"记tier2，否则tier1

宝贝原话问的是"4h/6h/8h/12h做均值回归有没有搞头"——擂台此前唯一的均值
回归战法(vwap_mean_reversion)只做到45m，日线级别(connors_rsi2/bollinger_
rsi_contrarian)又太慢，4h~12h这个波段区间此前完全没人覆盖。均值回归策略
最大的风险是在真趋势里逆势抄底摸顶被打穿——这条default就带着adx_gate=25
这道闸门(不是像其它v2那样默认关闭留白测试，是判断这类策略从设计上就该
有)，只在非趋势(ADX<25)状态下才做，减少被单边行情打穿的概率。
"""
from __future__ import annotations

from typing import Dict, List, Optional

from strategy_engine import indicators

DEFAULT_PARAMS = {
    "period": 20,
    "extreme": 100.0,
    "extreme_tier2": 150.0,
    "exit_level": 0.0,
    "atr_len": 14,
    "atr_stop_mult": 2.0,
    "adx_gate": 25.0,  # ADX<此值才做(震荡/非趋势状态)，0=关闭这道闸门
}


def generate_signal(bars_by_tf: Dict[str, List[dict]], params: Optional[dict] = None, position: Optional[dict] = None) -> Optional[dict]:
    bars = bars_by_tf.get("base") or []
    p = {**DEFAULT_PARAMS, **(params or {})}
    period = int(p["period"])
    atr_len = int(p["atr_len"])
    if len(bars) < period + atr_len + 5:
        return None

    cci_series = indicators.cci(bars, period)
    if len(cci_series) < 2:
        return None
    cci0, cci1 = cci_series[-2], cci_series[-1]

    last = bars[-1]
    price = float(last["c"])
    bar_time = int(last["t"])
    extreme = float(p["extreme"])
    exit_level = float(p["exit_level"])

    cross_dn_from_high = cci0 >= extreme and cci1 < extreme
    cross_up_from_low = cci0 <= -extreme and cci1 > -extreme

    if position:
        side = str(position.get("side") or "").upper()
        if side == "LONG" and (cci1 >= exit_level or cross_dn_from_high):
            return {
                "action": "CLOSE_QUICK_EXIT", "price": round(price, 6),
                "reason": f"CCI回归中性({cci1:.1f})或再次转空", "bar_time": bar_time,
            }
        if side == "SHORT" and (cci1 <= exit_level or cross_up_from_low):
            return {
                "action": "CLOSE_QUICK_EXIT", "price": round(price, 6),
                "reason": f"CCI回归中性({cci1:.1f})或再次转多", "bar_time": bar_time,
            }
        return None

    d = 0
    if cross_up_from_low:
        d = 1
    elif cross_dn_from_high:
        d = -1
    if d == 0:
        return None

    adx_gate = float(p.get("adx_gate") or 0)
    if adx_gate > 0:
        adx = indicators.wilder_adx(bars, atr_len)
        if adx >= adx_gate:
            return None  # 趋势状态太强，均值回归先不做，避免逆势被打穿

    atr = indicators.wilder_atr(bars, atr_len)
    if atr <= 0:
        return None

    extreme_tier2 = float(p["extreme_tier2"])
    peak = cci0 if d == -1 else cci0  # 触发那一刻的极值读数
    tier = 2 if abs(peak) >= extreme_tier2 else 1

    return {
        "action": "LONG" if d == 1 else "SHORT",
        "price": round(price, 6), "atr": round(atr, 6),
        "stop_loss": round(price - d * atr * float(p["atr_stop_mult"]), 6),
        "tier": tier, "bar_time": bar_time,
        "reason": f"CCI({period}) {'超卖回升' if d == 1 else '超买回落'}(CCI={cci1:.1f})",
    }
