# -*- coding: utf-8 -*-
"""xproj common helpers. READ-ONLY use of production modules: _cache patched to never write."""
import sys, os, json, math, time, pickle, inspect, calendar
sys.path.insert(0, "/home/stratroster/unicorn_quant"); sys.path.insert(0, "/home/stratroster/eth-webhook-server")
from engine import optimal as O

XP = "/tmp/xproj"
H_SPLIT = 18993  # 2022-01-01
START = 17410


def _ro_cache(name, ttl, fn):
    p = os.path.join(O.OUT, name)
    if os.path.exists(p):
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    raise RuntimeError("cache missing (refusing to fetch/write): " + name)


O._cache = _ro_cache
_TR = None


def _tr():
    global _TR
    p = os.path.join(XP, "tradable.json")
    if _TR is None:
        if os.path.exists(p):
            _TR = set(json.load(open(p)))
        else:
            _TR = set(O.TRADABLE_FALLBACK)
            try:
                info = json.load(__import__("urllib.request").request.urlopen("https://fapi.binance.com/fapi/v1/exchangeInfo", timeout=30))
                listed = {s["symbol"] for s in info["symbols"] if s.get("contractType") == "TRADIFI_PERPETUAL" and s.get("status") == "TRADING"}
                got = {t for t in O.SEC_OF if t + "USDT" in listed}
                if got: _TR = got
            except Exception as e:
                print("tradable fetch failed, fallback", e)
            json.dump(sorted(_TR), open(p, "w"))
    return _TR


O.tradable_set = _tr

# ---------- exposure instrumentation (in-process copies only) ----------
EXPO = {}  # name -> sym -> day -> signed exposure (notional/equity) at day end


def instrument():
    src = inspect.getsource(O.crypto_leg)
    a = "            curve[t // DAY] = max(eq, 0)\n            if i < 32:"
    assert a in src
    src = src.replace(a, "            curve[t // DAY] = max(eq, 0); EXPO.setdefault('d', {}).setdefault(sym, {})[t // DAY] = (qty * cl / eq if eq > 0 else 0)\n            if i < 32:")
    b = "                curve[t // DAY] = max(eq, 0)\n                continue"
    assert b in src
    src = src.replace(b, "                curve[t // DAY] = max(eq, 0); EXPO.setdefault('d', {}).setdefault(sym, {})[t // DAY] = 0.0\n                continue")
    O.EXPO = EXPO
    exec(src, O.__dict__)
    src = inspect.getsource(O.crypto_8h_leg)
    c = "            curve[t // DAY] = max(eq, 0)\n            if i < 60:"
    assert c in src
    src = src.replace(c, "            curve[t // DAY] = max(eq, 0); _dd = EXPO.setdefault(kind, {}).setdefault(sym, {}); _dd[t // DAY] = max(_dd.get(t // DAY, 0), abs(qty * cl / eq) if eq > 0 else 0)\n            if i < 60:")
    exec(src, O.__dict__)
    src = inspect.getsource(O.stock_leg)
    d = "        curve[d] = E\n        gross = sum(abs(q) * l for q, l, _ in pos.values())\n"
    assert d in src
    src = src.replace(d, d + "        EXPO.setdefault('stk', {})[d] = gross / E if E > 0 else 0\n")
    exec(src, O.__dict__)


# ---------- stats ----------
def _seg(curve, a, z=None):
    ks = [k for k in sorted(curve) if k >= a and (z is None or k < z)]
    v = [curve[k] for k in ks]
    pk = 0; dd = 0
    for x in v:
        pk = max(pk, x); dd = max(dd, 1 - x / pk)
    yrs = (ks[-1] - ks[0]) / 365.25
    cagr = (v[-1] / v[0]) ** (1 / yrs) - 1
    return cagr, dd, (cagr / dd if dd else float("nan"))


def S(curve):
    c, d, r = _seg(curve, START)
    _, _, r1 = _seg(curve, START, H_SPLIT)
    _, _, r2 = _seg(curve, H_SPLIT)
    return {"cagr": round(100 * c, 1), "dd": round(100 * d, 1), "r": round(r, 3), "h1": round(r1, 3), "h2": round(r2, 3)}


def yearly(curve):
    ks = [k for k in sorted(curve) if k >= START]; yr = {}
    for k in ks:
        y = time.gmtime(k * 86400).tm_year; yr.setdefault(y, [curve[k], curve[k], curve[k], 0.0])
        e = yr[y]; e[1] = curve[k]; e[2] = max(e[2], curve[k]); e[3] = max(e[3], 1 - curve[k] / e[2])
    ys = sorted(yr); out = {}
    prev = None
    for y in ys:
        base = prev if prev is not None else yr[y][0]
        out[y] = (round(100 * (yr[y][1] / base - 1), 1), round(100 * yr[y][3], 1)); prev = yr[y][1]
    return out


def fmt(name, s):
    return f"{name:<48s} CAGR {s['cagr']:6.1f}% DD {s['dd']:5.1f}% R {s['r']:.2f} | H1 {s['h1']:.2f} H2 {s['h2']:.2f}"


def load_base():
    return pickle.load(open(os.path.join(XP, "base.pkl"), "rb"))


def vc(curves, ws, gross, target=0.40, lb=60, up=False, cap=1.0, expo=None, gross_cap=2.0, ewma=None):
    """generalised vol control. returns (curve, scale, gross_series).
    expo: list of dicts day->gross exposure per unit weight of each curve (for gross accounting / up-scaling cap)."""
    days = sorted(set().union(*[set(c) for c in curves])); last = [None] * len(curves); eq = 1.0; out = {}; hist = []; s_prev = 1.0; sc = {}; G = {}
    lastx = [0.0] * len(curves); var = None
    for d in days:
        r = 0.0
        for j, c in enumerate(curves):
            if d in c:
                if last[j]:
                    r += ws[j] * (c[d] / last[j] - 1)
                last[j] = c[d]
        s = 1.0
        if ewma:
            if var is not None and len(hist) >= 20:
                sd = math.sqrt(var * 365); s = target / sd if sd > 0 else 1.0
        elif len(hist) >= lb:
            w = hist[-lb:]; m = sum(w) / len(w)
            sd = math.sqrt(sum((x - m) ** 2 for x in w) / (len(w) - 1)) * math.sqrt(365)
            s = target / sd if sd > 0 else 1.0
        s = min(cap if up else 1.0, s)
        # gross cap using yesterday's exposures (known at decision time)
        if expo is not None:
            gx = sum(ws[j] * lastx[j] for j in range(len(curves)))
            if gx > 0:
                s = min(s, gross_cap / gx) if up else s
        eq *= 1 + s * r - abs(s - s_prev) * gross * O.FEE; s_prev = s; hist.append(r); out[d] = eq; sc[d] = s
        if ewma:
            var = r * r if var is None else (1 - ewma) * var + ewma * r * r
        if expo is not None:
            for j in range(len(curves)):
                if d in expo[j]: lastx[j] = expo[j][d]
            G[d] = s * sum(ws[j] * lastx[j] for j in range(len(curves)))
    return out, sc, G
