"""批次6 (2023-12..2024-02): Darek PA+MACD(4h供需区+孕线/吞没+MACD变色) / Minervini SEPA / Qullamaggie 突破。
美股数据: VPS optimal_data/bars_*.json 日线(2017起, 78只, 无成交量; 有选股后视偏差: 名单是今天挑的)。"""
import os, json, glob, itertools, pandas as pd, numpy as np
from lib import *
from batch01 import shift

STK = os.path.join(HERE, "stocks")
CRYPTO = {"BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "BTC", "ETH", "SOL", "BNB", "XRP"}


def stock_bars():
    out = {}
    for f in sorted(glob.glob(os.path.join(STK, "bars_*.json"))):
        s = os.path.basename(f)[5:-5]
        a = np.array(json.load(open(f)), float)
        if len(a) < 300: continue
        out[s] = pd.DataFrame({"t": a[:, 0].astype(np.int64), "o": a[:, 1], "h": a[:, 2], "l": a[:, 3], "c": a[:, 4]})
    return out


# ---- 27 Darek: 供需区 + 孕线/吞没/pinbar + 突破形态 + MACD柱变色 ----
def darek(b, rr=1.0, k=0.6, max_age=300):
    o, h, l, c = b.o.values, b.h.values, b.l.values, b.c.values
    a = atr(b); body = np.abs(c - o); _, _, hist = macd(c)
    big_up = (c > o) & (body >= k * a); big_dn = (c < o) & (body >= k * a)
    n = len(c); sig = np.zeros(n); stop = np.full(n, np.nan); tgt = np.full(n, np.nan)
    zones = []
    inside = (h < shift(h)) & (l > shift(l))
    rng = np.maximum(h - l, 1e-12)
    bull_pat = inside | ((c > o) & (c >= shift(o)) & (o <= shift(c)) & (shift(c) < shift(o))) | ((np.minimum(o, c) - l) >= 0.6 * rng)
    bear_pat = inside | ((c < o) & (c <= shift(o)) & (o >= shift(c)) & (shift(c) > shift(o))) | ((h - np.maximum(o, c)) >= 0.6 * rng)
    for i in range(30, n):
        if big_up[i] and big_up[i - 1] and not big_up[i - 2]:
            j = i - 2; zones.append([1, max(o[j], c[j]) if (h[j] - l[j]) >= a[j] else h[j], l[j], i])
        if big_dn[i] and big_dn[i - 1] and not big_dn[i - 2]:
            j = i - 2; zones.append([-1, h[j], min(o[j], c[j]) if (h[j] - l[j]) >= a[j] else l[j], i])
        zones = [z for z in zones if i - z[3] <= max_age and not ((z[0] == 1 and c[i] < z[2]) or (z[0] == -1 and c[i] > z[1]))]
        p = i - 1
        for z in zones:
            if i - z[3] < 3: continue
            if z[0] == 1 and l[p] <= z[1] and bull_pat[p] and c[i] > h[p] and hist[i] > 0 >= hist[i - 1]:
                st = z[2] - 0.2 * a[i]
                if st < c[i]: sig[i] = 1; stop[i] = st; tgt[i] = c[i] + rr * (c[i] - st); break
            if z[0] == -1 and h[p] >= z[2] and bear_pat[p] and c[i] < l[p] and hist[i] < 0 <= hist[i - 1]:
                st = z[1] + 0.2 * a[i]
                if st > c[i]: sig[i] = -1; stop[i] = st; tgt[i] = c[i] - rr * (st - c[i]); break
    return dict(sig=sig, stop=stop, tgt=tgt)


# ---- 28 Minervini 趋势模板 + 波动收缩突破 ----
def minervini(b, exit_mode="sma50", rr=3.0, max_stop=0.08):
    o, h, l, c = b.o.values, b.h.values, b.l.values, b.c.values
    s50, s150, s200 = sma(c, 50), sma(c, 150), sma(c, 200)
    lo52 = pd.Series(l).rolling(252, min_periods=200).min().values; hi52 = pd.Series(h).rolling(252, min_periods=200).max().values
    tmpl = (c > s150) & (c > s200) & (s50 > s150) & (s150 > s200) & (s200 > shift(s200, 22)) & (c >= 1.25 * lo52) & (c >= 0.75 * hi52)
    r1 = pd.Series(h).rolling(10).max() - pd.Series(l).rolling(10).min()
    r2 = (pd.Series(h).rolling(10).max() - pd.Series(l).rolling(10).min()).shift(10)
    r3 = (pd.Series(h).rolling(20).max() - pd.Series(l).rolling(20).min()).shift(20)
    vcp = ((r1 < r2) & (r2 < r3)).values
    top = pd.Series(h).rolling(20).max().shift(1).values; lo10 = pd.Series(l).rolling(10).min().values
    brk = tmpl & np.r_[False, vcp[:-1]] & (c > top) & (shift(c) <= shift(top))
    sig = np.where(brk, 1, 0)
    stop = np.maximum(lo10, c * (1 - max_stop))
    tgt = c + rr * (c - stop) if exit_mode == "rr" else None
    ex = np.where(c < s50, 1, 0) if exit_mode == "sma50" else None
    return dict(sig=sig, stop=stop, tgt=tgt, exit_sig=ex)


# ---- 30 Qullamaggie 突破 + 分批(第5天1/3+保本, 破EMA10 1/3, 破EMA20 全出) ----
def qull_trades(b, gain=0.30, cons=15, rng_max=0.15, max_atr=1.0):
    o, h, l, c, t = b.o.values, b.h.values, b.l.values, b.c.values, b.t.values
    e10, e20, e50 = ema(c, 10), ema(c, 20), ema(c, 50); a = atr(b)
    lo66 = pd.Series(l).rolling(66).min().values
    hi_c = pd.Series(h).rolling(cons).max().shift(1).values; lo_c = pd.Series(l).rolling(cons).min().shift(1).values
    n = len(c); out = []; i = 70
    while i < n - 1:
        g = c[i] / lo66[i] - 1
        ok = gain <= g <= max(1.0, 3 * gain) and e10[i] > e20[i] > e50[i] \
            and (hi_c[i] - lo_c[i]) / c[i] <= rng_max and c[i] > hi_c[i] and (c[i] - l[i]) <= max_atr * a[i] and c[i] > l[i]
        if not ok:
            i += 1; continue
        ent = c[i]; st = l[i]; R = ent - st; parts = []; rem = 3; j = i + 1; be = False
        while j < n and rem > 0:
            if l[j] <= st:
                px = min(o[j], st); parts += [(px - ent) / R] * rem; rem = 0; break
            if j - i == 5 and c[j] > ent and rem == 3:
                parts.append((c[j] - ent) / R); rem -= 1; st = max(st, ent); be = True
            elif rem == 2 and c[j] < e10[j] and be:
                parts.append((c[j] - ent) / R); rem -= 1
            if rem >= 1 and c[j] < e20[j]:
                parts += [(c[j] - ent) / R] * rem; rem = 0; break
            j += 1
        if rem: parts += [(c[min(j, n - 1)] - ent) / R] * rem
        rg = float(np.mean(parts)); rp = R / ent
        out.append(dict(t=t[i], side=1, risk=rp, r_gross=rg, r_net=rg - (2 * (FEE + SLIP)) / rp, bars=j - i))
        i = j + 1
    return pd.DataFrame(out)


if __name__ == "__main__":
    rows = []
    def rec(name, tf, univ, tr, **kw):
        s = summarize(tr); s.update(strategy=name, tf=tf, univ=univ, **{k: str(v) for k, v in kw.items()}); rows.append(s)
        print(name, tf, univ, kw, {k: s.get(k) for k in ("n", "win", "r_gross", "r_net", "r_is", "r_oos", "cagr", "mdd", "risk_med")}, flush=True)
    for rr, k in itertools.product((0.8, 1.0, 1.5, 2.0), (0.6, 1.0)):
        rec("Darek PA+MACD", "4h", "crypto5", run_all(darek, "4h", rr=rr, k=k), rr=rr, k=k)
        rec("Darek PA+MACD", "1h", "crypto5", run_all(darek, "1h", rr=rr, k=k), rr=rr, k=k)
    SB = stock_bars()
    stocks = {s: b for s, b in SB.items() if s not in CRYPTO}
    cryd = {s: b for s, b in SB.items() if s in CRYPTO}
    print("stocks", len(stocks), "crypto daily", list(cryd), flush=True)
    for univ, U, fee_mult in (("美股78", stocks, 1), ("币圈日线(2017起)", cryd, 1)):
        for em, rr in (("sma50", 0), ("rr", 2.0), ("rr", 3.0)):
            trs = [backtest(b, **minervini(b, exit_mode=em, rr=rr)).assign(sym=s) for s, b in U.items()]
            rec("Minervini SEPA", "1d", univ, pd.concat(trs, ignore_index=True), exit=em, rr=rr)
        for gain, rm in itertools.product((0.3, 0.5), (0.15, 0.25)):
            trs = [qull_trades(b, gain=gain, rng_max=rm).assign(sym=s) for s, b in U.items()]
            rec("Qullamaggie突破", "1d", univ, pd.concat(trs, ignore_index=True), gain=gain, rng_max=rm)
    pd.DataFrame(rows).to_csv("batch06.csv", index=False)
