"""交易伦 破位清单 4h RSI70 几组 → 逐日盯市接进方案F。"""
import os, sys, json, numpy as np, pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__)); SC = os.path.join(HERE, "..", "speculation_channel")
sys.path.insert(0, SC); sys.path.insert(0, HERE)
from combo import stats
from combo_mtm import mtm_daily
from lun_hist import lun_brk
F = json.load(open(os.path.join(SC, "planF_series.json")))
a = np.array(F["F"]); s = pd.Series(a[:, 1], index=pd.to_datetime(a[:, 0], unit="ms").normalize())
fF = s[~s.index.duplicated()].asfreq("D").ffill().pct_change().fillna(0)[pd.Timestamp("2022-03-01"):pd.Timestamp("2026-09-30")]
acc = []
for kw in [dict(rr=2.0, stop_mode="struct", hi_rsi=70), dict(rr=3.0, stop_mode="struct", hi_rsi=70), dict(rr=3.0, stop_mode="bar", hi_rsi=70), dict(rr=2.0, stop_mode="bar", hi_rsi=70)]:
    d = mtm_daily(lun_brk, "4h", kw, fF.index); acc.append(d); cs = stats(d)
    row = [str(kw), f"单独{cs[0]:.1%}/{cs[1]:.1%}/{cs[2]:.2f}", f"相关{d.corr(fF):.2f}"]
    for m in (0.5, 1.0):
        x = stats(fF + m * d); xi = stats(fF[:"2024-12-31"] + m * d[:"2024-12-31"]); xo = stats(fF["2025-01-01":] + m * d["2025-01-01":])
        row.append(f"{m}%:{x[2]:.2f}({xi[2]:.2f}/{xo[2]:.2f})")
    print(" | ".join(row), flush=True)
