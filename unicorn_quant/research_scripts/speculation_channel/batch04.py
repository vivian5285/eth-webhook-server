"""批次4 (2023-03..07): 供需区(3根大K线前的基础K线)回踩 / MACD+OBV双背离 / BNF 25日均线偏离率逆向。"""
import itertools, pandas as pd, numpy as np
from lib import *


# ---- 17a 供需区: 连续≥3根同向大K线(实体≥k×ATR), 其前一根为基础K线 → 区域; 价格第一次回到区域挂单(limit)或等收阳确认 ----
def supply_demand(b, k=0.8, mode="limit", rr=2.0, max_age=300):
    o, h, l, c = b.o.values, b.h.values, b.l.values, b.c.values
    a = atr(b); body = np.abs(c - o)
    big_up = (c > o) & (body >= k * a); big_dn = (c < o) & (body >= k * a)
    n = len(c); sig = np.zeros(n); stop = np.full(n, np.nan); tgt = np.full(n, np.nan); lim = np.full(n, np.nan)
    zones = []  # [side, top, bottom, born, used]
    for i in range(30, n):
        if big_up[i] and big_up[i - 1] and big_up[i - 2] and not big_up[i - 3]:
            j = i - 3; top = h[j] if (h[j] - l[j]) < a[j] else max(o[j], c[j])
            zones.append([1, top, l[j], i, False])
        if big_dn[i] and big_dn[i - 1] and big_dn[i - 2] and not big_dn[i - 3]:
            j = i - 3; bot = l[j] if (h[j] - l[j]) < a[j] else min(o[j], c[j])
            zones.append([-1, h[j], bot, i, False])
        zones = [z for z in zones if not z[4] and i - z[3] <= max_age]
        for z in zones:
            side, top, bot = z[0], z[1], z[2]
            if i == z[3]: continue
            if side == 1:
                if l[i] < bot: z[4] = True; continue  # 跌穿作废
                st = bot - 0.2 * a[i]
                if mode == "limit" and l[i] > top:
                    sig[i] = 1; lim[i] = top; stop[i] = st; tgt[i] = top + rr * (top - st); break
                if mode == "confirm" and l[i] <= top and c[i] > o[i] and c[i] > st:
                    sig[i] = 1; stop[i] = st; tgt[i] = c[i] + rr * (c[i] - st); z[4] = True; break
            else:
                if h[i] > top: z[4] = True; continue
                st = top + 0.2 * a[i]
                if mode == "limit" and h[i] < bot:
                    sig[i] = -1; lim[i] = bot; stop[i] = st; tgt[i] = bot - rr * (st - bot); break
                if mode == "confirm" and h[i] >= bot and c[i] < o[i] and c[i] < st:
                    sig[i] = -1; stop[i] = st; tgt[i] = c[i] - rr * (st - c[i]); z[4] = True; break
    return dict(sig=sig, stop=stop, tgt=tgt, limit=lim if mode == "limit" else None)


# ---- 17b MACD 柱 + OBV 同时底/顶背离, 柱子转虚进场, 止损 ATR Stop Loss Finder(14,1.5) ----
def macd_obv(b, rr=2.0, need_obv=True):
    c, h, l, v = b.c.values, b.h.values, b.l.values, b.v.values
    _, _, hist = macd(c); a = atr(b, 14)
    obv = np.cumsum(np.sign(np.diff(c, prepend=c[0])) * v)
    n = len(c); sig = np.zeros(n); stop = np.full(n, np.nan); tgt = np.full(n, np.nan)
    tl, th = [], []
    for i in range(3, n):
        if hist[i] >= 0: tl = []
        if hist[i] <= 0: th = []
        j = i - 1
        if hist[j] < 0 and hist[j] < hist[j - 1] and hist[i] > hist[j]:
            k = int(np.argmin(l[max(0, j - 3):j + 2])) + max(0, j - 3)
            tl.append((hist[j], l[k], obv[k]))
            if len(tl) >= 2:
                p, q = tl[-2], tl[-1]
                if q[0] > p[0] and q[1] < p[1] and (not need_obv or q[2] > p[2]):
                    sig[i] = 1; stop[i] = l[i] - 1.5 * a[i]; tgt[i] = c[i] + rr * (c[i] - stop[i]); tl = []
        if hist[j] > 0 and hist[j] > hist[j - 1] and hist[i] < hist[j]:
            k = int(np.argmax(h[max(0, j - 3):j + 2])) + max(0, j - 3)
            th.append((hist[j], h[k], obv[k]))
            if len(th) >= 2:
                p, q = th[-2], th[-1]
                if q[0] < p[0] and q[1] > p[1] and (not need_obv or q[2] < p[2]):
                    sig[i] = -1; stop[i] = h[i] + 1.5 * a[i]; tgt[i] = c[i] - rr * (stop[i] - c[i]); th = []
    return dict(sig=sig, stop=stop, tgt=tgt)


