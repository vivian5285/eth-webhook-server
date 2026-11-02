"""BNF 25日均线负乖离抄底(b10.bnf) 接新基准全部提升 → 扩大邻域(4h/6h/8h/1d × 均线20/25/30天 × 乖离10~30% × 回收0/5/10% × 最长持有5/10/20天 = 540组)，
全部平均不挑；看分年、笔数、和上一批逆势候选(擠壓+KDJ zb_sqz)的相关/合并。"""
import os, sys, itertools, numpy as np, pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE); sys.path.insert(0, os.path.join(HERE, "..", "youtube_b5"))
import b10
from ov5 import ov, cur, m4, fF, stats
import b4, lib
b4.TFMS.update(b10.TFMS)

if __name__ == "__main__":
    acc = {}; rows = []
    for tf, nd, thr, ex, hold in itertools.product(("4h", "6h", "8h", "1d"), (20, 25, 30), (0.10, 0.15, 0.20, 0.25, 0.30), (0.0, 0.05, 0.10), (5, 10, 20)):
        kw = dict(thr=thr, ex=ex, hold=hold, n_days=nd)
        d = m4.mtm(b10.bnf, tf, kw); acc.setdefault(tf, []).append(d)
        sm = lib.summarize(b10.run(b10.bnf, tf, **kw))
        rows.append(dict(tf=tf, kw=str(kw), n=sm.get("n"), Rnet=sm.get("r_net"), r_is=sm.get("r_is"), r_oos=sm.get("r_oos"), ov1=ov(d, 1.0)[0]))
    pd.DataFrame(rows).to_csv(os.path.join(HERE, "b10_nb.csv"), index=False)
    allm = []
    for tf, lst in acc.items():
        a = sum(lst) / len(lst); allm += lst; beat = sum(ov(x, 1.0)[0] > 7.75 for x in lst)
        sub = [r for r in rows if r["tf"] == tf]; ns = [r["n"] or 0 for r in sub]
        print(f"== {tf} {len(lst)}组: {beat}组>新基准; 平均 +0.5% {ov(a, 0.5)} +1% {ov(a, 1.0)} +2% {ov(a, 2.0)} 相关F {a.corr(fF):.2f} 笔数中位{int(np.median(ns))} "
              f"各年(1%) {((1 + a).groupby(a.index.year).prod() - 1).round(3).to_dict()}", flush=True)
    A = sum(allm) / len(allm); A.to_pickle(os.path.join(HERE, "bnf_avg.pkl"))
    print(f"==== 全部{len(allm)}组平均 +0.5% {ov(A, 0.5)} +1% {ov(A, 1.0)} +2% {ov(A, 2.0)}", flush=True)
    zb = pd.read_pickle(os.path.join(HERE, "..", "youtube_b5", "zb_sqz_avg.pkl")).reindex(fF.index).fillna(0)
    print(f"与擠壓+KDJ候选相关 {A.corr(zb):.2f}；两者各半 +1% {ov((A + zb) / 2, 1.0)} +2% {ov((A + zb) / 2, 2.0)}", flush=True)
