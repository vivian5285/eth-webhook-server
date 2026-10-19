"""robustness analysis of top candidates for current seed: extra metrics, sub-periods, rolling windows, block bootstrap vs base."""
import sys, json, math, random, calendar
import cands, extra_cands
from cands import *
from xlib import _seg
TIDS = sys.argv[1].split(",")
sc0 = stock(); F0 = assemble(scv=sc0)[0]


def dret(c):
    ks = [k for k in sorted(c) if k >= START]; return ks, [c[ks[i]] / c[ks[i - 1]] - 1 for i in range(1, len(ks))]


def metrics(c):
    ks, r = dret(c); m = sum(r) / len(r); sd = (sum((x - m) ** 2 for x in r) / (len(r) - 1)) ** .5
    dn = (sum(min(x, 0) ** 2 for x in r) / len(r)) ** .5
    v = [c[k] for k in ks]; pk = 0; u = 0; dds = []; cur = 0
    for x in v:
        pk = max(pk, x); d = 1 - x / pk; u += d * d
    ulcer = (u / len(v)) ** .5
    # top-5 distinct drawdown episodes
    eps = []; pk = v[0]; trough = 0
    for x in v:
        if x >= pk:
            if trough > 0: eps.append(trough)
            pk = x; trough = 0
        else: trough = max(trough, 1 - x / pk)
    if trough > 0: eps.append(trough)
    eps.sort(reverse=True); cg = _seg(c, START)[0]
    return {"sharpe": round(m / sd * 365 ** .5, 3), "sortino": round(m / dn * 365 ** .5, 3), "ulcer": round(100 * ulcer, 2),
            "top5dd": round(100 * sum(eps[:5]) / 5, 1), "cagr_top5": round(cg / (sum(eps[:5]) / 5), 2)}


def per(c):
    cuts = [(START, 18262, "17-19"), (18262, 19358, "20-22"), (19358, None, "23-26")]
    return {n: round(_seg(c, a, z)[2], 2) for a, z, n in cuts}


def rolling(c, b, win=1095, step=91):
    ks = sorted(k for k in c if k >= START); wins = 0; n = 0; a = ks[0]
    while a + win <= ks[-1]:
        rc = _seg(c, a, a + win)[2]; rb = _seg(b, a, a + win)[2]; n += 1; wins += rc > rb; a += step
    return wins, n


def boot(c, b, B=1000, blk=60, seed=1):
    ks = [k for k in sorted(c) if k >= START and k in b]
    rc = [c[ks[i]] / c[ks[i - 1]] - 1 for i in range(1, len(ks))]; rb = [b[ks[i]] / b[ks[i - 1]] - 1 for i in range(1, len(ks))]
    n = len(rc); rnd = random.Random(seed); better_sh = 0; better_ratio = 0
    def stat(r):
        m = sum(r) / len(r); sd = (sum((x - m) ** 2 for x in r) / len(r)) ** .5
        eq = 1; pk = 1; dd = 0
        for x in r: eq *= 1 + x; pk = max(pk, eq); dd = max(dd, 1 - eq / pk)
        cg = eq ** (365 / len(r)) - 1
        return m / sd, cg / dd
    for _ in range(B):
        idx = []
        while len(idx) < n:
            s0 = rnd.randrange(0, n - blk); idx.extend(range(s0, s0 + blk))
        idx = idx[:n]
        a = stat([rc[i] for i in idx]); z = stat([rb[i] for i in idx])
        better_sh += a[0] > z[0]; better_ratio += a[1] > z[1]
    return round(better_sh / B, 3), round(better_ratio / B, 3)


rows = {"BASE": (F0, None)}
for t in TIDS:
    kw = C[t]()
    if "scv" not in kw: kw["scv"] = sc0
    rows[t] = (assemble2(**kw)[0], None)
for t, (c, _) in rows.items():
    out = {"tid": t, "S": S(c), "M": metrics(c), "P": per(c)}
    if t != "BASE":
        out["roll3y_wins"] = rolling(c, F0); out["boot_p_better(sharpe,ratio)"] = boot(c, F0)
    print("AN", json.dumps(out, ensure_ascii=False), flush=True)
    print("YR", t, json.dumps(yearly(c)), flush=True)
