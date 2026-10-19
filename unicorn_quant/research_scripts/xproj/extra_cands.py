import json, calendar, time
from cands import *
from cands import reg, C

P8 = 8 * 3600000


def anchored(sym, k):
    b8 = json.load(open(f"{XP}/k8v_{sym}.json")); out = []; cur = None
    for x in b8:
        if (x["t"] - k * P8) % O.DAY == 0:
            if cur and cur["n"] == 3: out.append(cur)
            cur = {"t": x["t"], "o": x["o"], "h": x["h"], "l": x["l"], "c": x["c"], "v": x["v"], "n": 1, "last": x["t"]}
        elif cur and x["t"] == cur["last"] + P8:
            cur["h"] = max(cur["h"], x["h"]); cur["l"] = min(cur["l"], x["l"]); cur["c"] = x["c"]; cur["v"] += x["v"]; cur["n"] += 1; cur["last"] = x["t"]
        else:
            cur = None
    if cur and cur["n"] == 3: out.append(cur)
    return [{kk: v for kk, v in b.items() if kk not in ("n", "last")} for b in out]


def anchor_leg(k):
    bars = {s: anchored(s, k) for s in O.M5}
    return daily_leg((), {"spot_daily": lambda s: bars[s]})


@reg("T09")
def _t09(p=None):
    return dict(Dc=avg_coin([anchor_leg(k) for k in (0, 1, 2)]))


@reg("T09a0")   # sanity: anchor 0 from 8h bars should ~= production daily leg
def _t09a0(p=None): return dict(Dc=anchor_leg(0))


def month_weights(coin, lookback=1095, cap=0.4):
    W = {}
    for y in range(2017, 2027):
        for m in range(1, 13):
            m0 = calendar.timegm((y, m, 1, 0, 0, 0)) // 86400; m1 = calendar.timegm((y + (m == 12), m % 12 + 1, 1, 0, 0, 0)) // 86400
            avail = [s for s, cv in coin.items() if any(m0 <= kk < m1 for kk in cv)]
            sc = {}
            for s in avail:
                ks = [kk for kk in coin[s] if m0 - lookback <= kk < m0]
                if len(ks) >= 300:
                    r = O._win_ratio(coin[s], m0 - lookback, m0 - 1)
                    if r is not None: sc[s] = max(0.0, r)
            w = O._capw(sc, cap)
            W[(y, m)] = (w or {s: 1 / len(avail) for s in avail}) if avail else {}
    return W


def basket_ym(coin, W):
    days = sorted(set().union(*[set(v) for v in coin.values()])); prev = {}; v = 1.0; out = {}
    for d in days:
        g = time.gmtime(d * 86400); w = W.get((g.tm_year, g.tm_mon), {}); r = 0.0
        for s, cv in coin.items():
            if d in cv:
                if s in prev: r += w.get(s, 0.0) * (cv[d] / prev[s] - 1)
                prev[s] = cv[d]
        v *= 1 + r; out[d] = v
    return out


@reg("T16")
def _t16(p=None): return dict(basketfn=lambda coin: basket_ym(coin, month_weights(coin)))


def combine_dyn(cs, wf):
    days = sorted(set().union(*[set(c) for c in cs])); last = [None] * len(cs); eq = 1.0; out = {}
    for d in days:
        ws = wf(d); r = 0.0
        for j, c in enumerate(cs):
            if d in c:
                if last[j]: r += ws[j] * (c[d] / last[j] - 1)
                last[j] = c[d]
        eq *= 1 + r; out[d] = eq
    return out


def dyn_sub(Dc, STc, BBc, cap=0.6, lookback=1095, default=(.5, .25, .25)):
    coin = {}
    for s in O.M5:
        cs = [Dc[s], STc[s], BBc[s]]; WYs = {}
        for y in range(2017, 2027):
            y0 = calendar.timegm((y, 1, 1, 0, 0, 0)) // 86400; sc = {}
            for j, cv in enumerate(cs):
                ks = [kk for kk in cv if y0 - lookback <= kk < y0]
                if len(ks) >= 300:
                    r = O._win_ratio(cv, y0 - lookback, y0 - 1)
                    if r is not None: sc[j] = max(0.0, r)
            w = O._capw(sc, cap) if len(sc) == 3 else {}
            WYs[y] = [w.get(j, 0.0) for j in range(3)] if w else list(default)
        coin[s] = combine_dyn(cs, lambda d, W=WYs: W.get(time.gmtime(d * 86400).tm_year, list(default)))
    return coin


@reg("T17")
def _t17(p=None): return dict(coinfn=lambda D, S_, Bb: dyn_sub(D, S_, Bb))


def qweights(coin, lookback=1095, cap=0.4, months=(1, 4, 7, 10)):
    W = month_weights(coin, lookback, cap); out = {}
    for (y, m), w in sorted(W.items()):
        q = max(mm for mm in months if mm <= m); out[(y, m)] = W[(y, q)]
    return out


