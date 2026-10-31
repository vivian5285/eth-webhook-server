"""交易研究院(UCktqp7siztf5IWcMiVA9KbA)往期：一堆 TradingView 指标"翻色/翻向"趋势策略，统一成同一框架跨周期测。
VIDYA趋势 / JMA / 珊瑚趋势(Coral) / 线性回归斜率 / 估波曲线(Coppock) / ALMA双线 / RMI趋势 / 平均力量(Average Force) / 袋鼠尾(K线反转+爆量)。
进场=翻向那根收盘，下一根开盘成交；离场=反向翻转(反手不另开，等下一次信号)；初始止损 2×ATR(或指标线)。
Supertrend/MACD/TTM/一目/随机/斐波那契 前几批已测，不重复。"""
import os, sys, itertools, numpy as np, pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
from b3 import *
HERE = os.path.dirname(os.path.abspath(__file__))


def flip(b, up, stop_atr=2.0):
    """up: 布尔趋势状态。翻多/翻空产生信号，反向翻转离场。"""
    c = b.c.values; a = atr(b); up = np.asarray(up, bool); prev = shift(up.astype(float), 1)
    fu = up & (prev == 0); fd = ~up & (prev == 1)
    sig = np.where(fu, 1, np.where(fd, -1, 0))
    return dict(sig=sig, stop=np.where(sig != 0, c - sig * stop_atr * a, np.nan), exit_sig=np.where(fd, 1, np.where(fu, -1, 0)))


def cmo(c, n):
    d = np.diff(c, prepend=c[0]); u = pd.Series(np.maximum(d, 0)).rolling(n).sum().values; v = pd.Series(np.maximum(-d, 0)).rolling(n).sum().values
    return 100 * (u - v) / np.maximum(u + v, 1e-12)


def vidya_line(c, n=10, mom=20):
    k = np.nan_to_num(np.abs(cmo(c, mom)) / 100); al = 2 / (n + 1); out = np.empty(len(c)); out[0] = c[0]
    for i in range(1, len(c)): out[i] = al * k[i] * c[i] + (1 - al * k[i]) * out[i - 1]
    return out


def vidya_tr(b, n=10, mom=20, band=2.0):
    """BigBeluga Volumatic VIDYA：VIDYA ± band×ATR(200)，收盘上穿上轨翻多、下穿下轨翻空。"""
    c = b.c.values; v = sma(vidya_line(c, n, mom), 15); a = atr(b, 200); up = np.zeros(len(c), bool); st = False
    for i in range(len(c)):
        if not np.isnan(v[i]) and not np.isnan(a[i]):
            if c[i] > v[i] + band * a[i]: st = True
            elif c[i] < v[i] - band * a[i]: st = False
        up[i] = st
    return flip(b, up)


def jma_line(c, n=7, phase=50, power=2):
    beta = 0.45 * (n - 1) / (0.45 * (n - 1) + 2); al = beta ** power; pr = 0.5 if phase < -100 else (2.5 if phase > 100 else phase / 100 + 1.5)
    e0 = e1 = e2 = 0.0; j = c[0]; out = np.empty(len(c))
    for i in range(len(c)):
        e0 = (1 - al) * c[i] + al * e0; e1 = (c[i] - e0) * (1 - beta) + beta * e1
        e2 = (e0 + pr * e1 - j) * (1 - al) ** 2 + al * al * e2; j = e2 + j; out[i] = j
    return out


def jma_tr(b, n=50):
    j = jma_line(b.c.values, n); return flip(b, j > shift(j, 1))


def coral_line(c, sm=21, cd=0.4):
    di = (sm - 1) / 2 + 1; c1 = 2 / (di + 1); c2 = 1 - c1
    c3 = 3 * (cd ** 2 + cd ** 3); c4 = -3 * (2 * cd ** 2 + cd + cd ** 3); c5 = 3 * cd + 1 + cd ** 3 + 3 * cd ** 2
    i = [np.zeros(len(c)) for _ in range(6)]; prev = [c[0]] * 6
    for t in range(len(c)):
        x = c[t]
        for k in range(6):
            prev[k] = c1 * x + c2 * prev[k]; i[k][t] = prev[k]; x = prev[k]
    return -cd ** 3 * i[5] + c3 * i[4] + c4 * i[3] + c5 * i[2]


