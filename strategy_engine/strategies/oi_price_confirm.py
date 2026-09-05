#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
持仓量+价格确认(Open Interest + Price Confirmation)——2026-09-05应宝贝
转发的"永续合约主流战法大全"整理稿新增。永续合约持仓量(Open Interest,
简称OI)是公开、可查证的市场数据(币安 GET /futures/data/openInterestHist,
无需API Key)，"价格涨跌配合OI涨跌判断这波行情是不是真的有新钱进场"是
永续合约交易圈里被反复讨论的公开框架，不是私有指标。

核心框架(该文档本身也整理过的经典四象限，公开常识不是本仓库发明)：
  - 价格↑ + OI↑ = 新增杠杆资金进场做多，趋势确认
  - 价格↑ + OI↓ = 空头平仓推动的反弹(短线挤压)，不是新增买盘，偏弱
  - 价格↓ + OI↑ = 新增杠杆资金进场做空，趋势确认
  - 价格↓ + OI↓ = 多头平仓踩踏，不是新增卖盘，偏弱(接近筹码出清尾声)
  本模块只做多头/空头各自"OI确认"这一半(↑OI才进场)，↓OI的两种情况都
  视为"这次突破不可信"直接跳过，不做反向操作(避免过度解读"平仓driven"
  行情的方向)。

规则(骨架完全复用funding_trend.py的"EMA50/200方向+突破"，只把资金
费率滤网换成OI滤网，方便直接对照"资金费率滤网 vs OI滤网，哪个更好
用"这个单变量实验，是本仓库dual_momentum vs cross_momentum那种对照
思路的又一组)：
  - 方向：EMA(ema_fast,默认50) vs EMA(ema_slow,默认200)，金叉上方=多头，
    死叉下方=空头
  - 突破：收盘价创breakout_lookback(默认20)根新高/新低，方向须跟EMA
    趋势一致
  - OI确认滤网：oi_period(默认4h)周期的OI历史，最新值 vs oi_lookback
    (默认5)个数据点之前的值——**必须是上升**才放行(不管做多还是做空，
    都要求"有新钱真的在进场"，OI下降一律否决，不做方向猜测)
  - 离场：EMA反向交叉，ATR止损兜底，不设固定止盈(跟funding_trend/
    turtle_breakout同一个"别在趋势中途设死止盈"的理由)

⚠️ 诚实说明的架构限制(照抄funding_trend.py同一条边界)：币安OI历史
端点**保留时间有限**(实测约1个月内)，没法像K线一样做逐bar历史长期
回放——这套战法只在live擂台(multi_strategy_runner，每轮实时拉)里跑，
backtest_runner的历史回放不驱动它。

跟本仓库其余"衍生品数据滤网"类战法的关键区别：funding_trend用资金
费率的历史分位数(判断"多空哪一边太拥挤")当否决/加成门槛；这套用OI
的**趋势方向**(判断"这波价格波动背后有没有真的新钱")当二元开关，是
两种完全不同的衍生品数据用法——一个看情绪极端，一个看资金流增减。

周期选择理由：4h，OI数据点周期跟K线周期对齐(oi_period="4h")，保证
"最新OI vs 5个点前的OI"这个比较窗口跟K线的时间尺度一致，也落在OI
历史数据~1个月保留期内能覆盖足够样本。

数据要求：base至少ema_slow+breakout_lookback+2根。OI历史由
open_interest.py拉取+缓存，至少oi_lookback+1个数据点才能判断趋势。
"""
from __future__ import annotations

from typing import Dict, List, Optional

from strategy_engine import indicators, open_interest

DEFAULT_PARAMS = {
    "ema_fast": 50,
    "ema_slow": 200,
    "breakout_lookback": 20,
    "oi_period": "4h",
    "oi_lookback": 5,
    "atr_len": 14,
    "atr_stop_mult": 2.5,
}


def _ema_dir_cross(cs: List[float], fast_n: int, slow_n: int):
    fast = indicators.ema(cs, fast_n)
    slow = indicators.ema(cs, slow_n)
    if len(fast) < 2 or len(slow) < 2:
        return 0, None
    f0, f1, s0, s1 = fast[-2], fast[-1], slow[-2], slow[-1]
    direction = 1 if f1 > s1 else (-1 if f1 < s1 else 0)
    cross = "up" if (f0 <= s0 and f1 > s1) else ("down" if (f0 >= s0 and f1 < s1) else None)
    return direction, cross


def generate_signal(bars_by_tf: Dict[str, List[dict]], params: Optional[dict] = None, position: Optional[dict] = None) -> Optional[dict]:
    bars = bars_by_tf.get("base") or []
    p = {**DEFAULT_PARAMS, **(params or {})}
    ema_fast = int(p["ema_fast"])
    ema_slow = int(p["ema_slow"])
    bl = int(p["breakout_lookback"])
    atr_len = int(p["atr_len"])
    need = ema_slow + bl + 2
    if len(bars) < need:
        return None

    last = bars[-1]
    price = float(last["c"])
    bar_time = int(last["t"])
    symbol = str((params or {}).get("symbol") or "")
    cs = indicators.closes(bars)
    direction, cross = _ema_dir_cross(cs, ema_fast, ema_slow)

    if position:
        side = str(position.get("side") or "").upper()
        if side == "LONG" and cross == "down":
            return {
                "action": "CLOSE_QUICK_EXIT", "price": round(price, 6),
                "reason": f"EMA{ema_fast}/{ema_slow}死叉，多头趋势前提消失",
                "bar_time": bar_time,
            }
        if side == "SHORT" and cross == "up":
            return {
                "action": "CLOSE_QUICK_EXIT", "price": round(price, 6),
                "reason": f"EMA{ema_fast}/{ema_slow}金叉，空头趋势前提消失",
                "bar_time": bar_time,
            }
        return None

    if direction == 0:
        return None

    prior = cs[-bl - 1:-1]
    hh, ll = max(prior), min(prior)
    if direction == 1 and price > hh:
        action, d = "LONG", 1
    elif direction == -1 and price < ll:
        action, d = "SHORT", -1
    else:
        return None

    oi_lookback = int(p["oi_lookback"])
    oi_values = open_interest.get_oi_history(symbol, str(p["oi_period"]), limit=oi_lookback + 30) if symbol else []
    if len(oi_values) < oi_lookback + 1:
        return None  # OI数据不足，滤网没法生效，这套战法就不进场(跟funding_trend"拉不到就退化成纯趋势"不同：
        # 那套OI确认是硬性开关不是加成，数据不足时宁可不做，不做方向猜测)
    oi_now, oi_then = oi_values[-1], oi_values[-1 - oi_lookback]
    if not (oi_now > oi_then):
        return None  # OI没有上升，判定这波突破缺乏新增资金支撑，否决

    atr = indicators.wilder_atr(bars, atr_len)
    if atr <= 0:
        return None

    oi_pct = (oi_now / oi_then - 1.0) * 100.0 if oi_then > 0 else 0.0
    return {
        "action": action,
        "price": round(price, 6),
        "atr": round(atr, 6),
        "stop_loss": round(price - d * atr * float(p["atr_stop_mult"]), 6),
        "tier": 1,
        "bar_time": bar_time,
        "reason": (
            f"EMA{ema_fast}/{ema_slow}{'多' if direction == 1 else '空'}头趋势 + "
            f"{bl}根{'新高' if action == 'LONG' else '新低'}突破 + "
            f"OI{oi_lookback}期上升{oi_pct:+.1f}%(新增资金确认)"
        ),
    }
