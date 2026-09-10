#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
TTM Squeeze / Squeeze Momentum —— 2026-09-10 应宝贝"加著名指标组合"要求
新增。John Carter《Mastering the Trade》里的 TTM Squeeze，LazyBear 在
TradingView 上做成 "Squeeze Momentum Indicator"，是 TV 上最流行的动量
组合之一，规则完全公开可复现。

核心思想：布林带(20,2)缩进 Keltner 通道(20,1.5×ATR)内部 = 市场"挤压"
(低波动蓄势)；挤压释放的那一刻，按动量柱的方向顺势进场。

跟擂台已有的关键区别：
  · bollinger_squeeze：只看布林带自己的带宽收缩到 N 日最低，没有 Keltner
    这条参照，也没有动量柱定方向(靠突破方向)
  · keltner_channel：只用 Keltner 通道做突破/回归，不看布林带
  · 本战法：**布林带 vs Keltner 的相对关系**判挤压，**动量柱**(LazyBear
    的 linreg 版)定方向——这个"合体"信号才是 TTM Squeeze 本体

规则：
  · 挤压中(squeeze on)：BB_up < KC_up 且 BB_lo > KC_lo
  · 触发：上一根还在挤压、这一根挤压释放(BB 重新扩出 KC 之外)
  · 动量柱 mom = linreg(close - avg(最高最低中点, SMA收盘), mom_len) 在最后
    一点的拟合值；mom > 0 且比上一根大 → 做多；mom < 0 且比上一根小 → 做空
  · 止损：ATR × atr_stop_mult；离场：动量柱穿越零轴反向，或 ATR 止损。
    不设固定止盈(挤压释放后是趋势段，让利润跑)。

周期 4h（跟 bollinger_squeeze / keltner_channel 同周期，方便三种通道
构造方式直接对照）。
"""
from __future__ import annotations

from typing import Dict, List, Optional

from strategy_engine import indicators

DEFAULT_PARAMS = {
    "bb_len": 20,
    "bb_mult": 2.0,
    "kc_len": 20,
    "kc_mult": 1.5,
    "mom_len": 20,
    "atr_len": 14,
    "atr_stop_mult": 2.0,
}


def _linreg_endpoint(w: List[float]) -> float:
    """对 w 做最小二乘线性回归，返回拟合直线在最后一点的值
    （= LazyBear Squeeze Momentum 里 ta.linreg(src, len, 0)）。"""
    n = len(w)
    if n < 2:
        return 0.0
    mx = (n - 1) / 2.0
    my = sum(w) / n
    sxx = sum((x - mx) ** 2 for x in range(n))
    if sxx <= 0:
        return 0.0
    sxy = sum((i - mx) * (w[i] - my) for i in range(n))
    b = sxy / sxx
    return (my - b * mx) + b * (n - 1)


def generate_signal(bars_by_tf: Dict[str, List[dict]], params: Optional[dict] = None, position: Optional[dict] = None) -> Optional[dict]:
    p = {**DEFAULT_PARAMS, **(params or {})}
    bars = bars_by_tf.get("base") or []
    bb_len, kc_len, mom_len = int(p["bb_len"]), int(p["kc_len"]), int(p["mom_len"])
    atr_len = int(p["atr_len"])
    need = max(bb_len, kc_len, mom_len, atr_len) + mom_len + 6
    if len(bars) < need:
        return None

    cs = indicators.closes(bars)
    highs = [float(b["h"]) for b in bars]
    lows = [float(b["l"]) for b in bars]

    mid = indicators.sma(cs, bb_len)          # 对齐到 bars[bb_len-1:]
    sd = indicators.stdev(cs, bb_len)
    ma = indicators.sma(cs, kc_len)
    atr_ser = indicators.atr_series(bars, atr_len)  # 对齐到 bars[atr_len:]
    if len(mid) < 2 or len(sd) < 2 or len(ma) < 2 or len(atr_ser) < 2:
        return None

    def _sq(off: int) -> bool:
        # off=0 最后一根, off=1 上一根
        m, s = mid[-1 - off], sd[-1 - off]
        a, t = ma[-1 - off], atr_ser[-1 - off]
        bb_up, bb_lo = m + float(p["bb_mult"]) * s, m - float(p["bb_mult"]) * s
        kc_up, kc_lo = a + float(p["kc_mult"]) * t, a - float(p["kc_mult"]) * t
        return bb_up < kc_up and bb_lo > kc_lo

    sm = indicators.sma(cs, mom_len)  # 对齐到 bars[mom_len-1:]

    def _mom(off: int) -> float:
        end = len(bars) - off
        w_bars = bars[end - mom_len:end]
        hh = max(float(b["h"]) for b in w_bars)
        ll = min(float(b["l"]) for b in w_bars)
        basis = ((hh + ll) / 2.0 + sm[-1 - off]) / 2.0
        dev = [float(bars[i]["c"]) - basis for i in range(end - mom_len, end)]
        return _linreg_endpoint(dev)

    sq_now, sq_prev = _sq(0), _sq(1)
    mom_now, mom_prev = _mom(0), _mom(1)

    last = bars[-1]
    price = float(last["c"])
    bar_time = int(last["t"])
    atr = float(atr_ser[-1])
    if atr <= 0:
        return None

    if position:
        side = str(position.get("side") or "").upper()
        if side == "LONG" and mom_now <= 0:
            return {"action": "CLOSE_QUICK_EXIT", "price": round(price, 6),
                    "reason": f"动量柱转负({mom_now:+.4f})", "bar_time": bar_time}
        if side == "SHORT" and mom_now >= 0:
            return {"action": "CLOSE_QUICK_EXIT", "price": round(price, 6),
                    "reason": f"动量柱转正({mom_now:+.4f})", "bar_time": bar_time}
        return None

    if not (sq_prev and not sq_now):  # 需要"上一根挤压、这一根释放"
        return None
    d = 0
    if mom_now > 0 and mom_now > mom_prev:
        d = 1
    elif mom_now < 0 and mom_now < mom_prev:
        d = -1
    if d == 0:
        return None
    return {
        "action": "LONG" if d == 1 else "SHORT",
        "price": round(price, 6), "atr": round(atr, 6),
        "stop_loss": round(price - d * atr * float(p["atr_stop_mult"]), 6),
        "tier": 1, "bar_time": bar_time,
        "reason": f"挤压释放 + 动量柱{mom_now:+.4f}({'升' if d == 1 else '降'})",
    }
