"""第四批接进方案F过线的两个候选做参数邻域(防挑参数)：
  mj 只做多+J提前离场：周期 6h/8h/12h × MACD(12,26,9)/(8,21,5)/(16,34,9) × J中轴 45/50/55 × KDJ 9/14
  s2560 4h/8h 只做多 跌破MA25离场：回踩容差 0.3/0.5/1.0 ATR × 出场 ma25/trail
每组：单独 R、+0.5/1/2% 接进方案F 比值(前/后)；再看邻域整体平均序列。"""
import os, sys, itertools, numpy as np, pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE); sys.path.insert(0, os.path.join(HERE, "..", "youtube_b3"))
import b4, b4b
import combo_mtm
combo_mtm.TFMS.update(b4.TFMS)
from combo import stats
from combo_mtm import mtm_daily
from lib import summarize
from b3_overlay import fF
from b4_overlay import ov

G = {
    "mj": [(b4b.mj, tf, dict(early=True, side=1, mp=mp, jmid=j, kn=k)) for tf, mp, j, k in
           itertools.product(("6h", "8h", "12h"), ((12, 26, 9), (8, 21, 5), (16, 34, 9)), (45, 50, 55), (9, 14))],
    "s2560": [(b4b.s2560, tf, dict(exit_mode=em, side=1, touch=t)) for tf, em, t in
              itertools.product(("4h", "8h"), ("ma25", "trail"), (0.3, 0.5, 1.0))],
}

if __name__ == "__main__":
    b0 = stats(fF); print(f"方案F {b0[2]:.2f}", flush=True)
    rows = []; avg = {}
    for g, lst in G.items():
        acc = {}
        for fn, tf, kw in lst:
            sm = summarize(b4.run(fn, tf, **kw)); d = mtm_daily(fn, tf, kw, fF.index); acc.setdefault(tf, []).append(d)
            r = dict(grp=g, tf=tf, kw=str(kw), n=sm["n"], R0=sm["r_gross"], Rnet=sm["r_net"], r_is=sm["r_is"], r_oos=sm["r_oos"],
                     ov05=ov(d, 0.5)[:2], ov10=ov(d, 1.0)[:2], ov20=ov(d, 2.0)[:2], corr=round(d.corr(fF), 2))
            rows.append(r); print(r, flush=True)
        for tf, lst2 in acc.items():
            a = sum(lst2) / len(lst2); avg[f"{g} {tf}"] = a
            beat = sum(ov(x, 1.0)[0] > b0[2] for x in lst2)
            print(f"== {g} {tf} 邻域{len(lst2)}组: 过方案F {beat}/{len(lst2)}；平均序列 +0.5% {ov(a, 0.5)}  +1% {ov(a, 1.0)}  +2% {ov(a, 2.0)}  相关F {a.corr(fF):.2f}", flush=True)
        allg = sum(sum(v) for v in acc.values()) / sum(len(v) for v in acc.values())
        print(f"==== {g} 全部{sum(len(v) for v in acc.values())}组平均 +0.5% {ov(allg, 0.5)}  +1% {ov(allg, 1.0)}  +2% {ov(allg, 2.0)}", flush=True)
    pd.DataFrame(rows).to_csv(os.path.join(HERE, "b4_nb.csv"), index=False)
    pd.DataFrame(avg).to_pickle(os.path.join(HERE, "b4_nb_avg.pkl"))
