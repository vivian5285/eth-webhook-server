#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
双均线(7/25)+雷达ADX分级移动止盈——2026-09-29应宝贝要求，在dual_ema_band_
7_25基础上加两处：①入场额外要求快线站在慢线上方/下方(不只是价格站上
两线，两条线自己也要排好队)；②止损管理照搬本仓库TV镜像雷达系统
(sndk_dual_ma_strategy.py::evaluate_protective_stop，实盘SNDK真实在用
的同一套状态机)——初始硬止损→浮盈达1R后保本→浮盈达1.5R后启动移动止损，
移动止损的呼吸空间按ADX分级：强趋势(ADX>30)给3.5倍ATR空间，弱趋势
(ADX<20)收紧到1.5倍，居中给2.5倍——这就是"雷达根据趋势ADX不同，呼吸
空间不同"。

跟dual_ema_band_7_25(不带_radar后缀的版本)的区别：
  · 入场：那版只要求"收盘价站上/跌破两条线"；这版额外要求"快线也要在
    慢线上方/下方"，是更严格的确认，两版本并排跑对照，看多这一道
    过滤到底是帮忙还是碍事(本仓库好几次"加过滤"实验都是负面结果，
    这次照样如实验证，不预设立场)。
  · 离场：那版止损是固定的ATR×2.5，一次定死；这版止损会随盈利推进
    分阶段收紧(硬止损→保本→ADX分级追踪)，理论上能让大趋势的利润跑
    得更远，但也可能因为分级追踪不够紧而回吐更多浮盈——同样是对照，
    不是预判谁更好。
  · 止损状态机本身不在这个文件里维护(引擎侧新增_maybe_radar_trail
    持续更新pos["stop_loss"]，复用引擎已有的_check_stop_tp机制触发
    平仓，不需要这个文件自己判断止损触发)，这个文件只负责入场信号+
    双均线反转离场信号+入场时给一个初始硬止损。

周期：1h和90m两个独立版本(90m靠klines.py既有15m合成)，各自单独开单，
不互相确认，跟不带_radar的版本同一套周期安排。
"""
from __future__ import annotations

from typing import Dict, List, Optional

from strategy_engine import indicators

DEFAULT_PARAMS = {
    "fast_period": 7,
    "slow_period": 25,
    "atr_len": 14,
    "initial_stop_atr_mult": 2.5,  # 跟sndk_dual_ma_strategy.py同一个取值
}


def generate_signal(bars_by_tf: Dict[str, List[dict]], params: Optional[dict] = None, position: Optional[dict] = None) -> Optional[dict]:
    p = {**DEFAULT_PARAMS, **(params or {})}
    bars = bars_by_tf.get("base") or []
    fast_period = int(p["fast_period"])
    slow_period = int(p["slow_period"])
    atr_len = int(p["atr_len"])
    need = max(fast_period, slow_period, atr_len) + 5
    if len(bars) < need:
        return None

    cs = indicators.closes(bars)
    ema_fast = indicators.ema(cs, fast_period)
    ema_slow = indicators.ema(cs, slow_period)
    if not ema_fast or not ema_slow:
        return None

    atr_ser = indicators.atr_series(bars, atr_len)
    if not atr_ser:
        return None
    atr = float(atr_ser[-1])
    if atr <= 0:
        return None

    last = bars[-1]
    close = float(last["c"])
    bar_time = int(last["t"])
    fast = float(ema_fast[-1])
    slow = float(ema_slow[-1])

    # 多头：价格站上两线 且 快线在慢线上方；空头对称
    long_ok = close > fast and close > slow and fast > slow
    short_ok = close < fast and close < slow and fast < slow

    if position:
        side = str(position.get("side") or "").upper()
        # 双均线反转完全平仓：只看价格是否已经反向站上/跌破两线，不要求
        # 快慢线也排好队(排好队是入场的确认条件，离场只看反转本身，跟
        # 不带_radar的版本同一个离场哲学，也是sndk_dual_ma_strategy.py
        # close_cond的同款设计：不额外加条件)。
        below_both = close < fast and close < slow
        above_both = close > fast and close > slow
        if side == "LONG" and below_both:
            return {"action": "CLOSE_QUICK_EXIT", "price": round(close, 6),
                    "reason": "跌破双均线，多头离场", "bar_time": bar_time}
        if side == "SHORT" and above_both:
            return {"action": "CLOSE_QUICK_EXIT", "price": round(close, 6),
                    "reason": "站上双均线，空头离场", "bar_time": bar_time}
        return None

    # reverse_now=True：注意这版的离场条件(below_both/above_both)比入场
    # 条件(long_ok/short_ok多要求快慢线排好队)更宽松，不是恒等式——平仓
    # 那一刻不一定立刻满足反向入场，引擎侧的反手检查(2026-09-30新增，见
    # multi_strategy_runner.py里hma_trend_reverse_*那段同款逻辑)会真的
    # 用position=None重新调一次这个函数，只有long_ok/short_ok这时也为真
    # 才会立即反手，否则等下一根K线快慢线真排好队了再开——这个标记只是
    # "这份信号本身经过了完整入场条件校验，可信"，不是无条件放行。
    if long_ok:
        return {
            "action": "LONG", "price": round(close, 6), "atr": round(atr, 6),
            "stop_loss": round(close - atr * float(p["initial_stop_atr_mult"]), 6),
            "tier": 1, "bar_time": bar_time,
            "reason": f"站上双均线且快线上穿慢线(EMA{fast_period}={fast:.6g}>EMA{slow_period}={slow:.6g})",
            "reverse_now": True,
        }
    if short_ok:
        return {
            "action": "SHORT", "price": round(close, 6), "atr": round(atr, 6),
            "stop_loss": round(close + atr * float(p["initial_stop_atr_mult"]), 6),
            "tier": 1, "bar_time": bar_time,
            "reason": f"跌破双均线且快线下穿慢线(EMA{fast_period}={fast:.6g}<EMA{slow_period}={slow:.6g})",
            "reverse_now": True,
        }
    return None
