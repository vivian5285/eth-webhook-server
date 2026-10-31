"""第三批 10 个视频 + 各博主往期(2026-11-01)，同 speculation_channel/lib 口径(主流5币, 吃单5bp+滑点1.5bp, 样本外2025起)。
重点按宝贝要求跨周期测：5m / 15m / 1h / 4h / 8h / 1d。

已测过不重复: J3jBN_qzi1Q(Trader小Q加密参数) / f-YB3Qdw_EU(已泰EMA+威廉分型)。
本文件:
  vegas2   币需暴富 auAnQXtiNNo  EMA144/169 + 288/338 + EMA12过滤，金叉后首次回踩不破(上次测的是 576/676 大众版)
  stiff    交易研究院 CGZ8GYwqkms T3(100) + Trendilo(ALMA) + Stiffness>50，摆动点止损 2R / 1.8R+3ATR追踪
  phenom   Lone Wolf _rNN1Xscm6c 价格远离EMA(超伸) + 反转K → 第二根进场，前K止损 1.5R
  rsi_sc   威廉 2CUvhvcjo1w 5m RSI 70/30 反向，固定1%/1%
  neo_box  卫央Neo 8oXyRhBKpnM 风林火山/趋势七步①区间突破：大级别收敛箱体 → 突破，等距目标
  neo_pb   卫央Neo 趋势七步②横盘回调：急涨后横盘、低点抬高、阳线承接，1:1等距
"""
import os, sys, itertools, numpy as np, pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "speculation_channel")); sys.path.insert(0, os.path.join(HERE, "..", "youtube_4ch"))
from lib import *
from batch01 import shift
from yitai_hist import supertrend, htf_map, wma, hma
HERE = os.path.dirname(os.path.abspath(__file__))  # lib 的 * 导入会覆盖 HERE

TFMS = {"5m": 300e3, "15m": 900e3, "30m": 1800e3, "1h": 3600e3, "2h": 7200e3, "4h": 14400e3, "8h": 28800e3, "12h": 43200e3, "1d": 86400e3}


def run(fn, tf, syms=SYMS, **kw):
    out = []
    for s in syms:
        b = bars(s, tf); b.attrs["sym"] = s; b.attrs["tf"] = tf
        sp = fn(b, **kw); bt = {k: v for k, v in sp.items() if k in ("sig", "stop", "tgt", "exit_sig", "max_bars", "trail_atr", "be_r", "limit")}
        tr = backtest(b, **bt); tr["sym"] = s; out.append(tr)
    return pd.concat(out, ignore_index=True).sort_values("t")


def alma(x, n=50, off=0.85, sig=6.0):
    m = off * (n - 1); s = n / sig; w = np.exp(-((np.arange(n) - m) ** 2) / (2 * s * s)); w /= w.sum()
    out = np.full(len(x), np.nan); xx = np.nan_to_num(x)
    if len(x) >= n:
        out[n - 1:] = np.convolve(xx, w[::-1], mode="valid")  # w[0]=最老
    return out


def t3(x, n=100, a=0.7):
    e1 = ema(x, n); e2 = ema(e1, n); e3 = ema(e2, n); e4 = ema(e3, n); e5 = ema(e4, n); e6 = ema(e5, n)
    c1 = -a ** 3; c2 = 3 * a * a + 3 * a ** 3; c3 = -6 * a * a - 3 * a - 3 * a ** 3; c4 = 1 + 3 * a + a ** 3 + 3 * a * a
    return c1 * e6 + c2 * e5 + c3 * e4 + c4 * e3


def swing_stop(b, sig, look=10, pad=0.1):
    a = atr(b); lo = pd.Series(b.l.values).rolling(look).min().values; hi = pd.Series(b.h.values).rolling(look).max().values
    return np.where(sig == 1, lo - pad * a, np.where(sig == -1, hi + pad * a, np.nan))


