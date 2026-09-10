#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
币圈专属：价格 / 持仓量(OI) / 资金费率 三者背离 —— 2026-09-10 应宝贝转发的
「跨资产量化战法大全」新增。只挂纯加密品种（不含代币化美股/贵金属）。

为什么这是币圈独有的武器：
  永续合约的价格只是"结果"，OI（未平仓合约）和资金费率能看出杠杆资金
  到底在干什么。经典四象限：
    · 价格↑ + OI↑ = 新多进场，趋势健康（不在本战法范围，趋势跟随类已覆盖）
    · 价格↑ + OI↓ = 空头平仓推的反弹，没有新买盘 → 弱，往回做空（fade）
    · 价格↓ + OI↓ = 多头去杠杆踩踏，没有新卖盘 → 常见反弹，往回做多
    · 价格↓ + OI↑ = 新空进场（本战法不追这个方向，交给趋势类）
  再叠加资金费率极端 + 单根反转K线，识别"杠杆清算接近尾声"的高确信反转：
    · 暴跌 + OI 暴降 + 费率分位极低 + 阳线反包 → 多头清算瀑布见底，做多（tier2）
    · 暴涨 + OI 暴降 + 费率分位极高 + 阴线反包 → 空头挤压见顶，做空（tier2）

跟擂台已有 funding_trend / oi_price_confirm 的区别：那两套把资金费率 / OI
当**过滤器**（趋势突破骨架 + 一道否决门）；这套把"价格与 OI 的背离"当
**信号本身**，是均值回归/反转性质，方向常常跟当前价格短期动量相反。

规则（4h）：
  · ret_n = 收盘价 lookback(6根=1天) 的涨跌幅
  · oi_chg = OI 同窗口涨跌幅（open_interest.get_oi_history，4h 周期）
  · fpct = 资金费率当前值在自身历史的分位（funding.funding_percentile）
  · ADX(14) < adx_max(25)：强趋势里背离容易被无视，只在非强趋势里做
  · 背离 SHORT：ret_n ≥ div_pct(5%) 且 oi_chg ≤ -div_oi_pct(2%) 且当根阴线
    （收 < 前收）→ fade。止损 = 近端摆动高 + 0.5ATR。
  · 背离 LONG：ret_n ≤ -div_pct 且 oi_chg ≤ -div_oi_pct 且当根阳线 → fade。
  · 清算反转（覆盖背离条件，tier2）：|ret_n| ≥ crash_pct(8%) 且 oi_chg ≤
    -oi_crash_pct(6%) 且 费率分位站在极端（跌时 ≤ fund_lo / 涨时 ≥ fund_hi）
    且当根反包 → 逆势进，tier=2。
  · 离场：OI 重新上升超过 oi_recover_pct(2%)（杠杆重新进场，背离修复）；或
    持有超过 max_hold(12根=2天)（均值回归是快照式的，不赖着）；或 ATR 止损
    兜底（runner 通用逻辑，另加 5x 模拟强平）。不设固定止盈。

