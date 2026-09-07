#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
多周期 EMA7/30 + MACD柱(实心/空心) + 量能 + CCI 冲量确认——2026-09-07
应宝贝要求新增。宝贝的设计意图：日线定大方向、4h 定进出场、再叠一个
更快的指标精确择时。全部用公开经典指标，无黑箱。

为什么快指标选 CCI（宝贝让我在 RSI / CCI / SKDJ 里挑）：
  - RSI 在本擂台已经被 connors_rsi2 / mtf_ema_pullback / bollinger_rsi_
    contrarian / adx_regime_switch 反复用了，随机指标K被 TV 复刻那几套
    用了；CCI 一套都没用过——对"擂台是用来横向对比不同信号源"这个
    目的，选没人用过的 CCI 差异化最大。
  - CCI 无上下界、对短周期冲量敏感，±100 是 Lambert 原始定义里的常规
    波动边界，"CCI 上穿 +100"正好当"这一根 4h 冲量确认"的触发事件，
    叠在更慢的 EMA 排列 + MACD 柱确认之上，扮演"现在这一下够不够猛"
    的角色，不是又一个趋势过滤器（那样就跟 EMA/MACD 冗余了）。
  - CCI 跌回 0 轴以下（多头持仓时）还能兼当"动能耗尽"的早离场预警。

结构（base=4h，mtf 额外拉 1d）：
  ── 日线（大方向，硬门槛）──
    日线 EMA7 vs EMA30：EMA7>EMA30 只做多，EMA7<EMA30 只做空，纠缠不做。
  ── 4h（进出场）──
    入场做多（日线为多头时）：4h 也是 EMA7>EMA30 多头排列 + MACD 柱在
      零轴上方"实心"(|柱|在放大=动能加速) + 当根成交量 ≥ vol_mult ×
      量能加权均量 + CCI > +100；且这一根是"刚触发"(4h EMA 金叉 或 CCI
      刚上穿 +100)。做空完全对称。
    离场做多：日线大方向翻空 / 4h EMA 死叉 / MACD 零轴上方转"空心"
      (|柱|在缩小) 且 CCI 跌破 0（动能衰竭）。ATR 止损兜底 + 模拟强平
      由通用 runner 处理。

跟本仓库其它战法的关键区别：
  - ema_cross_7_30 是**单周期、纯 EMA 交叉**；这套 EMA 交叉只是 4h 的
    一个子条件，还要日线大方向 + MACD 柱形态 + 放量 + CCI 冲量四重叠加。
  - mtf_ema_pullback 是"高周期定方向、低周期等回踩 + RSI 抬头"，赌的是
    回调后趋势延续，进场点靠近回调低点；这套不等回踩，赌的是"多重确认
    同时点亮的冲量启动"，进场点在动能加速处，是两种不同的多周期择时
    哲学。
  - macd_histogram 是单周期、只看 MACD 柱变号；这套用的是 MACD 柱的
    "实心/空心"(放大/缩小)形态，且只当多因子里的一环。

周期选择理由：base=4h（跟 ema_cross_7_30 / mtf_ema_pullback 的低周期
腿同量级，方便横向比），mtf=["1d"] 提供日线大方向。

数据要求：base 至少 max(ema_slow, macd_slow+macd_signal, vol_len,
cci_len, atr_len)+6 根；1d 至少 ema_slow + macd_slow + macd_signal + 4 根。
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
    "vol_len": 20,
    "vol_mult": 1.15,
    "cci_len": 20,
    "cci_entry": 100.0,
    "cci_exit": 0.0,
    "atr_len": 14,
    "atr_stop_mult": 2.5,
}


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def _ema_dir(cs, fast_n, slow_n):
    f = indicators.ema(cs, fast_n)
    s = indicators.ema(cs, slow_n)
    if len(f) < 2 or len(s) < 2:
        return 0, False, False
    up = f[-1] > s[-1]
    cross_up = f[-2] <= s[-2] and f[-1] > s[-1]
    cross_dn = f[-2] >= s[-2] and f[-1] < s[-1]
    direction = 1 if f[-1] > s[-1] else (-1 if f[-1] < s[-1] else 0)
    return direction, cross_up, cross_dn