# ---------- 1 Vegas 改进版 ----------
def vegas2(b, mid=(288, 338), max_pb=1, stop_mode="mid", exit_mode="rr", rr=2.0, slope_n=20):
    """短隧道144/169在中隧道之上且都向上(=多头排列)；价格回踩短隧道上沿、收盘不破下沿、EMA12仍在短隧道之上 → 做多。
    max_pb: 每次金叉后只做前 N 次回踩(他说第1次回踩90%反弹、越往后越失效)。止损: mid=中隧道下方 / fast=短隧道下方0.5ATR。
    exit: rr / ema12(EMA12 跌破短隧道下沿离场)。"""
    c, h, l = b.c.values, b.h.values, b.l.values; a = atr(b)
    e12, f1, f2, m1, m2 = ema(c, 12), ema(c, 144), ema(c, 169), ema(c, mid[0]), ema(c, mid[1])
    ft, fb = np.maximum(f1, f2), np.minimum(f1, f2); mt, mb = np.maximum(m1, m2), np.minimum(m1, m2)
    rise = (fb > shift(fb, slope_n)) & (mb > shift(mb, slope_n)); fall = (ft < shift(ft, slope_n)) & (mt < shift(mt, slope_n))
    up = (fb > mt) & rise; dn = (ft < mb) & fall
    n = len(c); sig = np.zeros(n); stop = np.full(n, np.nan); ex = np.zeros(n); reg = 0; cnt = 0; away = False
    for i in range(mid[1] + slope_n, n):
        r = 1 if up[i] else (-1 if dn[i] else 0)
        if r != reg: reg = r; cnt = 0; away = False
        if r == 1:
            if l[i] > ft[i] + 1.0 * a[i]: away = True  # 先离开隧道，才算"回踩"
            if away and l[i] <= ft[i] and c[i] > fb[i] and e12[i] > fb[i] and cnt < max_pb:
                sig[i] = 1; cnt += 1; away = False
                stop[i] = (mb[i] if stop_mode == "mid" else fb[i]) - 0.5 * a[i]
        elif r == -1:
            if h[i] < fb[i] - 1.0 * a[i]: away = True
            if away and h[i] >= fb[i] and c[i] < ft[i] and e12[i] < ft[i] and cnt < max_pb:
                sig[i] = -1; cnt += 1; away = False
                stop[i] = (mt[i] if stop_mode == "mid" else ft[i]) + 0.5 * a[i]
        ex[i] = 1 if e12[i] < fb[i] else (-1 if e12[i] > ft[i] else 0)
    ok = (sig != 0) & ((stop - c) * sig < 0); sig = np.where(ok, sig, 0)
    d = dict(sig=sig, stop=np.where(ok, stop, np.nan))
    if exit_mode == "rr": d["tgt"] = c + sig * rr * np.abs(c - stop)
    else: d["exit_sig"] = ex
    return d


# ---------- 2 T3 + Trendilo + Stiffness ----------
def stiffness(c, n=60, ma_n=100, sm=3):
    bound = sma(c, ma_n) - 0.2 * pd.Series(c).rolling(ma_n).std().values
    return ema(pd.Series((c > bound).astype(float)).rolling(n).sum().values * 100 / n, sm)


def stiff(b, thr=50, exit_mode="rr", rr=2.0, t3n=100, event=True):
    c = b.c.values; T = t3(c, t3n); green = T > shift(T, 1)
    pch = np.diff(c, prepend=c[0]) / c * 100; av = alma(pch, 50, 0.85, 6)
    rms = np.sqrt(pd.Series(av * av).rolling(50).sum().values / 50)
    tu = av > rms; td = av < -rms
    if event: tu = tu & ~shift(tu, 1).astype(bool); td = td & ~shift(td, 1).astype(bool)
    st = stiffness(c) > thr
    L = green & (c > T) & tu & st; S = ~green & (c < T) & td & st
    sig = np.where(L, 1, np.where(S, -1, 0)); stop = swing_stop(b, sig)
    d = dict(sig=sig, stop=stop)
    if exit_mode == "rr": d["tgt"] = c + sig * rr * np.abs(c - stop)
    else: d["trail_atr"] = 3.0
    return d


