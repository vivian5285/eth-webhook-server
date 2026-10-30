"""网格: 趋势×位置×足迹确认×时段×量能 → 出场方式。每笔用 R 记账(含费)。
样本内 2022-01..2024-12，样本外 2025-01..2026-09。
"""
import os, sys, itertools, pickle, numpy as np, pandas as pd
from engine import load, value_areas, bars5, annotate, candidates, run_exits, DATA

SYMS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT"]
OOS = pd.Timestamp("2025-01-01").value // 10**6

WINS = {"NY90": (570, 660), "NYRTH": (570, 960), "ALL": (0, 1440)}
FPS = [dict(fp=0, wick_k=0, b2_k=0)] + [dict(fp=1, wick_k=a, b2_k=b) for a in (0.0, 0.1) for b in (0.0, 0.1)]
EXITS = [dict(tp=tp, be=be, trail=tr) for tp in (1.5, 2.0, "struct") for be in (0, 30) for tr in (0, 1)]

prep = {}
for s in SYMS:
    f = os.path.join(DATA, f"{s}_prep.pkl")
    if os.path.exists(f):
        prep[s] = pickle.load(open(f, "rb"))
        if "nym" not in prep[s][2]:
            annotate(prep[s][2]); pickle.dump(prep[s], open(f, "wb"))
        continue
    d = load(s); b = bars5(d); annotate(b); prep[s] = (d, value_areas(d), b)
    pickle.dump(prep[s], open(f, "wb")); print("prep", s, flush=True)

res = []
for wn, td, loc, fpd, vk in itertools.product(WINS, (0, 1, 2), (0, 1, 2), FPS, (0, 1.0)):
    P = dict(win=WINS[wn], trend_days=td, loc=loc, vol_k=vk, vol_ref="same_time", swing_w=288, max_day=2, **fpd)
    sigs = {s: candidates(prep[s][2], prep[s][1], P) for s in SYMS}
    for E in EXITS:
        E = dict(E, max_min=150 if wn == "NY90" else 390)
        tr = pd.concat([run_exits(prep[s][0], sigs[s], E).assign(sym=s) for s in SYMS if sigs[s]] or [pd.DataFrame()])
        if tr.empty: continue
        isr = tr[tr.t < OOS]; oos = tr[tr.t >= OOS]
        res.append(dict(win=wn, td=td, loc=loc, fp=fpd["fp"], wick_k=fpd["wick_k"], b2_k=fpd["b2_k"], vol_k=vk,
                        tp=E["tp"], be=E["be"], trail=E["trail"], n=len(tr), n_is=len(isr), n_oos=len(oos),
                        win_rate=(tr.r_net > 0).mean(), r_gross=tr.r_gross.mean(), r_net=tr.r_net.mean(),
                        r_is=isr.r_net.mean() if len(isr) else np.nan, r_oos=oos.r_net.mean() if len(oos) else np.nan,
                        rg_is=isr.r_gross.mean() if len(isr) else np.nan, rg_oos=oos.r_gross.mean() if len(oos) else np.nan,
                        r_maker=(tr.r_gross - (0.0004 + 0.0002 * (tr.why == 1)) / tr.risk_pct).mean(),
                        n_wide=int((tr.risk_pct >= 0.004).sum()), r_wide=tr[tr.risk_pct >= 0.004].r_net.mean(),
                        risk_pct=tr.risk_pct.median()))
    print(wn, td, loc, fpd, vk, len(res), flush=True)
pd.DataFrame(res).to_csv("grid.csv", index=False)
