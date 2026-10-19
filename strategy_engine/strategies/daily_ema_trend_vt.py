#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
日线EMA8/30多空 + 按波动率调仓 + 山寨币跟BTC同方向——2026-10-16新增，是 daily_ema8_30_ls 的降回撤对照版。
宝贝要求"思考多种不同方案降低回撤增加因子和胜率"。

在 daily_ema_trend(按状态持仓)基础上只加两条：
  1. 按波动率调仓：名义仓位 = 净值 × 1/16 × min(1, 目标日波动2% ÷ 该品种日ATR%)，波动越大买得越少
     (已经扣掉擂台 compute_qty 本身按止损距离缩仓的那一层，避免重复缩仓)。
  2. BTC方向过滤：非BTC品种只在 BTC 日线 EMA8/30 同方向时才开新仓(BTC多头只开多，空头只开空)；
     离场照常按自身均线翻转。BTC自己不过滤。

回测(/root/research/crypto_dd_optimization_20261016.py，币安现货日线2017起，16个币，固定数量逐笔、次日开盘成交)：
  基准多空          年化78.8% 回撤50.4% 收益/回撤1.56 前/后半段 4.54/0.50 盈亏因子1.75
  本方案(2%)        年化35.6% 回撤22.1% 收益/回撤1.61 前/后半段 2.40/0.92 盈亏因子2.25
  目标1.5%/3%       1.40/1.66，后半段都约0.9，说明不是参数凑出来的
  吊灯移动止损、分批止盈、1R保本、ADX过滤、跌破快线提前离场 → 全部变差，没有采用。
纸面实验，只跑币圈。
"""
from __future__ import annotations

import time
from typing import Dict, List, Optional

from strategy_engine import indicators
from strategy_engine.strategies import daily_ema_trend

DEFAULT_PARAMS = {
    **daily_ema_trend.DEFAULT_PARAMS,
    "mode": "both",
    # 回测里每个品种的名义仓位 = 净值 × target_notional_frac × min(1, vol_target/ATR%)。
    # 1/16≈0.0625：跟回测"16个币等权"一致，也让每单止损风险约0.5%净值，stress状态4%热度能放下约8单。
    "target_notional_frac": 0.0625,
    "vol_target": 0.02,
    "btc_filter": True,
    # 2026-10-17：可选 BTC 减半周期过滤(牛市阶段不开空、熊市阶段不开多、筑底都开)，见 strategy_engine/halving_cycle.py。
    # 默认关闭，已有的 daily_ema8_30_ls_vt_btc / _main5 行为不变。
    "halving_cycle": False,
}

_BTC_CACHE = {"ts": 0.0, "state": None}


def _btc_state() -> Optional[int]:
    """BTC日线(只用已收盘K线) EMA8>EMA30 返回1，否则-1；数据不足返回None。缓存10分钟。"""
    now = time.time()
    if now - _BTC_CACHE["ts"] < 600:
        return _BTC_CACHE["state"]
    from strategy_engine import klines
    state = None
    try:
        bars = klines.get_bars("BTCUSDT", "1d", 200)
        closes = indicators.closes(bars)
        if len(closes) >= 40:
            f, s = indicators.ema(closes, 8), indicators.ema(closes, 30)
            state = 1 if f[-1] > s[-1] else -1
    except Exception:
        state = None
    _BTC_CACHE.update(ts=now, state=state)
    return state


def generate_signal(bars_by_tf: Dict[str, List[dict]], params: Optional[dict] = None, position: Optional[dict] = None) -> Optional[dict]:
    p = {**DEFAULT_PARAMS, **(params or {})}
    sig = daily_ema_trend.generate_signal(bars_by_tf, p, position)
    if position or not sig or sig.get("action") not in ("LONG", "SHORT"):
        return sig
    symbol = str(p.get("symbol") or "").upper()
    counter_txt = ""
    if p.get("halving_cycle"):
        from strategy_engine import halving_cycle
        if not halving_cycle.allows(sig["action"], int(sig.get("bar_time") or 0) / 1000 or None):
            # 2026-10-17：熊市段唯一例外——极端底部反转(周线超卖+MACD拐头+放量+距年高-35%)，带3×ATR止损做多。
            if not (p.get("bottom_reversal") and sig["action"] == "LONG"):
                return None
            from strategy_engine import bottom_reversal, klines
            long_bars = klines.get_bars(symbol or "BTCUSDT", "1d", 600) or []
            long_bars = [x for x in long_bars if int(x["t"]) <= int(sig.get("bar_time") or 0)]
            ok, txt = bottom_reversal.check(long_bars)
            if not ok:
                return None
            sig = dict(sig)
            sig["stop_loss"] = round(float(sig["price"]) - 3.0 * float(sig.get("atr") or 0), 6)
            counter_txt = f"；熊市段底部反转例外【{txt}】；3×ATR止损"
    if p.get("btc_filter") and symbol != "BTCUSDT":
        btc = _btc_state()
        want = 1 if sig["action"] == "LONG" else -1
        if btc is None or btc != want:
            return None
    price, atr = float(sig.get("price") or 0), float(sig.get("atr") or 0)
    if price <= 0 or atr <= 0:
        return None
    atr_pct = atr / price
    alloc = min(1.0, float(p["vol_target"]) / atr_pct)
    # 擂台 compute_qty 已经按止损距离算仓位(止损=atr_stop_mult×ATR，波动大自动少买)，
    # 直接再乘 alloc 会按波动率平方缩仓。这里先算出擂台默认给的名义仓位占净值比例，
    # 再换算成回测要求的"名义仓位 ∝ 1/ATR%"。
    from strategy_engine import position_sizing as ps
    stop_frac = (3.0 if counter_txt else float(p["atr_stop_mult"])) * atr_pct   # 底部反转例外用3×ATR止损
    default_notional = (min(ps.LEVERAGE * ps.NOTIONAL_MARGIN_HAIRCUT, 1.0 / stop_frac)
                        * ps.RISK_PCT * ps.TIER_NOTIONAL_MULT[1])
    desired_notional = float(p["target_notional_frac"]) * alloc
    sig = dict(sig)
    sig["position_fraction"] = desired_notional / default_notional
    sig["reason"] = f"{sig.get('reason', '')}；波动率调仓×{alloc:.2f}" + ("；BTC同向" if symbol != "BTCUSDT" else "") + counter_txt
    return sig


# 2026-10-19 方案F复刻加固：BTC 日线方向用现货K线(跟 8890 回测同源)。供 daily_ema_brk / intraday_trend_cycle 用；其它老策略仍用 _btc_state。
_BTC_SPOT_CACHE = {"ts": 0.0, "state": None}


def _btc_state_spot() -> Optional[int]:
    now = time.time()
    if now - _BTC_SPOT_CACHE["ts"] < 600:
        return _BTC_SPOT_CACHE["state"]
    from strategy_engine import klines
    state = None
    try:
        closes = [float(x["c"]) for x in (klines.get_spot_bars("BTCUSDT", "1d", 200) or [])]
        if len(closes) >= 40:
            k8, k30 = 2 / 9, 2 / 31; a = b = None
            for c in closes:
                a = c if a is None else a + k8 * (c - a); b = c if b is None else b + k30 * (c - b)
            state = 1 if a > b else -1
    except Exception:
        state = None
    if state is not None:
        _BTC_SPOT_CACHE.update(ts=now, state=state)
    return state
