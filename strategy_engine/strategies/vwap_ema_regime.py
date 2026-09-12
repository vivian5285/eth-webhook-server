#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
VWAP·EMA Regime Switch —— 2026-09-12 应宝贝要求新增。把擂台里两个已验证
最强的组件，用同一套 ADX 状态开关拼在一起，而不是拿一个已经验证会输的
组件（裸奔 EMA 交叉，见 ema_cross_7_30：0% 胜率 −47.5R）硬凑：

  · 趋势腿 = EMA(10)/EMA(30) 金叉死叉，但**加 ADX≥25 才允许开仓这道闸门**
    （这道闸门就是 ema_cross_7_30 和它的差别——同样的均线交叉，裸奔
    0% 胜率，加了这道闸门的 adx_regime_switch 里同款趋势腿是正期望）。
  · 震荡腿 = 擂台当前排名第一的 vwap_mean_reversion 原版逻辑（anchored
    VWAP 偏离 n_std×σ 反向进场，回到 VWAP 附近离场），只在 ADX≤18 时启用。
  · 两者之间(18~25)：状态不明确，持有不动、不开新仓——跟 adx_regime_
    switch 同一套状态机。

跟 adx_regime_switch 的区别（这套是它的"升级版"，不是重复）：
  1. 震荡腿从通用布林带(20,2)+RSI(2) 换成擂台里**实测最强**的 VWAP 偏离
     回归（vwap_mean_reversion 现 +166R/PF 1.33，adx_regime_switch 自己
     的震荡腿从未单独验证过量级）。
  2. 趋势腿/状态判断的时钟从 4h 换成 **2h**（宝贝要求，"捕捉早期启动，
     多空都是"——同样的 EMA 金叉死叉，更快的周期能更早确认趋势刚启动，
     代价是噪声略多，ADX 闸门本身就是用来过滤噪声的）。
  3. 震荡腿的 VWAP/σ 单独走 **15m**（通过 roster 的 "mtf":["15m"] 注入）——
     2h 一天只有 12 根，VWAP 当日累计样本太薄、σ 会抖；15m 一天 96 根
     才是 anchored VWAP 该有的分辨率，跟 vwap_mean_reversion 原版一致。
     ADX/EMA 状态判断继续用 2h（宝贝明确要"2h看趋势"）。
  4. 趋势腿不设固定止盈（2026-09-10 全面审计的教训：固定 ATR 止盈会把
     趋势尾部切掉），只跟 EMA 反向交叉/ATR 止损离场——比 adx_regime_
     switch 自己趋势腿(已同样去掉固定止盈)更进一步验证这道教训。

