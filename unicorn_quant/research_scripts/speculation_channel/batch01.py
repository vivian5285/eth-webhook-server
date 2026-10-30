"""批次1 (2022-04..06): 半神MACD连续背离 / Vegas隧道 / N-MACD+RSI+MA13 / Rob Hoffman IRB。"""
import itertools, pandas as pd, numpy as np
from lib import *


def shift(x, k=1):
    return np.r_[np.full(k, np.nan), x[:-k]]


# ---- 1 半神MACD: 零轴下 2~3 个连续变浅的柱子波谷 + 价格低点连续走低 → 柱子转虚(回升)那根进场 ----
def banshen(b, k=3, diff=0.2, rr=2.0):
    c, h, l = b.c.values, b.h.values, b.l.values
    _, _, hist = macd(c, 13, 34, 9); a = atr(b, 13)
    n = len(c); sig = np.zeros(n); stop = np.full(n, np.nan); tgt = np.full(n, np.nan)
    tl, th = [], []  # (柱值, 价格极值)
    for i in range(3, n):
        if hist[i] >= 0: tl = []
        if hist[i] <= 0: th = []
        j = i - 1
        if hist[j] < 0 and hist[j] < hist[j - 1] and hist[i] > hist[j]:
            tl.append((hist[j], l[max(0, j - 3):j + 2].min()))
            if len(tl) >= k:
                w = tl[-k:]
                if all(abs(w[m + 1][0]) <= (1 - diff) * abs(w[m][0]) and w[m + 1][1] < w[m][1] for m in range(k - 1)):
                    sig[i] = 1; stop[i] = l[i] - a[i]; tgt[i] = c[i] + rr * (c[i] - stop[i]); tl = []
        if hist[j] > 0 and hist[j] > hist[j - 1] and hist[i] < hist[j]:
            th.append((hist[j], h[max(0, j - 3):j + 2].max()))
            if len(th) >= k:
                w = th[-k:]
                if all(abs(w[m + 1][0]) <= (1 - diff) * abs(w[m][0]) and w[m + 1][1] > w[m][1] for m in range(k - 1)):
                    sig[i] = -1; stop[i] = h[i] + a[i]; tgt[i] = c[i] - rr * (stop[i] - c[i]); th = []
    return dict(sig=sig, stop=stop, tgt=tgt)


# ---- 2 Vegas 隧道 ----
def vegas(b, stop_mode="tunnel", exit_mode="rr", rr=2.0, max_per=2):
    c, h, l = b.c.values, b.h.values, b.l.values
    e12, e144, e169, e576, e676 = (ema(c, n) for n in (12, 144, 169, 576, 676)); a = atr(b)
    top = np.maximum(e144, e169); bot = np.minimum(e144, e169); big_t = np.maximum(e576, e676); big_b = np.minimum(e576, e676)
    up = (bot > big_t) & (e12 > top); dn = (top < big_b) & (e12 < bot)
    n = len(c); sig = np.zeros(n); stop = np.full(n, np.nan); tgt = np.full(n, np.nan); ex = np.zeros(n)
    cnt = 0; reg = 0
    for i in range(700, n):
        r = 1 if up[i] else (-1 if dn[i] else 0)
        if r != reg: reg = r; cnt = 0
        if r == 1 and l[i] <= top[i] and l[i - 1] > top[i - 1] and c[i] > bot[i] and cnt < max_per:
            sig[i] = 1; cnt += 1
            stop[i] = (bot[i] if stop_mode == "tunnel" else big_b[i]) - 0.5 * a[i]
            if stop[i] >= c[i]: sig[i] = 0; cnt -= 1; continue
            tgt[i] = c[i] + rr * (c[i] - stop[i]) if exit_mode == "rr" else np.nan
        elif r == -1 and h[i] >= bot[i] and h[i - 1] < bot[i - 1] and c[i] < top[i] and cnt < max_per:
            sig[i] = -1; cnt += 1
            stop[i] = (top[i] if stop_mode == "tunnel" else big_t[i]) + 0.5 * a[i]
            if stop[i] <= c[i]: sig[i] = 0; cnt -= 1; continue
            tgt[i] = c[i] - rr * (stop[i] - c[i]) if exit_mode == "rr" else np.nan
        if exit_mode == "trend":
            ex[i] = 1 if e144[i] < big_b[i] else (-1 if e144[i] > big_t[i] else 0)
    return dict(sig=sig, stop=stop, tgt=tgt, exit_sig=ex)


