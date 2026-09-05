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


def supertrend(bars: Sequence[dict], period: int = 10, multiplier: float = 3.0) -> List[tuple]:
    """SuperTrend(ATR通道翻转指标)——公开经典指标(Olivier Seban提出，各大
    图表软件内置)，本仓库此前没有。返回 [(st_value, direction), ...]，
    direction: +1=多头(收盘价在SuperTrend线上方)，-1=空头。序列末尾对齐到
    最新一根K线，取负索引即可(out[-1]=当前，out[-2]=上一根，用来判断翻转)。

    算法(跟TradingView `ta.supertrend` 同口径)：
      hl2 = (high+low)/2
      basic_upper = hl2 + multiplier*ATR ；basic_lower = hl2 - multiplier*ATR
      final_upper/lower 带"棘轮"约束(只在更极端或价格穿越时才放开)
      SuperTrend 在 final_upper / final_lower 之间按收盘价与前值切换

    ATR 用本模块的 Wilder atr_series(跟系统其它地方 ATR 口径一致)。返回序列
    第一个点对应 bars 里第 period+1 根(需要 ATR 首值 + 一根前值做递推种子)。
    """
    atr = atr_series(bars, period)
    if len(atr) < 2:
        return []
    # atr[k] 对应 bars[period + k]
    offset = len(bars) - len(atr)  # = period
    fu_prev = fl_prev = st_prev = None
    dir_prev = 1
    out: List[tuple] = []
    for k in range(len(atr)):
        i = offset + k
        h = _f(bars[i]["h"])
        l = _f(bars[i]["l"])
        c = _f(bars[i]["c"])
        pc = _f(bars[i - 1]["c"])
        hl2 = (h + l) / 2.0
        basic_upper = hl2 + multiplier * atr[k]
        basic_lower = hl2 - multiplier * atr[k]
        if fu_prev is None:
            final_upper, final_lower = basic_upper, basic_lower
            st = final_lower if c >= hl2 else final_upper
            direction = 1 if c >= hl2 else -1
        else:
            final_upper = basic_upper if (basic_upper < fu_prev or pc > fu_prev) else fu_prev
            final_lower = basic_lower if (basic_lower > fl_prev or pc < fl_prev) else fl_prev
            if st_prev == fu_prev:
                if c <= final_upper:
                    st, direction = final_upper, -1
                else:
                    st, direction = final_lower, 1
            else:  # st_prev == fl_prev
                if c >= final_lower:
                    st, direction = final_lower, 1
                else:
                    st, direction = final_upper, -1
        out.append((float(st), int(direction)))
        fu_prev, fl_prev, st_prev, dir_prev = final_upper, final_lower, st, direction
    return out


def parabolic_sar(bars: Sequence[dict], af_init: float = 0.02, af_step: float = 0.02, af_max: float = 0.2) -> List[tuple]:
    """Wilder 抛物线转向指标(Parabolic SAR，《New Concepts in Technical
    Trading Systems》1978)。返回 [(sar, direction), ...]，跟 bars 等长
    (第一个点是初始化种子，从第二个点起才是真正逐根递推出来的值)。
    direction: +1=多头(SAR在价格下方，价格站上SAR才反手)，-1=空头。
    调用方取 out[-1]/out[-2] 判断本根是否刚刚翻转。"""
    n = len(bars or [])
    if n < 2:
        return []
    highs = [_f(b["h"]) for b in bars]
    lows = [_f(b["l"]) for b in bars]
    trend = 1 if (highs[1] + lows[1]) >= (highs[0] + lows[0]) else -1
    if trend == 1:
        sar, ep = lows[0], highs[0]
    else:
        sar, ep = highs[0], lows[0]
    af = af_init
    out: List[tuple] = [(float(sar), trend)]
    for i in range(1, n):
        sar = sar + af * (ep - sar)
        if trend == 1:
            lim = min(lows[i - 1], lows[i - 2]) if i >= 2 else lows[i - 1]
            sar = min(sar, lim)
            if lows[i] < sar:
                trend = -1
                sar = ep
                ep = lows[i]
                af = af_init
            elif highs[i] > ep:
                ep = highs[i]
                af = min(af + af_step, af_max)
        else:
            lim = max(highs[i - 1], highs[i - 2]) if i >= 2 else highs[i - 1]
            sar = max(sar, lim)
            if highs[i] > sar:
                trend = 1
                sar = ep
                ep = highs[i]
                af = af_init
            elif lows[i] < ep:
                ep = lows[i]
                af = min(af + af_step, af_max)
        out.append((float(sar), trend))
    return out


