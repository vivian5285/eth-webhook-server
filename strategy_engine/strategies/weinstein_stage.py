#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
温斯坦阶段分析(Weinstein Stage Analysis)——Stan Weinstein《Secrets for
Profiting in Bull and Bear Markets》(1988)公开出版，本人在《The
Professional Tape Reader》newsletter上有真实公开可查的选股战绩。

核心思想：把任何品种的价格周期分成4个阶段——Stage1(筑底整理)、
Stage2(上升趋势，唯一该做多的阶段)、Stage3(筑顶整理)、Stage4(下降趋势，
唯一该做空/离场的阶段)——用一条慢速均线的"位置+斜率"就能大致划分这4个
阶段，不需要更复杂的指标。原始版本用**30周均线**配周线图；本模块把
"30周"压缩成"30日"用在日线周期上(下面"周期选择理由"细说压缩逻辑)。

规则：
  - 均线：SMA(ma_len，默认30)
  - 斜率：比较当前均线值和slope_lookback(默认5)根前的均线值，涨幅超过
    min_slope_pct(默认0.5%)才算"确实在往上走"(Stage2的必要条件)，跌幅
    超过则算"确实在往下走"(Stage4)
  - Stage2信号(做多)：价格从下方收盘穿越回均线上方(上一根还在均线下，
    这一根收在均线上——"夺回均线"这个**事件**) 且均线正在上涨 且价格
    同时创出breakout_lookback(默认10)根以来的新高(Stage1筑底整理的
    上沿被突破，不是普通的假突破)——三个条件同时满足才是Weinstein说的
    "Stage1向Stage2转换"的经典买点，不是"均线之上就一直持有"这种简单
    趋势跟随。
  - Stage4信号(做空)：跟上面完全对称(跌破均线+均线下降+创新低)
  - 离场：价格重新收盘跌破(做多)/收盘站上(做空)均线——代表Stage2→3/4
    (或Stage4→1/2)的转换，Weinstein本人的规则就是"跌破均线就是危险信号，
    不管是不是已经跌了很多"，止损同时用ATR兜底。

跟本仓库其余"趋势/状态"类战法的关键区别：adx_regime_switch用ADX数值
高低做"趋势市/震荡市"二分状态开关；这套用**均线的位置+斜率**划出
"上升/下降/整理"四个阶段(震荡的Stage1/3天然被排除在交易之外，不像
adx_regime_switch会在震荡市里主动做均值回归)，且入场要求"阶段转换的
那一刻"(均线转向+夺回均线+创新高三件事同时发生)，不是"当前处于该阶段
就一直进"。

周期选择理由：1d。Stage Analysis的本质是"用一条足够慢的均线过滤掉短期
噪音、只看大方向"，原始设计用30周均线配周线图，是几个月量级的判断
周期；本仓库其余日线战法(connors_rsi2/volatility_breakout等)已经把
"周/月"量级的原始设计压缩到日线做加密货币适配，这里同样压缩：SMA(30)
配1d，大致对应"一个月量级的方向判断"。加密货币的周期本来就比股票快，
这里的压缩比例比其余日线战法更激进一些是刻意的，不是失误。

数据要求：至少 ma_len+slope_lookback+breakout_lookback+atr_len+4 根
bars_by_tf["base"]。
"""
from __future__ import annotations

from typing import Dict, List, Optional

from strategy_engine import indicators

DEFAULT_PARAMS = {
    "ma_len": 30,
    "slope_lookback": 5,
    "min_slope_pct": 0.5,
    "breakout_lookback": 10,
    "atr_len": 14,
    "atr_stop_mult": 2.5,
}


def generate_signal(bars_by_tf: Dict[str, List[dict]], params: Optional[dict] = None, position: Optional[dict] = None) -> Optional[dict]:
    bars = bars_by_tf.get("base") or []
    p = {**DEFAULT_PARAMS, **(params or {})}
    ma_len = int(p["ma_len"])
    slope_lb = int(p["slope_lookback"])
    bl = int(p["breakout_lookback"])
    atr_len = int(p["atr_len"])
    need = ma_len + slope_lb + bl + atr_len + 4
    if len(bars) < need:
        return None

    cs = indicators.closes(bars)
    ma = indicators.sma(cs, ma_len)
    if len(ma) < slope_lb + 2:
        return None

    last = bars[-1]
    price = float(last["c"])
    bar_time = int(last["t"])
    ma_now, ma_prev = ma[-1], ma[-2]
    ma_slope_ref = ma[-1 - slope_lb]
    min_slope = float(p["min_slope_pct"]) / 100.0
    rising = ma_now >= ma_slope_ref * (1 + min_slope)
    falling = ma_now <= ma_slope_ref * (1 - min_slope)

    if position:
        side = str(position.get("side") or "").upper()
        if side == "LONG" and price < ma_now and cs[-2] >= ma_prev:
            return {
                "action": "CLOSE_QUICK_EXIT", "price": round(price, 6),
                "reason": f"收盘跌破SMA{ma_len}({ma_now:.6f})，Stage2转Stage3/4",
                "bar_time": bar_time,
            }
        if side == "SHORT" and price > ma_now and cs[-2] <= ma_prev:
            return {
                "action": "CLOSE_QUICK_EXIT", "price": round(price, 6),
                "reason": f"收盘站上SMA{ma_len}({ma_now:.6f})，Stage4转Stage1/2",
                "bar_time": bar_time,
            }
        return None

    crossed_up = cs[-2] < ma_prev and price >= ma_now
    crossed_down = cs[-2] > ma_prev and price <= ma_now
    recent_high = max(float(b["h"]) for b in bars[-1 - bl:-1])
    recent_low = min(float(b["l"]) for b in bars[-1 - bl:-1])

    if crossed_up and rising and price > recent_high:
        action, d = "LONG", 1
    elif crossed_down and falling and price < recent_low:
        action, d = "SHORT", -1
    else:
        return None

    atr = indicators.wilder_atr(bars, atr_len)
    if atr <= 0:
        return None

    if action == "LONG":
        reason = f"Stage1→2转换：夺回SMA{ma_len}({ma_now:.6f})+均线上升+创{bl}根新高"
    else:
        reason = f"Stage3→4转换：跌破SMA{ma_len}({ma_now:.6f})+均线下降+创{bl}根新低"

    return {
        "action": action,
        "price": round(price, 6),
        "atr": round(atr, 6),
        "stop_loss": round(price - d * atr * float(p["atr_stop_mult"]), 6),
        "tier": 1,
        "bar_time": bar_time,
        "reason": reason,
    }