# ---------- 3 Lone Wolf: 远离均线 + 反转K ----------
def phenom(b, k=3.0, ema_n=50, rr=1.5, trend=False):
    """价格偏离 EMA50 超过 k×ATR(他说的"大量红色背景+远离中线")，随后出现反转K(收盘收回前一根高点/低点)，
    第二根开盘进场，止损前一根K的低/高点，1.5R。trend=True 时只做顺 EMA200 方向的回调(他的过滤思路)。"""
    o, h, l, c = b.o.values, b.h.values, b.l.values, b.c.values; a = atr(b); e = ema(c, ema_n); e2 = ema(c, 200)
    far_dn = (pd.Series(((e - l) > k * a).astype(float)).rolling(3).max().values > 0)
    far_up = (pd.Series(((h - e) > k * a).astype(float)).rolling(3).max().values > 0)
    revL = far_dn & (c > o) & (c > shift(h, 1)); revS = far_up & (c < o) & (c < shift(l, 1))
    if trend: revL &= c > e2; revS &= c < e2
    sig = np.where(revL, 1, np.where(revS, -1, 0))
    stop = np.where(sig == 1, np.minimum(l, shift(l, 1)) - 0.05 * a, np.where(sig == -1, np.maximum(h, shift(h, 1)) + 0.05 * a, np.nan))
    return dict(sig=sig, stop=stop, tgt=c + sig * rr * np.abs(c - stop))


# ---------- 4 威廉 5m RSI 70/30 反向 ----------
def rsi_sc(b, sl=0.01, tp=0.01, n=14, trend=0):
    c = b.c.values; r = rsi(c, n)
    L = (r < 30) & (shift(r, 1) >= 30); S = (r > 70) & (shift(r, 1) <= 70)
    if trend:  # 只做顺 1h EMA 方向(他第二期"4招"里的过滤，见 z2YNY3QBuXE)
        e = ema(c, trend); L &= c > e; S &= c < e
    sig = np.where(L, 1, np.where(S, -1, 0))
    return dict(sig=sig, stop=np.where(sig != 0, c * (1 - sig * sl), np.nan), tgt=c * (1 + sig * tp))


# ---------- 5 卫央Neo 区间突破 ----------
def neo_box(b, n=40, wmax=6.0, tgt_mult=1.0, exit_mode="tgt", trend_n=0, retest=False):
    """过去 n 根(不含当根)高低区间宽度 ≤ wmax×ATR = 收敛箱体；收盘突破箱顶 → 做多(箱底跌破做空)。
    止损箱体中线(他是二次低点抬升处，近似)；目标=箱高×tgt_mult(他的第一/第二目标位)；exit_mode=trail 用 3ATR 追踪。
    trend_n>0: 只做顺 EMA(trend_n) 方向(不做逆势单)。"""
    h, l, c = b.h.values, b.l.values, b.c.values; a = atr(b)
    hi = shift(pd.Series(h).rolling(n).max().values, 1); lo = shift(pd.Series(l).rolling(n).min().values, 1)
    w = hi - lo; box = w <= wmax * a
    L = box & (c > hi) & (shift(c, 1) <= shift(hi, 1)); S = box & (c < lo) & (shift(c, 1) >= shift(lo, 1))
    if trend_n:
        e = ema(c, trend_n); L &= e > shift(e, 5); S &= e < shift(e, 5)
    sig = np.where(L, 1, np.where(S, -1, 0)); midp = (hi + lo) / 2
    stop = np.where(sig != 0, midp, np.nan)
    d = dict(sig=sig, stop=stop)
    if exit_mode == "tgt": d["tgt"] = np.where(sig == 1, hi + tgt_mult * w, lo - tgt_mult * w)
    else: d["trail_atr"] = 3.0
    if retest:  # 回踩箱顶挂限价(突破后回踩承接)
        d["limit"] = np.where(sig == 1, hi + 0.1 * a, np.where(sig == -1, lo - 0.1 * a, np.nan))
    return d


