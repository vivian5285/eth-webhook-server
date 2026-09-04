#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MTF EMA Pullback(多周期趋势 + 回踩顺势)——2026-09-04应宝贝要求新增，补上
本擂台目前唯一的结构性缺口：现有13套单品种战法**全是单一周期**，没有
一套是"大周期定方向、小周期找入场点"的经典多时间框架(Multi-Timeframe)
打法。

多周期趋势跟随本身是公开、几十年的经典方法论(Alexander Elder《Trading
for a Living》的"三重滤网系统 Triple Screen"是最广为引用的成文版本：
用一个更高的时间框架判定潮汐方向，只在低时间框架里顺着这个方向、等一次
逆势回调结束再进场)。这里做的是三重滤网的精简版——高时间框架用 EMA50 vs
EMA200 判潮汐，低时间框架等价格回踩中期均线(EMA20/50)、且 RSI 从超卖区
重新抬头，才顺势进场。规则透明、每一步都能用收盘价手算复现，符合本擂台
准入线。

规则：
  - 方向(高时间框架 htf，默认拉 1h)：EMA(htf_fast,默认50) > EMA(htf_slow,
    默认200) → 只做多；反之 → 只做空；两者纠缠(差距 < 0)时不开新仓。
  - 入场点(base，默认 15m)：
    · 回踩确认——最近 pullback_window(默认6)根里，最低价至少有一次触及或
      跌破 EMA(pullback_ema,默认20)(做空则最高价触及/突破)，说明确实发生
      了一次逆势回调，不是追在趋势最伸展的地方。
    · 动能重新抬头——RSI(rsi_len,默认14)上一根 <= rsi_wake_long(默认40)、
      当前根 > rsi_wake_long(做空对称：上一根 >= 60、当前 < 60)。用"从
      超卖区往上穿"这个**事件**而不是"RSI 当前是否 < 40"这个状态，避免
      在还在下跌的半途中就抢反弹。
  - 离场：
    · 高时间框架潮汐翻转(EMA 金叉/死叉反向) → 主动平仓(趋势的前提没了)。
    · 另配 tp1/tp2/tp3 = 1.5/3/5 ×ATR 的分级止盈占位 + ATR 止损，由通用
      runner 逻辑处理触碰——回踩进场有明确的风险位(回调低点/EMA)，跟
      turtle/ema_cross_7_30 那种"纯趋势、不设固定止盈"的系统不同，这套
      本质是波段择时，给一个止盈目标是合理的(跟 adx_regime_switch 趋势腿
      同款 1.5/3/5 系数)。

跟本仓库其余战法的关键区别：
  - 唯一一套真正用到 bars_by_tf 里 base 以外周期的单品种战法。为此
    multi_strategy_runner._tick_single_symbol_entry 2026-09-04 加了对
    roster 条目 "mtf" 字段的支持(照搬 backtest_runner.py / symbol_
    registry.py 早就在用的同名机制)，其它不带 mtf 字段的战法行为完全
    不变。
  - 跟 ema_cross_7_30 都用 EMA 交叉，但那套是单周期、金叉即入场；这套
    EMA 交叉只用来在**高**周期定方向,低周期还要额外等一次回调+RSI 抬头
    才进场，进场点通常离趋势启动点更远、离回调低点更近。
  - 跟 connors_rsi2 都用"顺大势 + 等回调"，但 connors 是纯均值回归(赌
    短线反弹到 SMA5 就走)、单周期 SMA200 定方向;这套是趋势延续(赌回调
    结束后趋势继续)、方向来自独立的高时间框架。