def macd(values: Sequence[float], fast: int = 12, slow: int = 26, signal: int = 9):
    """经典 MACD(Gerald Appel)。DIF=EMA(fast)-EMA(slow)，DEA=EMA(DIF,signal)，
    柱状图=DIF-DEA。三条序列长度不同(ema()各自从各自的period-1才起算)，
    本函数已经把 dif 对齐裁到跟 ema_slow 等长、跟 dea/hist 都是"结尾对齐到
    同一根K线"，调用方直接用负索引取用即可。数据不够时三个都返回[]。"""
    ema_fast = ema(values, fast)
    ema_slow = ema(values, slow)
    if not ema_fast or not ema_slow:
        return [], [], []
    n = min(len(ema_fast), len(ema_slow))
    dif = [ema_fast[len(ema_fast) - n + i] - ema_slow[len(ema_slow) - n + i] for i in range(n)]
    dea = ema(dif, signal)
    if not dea:
        return dif, [], []
    dif_tail = dif[-len(dea):]
    hist = [dif_tail[i] - dea[i] for i in range(len(dea))]
    return dif, dea, hist


def kama(values: Sequence[float], er_period: int = 10, fast: int = 2, slow: int = 30) -> List[float]:
    """考夫曼自适应均线(Kaufman's Adaptive Moving Average)。效率比(ER)=
    净变化/波动路径总长，ER高(真趋势)→平滑常数变大→均线跟得紧，ER低
    (震荡)→平滑常数变小→均线趋于走平。返回从输入第er_period个点开始
    对齐的序列(种子=该点原始值)，长度=len(values)-er_period。"""
    n = len(values or [])
    if n <= er_period:
        return []
    fast_sc = 2.0 / (fast + 1.0)
    slow_sc = 2.0 / (slow + 1.0)
    out = [float(values[er_period])]
    for i in range(er_period + 1, n):
        change = abs(values[i] - values[i - er_period])
        volatility = sum(abs(values[j] - values[j - 1]) for j in range(i - er_period + 1, i + 1))
        er = (change / volatility) if volatility > 0 else 0.0
        sc = (er * (fast_sc - slow_sc) + slow_sc) ** 2
        out.append(out[-1] + sc * (values[i] - out[-1]))
    return out


def wilder_adx_di(bars: Sequence[dict], period: int = 14):
    """跟 wilder_adx 同一套 Wilder 算法，额外把最新一根的 +DI/-DI 也带出来
    (ADX 本身只讲趋势强度、不讲方向，Raschke ADX回踩战法需要方向)。
    返回 (adx, plus_di, minus_di)，数据不够时返回 (0.0, 0.0, 0.0)。跟
    wilder_adx 各自独立实现(没有共用内部函数)，避免改动会影响到已经在用
    wilder_adx 的既有战法。"""
    n = len(bars or [])
    if n < period * 2 + 2:
        return 0.0, 0.0, 0.0

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
        return 0.0, 0.0, 0.0

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
        return 0.0, pdi, mdi
    adx = sum(dx_list[:period]) / period
    for dx in dx_list[period:]:
        adx = (adx * (period - 1) + dx) / period
    return float(adx), float(pdi), float(mdi)


def obv(bars: Sequence[dict]) -> List[float]:
    """能量潮(On-Balance Volume)——Joseph Granville 1963年公开发表，成交量
    类指标里最经典的之一。收盘价比前一根高就把这根成交量加进累计值，
    比前一根低就减掉，平就不变。返回跟bars等长的序列(第一个点=0，
    作为累计起点)。"""
    n = len(bars or [])
    if n == 0:
        return []
    out = [0.0]
    for i in range(1, n):
        c, pc = _f(bars[i]["c"]), _f(bars[i - 1]["c"])
        v = _f(bars[i].get("v"))
        if c > pc:
            out.append(out[-1] + v)
        elif c < pc:
            out.append(out[-1] - v)
        else:
            out.append(out[-1])
    return out


