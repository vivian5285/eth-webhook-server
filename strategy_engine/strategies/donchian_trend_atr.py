#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
趋势过滤 + 唐奇安突破入场 + ATR止损 + 吊灯移动止盈 —— 2026-10-07 应宝贝要求新增。

宝贝的原话思路："抓趋势没有单一指标能同时做到稳、准、狠……真正更稳准狠的是
趋势过滤 + 突破入场 + ATR止损 + 移动止盈"。拆成四层，每一层都是公开经典的
单一职责组件(海龟 / 均线 / ADX / 吊灯止损)，不是自创指标：

  1. 趋势过滤(定方向)：EMA快 > EMA慢 且 收盘 > EMA快 才允许做多
     (默认 50/200，日线语义；周期换了请换参数，见 research 的参数研究)。
  2. 突破入场(定时机)：收盘突破"不含当前根"的过去 don_n 根最高价(唐奇安)。
     可选再叠加：ADX >= adx_min(确认有趋势)、成交量 >= vol_mult×均量、
     布林过滤(中轨向上且带宽扩张)——全是开关，默认关掉的不影响默认行为。
  3. ATR止损(定风险)：入场价 - stop_atr × ATR，作为硬止损随信号返回，
     由runner通用逻辑(价格穿过就平仓)执行。
  4. 移动止盈(让利润奔跑)：任一触发就收盘离场——
       · 吊灯：入场以来最高价 - trail_atr × 当前ATR
       · 反向唐奇安：收盘跌破过去 exit_n 根最低价
       · EMA快线：收盘跌破EMA快线(默认关)
     不设固定止盈；离场后**不反手**(宝贝："跌破中轨不反手，先出场")。

做空对称(allow_short=True 才启用)：EMA快<慢、收盘<EMA快、跌破 don_n 根最低价。
美股研究显示做空美股整体拖后腿，所以默认只做多；币圈/黄金是否开做空见参数研究结论。