# ---- 18 BNF 25日均线偏离率逆向(只做多现货, 满仓一笔) ----
def bnf(b, thr=-0.15, exit_mode="ma", max_days=20, fee=0.001):
    c = b.c.values; o = b.o.values; ma = sma(c, 25); dev = c / ma - 1
    eq = 1.0; curve = []; trades = []; pos = False; i = 25
    while i < len(c) - 1:
        if not pos and dev[i] <= thr:
            ent = o[i + 1]; ei = i + 1; pos = True; i += 1; continue
        if pos:
            held = i - ei
            if (exit_mode == "ma" and c[i] >= ma[i]) or held >= max_days:
                px = o[i + 1]; r = px / ent * (1 - fee) ** 2 - 1; eq *= 1 + r; trades.append(r); pos = False
        curve.append(eq * (c[i] / ent if pos else 1))
        i += 1
    cv = np.array(curve); dd = 1 - cv / np.maximum.accumulate(cv)
    yrs = len(c) / 365.25
    tr = np.array(trades)
    return dict(n=len(tr), win=round((tr > 0).mean(), 3) if len(tr) else np.nan, avg=round(tr.mean(), 4) if len(tr) else np.nan,
                cagr=round(eq ** (1 / yrs) - 1, 3), mdd=round(dd.max(), 3), bh_cagr=round((c[-1] / c[0]) ** (1 / yrs) - 1, 3),
                exposure=round(np.mean([1 if x else 0 for x in []]) if False else np.nan, 3))


if __name__ == "__main__":
    rows = []
    def go(name, fn, tf, **kw):
        s = summarize(run_all(fn, tf, **kw)); s.update(strategy=name, tf=tf, **{k: str(v) for k, v in kw.items()}); rows.append(s)
        print(name, tf, kw, {k: s.get(k) for k in ("n", "win", "r_gross", "r_net", "r_is", "r_oos", "cagr", "mdd", "risk_med")}, flush=True)
    for tf in ("15m", "1h", "4h"):
        for k, mode, rr in itertools.product((0.6, 1.0), ("limit", "confirm"), (1.0, 2.0, 3.0)):
            go("供需区回踩", supply_demand, tf, k=k, mode=mode, rr=rr)
        for rr, ob in itertools.product((1.0, 2.0, 3.0), (True, False)):
            go("MACD+OBV双背离", macd_obv, tf, rr=rr, need_obv=ob)
    pd.DataFrame(rows).to_csv("batch04.csv", index=False)
    br = []
    for s in SYMS:
        b = bars(s, "1d")
        for thr, em, md in itertools.product((-0.10, -0.15, -0.20, -0.25), ("ma", "time"), (10, 20)):
            r = bnf(b, thr=thr, exit_mode=em, max_days=md); r.update(sym=s, thr=thr, exit=em, max_days=md); br.append(r)
    br = pd.DataFrame(br); br.to_csv("batch04_bnf.csv", index=False)
    print(br.groupby(["thr", "exit", "max_days"])[["n", "win", "avg", "cagr", "mdd", "bh_cagr"]].mean().round(3).to_string())
