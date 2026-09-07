#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
4h 裸K突破 + 放量为主 + 日线参考方向 + EMA7/30 / MACD柱 / CCI 轻确认
——2026-09-07 宝贝设计，2026-09-07 按宝贝反馈调整：**以 4h 为主、日线
只当参考方向、裸K + 放量做更即时的进出场判断**。全部公开经典指标，无黑箱。

设计要点（宝贝原话拆解）：
  - "重点还是做 4h 为主"        → 入场/离场触发全部看 4h
  - "日线作为一个参考方向即可"  → 日线 EMA7/30 只用来定仓位档位(同向=tier2、
                                  否则 tier1)，**绝不 gate/否决交易**
  - "裸K + 放量更加即时判断"    → 主触发 = 4h 收盘突破近 N 根高/低点 + 当根
                                  放量 + 收盘价落在振幅强侧(强阳/强阴线)
  - EMA7/30 / MACD柱 / CCI      → 降级成"轻确认"(不逆 4h 慢均线、MACD柱不
                                  在反方向、CCI 动量同向)，不再是主判据

快指标为什么是 CCI（宝贝让我在 RSI/CCI/SKDJ 里挑）：本擂台 RSI 被
connors_rsi2/mtf_ema_pullback/bollinger_rsi_contrarian/adx_regime_switch
反复用了、随机K被 TV 复刻那几套用了，CCI 一套没用过——横向对比差异化
最大；且 CCI 无界、对短冲量敏感，这里只当"动量方向"的轻确认。

── 入场（做多，做空对称）──
  4h 收盘 > 近 brk_lookback 根(不含当根)最高高点   ← 裸K突破，主触发
  且 当根成交量 ≥ vol_mult × 量能加权均量          ← 放量确认
  且 收盘价在当根振幅的 candle_strength 以上位置    ← 强阳线，不是假突破/十字星
  且 4h 收盘价 > 4h EMA30                          ← 轻确认：不逆 4h 趋势
  且 MACD柱 ≥ 0 且 CCI > 0                         ← 轻确认：动量不在反方向

── 离场（做多，裸K优先）──
  收盘跌破"入场那根 4h K线"的低点                   ← 最即时的裸K反转
  或 放量强阴线且收盘低于前一根                     ← 裸K反转
  或 收盘跌破近 exit_struct_lookback 根结构低点     ← 结构破位(短回看，跟得紧)
  或 4h EMA7 下穿 EMA30                            ← 慢确认兜底
  ATR 止损 + 模拟强平由通用 runner 处理。

跟本仓库其它战法的关键区别：
  - ema_cross_7_30 / macd_histogram 是单周期、纯指标交叉/变号；这套指标
    全是轻确认，主判据是 4h 裸K突破 + 放量。
  - mtf_ema_pullback 是"高周期定方向、低周期等回踩"，赌回调后延续；这套
    是"4h 放量突破近端结构"，赌突破延续，进场点在突破处不在回调低点，
    且日线不 gate 只定档位——两种多周期用法不同。
  - breakout_retest / turtle_breakout 用 Donchian 通道、不看单根K线形态和
    成交量；这套要求突破那根本身是放量强实体K线，更"即时"、更挑质量。

周期选择理由：base=4h，mtf=["1d"] 仅供日线参考方向。

