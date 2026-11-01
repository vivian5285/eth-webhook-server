"""方向1 收缩突破家族：b5.csv 里单独为正(含费>0.1R、两段都正、≥40笔)的 → 逐日盯市接进方案F + 按(策略,周期)邻域平均(该策略该周期全部组合，不挑)。
再和已上实盘的叠加(真假突破4h+布林擠壓8h)合并，看能否再加分。逐日盯市同 combo_mtm.mtm_daily，但给K线带上 sym(mtf 要读同币日线)。"""
import os, sys, ast, numpy as np, pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE); sys.path.insert(0, os.path.join(HERE, "..", "youtube_b3"))
import b4, b5
from combo import stats
from lib import SYMS, bars, backtest
from b3_overlay import fF
from b4_overlay import ov

FN = {"nr": b5.nr, "atrp": b5.atrp, "kcbb": b5.kcbb, "mtf": b5.mtf, "vcp": b5.vcp}


def mtm(fn, tf, kw, idx=fF.index, risk=0.01):
    tot = pd.Series(0.0, index=idx)
    for s in SYMS:
        b = bars(s, tf); b.attrs["sym"] = s; b.attrs["tf"] = tf
        sp = fn(b, **kw); tr = backtest(b, **{k: v for k, v in sp.items() if k in ("sig", "stop", "tgt", "exit_sig", "trail_atr", "limit")})
        d = bars(s, "1d"); dc = pd.Series(d.c.values, index=pd.to_datetime(d.t.values, unit="ms").normalize())
        for r in tr.itertuples():
            size = min(risk / max(r.risk, 1e-4), 3.0)
            t0 = pd.to_datetime(int(r.t), unit="ms"); t1 = pd.to_datetime(int(r.t + r.bars * b4.TFMS[tf]), unit="ms")
            path = dc[t0.normalize():t1.normalize()]
            if not len(path): continue
            path = path.copy(); path.iloc[-1] = r.px; prev = r.ent
            for day, pxd in path.items():
                if day in tot.index: tot[day] += size * r.side * (pxd / prev - 1)
                prev = pxd
            if t1.normalize() in tot.index: tot[t1.normalize()] += size * (r.net - r.gross)
    return tot


if __name__ == "__main__":
    b0 = stats(fF); print(f"方案F {b0[2]:.2f}", flush=True)
    df = pd.read_csv(os.path.join(HERE, "b5.csv")); df = df[df.n.notna()] if "n" in df else df
    pick = df[(df.n >= 40) & (df.r_net > 0.1) & (df.r_is > 0) & (df.r_oos > 0)]
    print("全部", len(df), "入选", len(pick), flush=True); print(pick[["name", "tf", "kw", "n", "r_net", "r_is", "r_oos"]].to_string(), flush=True)
    groups = sorted(set(zip(pick["name"], pick["tf"])))
    cur = (pd.read_pickle(os.path.join(HERE, "brk4h_avg.pkl")) + pd.read_pickle(os.path.join(HERE, "mio8h_avg.pkl"))) / 2
    print("现有叠加(合并1%):", ov(cur, 1.0), flush=True)
    avgs = {}
    for name, tf in groups:
        allkw = df[(df["name"] == name) & (df.tf == tf)]
        ser = [mtm(FN[name], tf, ast.literal_eval(k)) for k in allkw.kw]
        beat = sum(ov(x, 1.0)[0] > b0[2] for x in ser); a = sum(ser) / len(ser); avgs[f"{name} {tf}"] = a
        print(f"== {name} {tf} 全部{len(ser)}组(不挑): {beat}组>方案F；平均 +1% {ov(a, 1.0)} +2% {ov(a, 2.0)} 相关F {a.corr(fF):.2f} 相关现有叠加 {a.corr(cur):.2f}"
              f" | 加进现有叠加(各半) +1% {ov((cur + a) / 2, 1.0)} +2% {ov((cur + a) / 2, 2.0)} | 三者等权 +1.5% {ov((2 * cur + a) / 3, 1.5)}", flush=True)
    pd.DataFrame(avgs).to_pickle(os.path.join(HERE, "b5_avgs.pkl"))