⚠️ 架构限制（同 funding_trend / oi_price_confirm）：币安 OI / 资金费率历史
保留期有限，没法逐 bar 长期回放——本战法只在 live 擂台跑，backtest_runner
不驱动它。
"""
from __future__ import annotations

from typing import Dict, List, Optional

from strategy_engine import indicators

DEFAULT_PARAMS = {
    "lookback": 6,              # 4h × 6 = 1 天
    "atr_len": 14,
    "adx_max": 28.0,
    "div_pct": 0.04,
    "div_oi_pct": 0.015,
    "crash_pct": 0.08,
    "oi_crash_pct": 0.06,
    "fund_hi": 0.85,
    "fund_lo": 0.15,
    "oi_recover_pct": 0.02,
    "max_hold": 12,
    "atr_stop_mult": 2.5,
    "oi_period": "4h",
}


def _oi_change(symbol: str, period: str, lookback: int) -> Optional[float]:
    try:
        from strategy_engine import open_interest
        vals = open_interest.get_oi_history(symbol, period, max(lookback + 5, 20))
    except Exception:
        return None
    if len(vals) < lookback + 1 or vals[-1 - lookback] <= 0:
        return None
    return vals[-1] / vals[-1 - lookback] - 1.0


def _funding_pctile(symbol: str) -> Optional[float]:
    try:
        from strategy_engine import funding
        return funding.funding_percentile(symbol)
    except Exception:
        return None


def _is_bull_bar(b: dict) -> bool:
    return float(b["c"]) > float(b["o"])


def generate_signal(bars_by_tf: Dict[str, List[dict]], params: Optional[dict] = None, position: Optional[dict] = None) -> Optional[dict]:
    p = {**DEFAULT_PARAMS, **(params or {})}
    pr = params or {}
    bars = bars_by_tf.get("base") or []
    symbol = str(pr.get("symbol") or "").upper()
    lb = int(p["lookback"])
    atr_len = int(p["atr_len"])
    if not symbol or len(bars) < atr_len + lb + 10:
        return None

    last = bars[-1]
    prev = bars[-2]
    price = float(last["c"])
    bar_time = int(last["t"])
    ret_n = price / float(bars[-1 - lb]["c"]) - 1.0 if float(bars[-1 - lb]["c"]) > 0 else 0.0
    oi_chg = _oi_change(symbol, str(p["oi_period"]), lb)
    atr = indicators.wilder_atr(bars, atr_len)
    if atr <= 0:
        return None

    # ── 持仓：背离修复 / 超时 ──────────────────────────────────────
    if position:
        side = str(position.get("side") or "").upper()
        entry_bt = int(position.get("entry_bar_time") or 0)
        held = sum(1 for b in bars if int(b["t"]) > entry_bt)
        if oi_chg is not None and oi_chg >= float(p["oi_recover_pct"]):
            return {"action": "CLOSE_QUICK_EXIT", "price": round(price, 6),
                    "reason": f"OI重新上升({oi_chg:+.1%})，杠杆回场、背离修复", "bar_time": bar_time}
        if held >= int(p["max_hold"]):
            return {"action": "CLOSE_QUICK_EXIT", "price": round(price, 6),
                    "reason": f"持有超过{p['max_hold']}根，均值回归窗口关闭", "bar_time": bar_time}
        return None

    # ── 空仓：需要 OI 数据；拉不到就不评估 ──────────────────────────
    if oi_chg is None:
        return None

    bull = _is_bull_bar(last) and price > float(prev["c"])
    bear = (not _is_bull_bar(last)) and price < float(prev["c"])
    fpct = _funding_pctile(symbol)
    lows = indicators.swing_points(bars[-40:], "low")
    highs = indicators.swing_points(bars[-40:], "high")
    swing_lo = lows[-1][1] if lows else min(float(b["l"]) for b in bars[-lb:])
    swing_hi = highs[-1][1] if highs else max(float(b["h"]) for b in bars[-lb:])

    crash = float(p["crash_pct"])
    oi_crash = float(p["oi_crash_pct"])
    div = float(p["div_pct"])
    div_oi = float(p["div_oi_pct"])

    # 清算瀑布反转（高确信，tier2）——这本身就是强趋势急跌/急涨，不受
    # ADX<25 门槛限制（清算行情 ADX 必然高）。
    if ret_n <= -crash and oi_chg <= -oi_crash and bull and (fpct is not None and fpct <= float(p["fund_lo"])):
        stop = swing_lo - 0.5 * atr
        if stop < price:
            return {"action": "LONG", "price": round(price, 6), "atr": round(atr, 6),
                    "stop_loss": round(stop, 6), "tier": 2, "bar_time": bar_time,
                    "reason": f"多头清算见底(跌{ret_n:.1%}/OI{oi_chg:.1%}/费率分位{fpct:.2f}/阳包)"}
    if ret_n >= crash and oi_chg <= -oi_crash and bear and (fpct is not None and fpct >= float(p["fund_hi"])):
        stop = swing_hi + 0.5 * atr
        if stop > price:
            return {"action": "SHORT", "price": round(price, 6), "atr": round(atr, 6),
                    "stop_loss": round(stop, 6), "tier": 2, "bar_time": bar_time,
                    "reason": f"空头挤压见顶(涨{ret_n:.1%}/OI{oi_chg:.1%}/费率分位{fpct:.2f}/阴包)"}

    # 普通背离 fade（tier1）——只在非强趋势里做（强趋势里越偏离越不回归）
    adx = indicators.wilder_adx(bars, atr_len)
    if adx >= float(p["adx_max"]):
        return None
    if ret_n >= div and oi_chg <= -div_oi and bear:
        stop = swing_hi + 0.5 * atr
        if stop > price:
            return {"action": "SHORT", "price": round(price, 6), "atr": round(atr, 6),
                    "stop_loss": round(stop, 6), "tier": 1, "bar_time": bar_time,
                    "reason": f"价涨OI跌背离(涨{ret_n:.1%}/OI{oi_chg:.1%})，空头平仓推的弱反弹"}
    if ret_n <= -div and oi_chg <= -div_oi and bull:
        stop = swing_lo - 0.5 * atr
        if stop < price:
            return {"action": "LONG", "price": round(price, 6), "atr": round(atr, 6),
                    "stop_loss": round(stop, 6), "tier": 1, "bar_time": bar_time,
                    "reason": f"价跌OI跌背离(跌{ret_n:.1%}/OI{oi_chg:.1%})，多头去杠杆踩踏近尾声"}
    return None
