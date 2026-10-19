#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""2026-10-05: Gate纸面擂台的ADX双引擎实验，非已验证盈利策略。

Claude维护入口: strategy_engine/research/ADX_DUAL_ENGINE_HANDOFF.md。
接口纯函数，不拉行情、不碰账户。base必须是按时间升序的已收盘K线。
ADX只判断强度；EMA7/30、慢线斜率和DI才判断方向。连续两根>=25走趋势，
连续两根<=18走回归；18~25为隔离区，不开新仓。这是确认+隔离区，不是
依赖进程内记忆的滞回状态机，重启/历史重放的结果保持可复现。

趋势: EMA排列+DI同向，快线回收/20根突破/ADX刚确认之一触发；避免旧版
只等EMA交叉与ADX达标碰巧在同一根。2ATR硬止损，无固定TP；EMA排列失效、
确认变成震荡、或收盘回撤超过前序极值3ATR时离场。
震荡: 前一根在BB(20,2)外+RSI(2)极值，当前收回带内才入场；慢线近三根
变化<=0.5ATR，目标仍在盈利方向且距离>=止损风险*1.0。1.2ATR硬止损；
到动态中轨、确认变成趋势、或12根超时离场。无冻结中轨TP。

entry_stage='adx_trend'/'adx_range'沿用现有DB字段持久化。离场由入场引擎
负责，绝不把回归仓改成趋势仓。硬止损、滑点、费用、仓位和组合风控由
multi_strategy_runner统一执行。trail是收盘退出条件，不是盘中挂单。
"""
from __future__ import annotations

import math
from typing import Dict, List, Optional
from strategy_engine import indicators

DEFAULT_PARAMS = {
    "engine_mode": "both",  # both/trend/range用于同规则消融对照
    "adx_len": 14, "trend_adx": 25.0, "range_adx": 18.0,
    "confirm_bars": 2, "ema_fast_len": 7, "ema_slow_len": 30,
    "breakout_len": 20, "bb_len": 20, "bb_mult": 2.0,
    # 低ADX里的局部摆动需要短RSI；RSI14极值更常出现在趋势下跌/上涨。
    # 2026-10-05触发漏斗检查发现6个币4h样本RSI14版本零回归入场，
    # 改为RSI2的10/90极值。按状态语义修正，不按回测收益挑参数。
    "rsi_len": 2, "rsi_long": 10.0, "rsi_short": 90.0,
    "atr_len": 14, "trend_stop_atr": 2.0, "range_stop_atr": 1.2,
    "trend_trail_atr": 3.0, "range_max_bars": 12,
    "range_slope_atr": 0.5, "min_range_reward_risk": 1.0,
    "max_entry_distance_atr": 2.0,
}


def _confirmed_regime(adx_values: List[float], p: dict) -> str:
    """输入每根已收盘K线的ADX，连续确认；缺值/NaN都不交易。"""
    n = int(p["confirm_bars"])
    tail = adx_values[-n:]
    if len(tail) < n or not all(math.isfinite(x) for x in tail):
        return "neutral"
    if all(x >= float(p["trend_adx"]) for x in tail):
        return "trend"
    if all(x <= float(p["range_adx"]) for x in tail):
        return "range"
    return "neutral"


def generate_signal(bars_by_tf: Dict[str, List[dict]], params: Optional[dict] = None,
                    position: Optional[dict] = None) -> Optional[dict]:
    p = {**DEFAULT_PARAMS, **(params or {})}
    if p["engine_mode"] not in {"both", "trend", "range"}:
        raise ValueError("engine_mode必须是both/trend/range")
    if not 0 <= float(p["range_adx"]) < float(p["trend_adx"]) <= 100:
        raise ValueError("ADX阈值必须0<=range_adx<trend_adx<=100")
    lengths = ("confirm_bars", "adx_len", "ema_fast_len", "ema_slow_len",
               "breakout_len", "bb_len", "rsi_len", "atr_len", "range_max_bars")
    if any(int(p[k]) < 1 for k in lengths):
        raise ValueError("周期参数必须为正整数")
    if int(p["ema_fast_len"]) >= int(p["ema_slow_len"]):
        raise ValueError("快线周期必须小于慢线")
    if any(float(p[k]) <= 0 for k in ("bb_mult", "trend_stop_atr", "range_stop_atr",
            "trend_trail_atr", "range_slope_atr", "min_range_reward_risk", "max_entry_distance_atr")):
        raise ValueError("风险/通道参数必须为正")
    bars = bars_by_tf.get("base") or []
    # 至少3倍ADX长度供Wilder平滑热身；生产roster共享550根缓存。
    need = max(3 * int(p["adx_len"]) + int(p["confirm_bars"]) + 2,
               int(p["ema_slow_len"]) + 4, int(p["bb_len"]) + 2,
               int(p["rsi_len"]) + 2, int(p["breakout_len"]) + 2,
               int(p["atr_len"]) + 2)
    if len(bars) < need:
        return None
    try:
        if any(not all(math.isfinite(float(b[k])) for k in ("o", "h", "l", "c"))
               or float(b["l"]) <= 0 or float(b["h"]) < max(float(b["o"]), float(b["c"]))
               or float(b["l"]) > min(float(b["o"]), float(b["c"])) for b in bars):
            return None
        if any(int(bars[i]["t"]) <= int(bars[i-1]["t"]) for i in range(1, len(bars))):
            return None
    except (KeyError, TypeError, ValueError, OverflowError):
        return None
    cs = indicators.closes(bars)
    fast = indicators.ema(cs, int(p["ema_fast_len"]))
    slow = indicators.ema(cs, int(p["ema_slow_len"]))
    mid = indicators.sma(cs, int(p["bb_len"]))
    sd = indicators.stdev(cs, int(p["bb_len"]))
    rsi = indicators.rsi(cs, int(p["rsi_len"]))
    atr = indicators.wilder_atr(bars, int(p["atr_len"]))
    adx, plus_di, minus_di = indicators.wilder_adx_di(bars, int(p["adx_len"]))
    if not all(math.isfinite(x) for x in (atr, adx, plus_di, minus_di)) or atr <= 0:
        return None
    # 只算最新confirm_bars+1个ADX，不对整个窗口逐根重算；[:-k]不含未来。
    n = int(p["confirm_bars"])
    adx_values = [indicators.wilder_adx(bars[:-k], int(p["adx_len"]))
                  for k in range(n, 0, -1)] + [adx]
    regime = _confirmed_regime(adx_values, p)
    prior_regime = _confirmed_regime(adx_values[:-1], p)
    price, t = cs[-1], int(bars[-1]["t"])

    def close(reason: str) -> dict:
        return {"action": "CLOSE_QUICK_EXIT", "price": price, "bar_time": t,
                "reason": f"ADX双引擎: {reason}; ADX={adx:.1f},当前={regime}"}

    if position:
        entry_t = int(position.get("entry_bar_time") or 0)
        if t <= entry_t:
            return None  # 不使用入场前已经发生过的high/low判退出
        side = str(position.get("side") or "").upper()
        if side not in {"LONG", "SHORT"}:
            return close("持仓方向异常，退出")
        d = 1 if side == "LONG" else -1
        engine = position.get("entry_stage")
        if engine == "adx_trend":
            if d * (fast[-1] - slow[-1]) <= 0:
                return close("趋势仓EMA排列失效")
            if regime == "range":
                return close("趋势仓确认转震荡，先退出")
            # 使用已持仓的前序K线极值；不使用入场K或当前K的high/low。
            prior = [b for b in bars[:-1] if int(b["t"]) > entry_t]
            if prior:
                atr0 = float(position.get("atr0") or atr)
                extreme = max(float(b["h"]) for b in prior) if d > 0 else min(float(b["l"]) for b in prior)
                trail = extreme - d * float(p["trend_trail_atr"]) * atr0
                if d * (price - trail) <= 0:
                    return close("趋势仓收盘触发3ATR极值回撤退出")
        elif engine == "adx_range":
            if d * (price - mid[-1]) >= 0:
                return close("震荡仓回到动态中轨")
            if regime == "trend":
                return close("震荡仓确认转趋势，先退出")
            held = sum(int(b["t"]) > entry_t for b in bars)
            if held >= int(p["range_max_bars"]):
                return close("震荡仓超时未回归")
        else:
            # 本策略的新仓一定写阶段；拒绝重启后猜测引擎。
            return close("缺少入场引擎标记，保护性退出")
        return None

    action, engine = None, None
    if regime == "trend" and p["engine_mode"] in {"both", "trend"}:
        up = fast[-1] > slow[-1] and slow[-1] > slow[-2] and plus_di > minus_di
        down = fast[-1] < slow[-1] and slow[-1] < slow[-2] and minus_di > plus_di
        past = bars[-int(p["breakout_len"])-1:-1]  # 当前K不参与突破边界
        recovered_long = cs[-2] <= fast[-2] and price > fast[-1]
        recovered_short = cs[-2] >= fast[-2] and price < fast[-1]
        newly_confirmed = prior_regime != "trend"
        if abs(price - fast[-1]) > float(p["max_entry_distance_atr"]) * atr:
            return None  # ADX迟到时也不追已经远离均线的价格
        if up and (recovered_long or price > max(float(b["h"]) for b in past) or newly_confirmed):
            action = "LONG"
        elif down and (recovered_short or price < min(float(b["l"]) for b in past) or newly_confirmed):
            action = "SHORT"
        engine, stop_mult = "adx_trend", float(p["trend_stop_atr"])
    elif regime == "range" and p["engine_mode"] in {"both", "range"}:
        if abs(slow[-1] - slow[-4]) > float(p["range_slope_atr"]) * atr or sd[-1] <= 0:
            return None  # 低ADX只是候选，仍排除斜向漂移
        lower_prev, upper_prev = mid[-2] - float(p["bb_mult"]) * sd[-2], mid[-2] + float(p["bb_mult"]) * sd[-2]
        lower_now, upper_now = mid[-1] - float(p["bb_mult"]) * sd[-1], mid[-1] + float(p["bb_mult"]) * sd[-1]
        if cs[-2] <= lower_prev and rsi[-2] <= float(p["rsi_long"]) and lower_now < price < mid[-1]:
            action = "LONG"
        elif cs[-2] >= upper_prev and rsi[-2] >= float(p["rsi_short"]) and mid[-1] < price < upper_now:
            action = "SHORT"
        engine, stop_mult = "adx_range", float(p["range_stop_atr"])
        if action and abs(mid[-1] - price) < stop_mult * atr * float(p["min_range_reward_risk"]):
            return None
    if not action:
        return None
    d = 1 if action == "LONG" else -1
    stop = price - d * atr * stop_mult
    if stop <= 0:
        return None
    # 不round到6位，低价品种不能把有效价格/止损四舍五入为0。
    return {"action": action, "price": price, "atr": atr, "stop_loss": stop,
            "tier": 1, "bar_time": t, "adx": adx, "entry_stage": engine,
            "reason": f"ADX双引擎 {engine}; ADX={adx:.1f},DI+={plus_di:.1f},DI-={minus_di:.1f}"}
