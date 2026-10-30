"""QQQ/TQQQ 日线候选 → 逐日盯市接进方案F(同窗口)，并和方案F自己的美股腿比。另用 Yahoo 1h(近730天)粗看他主打的小周期 EMA5/30、MACD。"""
import os, sys, json, numpy as np, pandas as pd, urllib.request
HERE = os.path.dirname(os.path.abspath(__file__)); SC = os.path.join(HERE, "..", "speculation_channel")
sys.path.insert(0, HERE)
from qqq_tqqq import yahoo, run, ema, regime_lev, dyn_alloc, macd_sig, stats
F = json.load(open(os.path.join(SC, "planF_series.json")))
def ser(k):
    a = np.array(F[k]); s = pd.Series(a[:, 1], index=pd.to_datetime(a[:, 0], unit="ms").normalize())
    return s[~s.index.duplicated()].asfreq("D").ffill().pct_change().fillna(0)
fF = ser("F")["2022-03-01":"2026-09-30"]; fS = ser("stock")["2022-03-01":"2026-09-30"]
q = yahoo("QQQ"); rf = (yahoo("^IRX").c / 100).reindex(q.index).ffill().fillna(0.02)
one = pd.Series(1.0, index=q.index); three = pd.Series(3.0, index=q.index)
C = {"EMA5/30 1x": run(q, (ema(q.c, 5) > ema(q.c, 30)).astype(float), one, rf),
     "EMA5/30 3x": run(q, (ema(q.c, 5) > ema(q.c, 30)).astype(float), three, rf),
     "EMA5/30 牛150 3x/熊1x": run(q, (ema(q.c, 5) > ema(q.c, 30)).astype(float), regime_lev(q, 150), rf),
     "动态分配 牛200 T70/熊T5": dyn_alloc(q, 200, 0.7, 0.05, rf),
     "长持QQQ": run(q, one, one, rf)}
s0 = stats(fF); print(f"方案F 同窗口 {s0[0]:.1%}/{s0[1]:.1%}/{s0[2]:.2f}；方案F美股腿自己 {stats(fS)[0]:.1%}/{stats(fS)[1]:.1%}/{stats(fS)[2]:.2f}，与F相关{fS.corr(fF):.2f}")
for k, r in C.items():
    d = r.reindex(fF.index).fillna(0); cs = stats(d)
    row = [k, f"单独{cs[0]:.1%}/{cs[1]:.1%}/{cs[2]:.2f}", f"相关F{d.corr(fF):.2f}", f"相关美股腿{d.corr(fS):.2f}"]
    for w in (0.1, 0.25, 0.5):
        x = stats(fF + w * d); xi = stats(fF[:"2024-12-31"] + w * d[:"2024-12-31"]); xo = stats(fF["2025-01-01":] + w * d["2025-01-01":])
        row.append(f"+{w:.0%}:{x[2]:.2f}({xi[2]:.2f}/{xo[2]:.2f})")
    print(" | ".join(row), flush=True)
# 1h 近730天
u = "https://query1.finance.yahoo.com/v8/finance/chart/QQQ?range=730d&interval=1h"
j = json.load(urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0"}), timeout=30))["chart"]["result"][0]
qq = j["indicators"]["quote"][0]
h = pd.DataFrame({"o": qq["open"], "c": qq["close"]}, index=pd.to_datetime(j["timestamp"], unit="s")).dropna()
ret = h.c.pct_change().fillna(0)
for name, sig in [("1h EMA5/30", (ema(h.c, 5) > ema(h.c, 30)).astype(float)), ("1h EMA3/65(他的'365')", (ema(h.c, 3) > ema(h.c, 65)).astype(float)),
                  ("1h MACD", macd_sig(h.c)), ("1h 长持", pd.Series(1.0, index=h.index))]:
    P = sig.shift(1).fillna(0); r = P * ret - 0.0002 * P.diff().abs().fillna(0)
    dr = (1 + r).groupby(r.index.normalize()).prod() - 1; s = stats(dr)
    print(f"{name}: {h.index[0].date()}~{h.index[-1].date()} 年化{s[0]:.1%} 回撤{s[1]:.1%} 比值{s[2]:.2f} 换仓{int(P.diff().abs().sum())}次")
