#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
RSI均值回归——2026-09-20新增，宝贝问"这几个周期的RSI均值回归要不要一起
并排测试"(在CCI均值回归4h/6h/8h/12h之后接着问)。Welles Wilder 1978年
公开发表RSI，70/30是他原始定义的超买超卖边界——比CCI±100知名度更高、
更"教科书"的均值回归读法，规则同样确定公开可查，不是黑箱。

擂台里已经有RSI相关的战法，但没有一个是"这个周期区间的纯RSI均值回归"：
  - connors_rsi2：RSI(2)极短周期+SMA200顺势过滤，1d，是"短线情绪极端
    +长线趋势不变"这个更复杂的组合玩法，不是单纯的RSI(14)超买超卖。
  - bollinger_rsi_contrarian：RSI只是布林带信号的辅助确认条件之一，1d。
  - kdj_cross：KDJ的K/D/J是RSI的近亲(都是随机振荡器家族)但公式不同，
    而且只在4h一个周期跑，宝贝记得"周期可能不够"是对的——没有6h/8h/12h。
  - mtf_ema_macd_cci的CCI/本仓库cci_mean_reversion：都是CCI不是RSI。
真正"RSI(14)经典70/30超买超卖、4h~12h波段周期"这个组合，之前确实是空白。

规则(跟kdj_cross.py/cci_mean_reversion.py同一副"只在极值区反向操作+
回归中位离场"骨架，kdj_cross那套39笔0次碰到止损、64.1%胜率验证过这个
骨架本身是干净的，这里换成RSI+同样4个周期跟CCI版并排对照)：
  - RSI(period=14，Wilder原始默认)
  - RSI从超买区(≥70)回落穿破70 → 做空(赌见顶回落)
  - RSI从超卖区(≤30)回升穿破30 → 做多(赌止跌反弹)
  - 离场：RSI回到中性区(exit_level，默认50)，或再次反向触及另一端极值
    (反转比预期更快更猛，提前认错离场)，或ATR止损兜底
  - tier：RSI超过80/低于20算"极端"记tier2，否则tier1
  - 默认带adx_gate=25(RSI/CCI两个均值回归战法统一口径)——只在非趋势
    (ADX<25)状态下才做，减少被真趋势打穿的概率，不是可选项。
"""
from __future__ import annotations

from typing import Dict, List, Optional

from strategy_engine import indicators

DEFAULT_PARAMS = {
    "period": 14,
    "ob": 70.0,
    "os": 30.0,
    "ob_tier2": 80.0,
    "os_tier2": 20.0,
    "exit_level": 50.0,
    "atr_len": 14,
    "atr_stop_mult": 2.0,
    "adx_gate": 25.0,  # ADX<此值才做(震荡/非趋势状态)，0=关闭这道闸门
}


def generate_signal(bars_by_tf: Dict[str, List[dict]], params: Optional[dict] = None, position: Optional[dict] = None) -> Optional[dict]:
    bars = bars_by_tf.get("base") or []
    p = {**DEFAULT_PARAMS, **(params or {})}
    period = int(p["period"])
    atr_len = int(p["atr_len"])
    if len(bars) < period + atr_len + 5:
        return None

    closes = indicators.closes(bars)
    rsi_series = indicators.rsi(closes, period)
    if len(rsi_series) < 2:
        return None
    rsi0, rsi1 = rsi_series[-2], rsi_series[-1]

    last = bars[-1]
    price = float(last["c"])
    bar_time = int(last["t"])
    ob = float(p["ob"])
    os_ = float(p["os"])
    exit_level = float(p["exit_level"])

    cross_dn_from_ob = rsi0 >= ob and rsi1 < ob
    cross_up_from_os = rsi0 <= os_ and rsi1 > os_

    if position:
        side = str(position.get("side") or "").upper()
        if side == "LONG" and (rsi1 >= exit_level or cross_dn_from_ob):
            return {
                "action": "CLOSE_QUICK_EXIT", "price": round(price, 6),
                "reason": f"RSI回归中性({rsi1:.1f})或再次转空", "bar_time": bar_time,
            }
        if side == "SHORT" and (rsi1 <= exit_level or cross_up_from_os):
            return {
                "action": "CLOSE_QUICK_EXIT", "price": round(price, 6),
                "reason": f"RSI回归中性({rsi1:.1f})或再次转多", "bar_time": bar_time,
            }
        return None

    d = 0
    if cross_up_from_os:
        d = 1
    elif cross_dn_from_ob:
        d = -1
    if d == 0:
        return None

    adx_gate = float(p.get("adx_gate") or 0)
    if adx_gate > 0:
        adx = indicators.wilder_adx(bars, atr_len)
        if adx >= adx_gate:
            return None  # 趋势状态太强，均值回归先不做，避免逆势被打穿

    atr = indicators.wilder_atr(bars, atr_len)
    if atr <= 0:
        return None

    ob_tier2 = float(p["ob_tier2"])
    os_tier2 = float(p["os_tier2"])
    extreme = (rsi0 >= ob_tier2) if d == -1 else (rsi0 <= os_tier2)
    tier = 2 if extreme else 1

    return {
        "action": "LONG" if d == 1 else "SHORT",
        "price": round(price, 6), "atr": round(atr, 6),
        "stop_loss": round(price - d * atr * float(p["atr_stop_mult"]), 6),
        "tier": tier, "bar_time": bar_time,
        "reason": f"RSI({period}) {'超卖回升' if d == 1 else '超买回落'}(RSI={rsi1:.1f})",
    }