@reg("N16a")
def _n16a(p=None): return dict(basketfn=lambda coin: basket_ym(coin, month_weights(coin, 730)))
@reg("N16b")
def _n16b(p=None): return dict(basketfn=lambda coin: basket_ym(coin, month_weights(coin, 1460)))
@reg("N16c")
def _n16c(p=None): return dict(basketfn=lambda coin: basket_ym(coin, qweights(coin)))
@reg("N16d")
def _n16d(p=None): return dict(basketfn=lambda coin: basket_ym(coin, month_weights(coin, 1095, 0.5)))
@reg("N17a")
def _n17a(p=None): return dict(coinfn=lambda D, S_, Bb: dyn_sub(D, S_, Bb, cap=0.5))
@reg("N17b")
def _n17b(p=None): return dict(coinfn=lambda D, S_, Bb: dyn_sub(D, S_, Bb, cap=0.7))
@reg("N17c")
def _n17c(p=None): return dict(coinfn=lambda D, S_, Bb: dyn_sub(D, S_, Bb, lookback=730))
@reg("N17d")
def _n17d(p=None): return dict(coinfn=lambda D, S_, Bb: dyn_sub(D, S_, Bb, lookback=1460))
@reg("T20")
def _t20(p=None): return dict(coinfn=lambda D, S_, Bb: dyn_sub(D, S_, Bb), basketfn=lambda coin: basket_ym(coin, month_weights(coin)))


def basket_ym_c(coin, W, fee=O.FEE):
    days = sorted(set().union(*[set(v) for v in coin.values()])); prev = {}; v = 1.0; out = {}; wprev = None; kprev = None
    for d in days:
        g = time.gmtime(d * 86400); key = (g.tm_year, g.tm_mon); w = W.get(key, {}); r = 0.0
        for s, cv in coin.items():
            if d in cv:
                if s in prev: r += w.get(s, 0.0) * (cv[d] / prev[s] - 1)
                prev[s] = cv[d]
        v *= 1 + r
        if key != kprev:
            if wprev is not None:
                v *= 1 - fee * sum(abs(w.get(s, 0) - wprev.get(s, 0)) for s in set(w) | set(wprev))
            wprev = w; kprev = key
        out[d] = v
    return out


def combine_dyn_c(cs, wf, fee=O.FEE):
    days = sorted(set().union(*[set(c) for c in cs])); last = [None] * len(cs); eq = 1.0; out = {}; wp = None
    for d in days:
        ws = wf(d); r = 0.0
        for j, c in enumerate(cs):
            if d in c:
                if last[j]: r += ws[j] * (c[d] / last[j] - 1)
                last[j] = c[d]
        eq *= 1 + r
        if wp is not None and ws != wp: eq *= 1 - fee * sum(abs(a - b) for a, b in zip(ws, wp))
        wp = list(ws); out[d] = eq
    return out


def dyn_sub2(Dc, STc, BBc, cap=0.6, lookback=1095, default=(.5, .25, .25), mode="ratio", cost=False):
    coin = {}
    for s in O.M5:
        cs = [Dc[s], STc[s], BBc[s]]; WYs = {}
        for y in range(2017, 2027):
            y0 = calendar.timegm((y, 1, 1, 0, 0, 0)) // 86400; sc = {}
            for j, cv in enumerate(cs):
                ks = [kk for kk in cv if y0 - lookback <= kk < y0]
                if len(ks) >= 300:
                    r = O._win_ratio(cv, y0 - lookback, y0 - 1)
                    if r is not None: sc[j] = max(0.0, r)
            if mode == "inv": sc = {j: (1 / x if x > 0 else 0) for j, x in sc.items()}
            w = O._capw(sc, cap) if len(sc) == 3 else {}
            WYs[y] = [w.get(j, 0.0) for j in range(3)] if w else list(default)
        f = lambda d, W=WYs: W.get(time.gmtime(d * 86400).tm_year, list(default))
        coin[s] = combine_dyn_c(cs, f) if cost else combine_dyn(cs, f)
    return coin


def month_weights_lag(coin, lag=365, lookback=1095, cap=0.4):
    W = {}
    for y in range(2017, 2027):
        for m in range(1, 13):
            m0 = calendar.timegm((y, m, 1, 0, 0, 0)) // 86400; m1 = calendar.timegm((y + (m == 12), m % 12 + 1, 1, 0, 0, 0)) // 86400
            e0 = m0 - lag
            avail = [s for s, cv in coin.items() if any(m0 <= kk < m1 for kk in cv)]
            sc = {}
            for s in avail:
                ks = [kk for kk in coin[s] if e0 - lookback <= kk < e0]
                if len(ks) >= 300:
                    r = O._win_ratio(coin[s], e0 - lookback, e0 - 1)
                    if r is not None: sc[s] = max(0.0, r)
            w = O._capw(sc, cap)
            W[(y, m)] = (w or {s: 1 / len(avail) for s in avail}) if avail else {}
    return W


