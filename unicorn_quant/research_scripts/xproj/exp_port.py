"""portfolio-level trials T01-T05, T15, T18"""
import json, math, sys
from xlib import *
B = load_base(); cB, sc, cx, sx = B["cB"], B["sc"], B["cx"], B["EXPO"]["stk"]
RES = {}


def rep(tid, cur, G=None, extra=""):
    s = S(cur)
    if G:
        g = [G[d] for d in G if d >= START]; s["gmean"] = round(sum(g) / len(g), 2); s["gmax"] = round(max(g), 2)
    RES[tid] = s; print(fmt(tid, s), s.get("gmean", ""), s.get("gmax", ""), extra, flush=True)


F, _, G0 = vc([cB, sc], [1, 1], 2.0, expo=[cx, sx]); rep("BASE", F, G0)


def gen(curves, ws, gross=2.0, volfn=None, target=0.40, lb=60, up=False, cap=1.0, expo=None, wdyn=None, ddbrake=None):
    days = sorted(set().union(*[set(c) for c in curves])); n = len(curves); last = [None] * n; eq = 1.0; out = {}; hist = []
    s_prev = 1.0; lastx = [0.0] * n; G = {}; legret = [[] for _ in range(n)]; w_prev = list(ws); pk = 1.0; brake = False
    for d in days:
        w = wdyn(legret) if wdyn else ws
        rs = [0.0] * n
        for j, c in enumerate(curves):
            if d in c:
                if last[j]: rs[j] = c[d] / last[j] - 1
                last[j] = c[d]
        r = sum(w[j] * rs[j] for j in range(n))
        s = 1.0
        if len(hist) >= lb:
            v = volfn(hist[-lb:]) if volfn else None
            if v is None:
                h = hist[-lb:]; m = sum(h) / len(h); v = math.sqrt(sum((x - m) ** 2 for x in h) / (len(h) - 1)) * math.sqrt(365)
            s = target / v if v > 0 else 1.0
        s = min(cap if up else 1.0, s)
        if up and expo is not None:
            gx = sum(w[j] * lastx[j] for j in range(n))
            if gx > 0: s = min(s, 2.0 / gx)
        if ddbrake:
            dd = 1 - eq / pk
            if not brake and dd > ddbrake[0]: brake = True
            elif brake and dd < ddbrake[1]: brake = False
            if brake: s *= ddbrake[2]
        cost = abs(s - s_prev) * gross * O.FEE + sum(abs(w[j] - w_prev[j]) for j in range(n)) * s * O.FEE
        eq *= 1 + s * r - cost; s_prev = s; w_prev = list(w); hist.append(r); out[d] = eq; pk = max(pk, eq)
        for j in range(n): legret[j].append(rs[j])
        if expo is not None:
            for j in range(n):
                if d in expo[j]: lastx[j] = expo[j][d]
            G[d] = s * sum(w[j] * lastx[j] for j in range(n))
    return out, G


ex = [cx, sx]
c, G = gen([cB, sc], [1, 1], expo=ex); rep("GENBASE", c, G)
# T01/T02 two-sided
c, G = gen([cB, sc], [1, 1], target=0.40, up=True, cap=2.0, expo=ex); rep("T01", c, G)
c, G = gen([cB, sc], [1, 1], target=0.30, up=True, cap=2.0, expo=ex); rep("T02", c, G)
# T03 EWMA
c, _, _ = vc([cB, sc], [1, 1], 2.0, target=0.40, ewma=0.06); rep("T03", c)
# T04 per-leg
cBv = vc([cB], [1], 1.0, target=0.30)[0]; scv = vc([sc], [1], 1.0, target=0.30)[0]
c, _, _ = vc([cBv, scv], [1, 1], 2.0); rep("T04", c, extra="(crypto leg alone %s / stock alone %s)" % (S(cBv)["r"], S(scv)["r"]))


# T05 downside semivol
def semi(h):
    return math.sqrt(sum(min(x, 0) ** 2 for x in h) / len(h)) * math.sqrt(365)


c, G = gen([cB, sc], [1, 1], volfn=semi, target=0.28, expo=ex); rep("T05", c, G)


# T15 leg momentum tilt
def tilt(lb=126, t=0.25):
    def f(lr):
        if len(lr[0]) < lb: return [1, 1]
        a = math.prod(1 + x for x in lr[0][-lb:]); b = math.prod(1 + x for x in lr[1][-lb:])
        return [1 + t, 1 - t] if a > b else [1 - t, 1 + t]
    return f


c, G = gen([cB, sc], [1, 1], wdyn=tilt(), expo=ex); rep("T15", c, G)
# T18 DD brake
c, G = gen([cB, sc], [1, 1], ddbrake=(0.12, 0.06, 0.5), expo=ex); rep("T18", c, G)
json.dump(RES, open(XP + "/res_port.json", "w"), indent=1)