为什么周期上不写死：同一份代码可以挂4h(加密/黄金)或1d(美股)，参数用 params 覆盖。
数据要求：至少 ema_slow + 5 根K线。
"""
from __future__ import annotations

from typing import Dict, List, Optional

from strategy_engine import indicators

DEFAULT_PARAMS = {
    "ema_fast": 50,
    "ema_slow": 200,
    "don_n": 20,
    "exit_n": 10,
    "adx_len": 14,
    "adx_min": 25.0,       # 0=关闭ADX过滤
    "vol_mult": 0.0,       # 0=关闭成交量过滤；>0 要求放量(>=均量×倍数)
    "vol_len": 20,
    "bb_filter": False,    # True=要求布林中轨同向且带宽相对3根前扩张
    "bb_n": 20,
    "bb_k": 2.0,
    "atr_len": 14,
    "stop_atr": 2.0,
    "trail_atr": 3.0,
    "chandelier": True,    # 吊灯移动止盈
    "don_exit": True,      # 反向exit_n通道破位离场
    "ema_exit": False,     # 收盘跌破EMA快线离场
    "allow_short": False,
}


def _bool(x) -> bool:
    return bool(x) and str(x).lower() not in ("0", "false", "no", "")


def generate_signal(bars_by_tf: Dict[str, List[dict]], params: Optional[dict] = None, position: Optional[dict] = None) -> Optional[dict]:
    bars = bars_by_tf.get("base") or []
    p = {**DEFAULT_PARAMS, **(params or {})}
    ef_n, es_n = int(p["ema_fast"]), int(p["ema_slow"])
    don_n, exit_n, atr_len = int(p["don_n"]), int(p["exit_n"]), int(p["atr_len"])
    need = max(es_n, don_n, exit_n, atr_len * 2, int(p["bb_n"]), int(p["adx_len"]) * 2 + 2) + 5
    if len(bars) < need:
        return None

    closes = indicators.closes(bars)
    ef = indicators.ema(closes, ef_n)
    es = indicators.ema(closes, es_n)
    if not ef or not es:
        return None
    ef_v, es_v = ef[-1], es[-1]
    last = bars[-1]
    price = float(last["c"])
    bar_time = int(last["t"])
    atr = indicators.wilder_atr(bars, atr_len)
    if atr <= 0:
        return None

    # ---------------- 持仓：只处理"主动离场" ----------------
    if position:
        side = str(position.get("side") or "").upper()
        entry_t = int(position.get("entry_bar_time") or 0)
        since = [b for b in bars if int(b["t"]) >= entry_t] or [last]
        prev = bars[-exit_n - 1:-1]  # 不含当前根
        if side == "LONG":
            if _bool(p["chandelier"]):
                hh = max(float(b["h"]) for b in since)
                lvl = hh - float(p["trail_atr"]) * atr
                if price < lvl:
                    return {"action": "CLOSE_QUICK_EXIT", "price": round(price, 6), "bar_time": bar_time,
                            "reason": f"吊灯移动止盈(最高{hh:.6g}-{p['trail_atr']}ATR={lvl:.6g})"}
            if _bool(p["don_exit"]) and prev and price < min(float(b["l"]) for b in prev):
                return {"action": "CLOSE_QUICK_EXIT", "price": round(price, 6), "bar_time": bar_time,
                        "reason": f"跌破{exit_n}根低点离场"}
            if _bool(p["ema_exit"]) and price < ef_v:
                return {"action": "CLOSE_QUICK_EXIT", "price": round(price, 6), "bar_time": bar_time,
                        "reason": f"收盘跌破EMA{ef_n}离场"}
        elif side == "SHORT":
            if _bool(p["chandelier"]):
                ll = min(float(b["l"]) for b in since)
                lvl = ll + float(p["trail_atr"]) * atr
                if price > lvl:
                    return {"action": "CLOSE_QUICK_EXIT", "price": round(price, 6), "bar_time": bar_time,
                            "reason": f"吊灯移动止盈(最低{ll:.6g}+{p['trail_atr']}ATR={lvl:.6g})"}
            if _bool(p["don_exit"]) and prev and price > max(float(b["h"]) for b in prev):
                return {"action": "CLOSE_QUICK_EXIT", "price": round(price, 6), "bar_time": bar_time,
                        "reason": f"突破{exit_n}根高点离场"}
            if _bool(p["ema_exit"]) and price > ef_v:
                return {"action": "CLOSE_QUICK_EXIT", "price": round(price, 6), "bar_time": bar_time,
                        "reason": f"收盘突破EMA{ef_n}离场"}
        return None

    # ---------------- 空仓：突破入场 ----------------
    ch = indicators.donchian_high_low(bars, don_n)
    if not ch:
        return None
    hh, ll = ch[-1]
    long_ok = ef_v > es_v and price > ef_v and price > hh
    short_ok = _bool(p["allow_short"]) and ef_v < es_v and price < ef_v and price < ll
    if not (long_ok or short_ok):
        return None
    action = "LONG" if long_ok else "SHORT"

    adx_min = float(p["adx_min"])
    if adx_min > 0 and indicators.wilder_adx(bars, int(p["adx_len"])) < adx_min:
        return None
    vm = float(p["vol_mult"])
    if vm > 0:
        vl = int(p["vol_len"])
        vols = [float(b.get("v") or 0) for b in bars[-vl - 1:-1]]
        if vols and float(last.get("v") or 0) < vm * (sum(vols) / len(vols)):
            return None
    if _bool(p["bb_filter"]):
        n, k = int(p["bb_n"]), float(p["bb_k"])
        mid = indicators.sma(closes, n)
        sd = indicators.stdev(closes, n)
        if len(mid) < 5 or len(sd) < 5:
            return None
        bw_now = 2 * k * sd[-1] / mid[-1] if mid[-1] else 0
        bw_old = 2 * k * sd[-4] / mid[-4] if mid[-4] else 0
        rising = mid[-1] > mid[-2] if action == "LONG" else mid[-1] < mid[-2]
        if not (rising and bw_now > bw_old):
            return None

    direction = 1 if action == "LONG" else -1
    return {
        "action": action,
        "price": round(price, 6),
        "atr": round(atr, 6),
        "stop_loss": round(price - direction * atr * float(p["stop_atr"]), 6),
        # 不返回tp：赢家靠吊灯/反向通道离场(同turtle_breakout的理由)
        "tier": 1,
        "bar_time": bar_time,
        "reason": f"{'突破' if action == 'LONG' else '跌破'}{don_n}根{'高' if action == 'LONG' else '低'}点,趋势EMA{ef_n}/{es_n}同向",
    }
