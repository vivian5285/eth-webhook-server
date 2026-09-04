#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Opening Range Breakout(开盘区间突破,ORB)——2026-09-04应宝贝要求新增。

ORB 是 Toby Crabel 在《Day Trading with Short Term Price Patterns and
Opening Range Breakout》(1990)里系统化的公开日内策略,几十年来被无数
文章/书籍复现讨论:取开盘后前 N 分钟的高低点作"开盘区间",价格突破
区间高点做多、跌破低点做空,失败则快速止损,收盘前平仓。规则透明可
手算复现,符合本擂台准入线。

⚠️ session 锚点的诚实说明：ORB 的前提是"有一个真实的市场开盘时刻"。
  - 代币化美股品种(TSLA/META/GS/MU/OPENAI/ANTHROPIC/SNDK/LITE):它们
    跟踪的是真实美股,有真实开盘 9:30 ET。本模块 anchor="us_equity"
    时按美东时间 9:30 算(自动处理夏令时:EDT=13:30 UTC / EST=14:30 UTC),
    这是最贴合 ORB 原始设计意图的用法。
  - 纯加密货币品种:没有"开盘"这回事。anchor="utc" 时退化成用 UTC 00:00
    作区间起点——这是一个**人为类比**,不是市场结构给的。加密货币
    24/7 连续交易,"UTC 0 点后的前 30 分钟"并没有任何特殊的流动性/
    参与者结构,这套在纯币品种上大概率不如在代币化股票上有意义,
    这个局限写在这里而不是藏着。

规则：
  - 开盘区间:session 锚点之后 or_minutes(默认30)分钟内所有K线的最高价
    /最低价 = OR_high / OR_low。区间必须已经走完(当前K线开盘时间 >=
    锚点 + or_minutes)才开始找突破。
  - 入场:当前K线收盘价 > OR_high → 做多;< OR_low → 做空。只认**当日
    session 内第一次**突破(之前已经有K线收盘越过同一侧的,不再重复进),
    避免一天内在区间边缘反复抽的时候刷出一堆交易。
  - 止损:区间的另一侧——做多止损放 OR_low,做空止损放 OR_high。ORB
    的经典风控就是"突破失败=价格缩回区间另一侧",止损位由区间本身
    定义,不用 ATR 倍数(ATR 只用于 pnl 归一)。
  - 离场:当前K线开盘时间超过 session 锚点 + trade_window_min(默认390,
    即 6.5 小时,一个美股交易日的长度)→ 主动平仓(ORB 是日内策略,
    不留夜)。

跟本仓库其余战法的关键区别：
  - 唯一一套**信号由"绝对时钟时间"驱动**的战法。其它所有战法都是
    "K线收盘就重算指标",跟这根K线出现在一天中的哪个时刻无关;ORB
    只在 session 锚点后的特定时间窗口里才可能开仓,换个时段同样的
    价格形态什么都不做。
  - 跟 turtle_breakout / breakout_retest / volatility_breakout 同属
    "突破"大类,但那三套的"区间"是滚动的(Donchian N 期 / 昨日振幅),
    这套的区间是"每天固定时刻起算、当天之内不变"的水平线。
  - 跟 volatility_breakout(也是日内、也快进快出)最接近,区别:那套
    突破位是"今日开盘 ± k×昨日振幅"(用昨天的信息),这套是"今日开盘
    后前 30 分钟自己的高低点"(只用今天开盘后的信息)。

周期选择理由：15m。要在 or_minutes(30)分钟的开盘区间里至少有 2 根K线,
又要在 trade_window(6.5h)里有足够多的判定点——15m 刚好(区间 2 根、
交易窗口 26 根)。5m 太碎(区间受微观结构影响大),1h 则一根就盖过
整个开盘区间、没法用。

