"""第五批：单独为正(含费>0.1R、两段都正、≥40笔)的 → 按(策略,周期)全部组合平均(不挑) → 接到"新基准"上比。
新基准 = 方案F + 现行叠加2%(真假突破4h+布林擠壓8h，11-01 实盘)，同窗口比值 7.75(5.33/17.44)。逐日盯市口径同 youtube_b4/b5_overlay.mtm。"""
import os, sys, ast, numpy as np, pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__)); B4 = os.path.join(HERE, "..", "youtube_b4")
sys.path.insert(0, HERE); sys.path.insert(0, B4); sys.path.insert(0, os.path.join(HERE, "..", "youtube_b3"))
import b6, b7, b8
import importlib.util
spec = importlib.util.spec_from_file_location("b4ov5", os.path.join(B4, "b5_overlay.py")); m4 = importlib.util.module_from_spec(spec); spec.loader.exec_module(m4)
import b4
b4.TFMS.update(b6.TFMS)
from combo import stats
from b3_overlay import fF

FN = {"zb_sqz": b6.zb_sqz, "rsi_pb": b6.rsi_pb, "dense": b6.dense, "kc2": b7.kc2, "hma_kdj": b7.hma_kdj, "cci2": b7.cci2, "ce_rsi": b7.ce_rsi,
      "macd_sar": b7.macd_sar, "dmi_brk": b8.dmi_brk, "lwma": b8.lwma, "dual20": b8.dual20}
cur = (pd.read_pickle(os.path.join(B4, "brk4h_avg.pkl")) + pd.read_pickle(os.path.join(B4, "mio8h_avg.pkl"))) / 2
BASE = fF + 2.0 * cur


def ov(d, m, base=BASE):
    x = base + m * d; a, i, o = stats(x), stats(x[:"2024-12-31"]), stats(x["2025-01-01":])
    return round(a[2], 2), f"{i[2]:.2f}/{o[2]:.2f}", f"{a[0]:.0%}/{a[1]:.1%}"


if __name__ == "__main__":
    b0 = ov(cur * 0, 0); print("新基准(方案F+叠加2%)", b0, " 方案F", round(stats(fF)[2], 2), flush=True)
    df = pd.concat([pd.read_csv(os.path.join(HERE, f)) for f in ("b6.csv", "b7.csv", "b8.csv") if os.path.exists(os.path.join(HERE, f))])
    df = df[df["n"].notna()]
    print("全部", len(df), "含费为正", int((df.r_net > 0).sum()), flush=True)
    pick = df[(df.n >= 40) & (df.r_net > 0.1) & (df.r_is > 0) & (df.r_oos > 0)]
    print(pick[["name", "tf", "kw", "n", "r_gross", "r_net", "r_is", "r_oos"]].to_string(), flush=True)
    for name, tf in sorted(set(zip(pick["name"], pick["tf"]))):
        allkw = df[(df["name"] == name) & (df.tf == tf)]
        ser = [m4.mtm(FN[name], tf, ast.literal_eval(k)) for k in allkw.kw]
        a = sum(ser) / len(ser); beat = sum(ov(x, 1.0)[0] > b0[0] for x in ser)
        print(f"== {name} {tf} 全部{len(ser)}组: {beat}组让新基准变好；平均 +0.5% {ov(a, 0.5)} +1% {ov(a, 1.0)} +2% {ov(a, 2.0)} | 单接方案F +1% {ov(a, 1.0, fF)} "
              f"| 相关F {a.corr(fF):.2f} 相关现叠加 {a.corr(cur):.2f}", flush=True)