# ---- 3 N-MACD 金叉 + RSI(21) 上穿其 SMA55 + 收在 MA13 上 ----
def nmacd(b, rr=2.0, look=10):
    c, h, l = b.c.values, b.h.values, b.l.values
    m, g, _ = macd(c, 13, 26, 9)
    # Normalized MACD: 用近 50 根 MACD 的极差归一(只影响刻度，不影响金叉)
    r = rsi(c, 21); rs = sma(r, 55); ma = sma(c, 13)
    xu = (r > rs) & (shift(r) <= shift(rs)); xd = (r < rs) & (shift(r) >= shift(rs))
    sig = np.where(xu & (m > g) & (c > ma), 1, np.where(xd & (m < g) & (c < ma), -1, 0))
    lo = pd.Series(l).rolling(look).min().values; hi = pd.Series(h).rolling(look).max().values
    stop = np.where(sig == 1, lo, hi)
    tgt = np.where(sig == 1, c + rr * (c - lo), c - rr * (hi - c))
    return dict(sig=sig, stop=stop, tgt=tgt)


# ---- 5 Rob Hoffman IRB ----
def hoffman(b, rr=2.0, entry="close", stop_mode="fast", max_per=2):
    c, o, h, l = b.c.values, b.o.values, b.h.values, b.l.values
    f = sma(c, 5); s = ema(c, 20); whites = np.vstack([sma(c, 50), sma(c, 89), ema(c, 144), ema(c, 35)])
    wmax, wmin = whites.max(0), whites.min(0); a = atr(b)
    up = (f > s) & (s > wmax); dn = (f < s) & (s < wmin)
    rng = np.maximum(h - l, 1e-12)
    irb_l = up & ((h - np.maximum(o, c)) >= 0.45 * rng) & (l > s)
    irb_s = dn & ((np.minimum(o, c) - l) >= 0.45 * rng) & (h < s)
    n = len(c); sig = np.zeros(n); stop = np.full(n, np.nan); tgt = np.full(n, np.nan)
    reg = 0; cnt = 0
    for i in range(200, n - 1):
        r = 1 if up[i] else (-1 if dn[i] else 0)
        if r != reg: reg = r; cnt = 0
        if cnt >= max_per: continue
        for side, ok in ((1, irb_l), (-1, irb_s)):
            k = i if entry == "close" else i - 1
            if not ok[k]: continue
            if entry == "break" and not ((side == 1 and h[i] > h[k]) or (side == -1 and l[i] < l[k])): continue
            st = (s[i] if stop_mode == "fast" else (wmin[i] if side == 1 else wmax[i])) - side * 0.2 * a[i]
            if (c[i] - st) * side <= 0: continue
            sig[i] = side; stop[i] = st; tgt[i] = c[i] + side * rr * abs(c[i] - st); cnt += 1
    return dict(sig=sig, stop=stop, tgt=tgt)


if __name__ == "__main__":
    rows = []
    def go(name, fn, tf, **kw):
        s = summarize(run_all(fn, tf, **kw)); s.update(strategy=name, tf=tf, **{k: str(v) for k, v in kw.items()}); rows.append(s)
        print(name, tf, kw, {k: s.get(k) for k in ("n", "win", "r_gross", "r_net", "r_is", "r_oos", "cagr", "mdd", "risk_med")}, flush=True)
    for tf in ("15m", "1h", "4h"):
        for k, d, rr in itertools.product((2, 3), (0.2, 0.4), (1.0, 2.0, 3.0)):
            go("半神MACD", banshen, tf, k=k, diff=d, rr=rr)
        for sm, em, rr in [("tunnel", "rr", 2.0), ("tunnel", "rr", 3.0), ("big", "rr", 2.0), ("big", "rr", 3.0), ("big", "trend", 0), ("tunnel", "trend", 0)]:
            go("Vegas", vegas, tf, stop_mode=sm, exit_mode=em, rr=rr)
        for rr, lk in itertools.product((1.0, 1.5, 2.0, 3.0), (5, 10)):
            go("NMACD+RSI+MA13", nmacd, tf, rr=rr, look=lk)
        for rr, en, sm in itertools.product((1.0, 2.0, 3.0), ("close", "break"), ("fast", "white")):
            go("Hoffman IRB", hoffman, tf, rr=rr, entry=en, stop_mode=sm)
    pd.DataFrame(rows).to_csv("batch01.csv", index=False)
