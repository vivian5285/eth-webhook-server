"""第五批·指標與策略 频道往期(每期都有"交易规则"段，按原文实现)，同 b6 口径，跨周期。
  kc2      8FwaLBJ8Uss  双肯特纳(50, 2.75/3.75ATR)：收盘先跌破外轨(3.75)=基础信号 → 第一根收回内轨(2.75)上且收阳 → 收盘做多，止损近期低点，1:1(ETH 1h 说+521%)
  hma_kdj  tqUxGBKRQVk  HMA>EMA200 + 实体完全在 HMA 上且 HMA 向上 + 收阳 + KDJ(9) K>80 且比上一根高 → 收盘做多，止损 HMA(或近期低)，1:1(EURUSD 2h)
  cci2     PFYA-WSwWh8  CCI(18) 与其 54 周期均线：快线到 +200 上方后下穿慢线=做空，到 -200 下方后上穿=做多(多空对翻)，止损 6×ATR(108)，反向信号平仓(3h 说+626%)
  ce_rsi   0SRBFMnMkR4  吊灯止损(CE)翻多=多头 + RSI(14) 上穿其均线且 RSI>50 → 收盘做多，止损上一根低点，1:1；趋势内再次上穿=回调加单(ETH 4h)
  macd_sar BaZ3I9Za86w  收盘在 EMA200 上 + MACD 金叉(柱子转正) + SAR 在K线下方 → 收盘做多，止损 SAR，1:1(ETH 30m)
"""
import os, sys, itertools, numpy as np, pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
from b6 import *
from b6 import kd
sys.path.insert(0, os.path.join(HERE, "..", "youtube_4ch"))
from yitai_hist import hma


def _rr(sig, stop, c, rr):
    ok = (sig != 0) & ((stop - c) * sig < 0); sig = np.where(ok, sig, 0); stop = np.where(ok, stop, np.nan)
    return dict(sig=sig, stop=stop, tgt=c + sig * rr * np.abs(c - stop))


def kc2(b, n=50, inner=2.75, outer=3.75, look=20, rr=1.0, side=0):
    o, h, l, c = b.o.values, b.h.values, b.l.values, b.c.values; a = atr(b); N = len(c)
    m = ema(c, n); ar = atr(b, n); lo5 = pd.Series(l).rolling(10).min().values; hi5 = pd.Series(h).rolling(10).max().values
    sig = np.zeros(N); stop = np.full(N, np.nan); armL = armS = -10**9
    for i in range(n, N):
        if c[i] < m[i] - outer * ar[i]: armL = i
        if c[i] > m[i] + outer * ar[i]: armS = i
        if side >= 0 and 0 < i - armL <= look and c[i] > m[i] - inner * ar[i] and c[i] > o[i]:
            sig[i] = 1; stop[i] = lo5[i] - 0.1 * a[i]; armL = -10**9
        elif side <= 0 and 0 < i - armS <= look and c[i] < m[i] + inner * ar[i] and c[i] < o[i]:
            sig[i] = -1; stop[i] = hi5[i] + 0.1 * a[i]; armS = -10**9
    return _rr(sig, stop, c, rr)


def hma_kdj(b, hn=55, rr=1.0, side=0):
    o, h, l, c = b.o.values, b.h.values, b.l.values, b.c.values; a = atr(b)
    H = hma(c, hn); e = ema(c, 200); K = kd(b, 9); J = 3 * K - 2 * pd.Series(K).ewm(alpha=1 / 3, adjust=False).mean().values
    up = H > shift(H, 1); dn = H < shift(H, 1); lo5 = pd.Series(l).rolling(5).min().values; hi5 = pd.Series(h).rolling(5).max().values
    L = (H > e) & up & (np.minimum(o, c) > H) & (c > o) & (J > 80) & (J > shift(J, 1)) & (side >= 0)
    S = (H < e) & dn & (np.maximum(o, c) < H) & (c < o) & (J < 20) & (J < shift(J, 1)) & (side <= 0)
    sig = np.where(L, 1, np.where(S, -1, 0))
    stop = np.where(sig == 1, np.where(l > H, H, lo5) - 0.1 * a, np.where(sig == -1, np.where(h < H, H, hi5) + 0.1 * a, np.nan))
    return _rr(sig, stop, c, rr)


def cci2(b, fast=18, slow=54, lvl=200, atr_n=108, k=6.0):
    h, l, c = b.h.values, b.l.values, b.c.values; tp = (h + l + c) / 3; N = len(c)
    ma = pd.Series(tp).rolling(fast).mean().values; md = pd.Series(tp).rolling(fast).apply(lambda w: np.mean(np.abs(w - w.mean())), raw=True).values
    cci = (tp - ma) / (0.015 * md); sl = sma(cci, slow); A = atr(b, atr_n)
    sig = np.zeros(N); armL = armS = False
    for i in range(slow + fast, N):
        if cci[i] > lvl: armS = True
        if cci[i] < -lvl: armL = True
        if armS and cci[i] < sl[i] and cci[i - 1] >= sl[i - 1]: sig[i] = -1; armS = False
        elif armL and cci[i] > sl[i] and cci[i - 1] <= sl[i - 1]: sig[i] = 1; armL = False
    stop = np.where(sig != 0, c - sig * k * A, np.nan)
    return dict(sig=sig, stop=stop, exit_sig=np.where(sig == -1, 1, np.where(sig == 1, -1, 0)))