# ---------- 6 卫央Neo 横盘回调 ----------
def neo_pb(b, imp=4.0, imp_n=12, box_n=8, box_w=2.0, rr=1.0, trend_n=0):
    """前 imp_n 根内有 ≥imp×ATR 的急涨(风)，之后 box_n 根横盘(宽≤box_w×ATR)，横盘内低点抬高，
    出现阳线且收在箱体下半部 → 做多；止损箱底；目标 1:1 等距(他做短的"1比1等距")或 rr 倍。"""
    o, h, l, c = b.o.values, b.h.values, b.l.values, b.c.values; a = atr(b)
    hi = pd.Series(h).rolling(box_n).max().values; lo = pd.Series(l).rolling(box_n).min().values
    flat = (hi - lo) <= box_w * a
    run_up = (shift(c, box_n) - shift(pd.Series(l).rolling(imp_n).min().values, box_n)) >= imp * a
    run_dn = (shift(pd.Series(h).rolling(imp_n).max().values, box_n) - shift(c, box_n)) >= imp * a
    half = box_n // 2
    lo1 = pd.Series(l).rolling(half).min().values; lo0 = shift(lo1, half)
    hi1 = pd.Series(h).rolling(half).max().values; hi0 = shift(hi1, half)
    L = flat & run_up & (lo1 > lo0) & (c > o) & (c < (hi + lo) / 2 + 0.25 * (hi - lo))
    S = flat & run_dn & (hi1 < hi0) & (c < o) & (c > (hi + lo) / 2 - 0.25 * (hi - lo))
    if trend_n:
        e = ema(c, trend_n); L &= c > e; S &= c < e
    sig = np.where(L, 1, np.where(S, -1, 0))
    stop = np.where(sig == 1, lo - 0.1 * a, np.where(sig == -1, hi + 0.1 * a, np.nan))
    return dict(sig=sig, stop=stop, tgt=c + sig * rr * np.abs(c - stop))


# ---------- 7 卫央Neo 区间顶部挤压(突破前进场) ----------
def neo_sq(b, n=60, k=6, sq_w=1.5, top_frac=0.67, exit_mode="tgt", tgt_mult=1.0):
    """他的原话：真正的启动不是突破那根，而是突破前的挤压——触碰上沿、波动收窄、低点抬升企稳，阳线后进场，止损在抬升低点下。
    箱体=过去 n 根(不含最近 k 根)高低；最近 k 根全在箱体上 1/3、总宽 ≤ sq_w×ATR、后半段低点高于前半段，收阳 → 做多(空单镜像)。
    目标：箱顶 + 箱高×tgt_mult("横有多宽竖有多长")；exit_mode=trail 3ATR 追踪。"""
    o, h, l, c = b.o.values, b.h.values, b.l.values, b.c.values; a = atr(b)
    bh = shift(pd.Series(h).rolling(n).max().values, k); bl = shift(pd.Series(l).rolling(n).min().values, k); w = bh - bl
    rh = pd.Series(h).rolling(k).max().values; rl = pd.Series(l).rolling(k).min().values
    half = k // 2; l2 = pd.Series(l).rolling(half).min().values; l1 = shift(l2, half); h2 = pd.Series(h).rolling(half).max().values; h1 = shift(h2, half)
    tight = (rh - rl) <= sq_w * a
    L = tight & (rl >= bl + top_frac * w) & (rh >= bh - 0.2 * w) & (l2 > l1) & (c > o)
    S = tight & (rh <= bh - top_frac * w) & (rl <= bl + 0.2 * w) & (h2 < h1) & (c < o)
    sig = np.where(L, 1, np.where(S, -1, 0))
    stop = np.where(sig == 1, rl - 0.1 * a, np.where(sig == -1, rh + 0.1 * a, np.nan))
    d = dict(sig=sig, stop=stop)
    if exit_mode == "tgt": d["tgt"] = np.where(sig == 1, np.maximum(bh, rh) + tgt_mult * w, np.minimum(bl, rl) - tgt_mult * w)
    else: d["trail_atr"] = 3.0
    return d


