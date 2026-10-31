"""接进方案F 两段都升的 3 个候选做参数邻域(防挑参数)：线性回归斜率 4h / T3+Trendilo+Stiffness 8h 追踪 / 卫央区间突破 4h 追踪。
每个邻域：单独 R、+0.5% 接进方案F 比值(前/后)；再看邻域平均序列和三者合并。"""
import os, sys, json, itertools, numpy as np, pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__)); SC = os.path.join(HERE, "..", "speculation_channel")
sys.path.insert(0, SC); sys.path.insert(0, HERE)
import combo_mtm
import b3, b3_yj
b3.TFMS["6h"] = 21600e3; combo_mtm.TFMS.update(b3.TFMS)
from combo import stats
from combo_mtm import mtm_daily
from lib import summarize
from b3_overlay import fF

G = {
    "lrs": [(b3_yj.lrs_tr, tf, dict(n=n)) for tf, n in itertools.product(("2h", "4h", "6h"), (30, 40, 50, 60, 80))],
    "stiff": [(b3.stiff, tf, dict(exit_mode="trail", event=True, thr=th, t3n=t)) for tf, th, t in itertools.product(("6h", "8h", "12h"), (40, 50, 60), (50, 100, 150))],
    "neo_box": [(b3.neo_box, tf, dict(n=n, wmax=w, exit_mode="trail")) for tf, n, w in itertools.product(("4h", "6h"), (40, 60, 80), (6.0, 7.0, 8.0))],
}


def ov(d, m):
    x = stats(fF + m * d); xi = stats(fF[:"2024-12-31"] + m * d[:"2024-12-31"]); xo = stats(fF["2025-01-01":] + m * d["2025-01-01":])
    return f"{x[2]:.2f}({xi[2]:.2f}/{xo[2]:.2f})"


if __name__ == "__main__":
    b0 = stats(fF); print(f"方案F {b0[2]:.2f}", flush=True)
    avg = {}; rows = []
    for g, lst in G.items():
        acc = []
        for fn, tf, kw in lst:
            sm = summarize(b3.run(fn, tf, **kw)); d = mtm_daily(fn, tf, kw, fF.index); acc.append(d)
            r = dict(grp=g, tf=tf, kw=str(kw), n=sm["n"], R0=sm["r_gross"], Rnet=sm["r_net"], r_is=sm["r_is"], r_oos=sm["r_oos"], ov05=ov(d, 0.5), ov10=ov(d, 1.0))
            rows.append(r); print(r, flush=True)
        avg[g] = sum(acc) / len(acc)
        print(f"== {g} 邻域{len(acc)}组平均: +0.5% {ov(avg[g], 0.5)}  +1% {ov(avg[g], 1.0)}  +2% {ov(avg[g], 2.0)} 相关F {avg[g].corr(fF):.2f}", flush=True)
    allm = sum(avg.values()) / 3
    for m in (0.5, 1.0, 1.5, 2.0):
        print(f"== 三者合并平均 +{m}%: {ov(allm, m)}", flush=True)
    pd.DataFrame(rows).to_csv(os.path.join(HERE, "b3_nb.csv"), index=False)
    pd.DataFrame(avg).to_pickle(os.path.join(HERE, "b3_nb_avg.pkl"))
