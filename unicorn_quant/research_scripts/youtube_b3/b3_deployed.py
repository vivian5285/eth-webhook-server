"""已上擂台的 4 个配置(neo_box 4h/6h n60 宽7、stiff 6h/8h 阈值50 T3100)逐日盯市接进方案F：整体/前后半/逐年/回撤。"""
import os, sys, numpy as np, pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.join(HERE, "..", "speculation_channel")); sys.path.insert(0, HERE)
import combo_mtm, b3
b3.TFMS["6h"] = 21600e3; combo_mtm.TFMS.update(b3.TFMS)
from combo import stats
from combo_mtm import mtm_daily
from b3_overlay import fF
C = {"区间突破4h": (b3.neo_box, "4h", dict(n=60, wmax=7.0, exit_mode="trail")), "区间突破6h": (b3.neo_box, "6h", dict(n=60, wmax=7.0, exit_mode="trail")),
     "Stiff 6h": (b3.stiff, "6h", dict(exit_mode="trail", event=True, thr=50, t3n=100)), "Stiff 8h": (b3.stiff, "8h", dict(exit_mode="trail", event=True, thr=50, t3n=100))}
S = {k: mtm_daily(fn, tf, kw, fF.index) for k, (fn, tf, kw) in C.items()}
S["4个等权"] = sum(S.values()) / 4
def row(name, r):
    a, i, o = stats(r), stats(r[:"2024-12-31"]), stats(r["2025-01-01":])
    return f"{name:22s} 年化{a[0]:6.1%} 回撤{a[1]:5.1%} 比值{a[2]:5.2f} | 前半{i[2]:5.2f} 后半{o[2]:5.2f}"
print(row("方案F", fF))
for k, d in S.items():
    print(row(k + " 单独", d), f"相关F {d.corr(fF):.2f}")
    for m in (0.5, 1.0, 1.5, 2.0):
        print(row(f"  F + {k} {m}%", fF + m * d))
print("逐年(收益/回撤)：")
for y in ("2022", "2023", "2024", "2025", "2026"):
    out = [y]
    for nm, r in (("F", fF[y]), ("F+4个1%", fF[y] + S["4个等权"][y]), ("F+4个1.5%", fF[y] + 1.5 * S["4个等权"][y]), ("候选自身1%", S["4个等权"][y])):
        eq = (1 + r).cumprod(); out.append(f"{nm} {eq.iloc[-1]-1:+.1%}/{(1-eq/eq.cummax()).max():.1%}")
    print("  ".join(out))
w = (fF + S["4个等权"]); eqF = (1 + fF).cumprod(); eqW = (1 + w).cumprod()
ddF = 1 - eqF / eqF.cummax(); ddW = 1 - eqW / eqW.cummax()
print("最大回撤日期 F:", ddF.idxmax().date(), f"{ddF.max():.1%}", " F+4个1%:", ddW.idxmax().date(), f"{ddW.max():.1%}")
print("F 最大回撤那天 候选当期:", f"{(1+S['4个等权'][eqF[:ddF.idxmax()].idxmax():ddF.idxmax()]).prod()-1:+.2%}")
