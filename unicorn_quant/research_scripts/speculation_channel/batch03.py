"""批次3 (2022-10..12): Vegas隧道震荡区间 / 马丁格尔(欧易现货DCA) / 日枢轴点P回踩。"""
import itertools, pandas as pd, numpy as np
from lib import *
from batch01 import shift


# ---- 11 Vegas 隧道当中轴的震荡: 大级别(4h隧道)定方向, 价格在15m隧道外又碰到前一个摆动高/低点 → 反向做, 目标隧道 ----
def vegas_range(b, trend=True, stop_atr=1.0, big=16, min_rr=1.0):
    c, h, l = b.c.values, b.h.values, b.l.values
    e144, e169 = ema(c, 144), ema(c, 169); top, bot = np.maximum(e144, e169), np.minimum(e144, e169); mid = (top + bot) / 2
    B1, B2 = ema(c, 144 * big), ema(c, 169 * big); a = atr(b)
    ph, pl = pivots(h, l, 5, 5)
    n = len(c); sig = np.zeros(n); stop = np.full(n, np.nan); tgt = np.full(n, np.nan)
    for i in range(200 * big if trend else 300, n):
        dn_ok = (not trend) or c[i] < min(B1[i], B2[i]); up_ok = (not trend) or c[i] > max(B1[i], B2[i])
        if dn_ok and l[i] > top[i] and h[i] >= ph[i - 1] and c[i] < ph[i - 1] and h[i - 1] < ph[i - 1]:
            st = ph[i - 1] + stop_atr * a[i]
            if mid[i] < c[i] and (c[i] - mid[i]) >= min_rr * (st - c[i]):
                sig[i] = -1; stop[i] = st; tgt[i] = mid[i]
        elif up_ok and h[i] < bot[i] and l[i] <= pl[i - 1] and c[i] > pl[i - 1] and l[i - 1] > pl[i - 1]:
            st = pl[i - 1] - stop_atr * a[i]
            if mid[i] > c[i] and (mid[i] - c[i]) >= min_rr * (c[i] - st):
                sig[i] = 1; stop[i] = st; tgt[i] = mid[i]
    return dict(sig=sig, stop=stop, tgt=tgt)


# ---- 14 日枢轴点: P、S1 都高于前一天 P → 回踩 P 做多, 止损 S1, 目标 rr ----
def pivot_p(b, rr=2.0, stop_mode="s1"):
    c, h, l, t = b.c.values, b.h.values, b.l.values, b.t.values
    day = t // 86400000
    dd = b.groupby(day).agg(H=("h", "max"), L=("l", "min"), C=("c", "last"))
    P = (dd.H + dd.L + dd.C) / 3; S1 = 2 * P - dd.H; R1 = 2 * P - dd.L
    # 当天用前一天算出的枢轴
    Pt = P.shift(1); S1t = S1.shift(1); R1t = R1.shift(1); Pp = P.shift(2)
    m = lambda s: s.reindex(day).values
    p, s1, r1, pp = m(Pt), m(S1t), m(R1t), m(Pp)
    up = (p > pp) & (s1 > pp); dn = (p < pp) & (r1 < pp)
    n = len(c); sig = np.zeros(n); stop = np.full(n, np.nan); tgt = np.full(n, np.nan)
    done = -1
    lo20 = pd.Series(l).rolling(20).min().values; hi20 = pd.Series(h).rolling(20).max().values
    for i in range(1, n):
        if day[i] == done or np.isnan(p[i]): continue
        if up[i] and l[i] <= p[i] < l[i - 1] and c[i] > s1[i]:
            st = s1[i] if stop_mode == "s1" else lo20[i]
            if st < c[i]: sig[i] = 1; stop[i] = st; tgt[i] = c[i] + rr * (c[i] - st); done = day[i]
        elif dn[i] and h[i] >= p[i] > h[i - 1] and c[i] < r1[i]:
            st = r1[i] if stop_mode == "s1" else hi20[i]
            if st > c[i]: sig[i] = -1; stop[i] = st; tgt[i] = c[i] - rr * (st - c[i]); done = day[i]
    return dict(sig=sig, stop=stop, tgt=tgt)


# ---- 13 马丁格尔(现货, 只做多, 循环) ----
def martingale(b, step=0.05, step_mult=1.5, tp=0.10, base=10, add=20, add_mult=2, n_add=4, sl=0.50, fee=0.001):
    o, h, l, c = b.o.values, b.h.values, b.l.values, b.c.values
    cap = base + sum(add * add_mult ** k for k in range(n_add))
    realized = 0.0; eq_curve = []; cycles = 0; stops = 0; i = 0; n = len(c)
    while i < n:
        p0 = o[i]; qty = base / p0 * (1 - fee); cost = base; k = 0
        levels = [p0 * (1 - step * sum(step_mult ** j for j in range(m + 1))) for m in range(n_add)]
        slp = p0 * (1 - sl); closed = False
        while i < n:
            while k < n_add and l[i] <= levels[k]:
                amt = add * add_mult ** k; qty += amt / levels[k] * (1 - fee); cost += amt; k += 1
            avg = cost / qty; tpp = avg * (1 + tp)
            if l[i] <= slp:
                realized += qty * slp * (1 - fee) - cost; stops += 1; closed = True
            elif h[i] >= tpp:
                realized += qty * tpp * (1 - fee) - cost; cycles += 1; closed = True
            eq_curve.append(realized + (0 if closed else qty * c[i] - cost))
            i += 1
            if closed: break
    eq = cap + np.array(eq_curve)
    dd = 1 - eq / np.maximum.accumulate(eq)
    yrs = (b.t.iloc[-1] - b.t.iloc[0]) / (365.25 * 86400e3)
    bh = c[-1] / c[0] - 1
    return dict(cap=cap, cycles=cycles, stops=stops, total=round(eq[-1] / cap - 1, 3), cagr=round((eq[-1] / cap) ** (1 / yrs) - 1, 3),
                mdd=round(dd.max(), 3), bh=round(bh, 3))


if __name__ == "__main__":
    rows = []
    def go(name, fn, tf, **kw):
        s = summarize(run_all(fn, tf, **kw)); s.update(strategy=name, tf=tf, **{k: str(v) for k, v in kw.items()}); rows.append(s)
        print(name, tf, kw, {k: s.get(k) for k in ("n", "win", "r_gross", "r_net", "r_is", "r_oos", "cagr", "mdd", "risk_med")}, flush=True)
    for tf, big in (("15m", 16), ("1h", 4)):
        for tr_, sa in itertools.product((True, False), (0.5, 1.0)):
            go("Vegas震荡", vegas_range, tf, trend=tr_, stop_atr=sa, big=big)
    for tf in ("15m", "1h"):
        for rr, sm in itertools.product((1.0, 2.0, 3.0), ("s1", "swing")):
            go("日枢轴P回踩", pivot_p, tf, rr=rr, stop_mode=sm)
    pd.DataFrame(rows).to_csv("batch03.csv", index=False)
    mrows = []
    for s in ("BTCUSDT", "ETHUSDT", "SOLUSDT"):
        b = bars(s, "15m")
        for kw in (dict(), dict(step=0.02, tp=0.03), dict(step=0.03, tp=0.05), dict(step=0.05, tp=0.05), dict(step=0.08, tp=0.10, sl=0.6)):
            r = martingale(b, **kw); r.update(sym=s, **{k: str(v) for k, v in kw.items()}); mrows.append(r); print("马丁", s, kw, r, flush=True)
    pd.DataFrame(mrows).to_csv("batch03_martingale.csv", index=False)
