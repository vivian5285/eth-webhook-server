"""批次19 币哥均线密集 4h 邻域内几组 → 逐日盯市接进方案F(同 combo_mtm 口径)。"""
import json, numpy as np, pandas as pd
from combo import stats, HERE, os
from combo_mtm import mtm_daily
from batch19 import ribbon
from lib import *
F = json.load(open(os.path.join(HERE, "planF_series.json")))
a = np.array(F["F"]); s = pd.Series(a[:, 1], index=pd.to_datetime(a[:, 0], unit="ms").normalize())
fF = s[~s.index.duplicated()].asfreq("D").ffill().pct_change().fillna(0)[pd.Timestamp("2022-03-01"):pd.Timestamp("2026-09-30")]
b0 = stats(fF); print(f"方案F {b0[0]:.1%}/{b0[1]:.1%}/{b0[2]:.2f} 前{stats(fF[:'2024-12-31'])[2]:.2f} 后{stats(fF['2025-01-01':])[2]:.2f}")
C = [(f"w{w} m{m} {ex}", dict(w=w, m=m, exit_mode=ex, rr=4.0)) for w, m, ex in
     [(1.5, 10, "ma20"), (2.0, 10, "ma20"), (2.0, 20, "ma20"), (2.5, 10, "ma20"), (1.5, 20, "rr"), (2.0, 10, "rr")]]
pn = {}
for name, kw in C:
    pn[name] = mtm_daily(ribbon, "4h", kw, fF.index)
pn["6组平均"] = sum(pn.values()) / len(pn)
for name, d in pn.items():
    cs = stats(d); row = [name, f"单独{cs[0]:.1%}/{cs[1]:.1%}/{cs[2]:.2f}", f"相关{d.corr(fF):.2f}"]
    for m in (0.5, 1.0, 2.0):
        x = stats(fF + m * d); xi = stats(fF[:"2024-12-31"] + m * d[:"2024-12-31"]); xo = stats(fF["2025-01-01":] + m * d["2025-01-01":])
        row.append(f"{m}%:{x[2]:.2f}({xi[2]:.2f}/{xo[2]:.2f})")
    print(" | ".join(row), flush=True)
