#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
可复用指标库。ATR/ADX 公式照搬 market_engine.py 的 Wilder 算法（口径跟系统
里已有的 ATR/ADX 概念保持一致），只是把入参从原始交易所数组改成本模块
klines.py 用的 dict 形状（{"t","o","h","l","c","v"}）。均线/RSI 等按用户
实际 Pine 脚本需要的指标陆续补充。
"""
from __future__ import annotations

from typing import List, Sequence


def _f(x, default=0.0) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return float(default)


def closes(bars: Sequence[dict]) -> List[float]:
    return [_f(b.get("c")) for b in bars or []]


def sma(values: Sequence[float], period: int) -> List[float]:
    """简单移动平均，返回从第 period-1 个点开始的序列（与输入等长的前 period-1 个位置省略）。"""
    out = []
    if period <= 0 or len(values) < period:
        return out
    window_sum = sum(values[:period])
    out.append(window_sum / period)
    for i in range(period, len(values)):
        window_sum += values[i] - values[i - period]
        out.append(window_sum / period)
    return out


def ema(values: Sequence[float], period: int) -> List[float]:
    """指数移动平均，种子用前 period 根的 SMA（跟大多数图表软件默认口径一致）。"""
    if period <= 0 or len(values) < period:
        return []
    k = 2.0 / (period + 1.0)
    seed = sum(values[:period]) / period
    out = [seed]
    for v in values[period:]:
        out.append(v * k + out[-1] * (1 - k))
    return out


def rsi(values: Sequence[float], period: int = 14) -> List[float]:
    """Wilder RSI。"""
    if period <= 0 or len(values) < period + 1:
        return []
    gains, losses = [], []
    for i in range(1, len(values)):
        d = values[i] - values[i - 1]
        gains.append(max(d, 0.0))
        losses.append(max(-d, 0.0))
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    out = []

    def _rsi_val(ag, al):
        if al <= 0:
            return 100.0
        rs = ag / al
        return 100.0 - (100.0 / (1.0 + rs))

    out.append(_rsi_val(avg_gain, avg_loss))
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        out.append(_rsi_val(avg_gain, avg_loss))
    return out


def _true_ranges(bars: Sequence[dict]) -> List[float]:
    trs = []
    for i in range(1, len(bars)):
        h = _f(bars[i]["h"])
        l = _f(bars[i]["l"])
        pc = _f(bars[i - 1]["c"])
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    return trs


def atr_series(bars: Sequence[dict], period: int = 14) -> List[float]:
    """逐根闭合后的 Wilder ATR 序列，跟 market_engine.py:atr_series 同算法。"""
    if not bars or len(bars) < period + 1:
        return []
    trs = _true_ranges(bars)
    if len(trs) < period:
        return []
    atr = sum(trs[:period]) / period
    series = [float(atr)]
    for tr in trs[period:]:
        atr = (atr * (period - 1) + tr) / period
        series.append(float(atr))
    return series


def wilder_atr(bars: Sequence[dict], period: int = 14) -> float:
    series = atr_series(bars, period)
    return float(series[-1]) if series else 0.0


def vwma_of_volume(bars: Sequence[dict], period: int = 20) -> List[float]:
    """Pine `ta.vwma(volume, period)` 用在volume自己身上时的真实公式：
    vwma(src,len) = sma(src*volume,len)/sma(volume,len)，src=volume时就是
    sma(volume^2,len)/sma(volume,len)——不是普通SMA(volume)，是volume自
    加权的量能均线，近期放量的K线权重更大。5套真实TV策略的量比判断
    (量比=volume/该均线)都是照这个公式来的，不能用普通SMA代替。"""
    vols = [_f(b.get("v")) for b in bars]
    if len(vols) < period:
        return []
    num = sma([v * v for v in vols], period)
    den = sma(vols, period)
    return [n / d if d > 0 else 0.0 for n, d in zip(num, den)]


def stoch_k(bars: Sequence[dict], period: int = 14, smooth: int = 3) -> List[float]:
    """随机指标%K，再做SMA(smooth)平滑——对齐真实TV Pine源码里的
    `ta.sma(ta.stoch(close, high, low, 14), 3)`（4份真实策略源码
    01-04版本共用的写法，2026-08-29核对）。"""
    n = len(bars or [])
    if n < period:
        return []
    raw_k = []
    for i in range(period - 1, n):
        window = bars[i - period + 1:i + 1]
        hh = max(_f(b["h"]) for b in window)
        ll = min(_f(b["l"]) for b in window)
        c = _f(bars[i]["c"])
        raw_k.append(100.0 * (c - ll) / max(hh - ll, 1e-9))
    return sma(raw_k, smooth)


def donchian_high_low(bars: Sequence[dict], period: int) -> List[tuple]:
    """Donchian通道：每个点对应"不含当前这根"的过去period根的最高高点/
    最低低点(经典海龟规则用昨天收盘时已知的通道，不把当前这根自己的高低
    点算进自己的突破判断，否则每根K线都会自我突破)。返回[(hh, ll), ...]，
    从第period+1个输入点开始对齐(需要period根历史 + 当前这根)。"""
    n = len(bars or [])
    if n < period + 1:
        return []
    out = []
    for i in range(period, n):
        window = bars[i - period:i]  # 不含bars[i]自己
        hh = max(_f(b["h"]) for b in window)
        ll = min(_f(b["l"]) for b in window)
        out.append((hh, ll))
    return out


def stdev(values: Sequence[float], period: int) -> List[float]:
    """滚动标准差(总体标准差，跟大多数图表软件的布林带口径一致)。"""
    n = len(values or [])
    if period <= 0 or n < period:
        return []
    out = []
    for i in range(period - 1, n):
        window = values[i - period + 1:i + 1]
        m = sum(window) / period
        var = sum((v - m) ** 2 for v in window) / period
        out.append(var ** 0.5)
    return out


def wilder_adx(bars: Sequence[dict], period: int = 14) -> float:
    """跟 market_engine.py:wilder_adx 同算法，dict 形状入参。"""
    n = len(bars or [])
    if n < period * 2 + 2:
        return 0.0

    plus_dm, minus_dm, trs = [], [], []
    for i in range(1, n):
        h = _f(bars[i]["h"])
        l = _f(bars[i]["l"])
        ph = _f(bars[i - 1]["h"])
        pl = _f(bars[i - 1]["l"])
        pc = _f(bars[i - 1]["c"])
        up = h - ph
        down = pl - l
        plus_dm.append(up if up > down and up > 0 else 0.0)
        minus_dm.append(down if down > up and down > 0 else 0.0)
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))

    if len(trs) < period:
        return 0.0

    sm_tr = sum(trs[:period])
    sm_plus = sum(plus_dm[:period])
    sm_minus = sum(minus_dm[:period])

    def _di(sp, sm, st):
        if st <= 0:
            return 0.0, 0.0
        return 100.0 * sp / st, 100.0 * sm / st

    dx_list = []
    pdi, mdi = _di(sm_plus, sm_minus, sm_tr)
    denom = pdi + mdi
    dx_list.append(100.0 * abs(pdi - mdi) / denom if denom > 0 else 0.0)

    for i in range(period, len(trs)):
        sm_tr = sm_tr - sm_tr / period + trs[i]
        sm_plus = sm_plus - sm_plus / period + plus_dm[i]
        sm_minus = sm_minus - sm_minus / period + minus_dm[i]
        pdi, mdi = _di(sm_plus, sm_minus, sm_tr)
        denom = pdi + mdi
        dx_list.append(100.0 * abs(pdi - mdi) / denom if denom > 0 else 0.0)

    if len(dx_list) < period:
        return 0.0
    adx = sum(dx_list[:period]) / period
    for dx in dx_list[period:]:
        adx = (adx * (period - 1) + dx) / period
    return float(adx)