@reg("T20c")
def _t20c(p=None): return dict(coinfn=lambda D, S_, Bb: dyn_sub2(D, S_, Bb, cost=True), basketfn=lambda coin: basket_ym_c(coin, month_weights(coin)))
@reg("P17eq")
def _p17eq(p=None): return dict(subw=(1 / 3, 1 / 3, 1 / 3))
@reg("P17inv")
def _p17inv(p=None): return dict(coinfn=lambda D, S_, Bb: dyn_sub2(D, S_, Bb, mode="inv"))
@reg("P16lag")
def _p16lag(p=None): return dict(basketfn=lambda coin: basket_ym(coin, month_weights_lag(coin)))


def month_weights_elig(coin, lookback=1095, cap=0.4):
    W = month_weights(coin, lookback, cap); out = {}
    for y in range(2017, 2027):
        y0 = calendar.timegm((y, 1, 1, 0, 0, 0)) // 86400
        elig = [s for s, cv in coin.items() if len([kk for kk in cv if y0 - lookback <= kk < y0]) >= 300]
        for m in range(1, 13):
            m0 = calendar.timegm((y, m, 1, 0, 0, 0)) // 86400; m1 = calendar.timegm((y + (m == 12), m % 12 + 1, 1, 0, 0, 0)) // 86400
            avail = [s for s, cv in coin.items() if any(m0 <= kk < m1 for kk in cv)]
            sc = {}
            for s in elig:
                r = O._win_ratio(coin[s], m0 - lookback, m0 - 1)
                if r is not None: sc[s] = max(0.0, r)
            w = O._capw(sc, cap)
            ya = [s for s, cv in coin.items() if any(y0 <= kk < y0 + 366 for kk in cv)]
            out[(y, m)] = w or ({s: 1 / len(ya) for s in ya} if ya else {})
    return out


@reg("D16")
def _d16(p=None): return dict(basketfn=lambda coin: basket_ym(coin, month_weights_elig(coin)))


def _allow(mclose, prev, base, SECTORS):
    ok = set()
    for sec, lst in SECTORS.items():
        rr = {s: mclose[s][prev] / mclose[s][base] - 1 for s in lst if s in mclose and prev in mclose[s] and base in mclose[s]}
        if not rr: continue
        v = sorted(rr.values()); med = v[len(v) // 2] if len(v) % 2 else (v[len(v) // 2 - 1] + v[len(v) // 2]) / 2
        ok |= {s for s, x in rr.items() if x >= med}
    return ok


@reg("T22")
def _t22(p=None):
    return dict(scv=stock([("sched[m] = rk[:TOP_K]; scores_by_m[m]", "sched[m] = rk[:TOP_K]; ALLOW[m] = _allow(mclose, prev, base, SECTORS); scores_by_m[m]"),
                           ('raw = P["dir"][i]; want = raw if s in act_set else 0',
                            'raw = P["dir"][i]; want = raw if (s in act_set and (s in pos or s in ALLOW.get(mkey(d), ()))) else 0')],
                          {"ALLOW": {}, "_allow": _allow}))


def _multi_sc(mclose, months, i, SECTORS, lbs=(3, 6, 12)):
    prev = months[i - 1]; sc = {}
    for sec, lst in SECTORS.items():
        vals = []
        for lb in lbs:
            base = months[i - 1 - lb]
            r = [mclose[s][prev] / mclose[s][base] - 1 for s in lst if s in mclose and prev in mclose[s] and base in mclose[s]]
            if len(r) >= 2: vals.append(sum(r) / len(r))
        if len(vals) == len(lbs): sc[sec] = sum(vals) / len(vals)
    return sc


@reg("T23")
def _t23(p=None):
    old = ("        prev, base = months[i - 1], months[i - 1 - LOOKBACK_M]\n        sc = {}\n        for sec, lst in SECTORS.items():\n"
           "            r = [mclose[s][prev] / mclose[s][base] - 1 for s in lst if s in mclose and prev in mclose[s] and base in mclose[s]]\n"
           "            if len(r) >= 2:\n                sc[sec] = sum(r) / len(r)\n        rk = sorted(sc, key=sc.get, reverse=True)\n        sched[m]")
    new = "        prev, base = months[i - 1], months[i - 1 - LOOKBACK_M]\n        sc = _multi_sc(mclose, months, i, SECTORS)\n        rk = sorted(sc, key=sc.get, reverse=True)\n        sched[m]"
    return dict(scv=stock([(old, new), ("if i < LOOKBACK_M + 1:", "if i < 13:")], {"_multi_sc": _multi_sc}))


@reg("N_eq_a")
def _neqa(p=None): return dict(subw=(.4, .3, .3))
@reg("N_eq_b")
def _neqb(p=None): return dict(subw=(.3, .35, .35))