周期选择理由：base=15m + htf=1h(4倍关系，是三重滤网推荐的"相邻两级
时间框架差 3~5 倍"区间内)。15m 足够密、能抓到日内级别的回调结束点，
又不像 1m/5m 那样噪声主导；1h 的 EMA50/200 是加密货币里常被引用的
"中期趋势"均线组合。不用更慢的 4h/1d 当 base——那样"回调"这个概念
的持仓周期会拉到好几天，跟本擂台已有的一堆慢周期趋势战法(turtle/
tsmom/ema_cross)就高度同质了，这套的价值恰恰在于填补"日内多周期择时"
这个空白。

数据要求：base 至少 rsi_len+pullback_window+2 根、且 EMA(pullback_ema)
要能算出;htf 至少 htf_slow+2 根(EMA200 需要 200 根种子 + 1 根前值判
交叉)。runner 按 roster 的 "mtf":["1h"] 配置拉取，htf 拉取量跟 base
同为 BARS_LIMIT(550)，1h×550≈22 天，EMA200 收敛充分。
"""
from __future__ import annotations

from typing import Dict, List, Optional

from strategy_engine import indicators

DEFAULT_PARAMS = {
    "htf_key": "1h",
    "htf_fast": 50,
    "htf_slow": 200,
    "pullback_ema": 20,
    "pullback_window": 6,
    "rsi_len": 14,
    "rsi_wake_long": 40.0,
    "rsi_wake_short": 60.0,
    "atr_len": 14,
    "atr_stop_mult": 2.0,
}


def _htf_direction(htf_bars: List[dict], fast_n: int, slow_n: int):
    """返回 (direction, cross) —— direction: +1 多头潮汐 / -1 空头潮汐 /
    0 纠缠不明；cross: 'up'/'down'/None 表示本根是否刚发生反向交叉。"""
    cs = indicators.closes(htf_bars)
    fast = indicators.ema(cs, fast_n)
    slow = indicators.ema(cs, slow_n)
    if len(fast) < 2 or len(slow) < 2:
        return 0, None
    f0, f1 = fast[-2], fast[-1]
    s0, s1 = slow[-2], slow[-1]
    direction = 1 if f1 > s1 else (-1 if f1 < s1 else 0)
    cross = None
    if f0 <= s0 and f1 > s1:
        cross = "up"
    elif f0 >= s0 and f1 < s1:
        cross = "down"
    return direction, cross


def generate_signal(bars_by_tf: Dict[str, List[dict]], params: Optional[dict] = None, position: Optional[dict] = None) -> Optional[dict]:
    p = {**DEFAULT_PARAMS, **(params or {})}
    bars = bars_by_tf.get("base") or []
    htf_bars = bars_by_tf.get(str(p["htf_key"])) or []
    htf_fast = int(p["htf_fast"])
    htf_slow = int(p["htf_slow"])
    pb_ema_n = int(p["pullback_ema"])
    pb_window = int(p["pullback_window"])
    rsi_len = int(p["rsi_len"])
    atr_len = int(p["atr_len"])

    need_base = max(rsi_len + 1, pb_ema_n, atr_len) + pb_window + 2
    if len(bars) < need_base or len(htf_bars) < htf_slow + 2:
        return None

    last = bars[-1]
    price = float(last["c"])
    bar_time = int(last["t"])

    direction, htf_cross = _htf_direction(htf_bars, htf_fast, htf_slow)

    if position:
        side = str(position.get("side") or "").upper()
        if side == "LONG" and htf_cross == "down":
            return {
                "action": "CLOSE_QUICK_EXIT", "price": round(price, 6),
                "reason": f"高周期({p['htf_key']})潮汐死叉翻转，趋势前提消失",
                "bar_time": bar_time,
            }
        if side == "SHORT" and htf_cross == "up":
            return {
                "action": "CLOSE_QUICK_EXIT", "price": round(price, 6),
                "reason": f"高周期({p['htf_key']})潮汐金叉翻转，趋势前提消失",
                "bar_time": bar_time,
            }
        return None

    if direction == 0:
        return None

    cs = indicators.closes(bars)
    ema_pb = indicators.ema(cs, pb_ema_n)
    rsi_series = indicators.rsi(cs, rsi_len)
    if len(ema_pb) < pb_window + 1 or len(rsi_series) < 2:
        return None

    # 回踩确认：最近 pb_window 根里价格是否至少触及过一次 pullback EMA
    recent = bars[-pb_window:]
    ema_recent = ema_pb[-pb_window:]
    rsi_prev, rsi_now = rsi_series[-2], rsi_series[-1]
    wake_long = float(p["rsi_wake_long"])
    wake_short = float(p["rsi_wake_short"])

    atr = indicators.wilder_atr(bars, atr_len)
    if atr <= 0:
        return None

    if direction == 1:
        touched = any(float(b["l"]) <= e for b, e in zip(recent, ema_recent))
        rsi_wake = rsi_prev <= wake_long < rsi_now
        if not (touched and rsi_wake):
            return None
        action, d = "LONG", 1
    else:
        touched = any(float(b["h"]) >= e for b, e in zip(recent, ema_recent))
        rsi_wake = rsi_prev >= wake_short > rsi_now
        if not (touched and rsi_wake):
            return None
        action, d = "SHORT", -1

    return {
        "action": action,
        "price": round(price, 6),
        "atr": round(atr, 6),
        "stop_loss": round(price - d * atr * float(p["atr_stop_mult"]), 6),
        "tp1": round(price + d * atr * 1.5, 6),
        "tp2": round(price + d * atr * 3.0, 6),
        "tp3": round(price + d * atr * 5.0, 6),
        "tier": 1,
        "bar_time": bar_time,
        "reason": (
            f"高周期({p['htf_key']})潮汐{'多' if direction == 1 else '空'}头 + "
            f"base回踩EMA{pb_ema_n} + RSI{rsi_len}从{wake_long if direction == 1 else wake_short:.0f}"
            f"区抬头({rsi_prev:.1f}->{rsi_now:.1f})"
        ),
    }
