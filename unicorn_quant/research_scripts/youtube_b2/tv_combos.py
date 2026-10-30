"""第二批博主里的 TradingView 指标组合(2026-10-30)，同 speculation_channel/lib 口径。
Trader小Q J3jBN_qzi1Q 加密参数(RSI9>70 + MACD(8,21,5)金叉 + 回踩3倍布林中/下轨) / Trader小Q Twin Range Filter+Supertrend+放量、Donchian+B-Xtrender /
Yuchi Halftrend+RSI / 交易聰明錢 Supertrend+KD / 威廉 吞没4分战法(吞没+均线多头+MACD转强+放量)。
Supertrend+DEMA、SSL、QQE、Impulse MACD 与之前已测同类，不重复。"""
import os, sys, itertools, numpy as np, pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "speculation_channel")); sys.path.insert(0, os.path.join(HERE, "..", "youtube_4ch"))
from lib import *
from batch01 import shift
from yitai_hist import supertrend, run


def xiaoq(b, rr=2.0, exit_mode="rr"):
    h, l, c = b.h.values, b.l.values, b.c.values
    r = rsi(c, 9); m, g, _ = macd(c, 8, 21, 5); mid = sma(c, 20); a = atr(b)
    up = (m > g) & (shift(m, 1) <= shift(g, 1)); dn = (m < g) & (shift(m, 1) >= shift(g, 1))
    L = (r > 70) & up & (l <= mid); S = (r < 30) & dn & (h >= mid)
    sig = np.where(L, 1, np.where(S, -1, 0)); stop = c - sig * 1.5 * a
    if exit_mode == "rr":
        return dict(sig=sig, stop=np.where(sig != 0, stop, np.nan), tgt=c + sig * rr * 1.5 * a)
    return dict(sig=sig, stop=np.where(sig != 0, stop, np.nan), exit_sig=np.where(dn, 1, np.where(up, -1, 0)))


def _smrng(x, t, m):
    return ema(ema(np.abs(np.diff(x, prepend=x[0])), t), 2 * t - 1) * m


def twin_range(b, vol_k=1.0):
    c, v = b.c.values, b.v.values
    r = (_smrng(c, 27, 1.6) + _smrng(c, 55, 2.0)) / 2
    N = len(c); f = c.copy(); up = np.zeros(N); dn = np.zeros(N)
    for i in range(1, N):
        p = f[i - 1]
        f[i] = (p if c[i] - r[i] < p else c[i] - r[i]) if c[i] > p else (p if c[i] + r[i] > p else c[i] + r[i])
        up[i] = up[i - 1] + 1 if f[i] > p else (0 if f[i] < p else up[i - 1])
        dn[i] = dn[i - 1] + 1 if f[i] < p else (0 if f[i] > p else dn[i - 1])
    lc = (c > f) & (up > 0); sc = (c < f) & (dn > 0)
    lc_first = lc & ~shift(lc, 1).astype(bool); sc_first = sc & ~shift(sc, 1).astype(bool)
    d, line = supertrend(b, 10, 3.0); vok = v > vol_k * sma(v, 20)
    L = lc_first & (d == 1) & vok; S = sc_first & (d == -1) & vok
    sig = np.where(L, 1, np.where(S, -1, 0))
    return dict(sig=sig, stop=np.where(sig != 0, line, np.nan), exit_sig=np.where(sc_first | (d == -1), 1, np.where(lc_first | (d == 1), -1, 0)))


def bxtrender(b, dn_n=20):
    h, l, c = b.h.values, b.l.values, b.c.values
    st = rsi(ema(c, 5) - ema(c, 20), 15) - 50; lt = rsi(ema(c, 20), 15) - 50
    hi = shift(pd.Series(h).rolling(dn_n).max().values, 1); lo = shift(pd.Series(l).rolling(dn_n).min().values, 1)
    L = (lt > 0) & (st > 0) & (st > shift(st, 1)) & (c > hi); S = (lt < 0) & (st < 0) & (st < shift(st, 1)) & (c < lo)
    sig = np.where(L, 1, np.where(S, -1, 0)); a = atr(b)
    lo10 = pd.Series(l).rolling(10).min().values; hi10 = pd.Series(h).rolling(10).max().values
    return dict(sig=sig, stop=np.where(L, lo10 - 0.1 * a, np.where(S, hi10 + 0.1 * a, np.nan)),
                exit_sig=np.where((st < 0) | (c < shift(lo10, 1)), 1, np.where((st > 0) | (c > shift(hi10, 1)), -1, 0)))


def halftrend(b, amp=2, dev=2.0):
    h, l, c = b.h.values, b.l.values, b.c.values; N = len(c); a = atr(b, 100) / 2 * dev
    hp = pd.Series(h).rolling(amp).max().values; lp = pd.Series(l).rolling(amp).min().values
    hm = sma(h, amp); lm = sma(l, amp)
    trend = np.zeros(N, int); nxt = 0; maxl = l[0]; minh = h[0]; up = np.full(N, np.nan); dn = np.full(N, np.nan); ht = np.full(N, np.nan)
    for i in range(amp, N):
        if nxt == 1:
            maxl = max(lp[i], maxl)
            if hm[i] < maxl and c[i] < l[i - 1]: trend[i] = 1; nxt = 0; minh = hp[i]
            else: trend[i] = trend[i - 1]
        else:
            minh = min(hp[i], minh)
            if lm[i] > minh and c[i] > h[i - 1]: trend[i] = 0; nxt = 1; maxl = lp[i]
            else: trend[i] = trend[i - 1]
        if trend[i] == 0:
            up[i] = maxl if (trend[i - 1] != 0 or np.isnan(up[i - 1])) else max(maxl, up[i - 1]); ht[i] = up[i]
        else:
            dn[i] = minh if (trend[i - 1] != 1 or np.isnan(dn[i - 1])) else min(minh, dn[i - 1]); ht[i] = dn[i]
    return trend, ht, a