def wma(values: Sequence[float], period: int) -> List[float]:
    """加权移动均线：越近的数据权重越大(权重按1..period线性递增)，是
    Hull Moving Average的构造基础。返回从第period个点开始对齐的序列。"""
    n = len(values or [])
    if period <= 0 or n < period:
        return []
    weights = list(range(1, period + 1))
    weight_sum = sum(weights)
    out = []
    for i in range(period - 1, n):
        window = values[i - period + 1:i + 1]
        out.append(sum(w * v for w, v in zip(weights, window)) / weight_sum)
    return out


def hma(values: Sequence[float], period: int) -> List[float]:
    """Hull Moving Average(Alan Hull，2005年公开发表)。公式：
    HMA(n) = WMA(2×WMA(close, n/2) - WMA(close, n), round(sqrt(n)))
    是"降低均线滞后"流派的代表构造法，比同周期SMA/EMA对价格反转的
    反应更快。返回结尾对齐到最新收盘价的序列，取负索引即可。"""
    n = len(values or [])
    half = max(1, period // 2)
    sqrt_n = max(1, round(period ** 0.5))
    wma_half = wma(values, half)
    wma_full = wma(values, period)
    if not wma_half or not wma_full:
        return []
    m = min(len(wma_half), len(wma_full))
    raw = [2 * wma_half[len(wma_half) - m + i] - wma_full[len(wma_full) - m + i] for i in range(m)]
    return wma(raw, sqrt_n)


def cvd(bars: Sequence[dict]) -> List[float]:
    """累计成交量差值(Cumulative Volume Delta)——用交易所自己上报的
    主动买入量(bars里的"tb"字段，klines.py 2026-09-05新增，来自币安K线
    接口原生自带的taker_buy_base_asset_volume，是真实数据，不是像OBV
    那样用价格涨跌方向猜的代理指标)算真实主动买卖盘差值：
    delta = 主动买量 - 主动卖量 = 2×tb - 总成交量v。累计求和，返回跟
    bars等长的序列。bars里没有"tb"字段时该根delta记0(不报错，只是当根
    不贡献信息，累计值保持不变)。"""
    n = len(bars or [])
    if n == 0:
        return []
    out = []
    running = 0.0
    for b in bars:
        v = _f(b.get("v"))
        tb = b.get("tb")
        delta = (2.0 * _f(tb) - v) if tb is not None else 0.0
        running += delta
        out.append(running)
    return out


def swing_points(bars: Sequence[dict], kind: str) -> List[tuple]:
    """轻量级摆动高低点识别(3根K线局部极值，不做K线合并处理——是比
    supertrend/chanlun_pivot那套更轻的独立实现，专供背离类战法用)。
    kind='high'找局部高点，'low'找局部低点。返回[(bars下标, 极值价), ...]。"""
    n = len(bars or [])
    out = []
    for i in range(1, n - 1):
        h, l = _f(bars[i]["h"]), _f(bars[i]["l"])
        ph, pl = _f(bars[i - 1]["h"]), _f(bars[i - 1]["l"])
        nh, nl = _f(bars[i + 1]["h"]), _f(bars[i + 1]["l"])
        if kind == "high" and h > ph and h > nh:
            out.append((i, h))
        elif kind == "low" and l < pl and l < nl:
            out.append((i, l))
    return out


def donchian_mid(bars: Sequence[dict], period: int) -> List[float]:
    """一目均衡表用的中值线：(period根内最高高点+最低低点)/2，**含当前这
    根**(跟donchian_high_low故意相反——那个是海龟规则"不含当前"，一目
    均衡表的转换线/基准线传统定义就是含当前这根)。返回从第period个点
    开始对齐的序列。"""
    n = len(bars or [])
    if n < period:
        return []
    out = []
    for i in range(period - 1, n):
        window = bars[i - period + 1:i + 1]
        hh = max(_f(b["h"]) for b in window)
        ll = min(_f(b["l"]) for b in window)
        out.append((hh + ll) / 2.0)
    return out