数据要求：至少覆盖 2 个 session + 若干缓冲。BARS_LIMIT(550)×15m ≈ 5.7 天,
足够(代币化股票只有工作日有美股 session,但K线是币安永续的 24/7 K线,
锚点计算不受影响)。
"""
from __future__ import annotations

import datetime
from typing import Dict, List, Optional

from strategy_engine import indicators

_DAY_MS = 24 * 60 * 60 * 1000
_MIN_MS = 60 * 1000

DEFAULT_PARAMS = {
    "anchor": "utc",           # "utc" | "us_equity"
    "or_minutes": 30,
    "trade_window_min": 390,
    "atr_len": 14,
}


def _us_eastern_is_dst(dt_utc: datetime.datetime) -> bool:
    """美国夏令时:3 月第二个周日 02:00 起,11 月第一个周日 02:00 止。
    用 UTC 日期近似判断(转换当天那 1 小时的边界误差对"日锚点"策略
    可以忽略)。"""
    year = dt_utc.year
    march1 = datetime.datetime(year, 3, 1)
    second_sun_mar = 1 + ((6 - march1.weekday()) % 7) + 7
    dst_start = datetime.datetime(year, 3, second_sun_mar, 7)   # 02:00 EST = 07:00 UTC
    nov1 = datetime.datetime(year, 11, 1)
    first_sun_nov = 1 + ((6 - nov1.weekday()) % 7)
    dst_end = datetime.datetime(year, 11, first_sun_nov, 6)     # 02:00 EDT = 06:00 UTC
    return dst_start <= dt_utc < dst_end


def _session_anchor_ms(bar_time_ms: int, anchor: str):
    """返回该K线所属交易日的 session 起点(epoch ms)。us_equity 且当天是
    周末(美股休市)时返回 None——币安永续周末照常有K线,但那天没有真实
    "开盘"可锚,不产生新开仓(已持仓的仍会在 session_end 前平掉)。"""
    day_start = (bar_time_ms // _DAY_MS) * _DAY_MS
    if anchor == "us_equity":
        dt = datetime.datetime.utcfromtimestamp(day_start / 1000.0)
        if dt.weekday() >= 5:  # 5=周六 6=周日
            return None
        open_min = 13 * 60 + 30 if _us_eastern_is_dst(dt) else 14 * 60 + 30
        return day_start + open_min * _MIN_MS
    return day_start  # utc: 00:00


def generate_signal(bars_by_tf: Dict[str, List[dict]], params: Optional[dict] = None, position: Optional[dict] = None) -> Optional[dict]:
    bars = bars_by_tf.get("base") or []
    p = {**DEFAULT_PARAMS, **(params or {})}
    anchor = str(p["anchor"])
    or_ms = int(p["or_minutes"]) * _MIN_MS
    win_ms = int(p["trade_window_min"]) * _MIN_MS
    atr_len = int(p["atr_len"])
    if len(bars) < atr_len + 10:
        return None

    last = bars[-1]
    price = float(last["c"])
    bar_time = int(last["t"])
    anchor_ms = _session_anchor_ms(bar_time, anchor)
    if anchor_ms is None:
        # us_equity 周末:不开新仓;若还持有(周五留下的,不应该发生因为
        # 会在周五 session_end 平掉),交给下面 position 分支用上一交易日
        # 的 session_end 兜底也来不及——直接强制平。
        if position:
            return {
                "action": "CLOSE_QUICK_EXIT", "price": round(price, 6),
                "reason": "美股周末休市,ORB日内仓强制离场", "bar_time": bar_time,
            }
        return None
    # 当前K线还没到今天的 session 锚点(us_equity 在美盘开盘前),不动
    if bar_time < anchor_ms:
        return None
    session_end_ms = anchor_ms + win_ms

    if position:
        if bar_time >= session_end_ms:
            return {
                "action": "CLOSE_QUICK_EXIT", "price": round(price, 6),
                "reason": "交易窗口结束,日内策略不留夜,平仓",
                "bar_time": bar_time,
            }
        return None

    # 开盘区间:锚点 <= K线开盘 < 锚点+or_minutes
    or_bars = [b for b in bars if anchor_ms <= int(b["t"]) < anchor_ms + or_ms]
    if len(or_bars) < 2:
        return None
    # 区间必须已走完
    if bar_time < anchor_ms + or_ms:
        return None
    # 只在交易窗口内开仓
    if bar_time >= session_end_ms:
        return None

    or_high = max(float(b["h"]) for b in or_bars)
    or_low = min(float(b["l"]) for b in or_bars)
    if or_high <= or_low:
        return None

    # 当日 session 内、区间走完之后、当前根之前的K线
    post_or = [b for b in bars if anchor_ms + or_ms <= int(b["t"]) < bar_time]
    already_up = any(float(b["c"]) > or_high for b in post_or)
    already_dn = any(float(b["c"]) < or_low for b in post_or)

    atr = indicators.wilder_atr(bars, atr_len)
    if atr <= 0:
        return None

    if price > or_high and not already_up:
        return {
            "action": "LONG", "price": round(price, 6), "atr": round(atr, 6),
            "stop_loss": round(or_low, 6), "tier": 1, "bar_time": bar_time,
            "reason": f"突破开盘区间高点{or_high:.6f}(区间{or_low:.6f}~{or_high:.6f},anchor={anchor})",
        }
    if price < or_low and not already_dn:
        return {
            "action": "SHORT", "price": round(price, 6), "atr": round(atr, 6),
            "stop_loss": round(or_high, 6), "tier": 1, "bar_time": bar_time,
            "reason": f"跌破开盘区间低点{or_low:.6f}(区间{or_low:.6f}~{or_high:.6f},anchor={anchor})",
        }
    return None