def chandelier(b, n=22, m=3.0):
    h, l, c = b.h.values, b.l.values, b.c.values; a = atr(b, n); N = len(c)
    ls = pd.Series(h).rolling(n).max().values - m * a; ss = pd.Series(l).rolling(n).min().values + m * a
    d = np.ones(N, int); lsp, ssp = ls.copy(), ss.copy()
    for i in range(1, N):
        if not np.isnan(ls[i - 1]):
            lsp[i] = max(ls[i], lsp[i - 1]) if c[i - 1] > lsp[i - 1] else ls[i]
            ssp[i] = min(ss[i], ssp[i - 1]) if c[i - 1] < ssp[i - 1] else ss[i]
        d[i] = 1 if c[i] > ssp[i - 1] else (-1 if c[i] < lsp[i - 1] else d[i - 1])
    return d


def ce_rsi(b, rn=14, ma_n=14, rr=1.0, side=0):
    h, l, c = b.h.values, b.l.values, b.c.values; a = atr(b)
    d = chandelier(b); r = rsi(c, rn); rm = sma(r, ma_n)
    xu = (r > rm) & (shift(r, 1) <= shift(rm, 1)); xd = (r < rm) & (shift(r, 1) >= shift(rm, 1))
    L = (d == 1) & xu & (r > 50) & (side >= 0); S = (d == -1) & xd & (r < 50) & (side <= 0)
    sig = np.where(L, 1, np.where(S, -1, 0)); stop = np.where(sig == 1, shift(l, 1) - 0.1 * a, np.where(sig == -1, shift(h, 1) + 0.1 * a, np.nan))
    return _rr(sig, stop, c, rr)


def psar(b, step=0.02, mx=0.2):
    h, l = b.h.values, b.l.values; N = len(h); s = np.zeros(N); up = True; ep = h[0]; af = step; s[0] = l[0]
    for i in range(1, N):
        s[i] = s[i - 1] + af * (ep - s[i - 1])
        if up:
            s[i] = min(s[i], l[i - 1], l[i - 2] if i > 1 else l[i - 1])
            if l[i] < s[i]: up = False; s[i] = ep; ep = l[i]; af = step
            elif h[i] > ep: ep = h[i]; af = min(af + step, mx)
        else:
            s[i] = max(s[i], h[i - 1], h[i - 2] if i > 1 else h[i - 1])
            if h[i] > s[i]: up = True; s[i] = ep; ep = h[i]; af = step
            elif l[i] < ep: ep = l[i]; af = min(af + step, mx)
    return s


def macd_sar(b, rr=1.0, side=0):
    c = b.c.values; e = ema(c, 200); m, g, hist = macd(c); s = psar(b)
    xu = (hist > 0) & (shift(hist, 1) <= 0); xd = (hist < 0) & (shift(hist, 1) >= 0)
    L = (c > e) & xu & (s < c) & (side >= 0); S = (c < e) & xd & (s > c) & (side <= 0)
    sig = np.where(L, 1, np.where(S, -1, 0)); stop = np.where(sig != 0, s, np.nan)
    return _rr(sig, stop, c, rr)


GRID7 = (
    [("kc2", kc2, tf, dict(inner=i_, outer=o_, rr=rr)) for tf, (i_, o_), rr in itertools.product(("1h", "4h", "8h", "1d"), ((2.75, 3.75), (2.0, 3.0)), (1.0, 2.0))]
    + [("hma_kdj", hma_kdj, tf, dict(hn=hn, rr=rr)) for tf, hn, rr in itertools.product(("1h", "2h", "4h", "8h"), (21, 55), (1.0, 2.0))]
    + [("cci2", cci2, tf, dict(fast=f, slow=s_, lvl=lv)) for tf, (f, s_), lv in itertools.product(("1h", "2h", "4h", "8h", "1d"), ((18, 54), (14, 42)), (150, 200))]
    + [("ce_rsi", ce_rsi, tf, dict(rr=rr, side=sd)) for tf, rr, sd in itertools.product(("1h", "4h", "8h", "1d"), (1.0, 2.0), (0, 1))]
    + [("macd_sar", macd_sar, tf, dict(rr=rr)) for tf, rr in itertools.product(("30m", "1h", "4h", "8h"), (1.0, 2.0))]
)

if __name__ == "__main__":
    rows = []
    for name, fn, tf, kw in GRID7:
        try:
            r = dict(name=name, tf=tf, kw=str(kw), **summarize(run(fn, tf, **kw)))
        except Exception as e:
            r = dict(name=name, tf=tf, kw=str(kw), err=f"{type(e).__name__}: {e}")
        rows.append(r); print(r, flush=True)
    pd.DataFrame(rows).to_csv(os.path.join(HERE, "b7.csv"), index=False)