def coral_tr(b, sm=21):
    f = coral_line(b.c.values, sm); return flip(b, f > shift(f, 1))


def linreg_slope(c, n):
    x = np.arange(n) - (n - 1) / 2; w = x / (x * x).sum()
    out = np.full(len(c), np.nan); out[n - 1:] = np.convolve(c, w[::-1], mode="valid"); return out


def lrs_tr(b, n=50, sig_n=9):
    """线性回归斜率上穿其 EMA(sig_n) 且 >0 翻多。"""
    s = linreg_slope(b.c.values, n); e = ema(np.nan_to_num(s), sig_n); return flip(b, (s > 0) & (s > e))


def coppock_tr(b, r1=14, r2=11, w=10):
    c = b.c.values; roc = lambda n: (c / shift(c, n) - 1) * 100
    cp = wma(np.nan_to_num(roc(r1) + roc(r2)), w); return flip(b, cp > 0)


def alma_tr(b, f=20, s=50):
    c = b.c.values; return flip(b, alma(c, f) > alma(c, s))


def mfi(b, n=14):
    tp = (b.h.values + b.l.values + b.c.values) / 3; mf = tp * b.v.values; d = np.diff(tp, prepend=tp[0])
    p = pd.Series(np.where(d > 0, mf, 0)).rolling(n).sum().values; m = pd.Series(np.where(d < 0, mf, 0)).rolling(n).sum().values
    return 100 - 100 / (1 + p / np.maximum(m, 1e-12))


def rmi_tr(b, pos=66, neg=30):
    """RMI Trend Sniper：(RSI14+MFI14)/2 > 66 且 EMA5 上行 → 多；< 30 且 EMA5 下行 → 空。"""
    c = b.c.values; x = (rsi(c, 14) + mfi(b, 14)) / 2; e = ema(c, 5)
    up = np.zeros(len(c), bool); st = False
    for i in range(1, len(c)):
        if x[i] > pos and e[i] > e[i - 1]: st = True
        elif x[i] < neg and e[i] < e[i - 1]: st = False
        up[i] = st
    return flip(b, up)


def avg_force(b, n=18, sm=6):
    """alexgrover Average Force：(收盘-区间低)/(区间高-区间低)-0.5 再 SMA 平滑，>0 多。"""
    h, l, c = b.h.values, b.l.values, b.c.values
    hh = pd.Series(h).rolling(n).max().values; ll = pd.Series(l).rolling(n).min().values
    return flip(b, sma((c - ll) / np.maximum(hh - ll, 1e-12) - 0.5, sm) > 0)


def kangaroo(b, vol_k=1.5, rr=2.0, trend=True):
    """袋鼠尾：长影线≥K线2/3、K线幅度≥1.5ATR、成交量≥vol_k×20均量、收盘回到前一根区间内；顺 EMA200。"""
    o, h, l, c, v = b.o.values, b.h.values, b.l.values, b.c.values, b.v.values; a = atr(b); rng = h - l
    lw = np.minimum(o, c) - l; uw = h - np.maximum(o, c); big = (rng >= 1.5 * a) & (v >= vol_k * sma(v, 20))
    L = big & (lw >= 2 / 3 * rng) & (c > shift(l, 1)) & (c < shift(h, 1)); S = big & (uw >= 2 / 3 * rng) & (c < shift(h, 1)) & (c > shift(l, 1))
    if trend: e = ema(c, 200); L &= c > e; S &= c < e
    sig = np.where(L, 1, np.where(S, -1, 0)); stop = np.where(sig == 1, l - 0.1 * a, np.where(sig == -1, h + 0.1 * a, np.nan))
    return dict(sig=sig, stop=stop, tgt=c + sig * rr * np.abs(c - stop))


