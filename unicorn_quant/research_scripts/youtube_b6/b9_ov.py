"""第六批：单独为正(含费>0.1R、两段都正、≥40笔)的 → 按(策略,周期)全部组合平均(不挑) → 接到新基准(方案F+叠加2%，7.75)上比。"""
import os, sys, ast, pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE); sys.path.insert(0, os.path.join(HERE, "..", "youtube_b5"))
import b9
from ov5 import ov, cur, m4, fF, stats
import b4
b4.TFMS.update(b9.TFMS)
FN = {"kj_mtf": b9.kj_mtf, "ans_bb": b9.ans_bb, "ans_srsi": b9.ans_srsi}

if __name__ == "__main__":
    print("新基准", ov(cur * 0, 0), flush=True)
    df = pd.read_csv(os.path.join(HERE, "b9.csv")); df = df[df["n"].notna()]
    print("全部", len(df), "含费为正", int((df.r_net > 0).sum()), flush=True)
    print(df.groupby(["name", "tf"]).r_net.mean().round(3).unstack().to_string(), flush=True)
    pick = df[(df.n >= 40) & (df.r_net > 0.1) & (df.r_is > 0) & (df.r_oos > 0)]
    print(pick[["name", "tf", "kw", "n", "r_net", "r_is", "r_oos"]].to_string(), flush=True)
    for name, tf in sorted(set(zip(pick["name"], pick["tf"]))):
        allkw = df[(df["name"] == name) & (df.tf == tf)]
        ser = [m4.mtm(FN[name], tf, ast.literal_eval(k)) for k in allkw.kw]
        a = sum(ser) / len(ser); beat = sum(ov(x, 1.0)[0] > ov(cur * 0, 0)[0] for x in ser)
        print(f"== {name} {tf} 全部{len(ser)}组: {beat}组让新基准变好；平均 +0.5% {ov(a, 0.5)} +1% {ov(a, 1.0)} | 相关F {a.corr(fF):.2f} 相关现叠加 {a.corr(cur):.2f}", flush=True)
