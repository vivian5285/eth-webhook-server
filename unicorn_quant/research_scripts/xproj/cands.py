"""candidate registry. evalseed.py TID[,TID..] -> prints JSON per trial for current PYTHONHASHSEED (paired with baseline)."""
import bisect, json, sys, os, zlib
from xvar import *

E_D = [("e8, e30 = ema(c, 8), ema(c, 30)", "e8, e30 = ema(c, PF), ema(c, PS)"), ("b8, b30 = ema(bc, 8), ema(bc, 30)", "b8, b30 = ema(bc, PF), ema(bc, PS)")]
E_S = [("f, sl = ema(c, 8), ema(c, 30)", "f, sl = ema(c, PF), ema(c, PS)")]
E8 = [('D = _st_dirs(b) if kind == "st" else _bb_states(b)', 'D = _st_dirs(b, 10, PM) if kind == "st" else _bb_states(b, PN, PK)')]
TR = O.tradable_set()
C = {}


def reg(tid):
    def deco(f):
        C[tid] = f; return f
    return deco


@reg("T08")
def _t08(p=None):
    st = [h8_leg("st", E8, {"PM": m, "PN": 20, "PK": 2.0}) for m in (2.5, 3.0, 3.5)]
    bb = [h8_leg("bb", E8, {"PM": 3.0, "PN": n, "PK": k}) for n, k in ((20, 2.0), (30, 2.0), (20, 2.5))]
    return dict(STc=avg_coin(st), BBc=avg_coin(bb))


def rs_stock(n, lbm=6):
    reps = [("if len(r) >= 2:", "if len(r) >= 1:")]
    if lbm != 6:
        reps.append(("prev, base = months[i - 1], months[i - 1 - LOOKBACK_M]", "prev, base = months[i - 1], months[i - 1 - LBM_"))
        reps[-1] = ("prev, base = months[i - 1], months[i - 1 - LOOKBACK_M]", "prev, base = months[i - 1], months[i - 1 - LBM_]")
        reps.append(("if i < LOOKBACK_M + 1:", "if i < LBM_ + 1:"))
    return stock(reps, {"SECTORS": {t: [t] for t in sorted(TR)}, "SEC_OF": {t: t for t in TR}, "TOP_K": n, "LBM_": lbm})


@reg("T10")
def _t10(p=None): return dict(scv=rs_stock(15))


@reg("T11")
def _t11(p=None):
    return dict(scv=stock([("if i < LOOKBACK_M + 1:", "if i < 14:"), ("prev, base = months[i - 1], months[i - 1 - LOOKBACK_M]", "prev, base = months[i - 2], months[i - 13]")]))


def nup_stock(mn):
    return stock([("act_set = active(d); k = max(1, len(act_set));",
                   "act_set = active(d); k = max(1, sum(1 for s_ in act_set if PRE[s_]['day'].get(d) is not None and PRE[s_]['dir'][PRE[s_]['day'][d]] > 0));")],
                 {"MIN_SLOTS": mn})


@reg("T12")
def _t12(p=None): return dict(scv=nup_stock(8))


def hyst_stock(hk):
    return stock([("sched[m] = rk[:TOP_K]; scores_by_m[m]", "sched[m] = rk[:TOP_K]; HOLD[m] = rk[:HOLD_K]; scores_by_m[m]"),
                  ('raw = P["dir"][i]; want = raw if s in act_set else 0',
                   'raw = P["dir"][i]; want = raw if (s in act_set or (s in pos and SEC_OF[s] in HOLD.get(mkey(d), ()))) else 0')],
                 {"HOLD": {}, "HOLD_K": hk})


@reg("T13")
def _t13(p=None): return dict(scv=hyst_stock(7))


def qqq_ok(n=200):
    q = O.stock_daily("QQQ"); qc = [x["c"] for x in q]; qd = [x["t"] // O.DAY for x in q]
    ok = [i >= n - 1 and qc[i] > sum(qc[i - n + 1:i + 1]) / n for i in range(len(q))]

    def f(d):
        j = bisect.bisect_right(qd, d) - 1
        return j >= 0 and ok[j]
    return f


@reg("T14")
def _t14(p=None):
    return dict(scv=stock([('raw = P["dir"][i]; want = raw if s in act_set else 0', 'raw = P["dir"][i]; want = raw if (s in act_set and (s in pos or QOK(d))) else 0')], {"QOK": qqq_ok(200)}))


INS_D = ("            if pos:\n                eq += qty * (cl - pc)\n            curve[t // DAY] = max(eq, 0)\n            if i < 32:",
         "            if pos:\n                eq += qty * (cl - pc)\n            if pos and stop is None and eq > 0:\n                _tq = eq * min(1.0, VOL_TGT / (A[i] / cl)) / cl * pos\n"
         "                if abs(_tq / qty - 1) > RS_TH:\n                    eq -= abs(_tq - qty) * cl * FEE; qty = _tq\n            curve[t // DAY] = max(eq, 0)\n            if i < 32:")
INS_8 = ("            if pos:\n                eq += qty * (cl - pc)\n            curve[t // DAY] = max(eq, 0)\n            if i < 60:",
         "            if pos:\n                eq += qty * (cl - pc)\n            if pos and eq > 0:\n                _tq = eq * min(1.0, vt / (A[i] / cl)) / cl * pos\n"
         "                if abs(_tq / qty - 1) > RS_TH:\n                    eq -= abs(_tq - qty) * cl * FEE; qty = _tq\n            curve[t // DAY] = max(eq, 0)\n            if i < 60:")


@reg("T19")
def _t19(p=None):
    th = 0.25
    return dict(Dc=daily_leg([INS_D], {"RS_TH": th}), STc=h8_leg("st", [INS_8], {"RS_TH": th}), BBc=h8_leg("bb", [INS_8], {"RS_TH": th}))


def assemble2(Dc=None, STc=None, BBc=None, scv=None, subw=(.5, .25, .25), coinfn=None, basketfn=None, **_):
    Dc = Dc or SUB[KD]; STc = STc or SUB[KS]; BBc = BBc or SUB[KB]
    coin = coinfn(Dc, STc, BBc) if coinfn else {s: O.combine([Dc[s], STc[s], BBc[s]], list(subw)) for s in O.M5}
    cB = basketfn(coin) if basketfn else O.weighted_basket(coin, O.coin_weights(coin, range(2017, 2027)))
    return O.vol_control([cB, scv], [1, 1], 2.0)[0], cB, coin


def run(tids):
    seed = os.environ.get("PYTHONHASHSEED")
    sc0 = stock(); F0 = assemble(scv=sc0)[0]; b = S(F0)
    for tid in tids:
        kw = C[tid]()
        if "scv" not in kw: kw["scv"] = sc0
        out = assemble2(**kw)
        F = out[0]
        if "post" in kw: F = kw["post"](out)
        s = S(F)
        print("JSON", json.dumps({"tid": tid, "seed": seed, "c": s, "b": b, "stock_r": S(kw["scv"])["r"], "crypto_r": S(out[1])["r"]}), flush=True)
        if os.environ.get("XYEAR"):
            print("YEARS", json.dumps({"tid": tid, "c": yearly(F), "b": yearly(F0)}))