def inst_brk(b, piv=180, rr=1.0, exit_mode="rr"):
    """VXtM09TpWvE 机构级突破：pivothigh/low(piv,piv) 确认的最近摆动高点首次被收盘突破 → 多；止损最近摆动低点-缓冲；1:1。
    piv=180 时摆动点要 180 根后才确认(和他的 Pine 一样有这个滞后)。"""
    h, l, c = b.h.values, b.l.values, b.c.values; a = atr(b); ph, pl = pivots(h, l, piv, piv)
    L = (c > ph) & (shift(c, 1) <= shift(ph, 1)); S = (c < pl) & (shift(c, 1) >= shift(pl, 1))
    sig = np.where(L, 1, np.where(S, -1, 0)); stop = np.where(sig == 1, pl - 0.2 * a, np.where(sig == -1, ph + 0.2 * a, np.nan))
    d = dict(sig=sig, stop=stop)
    if exit_mode == "rr": d["tgt"] = c + sig * rr * np.abs(c - stop)
    else: d["trail_atr"] = 3.0
    return d


def ema3_score(b, need=8, rr=2.0, exit_mode="rr"):
    """TdudXZ-S0i0 15分钟大趋势：EMA21>50>200 + 50<RSI<70 + K线动能五项评分(实体≥60%/最长影线≤30%/量≥1.5倍/顺200EMA/振幅>0.7ATR，每项2分)≥8。
    视频没讲出场：止损 1.5ATR，2R 或 3ATR 追踪。"""
    o, h, l, c, v = b.o.values, b.h.values, b.l.values, b.c.values, b.v.values; a = atr(b); rng = np.maximum(h - l, 1e-12)
    e21, e50, e200 = ema(c, 21), ema(c, 50), ema(c, 200); r = rsi(c, 14)
    body = np.abs(c - o) / rng; wick = np.maximum(h - np.maximum(o, c), np.minimum(o, c) - l) / rng
    base = 2 * ((body >= 0.6).astype(int) + (wick <= 0.3).astype(int) + (v > 1.5 * sma(v, 20)).astype(int) + (rng > 0.7 * a).astype(int))
    L = (e21 > e50) & (e50 > e200) & (r > 50) & (r < 70) & (c > o) & (base + 2 * (c > e200) >= need)
    S = (e21 < e50) & (e50 < e200) & (r < 50) & (r > 30) & (c < o) & (base + 2 * (c < e200) >= need)
    sig = np.where(L, 1, np.where(S, -1, 0)); stop = np.where(sig != 0, c - sig * 1.5 * a, np.nan)
    d = dict(sig=sig, stop=stop)
    if exit_mode == "rr": d["tgt"] = c + sig * rr * 1.5 * a
    else: d["trail_atr"] = 3.0
    return d


STRATS = [("vidya", vidya_tr, {}), ("jma50", jma_tr, dict(n=50)), ("jma21", jma_tr, dict(n=21)), ("coral21", coral_tr, dict(sm=21)),
          ("coral50", coral_tr, dict(sm=50)), ("lrs50", lrs_tr, dict(n=50)), ("coppock", coppock_tr, {}), ("alma20_50", alma_tr, {}),
          ("rmi", rmi_tr, {}), ("avgforce", avg_force, {})]

if __name__ == "__main__":
    for (name, fn, kw), tf in itertools.product(STRATS, ("30m", "1h", "4h", "8h", "1d")):
        rec(name, tf, run(fn, tf, **kw), **kw)
    for tf, vk, tr_ in itertools.product(("1h", "4h", "1d"), (1.5, 2.0), (True, False)):
        rec("kangaroo", tf, run(kangaroo, tf, vol_k=vk, trend=tr_), vk=vk, trend=tr_)
    for tf, piv, rr, ex in itertools.product(("15m", "1h", "4h"), (30, 180), (1.0, 2.0), ("rr", "trail")):
        if ex == "trail" and rr == 2.0: continue
        rec("inst_brk", tf, run(inst_brk, tf, piv=piv, rr=rr, exit_mode=ex), piv=piv, rr=rr, ex=ex)
    for tf, ex in itertools.product(("15m", "1h", "4h"), ("rr", "trail")):
        rec("ema3_score", tf, run(ema3_score, tf, exit_mode=ex), ex=ex)
    pd.DataFrame(ROWS).to_csv(os.path.join(HERE, "b3_yj.csv"), index=False)
