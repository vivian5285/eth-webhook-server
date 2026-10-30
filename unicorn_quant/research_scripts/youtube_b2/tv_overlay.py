"""第二批 TV 组合里单独为正的几条 → 逐日盯市接进方案F(同 youtube_4ch/overlay.py 口径)。"""
import os, sys, json, numpy as np, pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__)); SC = os.path.join(HERE, "..", "speculation_channel")
sys.path.insert(0, SC); sys.path.insert(0, HERE)
import combo_mtm
combo_mtm.TFMS.update({"2h": 7200e3, "8h": 28800e3})
from combo import stats
from combo_mtm import mtm_daily
import tv_combos as T
F = json.load(open(os.path.join(SC, "planF_series.json")))
a = np.array(F["F"]); s = pd.Series(a[:, 1], index=pd.to_datetime(a[:, 0], unit="ms").normalize())
fF = s[~s.index.duplicated()].asfreq("D").ffill().pct_change().fillna(0)[pd.Timestamp("2022-03-01"):pd.Timestamp("2026-09-30")]
C = [("小Q加密参数 4h MACD离场", T.xiaoq, "4h", dict(exit_mode="macd")), ("小Q加密参数 1h 2R", T.xiaoq, "1h", dict(exit_mode="rr")),
     ("Twin Range+ST+放量1.5 日线", T.twin_range, "1d", dict(vol_k=1.5)), ("Twin Range+ST 4h", T.twin_range, "4h", dict(vol_k=1.0)),
     ("Donchian55+B-Xtrender 日线", T.bxtrender, "1d", dict(dn_n=55)), ("Halftrend+RSI 4h", T.ht_rsi, "4h", dict(rl=50))]
for name, fn, tf, kw in C:
    d = mtm_daily(fn, tf, kw, fF.index); cs = stats(d)
    row = [name, f"单独{cs[0]:.1%}/{cs[1]:.1%}/{cs[2]:.2f}", f"相关F{d.corr(fF):.2f}"]
    for m in (0.5, 1.0, 2.0):
        x = stats(fF + m * d); xi = stats(fF[:"2024-12-31"] + m * d[:"2024-12-31"]); xo = stats(fF["2025-01-01":] + m * d["2025-01-01":])
        row.append(f"{m}%:{x[2]:.2f}({xi[2]:.2f}/{xo[2]:.2f})")
    print(" | ".join(row), flush=True)
