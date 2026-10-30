"""Twin Range+Supertrend+放量 日线：放量倍数邻域 + 8h/12h 周期，逐日盯市接进方案F。"""
import os, sys, json, numpy as np, pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__)); SC = os.path.join(HERE, "..", "speculation_channel")
sys.path.insert(0, SC); sys.path.insert(0, HERE)
import combo_mtm
combo_mtm.TFMS.update({"8h": 28800e3, "12h": 43200e3})
from combo import stats
from combo_mtm import mtm_daily
from lib import summarize
import tv_combos as T
F = json.load(open(os.path.join(SC, "planF_series.json")))
a = np.array(F["F"]); s = pd.Series(a[:, 1], index=pd.to_datetime(a[:, 0], unit="ms").normalize())
fF = s[~s.index.duplicated()].asfreq("D").ffill().pct_change().fillna(0)[pd.Timestamp("2022-03-01"):pd.Timestamp("2026-09-30")]
acc = []
for tf, vk in [("1d", 1.2), ("1d", 1.3), ("1d", 1.5), ("1d", 1.7), ("1d", 2.0), ("12h", 1.5), ("8h", 1.5)]:
    sm = summarize(T.run(T.twin_range, tf, vol_k=vk))
    d = mtm_daily(T.twin_range, tf, dict(vol_k=vk), fF.index); acc.append(d)
    x = stats(fF + d); xi = stats(fF[:"2024-12-31"] + d[:"2024-12-31"]); xo = stats(fF["2025-01-01":] + d["2025-01-01":])
    print(f"{tf} vk{vk}: n={sm['n']} R0={sm['r_gross']} Rnet={sm['r_net']} is={sm['r_is']} oos={sm['r_oos']} | +1%: {x[2]:.2f}({xi[2]:.2f}/{xo[2]:.2f}) 相关{d.corr(fF):.2f}", flush=True)
avg = sum(acc[:5]) / 5
for m in (0.5, 1.0, 2.0):
    x = stats(fF + m * avg); xi = stats(fF[:"2024-12-31"] + m * avg[:"2024-12-31"]); xo = stats(fF["2025-01-01":] + m * avg["2025-01-01":])
    print(f"日线5档平均 +{m}%: {x[2]:.2f}({xi[2]:.2f}/{xo[2]:.2f}) {x[0]:.0%}/{x[1]:.1%}")
