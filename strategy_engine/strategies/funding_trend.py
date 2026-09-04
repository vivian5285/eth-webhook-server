#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Funding Rate + Trend(资金费率拥挤度过滤的趋势突破)——2026-09-04应宝贝
要求新增。

永续合约资金费率是公开、可查证的市场数据(币安 GET /fapi/v1/fundingRate,
无需 API Key),"资金费率极端 = 一边人太多 = 逼空/踩踏的燃料"是永续合约
市场里被反复讨论的公开现象,不是私有指标。

⚠️ 核心纪律(务必看懂):资金费率**只能当"拥挤度过滤器",不能单独当买卖
信号**。费率高不代表要做空(强牛市里费率可以连续几周为正、价格照样涨),
费率低也不代表要做多。它唯一可靠的用法是:当趋势 + 突破已经给出方向后,
用费率判断"这个方向是不是已经太拥挤、接下来更可能是我这方向的人被
挤出去"。所以这套战法的骨架是"趋势 + 突破"(跟 turtle / ema_cross 同
血统),资金费率是叠在上面的一道**否决/加成**滤网,不是入场触发器。
(这条道理 turtle_breakout / time_series_momentum 的注释里"不用震荡指标
确认"是另一个版本的同一种克制——别让一个辅助信号越权当主信号。)

规则：
  - 方向：EMA(ema_fast,默认50) vs EMA(ema_slow,默认200)。金叉上方 = 多头
    趋势,死叉下方 = 空头趋势,纠缠不开仓。
  - 突破：收盘价创 breakout_lookback(默认20)根新高 → 多头突破;创新低 →
    空头突破。趋势方向和突破方向必须一致。
  - 资金费率滤网(funding.funding_percentile,当前费率在自己最近
    funding_lookback(默认500)次结算历史里的分位数):
    · 否决——要做多,但当前费率分位 >= veto_pct(默认0.85)(多头已经在
      大额付费给空头,多头拥挤到历史高位)→ 放弃这次做多。做空对称
      (费率分位 <= 1-veto_pct 时放弃做空)。
    · 加成——要做多,且费率分位 <= boost_pct(默认0.15)(空头拥挤/多头
      几乎不付费,一旦上涨空头容易被挤)→ tier 提到 2(仓位更大)。做空
      对称。
    · 费率数据拉不到 / 样本不足 → 滤网整体失效,退化成纯"趋势 + 突破",
      不因为费率接口抖动就完全停摆(funding.py 已做多层容错 + 缓存)。
  - 离场：EMA 反向交叉(趋势前提消失)→ 主动平仓。ATR 止损安全网
    (atr_stop_mult 默认2.5)。不设固定止盈——趋势突破类跟 turtle /
    ema_cross_7_30 同一个理由,固定止盈会把大趋势尾部切掉。

跟本仓库其余战法的关键区别：
  - 唯一一套引入"K线以外的市场数据"(资金费率)的战法。所有其它战法
    只吃 OHLCV,这套额外读一条独立的资金费率时间序列。代价:它不像
    turtle 那样"给定同样K线随时重放同样结论"——最新费率只能实时拉,
    所以这套只在 live 擂台(multi_strategy_runner,每轮实时拉)里跑,
    backtest_runner 的逐bar历史回放不驱动它(见 funding.py 注释)。
  - 骨架跟 ema_cross_7_30 / turtle_breakout(趋势 + 突破)高度相似,
    差异**只有**资金费率这一道滤网——刻意这样设计,方便直接对照
    "加一道拥挤度否决,到底是过滤掉了追高踩踏、还是错过了主升浪",
    跟 dual_momentum vs cross_momentum(只差一道绝对动量过滤)是同一种
    "单变量对照实验"思路。

周期选择理由：1h。资金费率每 8 小时结算一次,战法节奏不能比这更快
(否则一次结算周期内反复被同一个费率读数触发)。1h 的 EMA50/200 +
20 根突破,对应"最近一天的新高/新低",跟 8 小时的费率结算节奏错落
得开,又比 4h/1d 更能及时抓到费率转极端时的突破点。

数据要求：base 至少 ema_slow + breakout_lookback + 2 根。资金费率历史
由 funding.py 拉取 + 缓存,至少 30 个样本分位数才生效。
"""
from __future__ import annotations

from typing import Dict, List, Optional

from strategy_engine import funding, indicators

DEFAULT_PARAMS = {
    "ema_fast": 50,
    "ema_slow": 200,
    "breakout_lookback": 20,
    "funding_lookback": 500,
    "veto_pct": 0.85,
    "boost_pct": 0.15,
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
                "reason": f"EMA{ema_fast}/{ema_slow}死叉,多头趋势前提消失",
                "bar_time": bar_time,
            }
        if side == "SHORT" and cross == "up":
            return {
                "action": "CLOSE_QUICK_EXIT", "price": round(price, 6),
                "reason": f"EMA{ema_fast}/{ema_slow}金叉,空头趋势前提消失",
                "bar_time": bar_time,
            }
        return None

    if direction == 0:
        return None

    # 突破：收盘价 vs 前 bl 根(不含当根)的高/低点
    prior = cs[-bl - 1:-1]
    hh, ll = max(prior), min(prior)
    if direction == 1 and price > hh:
        action, d = "LONG", 1
    elif direction == -1 and price < ll:
        action, d = "SHORT", -1
    else:
        return None

    # 资金费率拥挤度滤网
    fpct = funding.funding_percentile(symbol, int(p["funding_lookback"])) if symbol else None
    veto = float(p["veto_pct"])
    boost = float(p["boost_pct"])
    tier = 1
    funding_note = "费率数据不可用,滤网失效"
    if fpct is not None:
        if action == "LONG":
            if fpct >= veto:
                return None  # 多头过度拥挤,否决
            if fpct <= boost:
                tier = 2
        else:
            if fpct <= 1.0 - veto:
                return None  # 空头过度拥挤,否决
            if fpct >= 1.0 - boost:
                tier = 2
        funding_note = f"费率分位={fpct:.2f}" + ("(逆向拥挤,加仓)" if tier == 2 else "")

    atr = indicators.wilder_atr(bars, atr_len)
    if atr <= 0:
        return None

    return {
        "action": action,
        "price": round(price, 6),
        "atr": round(atr, 6),
        "stop_loss": round(price - d * atr * float(p["atr_stop_mult"]), 6),
        "tier": tier,
        "bar_time": bar_time,
        "reason": (
            f"EMA{ema_fast}/{ema_slow}{'多' if direction == 1 else '空'}头趋势 + "
            f"{bl}根{'新高' if action == 'LONG' else '新低'}突破 | {funding_note}"
        ),
    }