⚠️ 诚实说明：这是今天(2026-09-12)现拼的新组合，此刻还没有一笔真实
K线之外的实盘/纸面历史——**不适合直接拿它做真实资金测试**，需要先在
擂台里跑出真实样本（哪怕几天）才有评判依据，这也是整个擂台"先纸面
验证、再谈真金"这条铁律的意义所在。
"""
from __future__ import annotations

from typing import Dict, List, Optional

from strategy_engine import indicators
from strategy_engine.strategies.vwap_mean_reversion import _session_vwap

_DAY_MS = 24 * 60 * 60 * 1000

DEFAULT_PARAMS = {
    "adx_len": 14,
    "trend_adx": 25.0,
    "range_adx": 18.0,
    "ema_fast_len": 10,
    "ema_slow_len": 30,
    "trend_atr_stop_mult": 2.0,
    "range_atr_stop_mult": 1.5,
    "n_std": 2.0,
    "exit_band": 0.3,
    "min_session_bars": 12,
    "atr_len": 14,
}


def _ema_cross(ema_fast: List[float], ema_slow: List[float]) -> Optional[str]:
    if len(ema_fast) < 2 or len(ema_slow) < 2:
        return None
    f0, f1 = ema_fast[-2], ema_fast[-1]
    s0, s1 = ema_slow[-2], ema_slow[-1]
    if f0 <= s0 and f1 > s1:
        return "up"
    if f0 >= s0 and f1 < s1:
        return "down"
    return None


def generate_signal(bars_by_tf: Dict[str, List[dict]], params: Optional[dict] = None, position: Optional[dict] = None) -> Optional[dict]:
    p = {**DEFAULT_PARAMS, **(params or {})}
    bars = bars_by_tf.get("base") or []      # 2h：ADX + EMA 状态判断
    bars_v = bars_by_tf.get("15m") or []      # 15m：VWAP 震荡腿专用分辨率
    adx_len, atr_len = int(p["adx_len"]), int(p["atr_len"])
    ema_fast_len, ema_slow_len = int(p["ema_fast_len"]), int(p["ema_slow_len"])
    need = max(adx_len * 2 + 2, ema_slow_len + 2, atr_len) + 2
    if len(bars) < need:
        return None

    cs = indicators.closes(bars)
    adx_now = indicators.wilder_adx(bars, adx_len)
    ema_f = indicators.ema(cs, ema_fast_len)
    ema_s = indicators.ema(cs, ema_slow_len)
    atr = indicators.wilder_atr(bars, atr_len)
    if not ema_f or not ema_s or atr <= 0:
        return None
    trend_adx, range_adx = float(p["trend_adx"]), float(p["range_adx"])

    # VWAP 震荡腿数据（15m，软依赖——拉不到就只跑趋势腿）
    vwap_now = dev_std = None
    price_v = bar_time_v = None
    min_bars = int(p["min_session_bars"])
    if bars_v:
        last_v = bars_v[-1]
        price_v, bar_time_v = float(last_v["c"]), int(last_v["t"])
        cur_day = bar_time_v // _DAY_MS
        session_bars = [b for b in bars_v if int(b["t"]) // _DAY_MS == cur_day]
        if len(session_bars) >= min_bars:
            vwap_now, dev_std = _session_vwap(session_bars)

    last = bars[-1]
    price = float(price_v if price_v is not None else last["c"])
    bar_time = int(bar_time_v if bar_time_v is not None else last["t"])
    n_std, exit_band = float(p["n_std"]), float(p["exit_band"])

    # ── 持仓：按离场那一刻的最新状态重新判断走哪条离场规则 ──────────
    if position:
        side = str(position.get("side") or "").upper()
        if adx_now >= trend_adx:
            cross = _ema_cross(ema_f, ema_s)
            if side == "LONG" and cross == "down":
                return {"action": "CLOSE_QUICK_EXIT", "price": round(price, 6),
                        "reason": f"趋势市(ADX={adx_now:.1f}·2h) EMA死叉离场", "bar_time": bar_time}
            if side == "SHORT" and cross == "up":
                return {"action": "CLOSE_QUICK_EXIT", "price": round(price, 6),
                        "reason": f"趋势市(ADX={adx_now:.1f}·2h) EMA金叉离场", "bar_time": bar_time}
            return None
        if adx_now <= range_adx and vwap_now and dev_std and dev_std > 0:
            if abs(price - vwap_now) <= exit_band * dev_std:
                return {"action": "CLOSE_QUICK_EXIT", "price": round(price, 6),
                        "reason": f"震荡市(ADX={adx_now:.1f}·2h) 回归VWAP({vwap_now:.6f}·15m)±{exit_band}σ完成",
                        "bar_time": bar_time}
            return None
        return None  # 状态不明确 / VWAP 数据不足，持有不动

    # ── 空仓：趋势腿(2h EMA金叉死叉 + ADX闸门) ─────────────────────
    if adx_now >= trend_adx:
        cross = _ema_cross(ema_f, ema_s)
        if cross not in ("up", "down"):
            return None
        d = 1 if cross == "up" else -1
        return {
            "action": "LONG" if d == 1 else "SHORT",
            "price": round(price, 6), "atr": round(atr, 6),
            "stop_loss": round(price - d * atr * float(p["trend_atr_stop_mult"]), 6),
            "tier": 1, "bar_time": bar_time,
            "reason": f"趋势腿：2h ADX={adx_now:.1f}≥{trend_adx:.0f} EMA{ema_fast_len}/{ema_slow_len}{cross}叉(无固定止盈)",
        }

    # ── 空仓：震荡腿(15m VWAP偏离回归 + ADX闸门) ───────────────────
    if adx_now <= range_adx and vwap_now and dev_std and dev_std > 0:
        upper, lower = vwap_now + n_std * dev_std, vwap_now - n_std * dev_std
        if price >= upper:
            action, d = "SHORT", -1
        elif price <= lower:
            action, d = "LONG", 1
        else:
            return None
        return {
            "action": action,
            "price": round(price, 6), "atr": round(atr, 6),
            "stop_loss": round(price - d * atr * float(p["range_atr_stop_mult"]), 6),
            "tp1": round(vwap_now, 6), "tp2": round(vwap_now, 6), "tp3": round(vwap_now, 6),
            "tier": 1, "bar_time": bar_time,
            "reason": (f"震荡腿：2h ADX={adx_now:.1f}≤{range_adx:.0f}，15m close偏离VWAP"
                       f"({vwap_now:.6f}) {(price - vwap_now) / dev_std:+.2f}σ(阈值{n_std}σ)"),
        }

    return None  # 状态不明确，不开新仓