def generate_signal(bars_by_tf: Dict[str, List[dict]], params: Optional[dict] = None, position: Optional[dict] = None) -> Optional[dict]:
    p = {**DEFAULT_PARAMS, **(params or {})}
    base = bars_by_tf.get("base") or []
    daily = bars_by_tf.get("1d") or []

    ema_fast, ema_slow = int(p["ema_fast"]), int(p["ema_slow"])
    mf, ms, msig = int(p["macd_fast"]), int(p["macd_slow"]), int(p["macd_signal"])
    vol_len = int(p["vol_len"])
    cci_len = int(p["cci_len"])
    atr_len = int(p["atr_len"])

    need_base = max(ema_slow, ms + msig, vol_len, cci_len, atr_len) + 6
    need_daily = ema_slow + ms + msig + 4
    if len(base) < need_base or len(daily) < need_daily:
        return None

    last = base[-1]
    price = _f(last["c"])
    bar_time = int(last["t"])

    # ── 日线大方向 ──
    dcs = indicators.closes(daily)
    daily_dir, _, _ = _ema_dir(dcs, ema_fast, ema_slow)

    # ── 4h 指标 ──
    cs = indicators.closes(base)
    dir4, cross_up4, cross_dn4 = _ema_dir(cs, ema_fast, ema_slow)
    _, _, hist = indicators.macd(cs, mf, ms, msig)
    if len(hist) < 2:
        return None
    hist_now, hist_prev = hist[-1], hist[-2]
    macd_solid_bull = hist_now > 0 and abs(hist_now) > abs(hist_prev)
    macd_solid_bear = hist_now < 0 and abs(hist_now) > abs(hist_prev)
    macd_hollow_bull = hist_now > 0 and abs(hist_now) < abs(hist_prev)
    macd_hollow_bear = hist_now < 0 and abs(hist_now) < abs(hist_prev)

    volb = indicators.vwma_of_volume(base, vol_len)
    if not volb:
        return None
    cur_vol = _f(last["v"])
    vol_ok = volb[-1] > 0 and cur_vol >= float(p["vol_mult"]) * volb[-1]

    cci_s = indicators.cci(base, cci_len)
    if len(cci_s) < 2:
        return None
    cci_now, cci_prev = cci_s[-1], cci_s[-2]
    cci_entry = float(p["cci_entry"])
    cci_exit = float(p["cci_exit"])

    # ── 持仓中：离场 ──
    if position:
        side = str(position.get("side") or "").upper()
        if side == "LONG":
            if daily_dir < 0:
                return {"action": "CLOSE_QUICK_EXIT", "price": round(price, 6),
                        "reason": "日线EMA7/30翻空，大方向丢失", "bar_time": bar_time}
            if cross_dn4:
                return {"action": "CLOSE_QUICK_EXIT", "price": round(price, 6),
                        "reason": "4h EMA7下穿EMA30", "bar_time": bar_time}
            if macd_hollow_bull and cci_now < cci_exit:
                return {"action": "CLOSE_QUICK_EXIT", "price": round(price, 6),
                        "reason": f"MACD柱转空心+CCI({cci_now:.0f})跌破0，多头动能衰竭", "bar_time": bar_time}
        elif side == "SHORT":
            if daily_dir > 0:
                return {"action": "CLOSE_QUICK_EXIT", "price": round(price, 6),
                        "reason": "日线EMA7/30翻多，大方向丢失", "bar_time": bar_time}
            if cross_up4:
                return {"action": "CLOSE_QUICK_EXIT", "price": round(price, 6),
                        "reason": "4h EMA7上穿EMA30", "bar_time": bar_time}
            if macd_hollow_bear and cci_now > -cci_exit:
                return {"action": "CLOSE_QUICK_EXIT", "price": round(price, 6),
                        "reason": f"MACD柱转空心+CCI({cci_now:.0f})涨破0，空头动能衰竭", "bar_time": bar_time}
        return None

    # ── 空仓：入场 ──
    if daily_dir == 0:
        return None

    action, d = None, 0
    if daily_dir > 0:
        aligned = dir4 > 0 and macd_solid_bull and vol_ok and cci_now > cci_entry
        just = cross_up4 or (cci_prev <= cci_entry < cci_now)
        if aligned and just:
            action, d = "LONG", 1
    else:
        aligned = dir4 < 0 and macd_solid_bear and vol_ok and cci_now < -cci_entry
        just = cross_dn4 or (cci_prev >= -cci_entry > cci_now)
        if aligned and just:
            action, d = "SHORT", -1

    if action is None:
        return None

    atr = indicators.wilder_atr(base, atr_len)
    if atr <= 0:
        return None

    return {
        "action": action,
        "price": round(price, 6),
        "atr": round(atr, 6),
        "stop_loss": round(price - d * atr * float(p["atr_stop_mult"]), 6),
        "tier": 1,
        "bar_time": bar_time,
        "reason": (f"日线{'多' if daily_dir > 0 else '空'}头大方向 + 4h EMA7/30{'多' if d > 0 else '空'}排 + "
                   f"MACD零轴{'上' if d > 0 else '下'}方实心 + 放量{cur_vol / volb[-1]:.1f}x + CCI={cci_now:.0f}冲量"),
    }