数据要求：base 至少 max(ema_slow, macd_slow+macd_signal, vol_len,
cci_len, atr_len, brk_lookback+2)+4 根；1d 不足时日线参考自动降级为中性
(不影响交易，只是 tier 一律 1)。
"""
from __future__ import annotations

from typing import Dict, List, Optional

from strategy_engine import indicators

DEFAULT_PARAMS = {
    "ema_fast": 7,
    "ema_slow": 30,
    "macd_fast": 12,
    "macd_slow": 26,
    "macd_signal": 9,
    "brk_lookback": 10,
    "exit_struct_lookback": 5,
    "vol_len": 20,
    "vol_mult": 1.3,
    "candle_strength": 0.6,
    "cci_len": 20,
    "atr_len": 14,
    "atr_stop_mult": 2.5,
}


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def generate_signal(bars_by_tf: Dict[str, List[dict]], params: Optional[dict] = None, position: Optional[dict] = None) -> Optional[dict]:
    p = {**DEFAULT_PARAMS, **(params or {})}
    base = bars_by_tf.get("base") or []
    daily = bars_by_tf.get("1d") or []

    ema_fast, ema_slow = int(p["ema_fast"]), int(p["ema_slow"])
    mf, ms, msig = int(p["macd_fast"]), int(p["macd_slow"]), int(p["macd_signal"])
    vol_len = int(p["vol_len"])
    cci_len = int(p["cci_len"])
    atr_len = int(p["atr_len"])
    brk_lb = int(p["brk_lookback"])
    exit_lb = int(p["exit_struct_lookback"])
    cstr = float(p["candle_strength"])

    need_base = max(ema_slow, ms + msig, vol_len, cci_len, atr_len, brk_lb + 2) + 4
    if len(base) < need_base:
        return None

    last = base[-1]
    price = _f(last["c"])
    hi, lo = _f(last["h"]), _f(last["l"])
    bar_time = int(last["t"])
    rng = hi - lo
    close_pos = (price - lo) / rng if rng > 0 else 0.5

    # ── 日线软参考（只定 tier，不 gate）──
    daily_bias = 0
    if len(daily) >= ema_slow + 2:
        dcs = indicators.closes(daily)
        df, ds = indicators.ema(dcs, ema_fast), indicators.ema(dcs, ema_slow)
        if df and ds:
            daily_bias = 1 if df[-1] > ds[-1] else (-1 if df[-1] < ds[-1] else 0)

    # ── 4h 轻确认指标 ──
    cs = indicators.closes(base)
    ef, es = indicators.ema(cs, ema_fast), indicators.ema(cs, ema_slow)
    if len(ef) < 2 or len(es) < 2:
        return None
    ema4_cross_dn = ef[-2] >= es[-2] and ef[-1] < es[-1]
    ema4_cross_up = ef[-2] <= es[-2] and ef[-1] > es[-1]
    es_now = es[-1]

    _, _, hist = indicators.macd(cs, mf, ms, msig)
    hist_now = hist[-1] if hist else 0.0
    cci_s = indicators.cci(base, cci_len)
    cci_now = cci_s[-1] if cci_s else 0.0

    volb = indicators.vwma_of_volume(base, vol_len)
    if not volb or volb[-1] <= 0:
        return None
    cur_vol = _f(last["v"])
    vol_ratio = cur_vol / volb[-1]
    vol_spike = vol_ratio >= float(p["vol_mult"])

    # 近 brk_lb 根高/低（不含当根）
    win = base[-1 - brk_lb:-1]
    hh = max(_f(b["h"]) for b in win)
    ll = min(_f(b["l"]) for b in win)

    # ── 持仓中：离场（裸K优先）──
    if position:
        side = str(position.get("side") or "").upper()
        ebt = int(position.get("entry_bar_time") or 0)
        entry_bar = next((b for b in reversed(base) if int(b["t"]) == ebt), None)
        prev_close = cs[-2]
        ex_win = base[-1 - exit_lb:-1]

        if side == "LONG":
            if entry_bar is not None and price < _f(entry_bar["l"]):
                return {"action": "CLOSE_QUICK_EXIT", "price": round(price, 6),
                        "reason": f"收盘跌破入场4h K线低点({_f(entry_bar['l']):.6f})", "bar_time": bar_time}
            if close_pos <= (1 - cstr) and vol_spike and price < prev_close:
                return {"action": "CLOSE_QUICK_EXIT", "price": round(price, 6),
                        "reason": f"放量强阴线({vol_ratio:.1f}x)收盘走低，裸K反转", "bar_time": bar_time}
            if ex_win and price < min(_f(b["l"]) for b in ex_win):
                return {"action": "CLOSE_QUICK_EXIT", "price": round(price, 6),
                        "reason": f"跌破近{exit_lb}根4h结构低点", "bar_time": bar_time}
            if ema4_cross_dn:
                return {"action": "CLOSE_QUICK_EXIT", "price": round(price, 6),
                        "reason": "4h EMA7下穿EMA30", "bar_time": bar_time}
        elif side == "SHORT":
            if entry_bar is not None and price > _f(entry_bar["h"]):
                return {"action": "CLOSE_QUICK_EXIT", "price": round(price, 6),
                        "reason": f"收盘涨破入场4h K线高点({_f(entry_bar['h']):.6f})", "bar_time": bar_time}
            if close_pos >= cstr and vol_spike and price > prev_close:
                return {"action": "CLOSE_QUICK_EXIT", "price": round(price, 6),
                        "reason": f"放量强阳线({vol_ratio:.1f}x)收盘走高，裸K反转", "bar_time": bar_time}
            if ex_win and price > max(_f(b["h"]) for b in ex_win):
                return {"action": "CLOSE_QUICK_EXIT", "price": round(price, 6),
                        "reason": f"涨破近{exit_lb}根4h结构高点", "bar_time": bar_time}
            if ema4_cross_up:
                return {"action": "CLOSE_QUICK_EXIT", "price": round(price, 6),
                        "reason": "4h EMA7上穿EMA30", "bar_time": bar_time}
        return None

    # ── 空仓：入场（4h 裸K突破 + 放量为主，指标轻确认）──
    long_ok = (
        price > hh and vol_spike and close_pos >= cstr
        and price > es_now and hist_now >= 0 and cci_now > 0
    )
    short_ok = (
        price < ll and vol_spike and close_pos <= (1 - cstr)
        and price < es_now and hist_now <= 0 and cci_now < 0
    )
    if long_ok:
        action, d = "LONG", 1
    elif short_ok:
        action, d = "SHORT", -1
    else:
        return None

    atr = indicators.wilder_atr(base, atr_len)
    if atr <= 0:
        return None

    tier = 2 if (d > 0 and daily_bias > 0) or (d < 0 and daily_bias < 0) else 1
    dref = "同向(tier2)" if tier == 2 else ("参考:多" if daily_bias > 0 else "参考:空" if daily_bias < 0 else "参考:中性")

    return {
        "action": action,
        "price": round(price, 6),
        "atr": round(atr, 6),
        "stop_loss": round(price - d * atr * float(p["atr_stop_mult"]), 6),
        "tier": tier,
        "bar_time": bar_time,
        "reason": (f"4h裸K突破近{brk_lb}根{'高' if d > 0 else '低'}点 + 放量{vol_ratio:.1f}x + "
                   f"{'强阳' if d > 0 else '强阴'}线收盘(位置{close_pos:.2f}) | 日线{dref}"),
    }
