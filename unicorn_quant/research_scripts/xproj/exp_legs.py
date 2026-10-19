import json, sys, bisect
from xvar import *
R = {}
which = sys.argv[1].split(",") if len(sys.argv) > 1 else None


def want(t): return which is None or t in which


F0, cB0, _ = assemble(); R["BASE"] = show("BASE(assemble)", F0, {"crypto": cB0, "stock": B["sc"]})

E_D = [("e8, e30 = ema(c, 8), ema(c, 30)", "e8, e30 = ema(c, PF), ema(c, PS)"), ("b8, b30 = ema(bc, 8), ema(bc, 30)", "b8, b30 = ema(bc, PF), ema(bc, PS)")]
if want("SANITY"):
    d = daily_leg(E_D, {"PF": 8, "PS": 30}); F, cB, _ = assemble(Dc=d); show("SANITY daily 8/30 via variant", F)
    st = stock([("f, sl = ema(c, 8), ema(c, 30)", "f, sl = ema(c, PF), ema(c, PS)")], {"PF": 8, "PS": 30}); F, cB, _ = assemble(scv=st); show("SANITY stock 8/30 via variant", F)

PAIRS = [(5, 20), (8, 30), (12, 45), (16, 60)]
if want("T06"):
    mem = [daily_leg(E_D, {"PF": a, "PS": b}) for a, b in PAIRS]
    for (a, b), m in zip(PAIRS, mem):
        F, cB, _ = assemble(Dc=m); show(f"  T06 member {a}/{b}", F, {"crypto": cB})
    F, cB, _ = assemble(Dc=avg_coin(mem)); R["T06"] = show("T06 daily EMA ensemble", F, {"crypto": cB})

if want("T07"):
    E_S = [("f, sl = ema(c, 8), ema(c, 30)", "f, sl = ema(c, PF), ema(c, PS)")]
    mem = [stock(E_S, {"PF": a, "PS": b}) for a, b in PAIRS]
    for (a, b), m in zip(PAIRS, mem):
        F, cB, _ = assemble(scv=m); show(f"  T07 member {a}/{b}", F, {"stock": m})
    se = O.combine(mem, [.25] * 4)
    F, cB, _ = assemble(scv=se); R["T07"] = show("T07 stock EMA ensemble", F, {"stock": se})

if want("T08"):
    E8 = [('D = _st_dirs(b) if kind == "st" else _bb_states(b)', 'D = _st_dirs(b, 10, PM) if kind == "st" else _bb_states(b, PN, PK)')]
    st = [h8_leg("st", E8, {"PM": m, "PN": 20, "PK": 2.0}) for m in (2.5, 3.0, 3.5)]
    bb = [h8_leg("bb", E8, {"PM": 3.0, "PN": n, "PK": k}) for n, k in ((20, 2.0), (30, 2.0), (20, 2.5))]
    F, cB, _ = assemble(STc=avg_coin(st), BBc=avg_coin(bb)); R["T08"] = show("T08 8h param ensemble", F, {"crypto": cB})

TR = O.tradable_set()
if want("T10"):
    for n in ([15] if which and "T10" in which else [15]):
        st = stock([("if len(r) >= 2:", "if len(r) >= 1:")], {"SECTORS": {t: [t] for t in sorted(TR)}, "SEC_OF": {t: t for t in TR}, "TOP_K": n})
        F, cB, _ = assemble(scv=st); R["T10"] = show(f"T10 stock RS top{n}", F, {"stock": st})

if want("T11"):
    st = stock([("if i < LOOKBACK_M + 1:", "if i < 14:"), ("prev, base = months[i - 1], months[i - 1 - LOOKBACK_M]", "prev, base = months[i - 2], months[i - 13]")])
    F, cB, _ = assemble(scv=st); R["T11"] = show("T11 sector 12-1 momentum", F, {"stock": st})

if want("T12"):
    st = stock([("act_set = active(d); k = max(1, len(act_set));",
                 "act_set = active(d); k = max(1, sum(1 for s_ in act_set if PRE[s_]['day'].get(d) is not None and PRE[s_]['dir'][PRE[s_]['day'][d]] > 0));")],
               {"MIN_SLOTS": 8})
    F, cB, _ = assemble(scv=st); R["T12"] = show("T12 stock divisor n_up(min8)", F, {"stock": st})

if want("T13"):
    st = stock([("sched[m] = rk[:TOP_K]; scores_by_m[m]", "sched[m] = rk[:TOP_K]; HOLD[m] = rk[:HOLD_K]; scores_by_m[m]"),
                ('raw = P["dir"][i]; want = raw if s in act_set else 0',
                 'raw = P["dir"][i]; want = raw if (s in act_set or (s in pos and SEC_OF[s] in HOLD.get(mkey(d), ()))) else 0')],
               {"HOLD": {}, "HOLD_K": 7})
    F, cB, _ = assemble(scv=st); R["T13"] = show("T13 sector hysteresis 5/7", F, {"stock": st})

if want("T14"):
    q = O.stock_daily("QQQ"); qc = [x["c"] for x in q]; qd = [x["t"] // O.DAY for x in q]; ok = []
    for i in range(len(q)):
        ok.append(i >= 199 and qc[i] > sum(qc[i - 199:i + 1]) / 200)

    def QOK(d):
        j = bisect.bisect_right(qd, d) - 1
        return j >= 0 and ok[j]
    st = stock([('raw = P["dir"][i]; want = raw if s in act_set else 0', 'raw = P["dir"][i]; want = raw if (s in act_set and (s in pos or QOK(d))) else 0')], {"QOK": QOK})
    F, cB, _ = assemble(scv=st); R["T14"] = show("T14 QQQ>SMA200 entry filter", F, {"stock": st})

if want("T19"):
    ins_d = ("            if pos:\n                eq += qty * (cl - pc)\n            curve[t // DAY] = max(eq, 0)\n            if i < 32:",
             "            if pos:\n                eq += qty * (cl - pc)\n            if pos and stop is None and eq > 0:\n                _tq = eq * min(1.0, VOL_TGT / (A[i] / cl)) / cl * pos\n"
             "                if abs(_tq / qty - 1) > RS_TH:\n                    eq -= abs(_tq - qty) * cl * FEE; qty = _tq\n            curve[t // DAY] = max(eq, 0)\n            if i < 32:")
    ins_8 = ("            if pos:\n                eq += qty * (cl - pc)\n            curve[t // DAY] = max(eq, 0)\n            if i < 60:",
             "            if pos:\n                eq += qty * (cl - pc)\n            if pos and eq > 0:\n                _tq = eq * min(1.0, vt / (A[i] / cl)) / cl * pos\n"
             "                if abs(_tq / qty - 1) > RS_TH:\n                    eq -= abs(_tq - qty) * cl * FEE; qty = _tq\n            curve[t // DAY] = max(eq, 0)\n            if i < 60:")
    th = float(sys.argv[2]) if len(sys.argv) > 2 else 0.25
    d = daily_leg([ins_d], {"RS_TH": th}); st = h8_leg("st", [ins_8], {"RS_TH": th}); bb = h8_leg("bb", [ins_8], {"RS_TH": th})
    F, cB, _ = assemble(Dc=d, STc=st, BBc=bb); R["T19"] = show(f"T19 continuous vol resize th{th}", F, {"crypto": cB})

json.dump(R, open(XP + "/res_legs_%s.json" % ("all" if which is None else "_".join(which)), "w"), indent=1)