# ---------- 8 卫央Neo 趋势七步④ 二波(回调后中部收敛突破) ----------
def neo_w2(b, imp=6.0, look=60, k=8, sq_w=2.0, ret=(0.3, 0.7), exit_mode="tgt"):
    """近 look 根内第一波涨幅 ≥ imp×ATR(低点→高点)，之后回调到第一波的 30%~70%(下跌回调)，
    最近 k 根收敛(宽 ≤ sq_w×ATR)、低点抬升，收盘突破收敛区上沿 → 做多；止损收敛区下沿；目标=第一波高度(二波≈一波)或 3ATR 追踪。"""
    h, l, c = b.h.values, b.l.values, b.c.values; a = atr(b); n = len(c)
    rh = shift(pd.Series(h).rolling(k).max().values, 1); rl = shift(pd.Series(l).rolling(k).min().values, 1)
    half = k // 2; l2 = shift(pd.Series(l).rolling(half).min().values, 1); l1 = shift(l2, half)
    h2 = shift(pd.Series(h).rolling(half).max().values, 1); h1 = shift(h2, half)
    sig = np.zeros(n); stop = np.full(n, np.nan); tgt = np.full(n, np.nan)
    H = pd.Series(h).rolling(look).max().values; Lo = pd.Series(l).rolling(look).min().values
    for i in range(look + k, n):
        if np.isnan(rh[i]) or (rh[i] - rl[i]) > sq_w * a[i]: continue
        seg_h = h[i - look - k:i - k]; seg_l = l[i - look - k:i - k]
        jh = int(np.argmax(seg_h)); jl = int(np.argmin(seg_l))
        if jl < jh:  # 先低后高 = 第一波上涨
            wave = seg_h[jh] - seg_l[jl]; pullback = (seg_h[jh] - rl[i]) / max(wave, 1e-12)
            if wave >= imp * a[i] and ret[0] <= pullback <= ret[1] and l2[i] > l1[i] and c[i] > rh[i]:
                sig[i] = 1; stop[i] = rl[i] - 0.1 * a[i]; tgt[i] = rl[i] + wave; continue
        if jh < jl:
            wave = seg_h[jh] - seg_l[jl]; pullback = (rh[i] - seg_l[jl]) / max(wave, 1e-12)
            if wave >= imp * a[i] and ret[0] <= pullback <= ret[1] and h2[i] < h1[i] and c[i] < rl[i]:
                sig[i] = -1; stop[i] = rh[i] + 0.1 * a[i]; tgt[i] = rh[i] - wave
    d = dict(sig=sig, stop=stop)
    if exit_mode == "tgt": d["tgt"] = tgt
    else: d["trail_atr"] = 3.0
    return d


# ---------- 9 Lone Wolf u9_G-I-ZIyM 相对趋势指数(RTI, Zeiierman) + Market Bias 色带 ----------
def rti(c, n=100, sens=95, ma_n=10):
    sd = pd.Series(c).rolling(2).std().values; up = c + sd; lo = c - sd; N = len(c); out = np.full(N, np.nan)
    iu = int(round(sens / 100 * (n - 1))); il = int(round((100 - sens) / 100 * (n - 1)))
    U = np.lib.stride_tricks.sliding_window_view(np.nan_to_num(up), n); Lw = np.lib.stride_tricks.sliding_window_view(np.nan_to_num(lo), n)
    ut = np.partition(U, iu, axis=1)[:, iu]; lt = np.partition(Lw, il, axis=1)[:, il]
    out[n - 1:] = (c[n - 1:] - lt) / np.maximum(ut - lt, 1e-12) * 100
    return out, ema(np.nan_to_num(out, nan=50), ma_n)


def market_bias(b, n=60, sm=10):
    o, h, l, c = (ema(x, n) for x in (b.o.values, b.h.values, b.l.values, b.c.values))
    hc = (o + h + l + c) / 4; ho = np.empty(len(c)); ho[0] = (o[0] + c[0]) / 2
    for i in range(1, len(c)): ho[i] = (ho[i - 1] + hc[i - 1]) / 2
    hh = np.maximum(h, np.maximum(ho, hc)); hl = np.minimum(l, np.minimum(ho, hc))
    return ema(ho, sm), ema(hc, sm), ema(hh, sm), ema(hl, sm)