def ht_rsi(b, rl=50):
    c = b.c.values; tr, ht, a = halftrend(b); r = rsi(c, 14)
    fu = (tr == 0) & (shift(tr, 1) == 1); fd = (tr == 1) & (shift(tr, 1) == 0)
    L = fu & (r > rl); S = fd & (r < 100 - rl); sig = np.where(L, 1, np.where(S, -1, 0))
    return dict(sig=sig, stop=np.where(L, ht - a, np.where(S, ht + a, np.nan)), exit_sig=np.where(fd, 1, np.where(fu, -1, 0)))


def st_kd(b, rr=2.0):
    h, l, c = b.h.values, b.l.values, b.c.values
    d, line = supertrend(b, 10, 3.0)
    ll = pd.Series(l).rolling(14).min().values; hh = pd.Series(h).rolling(14).max().values
    k = sma(100 * (c - ll) / np.maximum(hh - ll, 1e-12), 3); dd = sma(k, 3)
    L = (d == 1) & (k > dd) & (shift(k, 1) <= shift(dd, 1)) & (shift(k, 1) < 20)
    S = (d == -1) & (k < dd) & (shift(k, 1) >= shift(dd, 1)) & (shift(k, 1) > 80)
    sig = np.where(L, 1, np.where(S, -1, 0)); stop = np.where(sig != 0, line, np.nan)
    return dict(sig=sig, stop=stop, tgt=c + sig * rr * np.abs(c - stop))


def engulf4(b, rr=2.0, need=4):
    o, h, l, c, v = b.o.values, b.h.values, b.l.values, b.c.values, b.v.values
    e20, e60 = ema(c, 20), ema(c, 60); _, _, hist = macd(c); a = atr(b)
    bull = (c > o) & (shift(c, 1) < shift(o, 1)) & (c >= shift(o, 1)) & (o <= shift(c, 1))
    bear = (c < o) & (shift(c, 1) > shift(o, 1)) & (c <= shift(o, 1)) & (o >= shift(c, 1))
    vok = v > 1.2 * sma(v, 20)
    scL = 1 + ((e20 > e60) & (l <= e20 * 1.01)).astype(int) + (hist > shift(hist, 1)).astype(int) + vok.astype(int)
    scS = 1 + ((e20 < e60) & (h >= e20 * 0.99)).astype(int) + (hist < shift(hist, 1)).astype(int) + vok.astype(int)
    L = bull & (scL >= need); S = bear & (scS >= need); sig = np.where(L, 1, np.where(S, -1, 0))
    stop = np.where(L, np.minimum(l, shift(l, 1)) - 0.1 * a, np.where(S, np.maximum(h, shift(h, 1)) + 0.1 * a, np.nan))
    return dict(sig=sig, stop=stop, tgt=c + sig * rr * np.abs(c - stop))


if __name__ == "__main__":
    rows = []
    def rec(name, tf, tr, **kw):
        d = summarize(tr); d.update(name=name, tf=tf, kw=str(kw)); rows.append(d)
        print(f"{name:10s} {tf:4s} {str(kw):24s} n={d.get('n')} win={d.get('win')} R0={d.get('r_gross')} Rnet={d.get('r_net')} "
              f"is={d.get('r_is')} oos={d.get('r_oos')} cagr={d.get('cagr')} mdd={d.get('mdd')}", flush=True)
    for tf, ex in itertools.product(("1h", "4h", "1d"), ("rr", "macd")):
        rec("xiaoq", tf, run(xiaoq, tf, exit_mode=ex), ex=ex)
    for tf, vk in itertools.product(("1h", "4h", "1d"), (1.0, 1.5)):
        rec("twin_range", tf, run(twin_range, tf, vol_k=vk), vol_k=vk)
    for tf, n in itertools.product(("1h", "4h", "1d"), (20, 55)):
        rec("bxtrender", tf, run(bxtrender, tf, dn_n=n), n=n)
    for tf, rl in itertools.product(("1h", "4h", "1d"), (50, 55)):
        rec("ht_rsi", tf, run(ht_rsi, tf, rl=rl), rl=rl)
    for tf, rr in itertools.product(("1h", "4h", "1d"), (1.5, 2.0, 3.0)):
        rec("st_kd", tf, run(st_kd, tf, rr=rr), rr=rr)
    for tf, need, rr in itertools.product(("1h", "4h", "1d"), (3, 4), (2.0, 3.0)):
        rec("engulf4", tf, run(engulf4, tf, need=need, rr=rr), need=need, rr=rr)
    pd.DataFrame(rows).to_csv(os.path.join(HERE, "tv_combos.csv"), index=False)
