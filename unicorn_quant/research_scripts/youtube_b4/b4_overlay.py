"""第四批里单独为正(含费 R>0.1、前后两段都>0、n≥40)的组合 → 逐日盯市接进方案F，看比值能否超过方案F。
口径同 youtube_b3/b3_overlay.py：每笔冒 1% 风险(名义上限3倍)，叠加 0.5/1/2 份；再对过线的做参数邻域平均(防挑参数)。"""
import os, sys, ast, glob, numpy as np, pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE); sys.path.insert(0, os.path.join(HERE, "..", "youtube_b3"))
import b4, b4b, b4c, b4d
import combo_mtm
combo_mtm.TFMS.update(b4.TFMS)
from combo import stats
from combo_mtm import mtm_daily
from b3_overlay import fF

FN = {"fib": b4.fib, "rj": b4.rj, "yuchi": b4.yuchi, "mj": b4b.mj, "s2560": b4b.s2560, "cis": b4b.cis, "macd200": b4c.macd200, "brk": b4c.brk, "msmc": b4c.msmc, "mio_bb": b4d.mio_bb, "ema1664": b4d.ema1664}


def ov(d, m):
    x = stats(fF + m * d); xi = stats(fF[:"2024-12-31"] + m * d[:"2024-12-31"]); xo = stats(fF["2025-01-01":] + m * d["2025-01-01":])
    return round(x[2], 2), f"{xi[2]:.2f}/{xo[2]:.2f}", f"{x[0]:.0%}/{x[1]:.1%}"


if __name__ == "__main__":
    b0, bi, bo = stats(fF), stats(fF[:"2024-12-31"]), stats(fF["2025-01-01":])
    print(f"方案F 同窗口 {b0[0]:.1%}/{b0[1]:.1%}/比值{b0[2]:.2f} 前{bi[2]:.2f} 后{bo[2]:.2f}", flush=True)
    df = pd.concat([pd.read_csv(f) for f in glob.glob(os.path.join(HERE, "b4*.csv")) if "overlay" not in f]).drop_duplicates(["name", "tf", "kw"])
    print("全部组合", len(df), "含费为正", int((df.r_net > 0).sum()), flush=True)
    pick = df[(df.n >= 40) & (df.r_net > 0.1) & (df.r_is > 0) & (df.r_oos > 0)].sort_values("r_net", ascending=False)
    if len(sys.argv) > 1: pick = pick[pick["name"].isin(sys.argv[1].split(","))]   # 只跑指定的几个策略
    print(pick[["name", "tf", "kw", "n", "win", "r_gross", "r_net", "r_is", "r_oos", "cagr", "mdd"]].to_string(), flush=True)
    rows = []; series = {}
    for _, p in pick.iterrows():
        d = mtm_daily(FN[p["name"]], p.tf, ast.literal_eval(p.kw), fF.index); key = f"{p['name']} {p.tf} {p.kw}"; series[key] = d
        cs = stats(d); row = {"候选": key, "n": p.n, "Rnet": p.r_net, "单独": f"{cs[0]:.0%}/{cs[1]:.1%}/{cs[2]:.2f}", "相关F": round(d.corr(fF), 2)}
        for m in (0.5, 1.0, 2.0):
            r_, ph, ad = ov(d, m); row[f"+{m}%"] = r_; row[f"+{m}% 前/后"] = ph; row[f"+{m}% 年化/回撤"] = ad
        rows.append(row); print(row, flush=True)
    out = pd.DataFrame(rows); out.to_csv(os.path.join(HERE, "b4_overlay%s.csv" % ("_" + sys.argv[1].replace(",", "_") if len(sys.argv) > 1 else "")), index=False)
    pd.DataFrame(series).to_pickle(os.path.join(HERE, "b4_overlay_series.pkl"))
    # 邻域：同一策略+周期的全部入选组合取平均序列(不只挑最好的那组)
    if len(out):
        out["grp"] = out["候选"].str.split(" ").str[:2].str.join(" ")
        for g, sub in out.groupby("grp"):
            avg = sum(series[k] for k in sub["候选"]) / len(sub)
            print(f"== 邻域 {g} ({len(sub)}组平均): " + "  ".join(f"+{m}% {ov(avg, m)}" for m in (0.5, 1.0, 2.0)), flush=True)
