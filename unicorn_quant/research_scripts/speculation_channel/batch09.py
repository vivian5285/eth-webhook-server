"""批次9 (2024-05..08): Stamatoudis 决定性转折(跳空脱离区间次日买) / 供需区+Range Filter+WAE / 川沐 V字针底。"""
import itertools, pandas as pd, numpy as np
from lib import *
from batch01 import shift
from batch06 import stock_bars, CRYPTO


# ---- 41 Episodic pivot: 跳空≥gap 且收在前 N 日区间上方 → 次日开盘买, 止损跳空日低点, 收盘破 EMA(n) 离场 ----
def ep(b, gap=0.10, cons=40, exit_ema=10):
    o, h, l, c = b.o.values, b.h.values, b.l.values, b.c.values
    top = pd.Series(h).rolling(cons).max().shift(1).values
    sig = np.where((o / shift(c) - 1 >= gap) & (c > top) & (c > o * 0.98), 1, 0)
    e = ema(c, exit_ema)
    return dict(sig=sig, stop=l, exit_sig=np.where(c < e, 1, 0))


# ---- 43 Range Filter(Guikroth) + Waddah Attar Explosion + 需求/供给区 ----
def range_filter(c, per=100, mult=2.5):
    wper = per * 2 - 1
    smr = ema(ema(np.abs(np.diff(c, prepend=c[0])), per), wper) * mult
    f = np.empty_like(c); f[0] = c[0]
    for i in range(1, len(c)):
        p = f[i - 1]
        f[i] = (p if c[i] - smr[i] < p else c[i] - smr[i]) if c[i] > p else (p if c[i] + smr[i] > p else c[i] + smr[i])
    up = np.zeros(len(c), bool); dn = np.zeros(len(c), bool); u = d = 0
    for i in range(1, len(c)):
        u = u + 1 if f[i] > f[i - 1] else (0 if f[i] < f[i - 1] else u)
        d = d + 1 if f[i] < f[i - 1] else (0 if f[i] > f[i - 1] else d)
        up[i] = u > 0; dn[i] = d > 0
    longc = (c > f) & up; shortc = (c < f) & dn
    st = np.zeros(len(c)); s = 0
    buy = np.zeros(len(c), bool); sell = np.zeros(len(c), bool)
    for i in range(len(c)):
        if longc[i] and s != 1: buy[i] = True; s = 1
        elif shortc[i] and s != -1: sell[i] = True; s = -1
    return buy, sell


def wae(c, sens=150):
    m = ema(c, 20) - ema(c, 40); t1 = (m - shift(m)) * sens
    bb = pd.Series(c).rolling(20); e1 = (2 * 2 * bb.std(ddof=0)).values
    return np.where(t1 > 0, t1, 0), np.where(t1 < 0, -t1, 0), e1


def sd_rf_wae(b, rr=2.0, k=0.8, max_age=300, zone_look=6):
    o, h, l, c = b.o.values, b.h.values, b.l.values, b.c.values
    a = atr(b); body = np.abs(c - o)
    buy, sell = range_filter(c); up, dn, e1 = wae(c)
    big_up = (c > o) & (body >= k * a); big_dn = (c < o) & (body >= k * a)
    n = len(c); sig = np.zeros(n); stop = np.full(n, np.nan); tgt = np.full(n, np.nan)
    zones = []; last_touch = {1: -10**9, -1: -10**9}; zb = {1: np.nan, -1: np.nan}
    for i in range(30, n):
        if big_up[i] and big_up[i - 1] and not big_up[i - 2]:
            j = i - 2; zones.append([1, h[j], l[j], i])
        if big_dn[i] and big_dn[i - 1] and not big_dn[i - 2]:
            j = i - 2; zones.append([-1, h[j], l[j], i])
        zones = [z for z in zones if i - z[3] <= max_age and not ((z[0] == 1 and c[i] < z[2]) or (z[0] == -1 and c[i] > z[1]))]
        for z in zones:
            if i - z[3] < 3: continue
            if z[0] == 1 and l[i] <= z[1]: last_touch[1] = i; zb[1] = z[2]
            if z[0] == -1 and h[i] >= z[2]: last_touch[-1] = i; zb[-1] = z[1]
        if buy[i] and i - last_touch[1] <= zone_look and up[i] > e1[i]:
            st = min(zb[1], l[i - zone_look:i + 1].min()) - 0.2 * a[i]
            if st < c[i]: sig[i] = 1; stop[i] = st; tgt[i] = c[i] + rr * (c[i] - st)
        elif sell[i] and i - last_touch[-1] <= zone_look and dn[i] > e1[i]:
            st = max(zb[-1], h[i - zone_look:i + 1].max()) + 0.2 * a[i]
            if st > c[i]: sig[i] = -1; stop[i] = st; tgt[i] = c[i] - rr * (st - c[i])
    return dict(sig=sig, stop=stop, tgt=tgt)


# ---- 45 川沐 V 字针底: 15m 连续5根累计跌≥2% 后连续2根累计涨≥1% → 做多 ----
def chuanmu(b, d5=0.02, u2=0.01, rr=2.0, max_bars=96):
    o, l, c = b.o.values, b.l.values, b.c.values; a = atr(b)
    dn5 = (shift(c, 2) / shift(c, 7) - 1 <= -d5) & (shift(c, 2) < shift(o, 2)) & (shift(c, 3) < shift(o, 3))
    up2 = (c / shift(c, 2) - 1 >= u2) & (c > o) & (shift(c) > shift(o))
    sig = np.where(dn5 & up2, 1, 0)
    lo = pd.Series(l).rolling(8).min().values
    stop = lo - 0.2 * a
    return dict(sig=sig, stop=stop, tgt=c + rr * (c - stop), max_bars=max_bars)


if __name__ == "__main__":
    rows = []
    def go(name, fn, tf, **kw):
        s = summarize(run_all(fn, tf, **kw)); s.update(strategy=name, tf=tf, **{k: str(v) for k, v in kw.items()}); rows.append(s)
        print(name, tf, kw, {k: s.get(k) for k in ("n", "win", "r_gross", "r_net", "r_is", "r_oos", "cagr", "mdd", "risk_med")}, flush=True)
    for tf in ("15m", "1h", "4h"):
        for rr, k in itertools.product((1.0, 2.0, 3.0), (0.6, 1.0)):
            go("供需+RangeFilter+WAE", sd_rf_wae, tf, rr=rr, k=k)
    for d5, u2, rr in itertools.product((0.02, 0.04), (0.01, 0.02), (1.0, 2.0, 3.0)):
        go("川沐V字针底", chuanmu, "15m", d5=d5, u2=u2, rr=rr)
    SB = stock_bars()
    for univ, U in (("美股78", {s: b for s, b in SB.items() if s not in CRYPTO}), ("币圈日线(2017起)", {s: b for s, b in SB.items() if s in CRYPTO})):
        for gap, ee in itertools.product((0.05, 0.10), (10, 20)):
            tr = pd.concat([backtest(b, **ep(b, gap=gap, exit_ema=ee)).assign(sym=s) for s, b in U.items()], ignore_index=True)
            s = summarize(tr); s.update(strategy="Stamatoudis决定性转折(EP)", tf="1d", univ=univ, gap=gap, exit_ema=ee); rows.append(s)
            print("EP", univ, gap, ee, {k: s.get(k) for k in ("n", "win", "r_gross", "r_net", "r_is", "r_oos", "cagr", "mdd")}, flush=True)
    pd.DataFrame(rows).to_csv("batch09.csv", index=False)
