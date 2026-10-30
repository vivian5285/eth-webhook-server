"""四博主候选(单独为正的)逐日盯市接进方案F，同 speculation_channel/combo_mtm 口径(每笔1%风险，方案F不变+候选占闲置资金)。"""
import os, sys, json, numpy as np, pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__)); SC = os.path.join(HERE, "..", "speculation_channel")
sys.path.insert(0, SC); sys.path.insert(0, HERE)
import combo_mtm
combo_mtm.TFMS.update({"2h": 7200e3, "8h": 28800e3})
from combo import stats
from combo_mtm import mtm_daily
import yitai_hist as Y
from batch19 import ribbon
F = json.load(open(os.path.join(SC, "planF_series.json")))
a = np.array(F["F"]); s = pd.Series(a[:, 1], index=pd.to_datetime(a[:, 0], unit="ms").normalize())
fF = s[~s.index.duplicated()].asfreq("D").ffill().pct_change().fillna(0)[pd.Timestamp("2022-03-01"):pd.Timestamp("2026-09-30")]
b0 = stats(fF); print(f"方案F {b0[0]:.1%}/{b0[1]:.1%}/{b0[2]:.2f} 前{stats(fF[:'2024-12-31'])[2]:.2f} 后{stats(fF['2025-01-01':])[2]:.2f}", flush=True)
C = [("已泰 热力图+SMA200+AO 4h", Y.heatmap, "4h", dict(m=3.0, ao_mode="rising")),
     ("已泰 热力图 4h m2", Y.heatmap, "4h", dict(m=2.0, ao_mode="rising")),
     ("已泰 分型突破+放量 4h", Y.fractal_brk, "4h", dict(vol_k=1.5)),
     ("已泰 SuperTrend+HMA200 4h", Y.st_hma, "4h", {}),
     ("已泰 SuperTrend+HMA200 2h", Y.st_hma, "2h", {}),
     ("已泰 锚定VWAP追踪 4h", Y.avwap, "4h", dict(piv=20, exit_mode="trail")),
     ("已泰 锚定VWAP追踪 1d", Y.avwap, "1d", dict(piv=10, exit_mode="trail")),
     ("币哥 均线密集 4h w2.5", ribbon, "4h", dict(w=2.5, m=10, exit_mode="ma20"))]
class _B:  # mtm_daily 调 fn(b, **kw)，部分函数要 b.attrs['sym']
    pass
pn = {}
for name, fn, tf, kw in C:
    def wrapped(b, _fn=fn, **k):
        b.attrs.setdefault("sym", "BTCUSDT"); return _fn(b, **k)
    pn[name] = mtm_daily(wrapped, tf, kw, fF.index); print("done", name, flush=True)
rows = []
for name, d in pn.items():
    cs = stats(d); row = {"候选": name, "单独": f"{cs[0]:.1%}/{cs[1]:.1%}/{cs[2]:.2f}", "相关F": round(d.corr(fF), 2)}
    for m in (0.5, 1.0, 2.0):
        x = stats(fF + m * d); xi = stats(fF[:"2024-12-31"] + m * d[:"2024-12-31"]); xo = stats(fF["2025-01-01":] + m * d["2025-01-01":])
        row[f"{m}%"] = f"{x[2]:.2f}({xi[2]:.2f}/{xo[2]:.2f}) {x[0]:.0%}/{x[1]:.1%}"
    rows.append(row); print(row, flush=True)
pd.DataFrame(rows).to_csv(os.path.join(HERE, "overlay.csv"), index=False)