def lw_rti(b, ob=80, os_=20, rr=2.0, zone=True):
    c = b.c.values; r, m = rti(c); mo, mc, mh, ml = market_bias(b); a = atr(b)
    xu = (r > m) & (shift(r, 1) <= shift(m, 1)); xd = (r < m) & (shift(r, 1) >= shift(m, 1))
    L = xu & (mc > mo) & (c > np.maximum(mo, mc)); S = xd & (mc < mo) & (c < np.minimum(mo, mc))
    if zone: L &= r > ob; S &= r < os_
    sig = np.where(L, 1, np.where(S, -1, 0))
    stop = np.where(sig == 1, np.minimum(mo, mc) - 0.2 * a, np.where(sig == -1, np.maximum(mo, mc) + 0.2 * a, np.nan))
    return dict(sig=sig, stop=stop, tgt=c + sig * rr * np.abs(c - stop))


# ---------- 10 番人说 u-A6BTmVXpk 纽约时间首根4H区间 假突破回收 ----------
def fr_4h(b, rr=2.0, max_stop_rng=10.0, eod=True):
    """纽约时间每天第一根 4H K线(00:00-04:00 NY)收盘后标高低点；之后 5m 收盘在区间外，再收盘回到区间内(同一纽约日) → 反向进场；
    止损=突破段的极值，目标 2R。max_stop_rng: 止损距离 > 区间宽×该倍数则跳过(他说突破太远就换近的关键位，这里简化成跳过)。"""
    h, l, c = b.h.values, b.l.values, b.c.values; n = len(c)
    ny = pd.to_datetime(b.t.values, unit="ms", utc=True).tz_convert("America/New_York")
    day = ny.normalize().values.astype("datetime64[D]").astype(np.int64); hr = ny.hour.values
    sig = np.zeros(n); stop = np.full(n, np.nan)
    cur = None; rh = rl = np.nan; state = 0; ext = np.nan
    for i in range(n):
        if day[i] != cur: cur = day[i]; rh = -np.inf; rl = np.inf; state = 0
        if hr[i] < 4:
            rh = max(rh, h[i]); rl = min(rl, l[i]); continue
        if state == 0:
            if c[i] > rh: state = 1; ext = h[i]
            elif c[i] < rl: state = -1; ext = l[i]
        elif state == 1:
            ext = max(ext, h[i])
            if c[i] < rh and c[i] > rl:
                if (ext - c[i]) <= max_stop_rng * (rh - rl): sig[i] = -1; stop[i] = ext
                state = 0
        elif state == -1:
            ext = min(ext, l[i])
            if c[i] > rl and c[i] < rh:
                if (c[i] - ext) <= max_stop_rng * (rh - rl): sig[i] = 1; stop[i] = ext
                state = 0
    return dict(sig=sig, stop=stop, tgt=c + sig * rr * np.abs(c - stop), max_bars=int(86400e3 // TFMS[b.attrs.get("tf", "5m")]) if eod else 10 ** 9)


# ---------- 11 威廉 z2YNY3QBuXE 5m RSI 系统 + 4招 ----------
def rsi_sc2(b, vol=True, wick=True, cool=36, sweep=True, sl=0.01, tp=0.01, piv_h=12):
    """在 rsi_sc 上加他的4招：①开仓K放量(>1.5×20均量) ②超买K长上影/超卖K长下影(≥35%K线) ③短时间内一直超买超卖就不做(前 cool 根内有过信号或一直在区间外则跳过)
    ④价格碰到 1 小时级别的前一个阶段高/低点(扫流动性)再进：用 piv_h 小时摆动高低点近似(5m 上 = piv_h×12 根)。"""
    o, h, l, c, v = b.o.values, b.h.values, b.l.values, b.c.values, b.v.values; r = rsi(c, 14); rng = np.maximum(h - l, 1e-12)
    S = (r > 70) & (shift(r, 1) <= 70); L = (r < 30) & (shift(r, 1) >= 30)
    if vol: vk = v > 1.5 * sma(v, 20); S &= vk; L &= vk
    if wick: S &= (h - np.maximum(o, c)) / rng >= 0.35; L &= (np.minimum(o, c) - l) / rng >= 0.35
    if sweep:
        k = piv_h * 12; ph, pl = pivots(h, l, k, k)
        S &= h >= ph; L &= l <= pl
    sig = np.where(L, 1, np.where(S, -1, 0)).astype(float)
    if cool:
        out = (r > 70) | (r < 30); busy = pd.Series(out.astype(float)).rolling(cool).sum().values
        last = -10 ** 9
        for i in range(len(sig)):
            if sig[i] != 0:
                if i - last < cool or busy[i] > cool * 0.5: sig[i] = 0
                else: last = i
    return dict(sig=sig, stop=np.where(sig != 0, c * (1 - sig * sl), np.nan), tgt=c * (1 + sig * tp))


ROWS = []


def rec(name, tf, tr, **kw):
    d = summarize(tr); d.update(name=name, tf=tf, kw=str(kw)); ROWS.append(d)
    print(f"{name:10s} {tf:4s} {str(kw):44s} n={d.get('n')} win={d.get('win')} R0={d.get('r_gross')} Rnet={d.get('r_net')} "
          f"is={d.get('r_is')} oos={d.get('r_oos')} cagr={d.get('cagr')} mdd={d.get('mdd')}", flush=True)


if __name__ == "__main__":
    for tf, mp, sm, ex in itertools.product(("15m", "1h", "4h"), (1, 2), ("mid", "fast"), ("rr", "ema12")):
        rec("vegas2", tf, run(vegas2, tf, max_pb=mp, stop_mode=sm, exit_mode=ex), mp=mp, sm=sm, ex=ex)
    for tf, ex, ev in itertools.product(("15m", "1h", "4h", "8h"), ("rr", "trail"), (True, False)):
        rec("stiff", tf, run(stiff, tf, exit_mode=ex, event=ev), ex=ex, ev=ev)
    for tf, k, tr_ in itertools.product(("5m", "15m", "1h", "4h"), (2.5, 3.5), (False, True)):
        rec("phenom", tf, run(phenom, tf, k=k, trend=tr_), k=k, trend=tr_)
    for tf, (sl, tp) in itertools.product(("5m", "15m"), ((0.01, 0.01), (0.005, 0.01), (0.005, 0.015))):
        rec("rsi_sc", tf, run(rsi_sc, tf, sl=sl, tp=tp), sl=sl, tp=tp)
    for tf, n, wm, ex, tn in itertools.product(("15m", "1h", "4h", "1d"), (30, 60), (4.0, 7.0), ("tgt", "trail"), (0, 50)):
        rec("neo_box", tf, run(neo_box, tf, n=n, wmax=wm, exit_mode=ex, trend_n=tn), n=n, wm=wm, ex=ex, tn=tn)
    for tf, imp, rr, tn in itertools.product(("15m", "1h", "4h"), (3.0, 5.0), (1.0, 2.0), (0, 200)):
        rec("neo_pb", tf, run(neo_pb, tf, imp=imp, rr=rr, trend_n=tn), imp=imp, rr=rr, tn=tn)
    for tf, n, ex in itertools.product(("15m", "1h", "4h", "1d"), (40, 80), ("tgt", "trail")):
        rec("neo_sq", tf, run(neo_sq, tf, n=n, exit_mode=ex), n=n, ex=ex)
    for tf, imp, ex in itertools.product(("15m", "1h", "4h", "1d"), (5.0, 8.0), ("tgt", "trail")):
        rec("neo_w2", tf, run(neo_w2, tf, imp=imp, exit_mode=ex), imp=imp, ex=ex)
    for tf, z, rr in itertools.product(("15m", "1h", "4h", "1d"), (True, False), (2.0, 3.0)):
        rec("lw_rti", tf, run(lw_rti, tf, zone=z, rr=rr), zone=z, rr=rr)
    pd.DataFrame(ROWS).to_csv(os.path.join(HERE, "b3.csv"), index=False)
