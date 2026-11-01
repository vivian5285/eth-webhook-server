"""指標與策略 布林-肯特纳擠壓+KDJ 逆势(zb_sqz) 接到新基准(方案F+叠加2%)明显加分 → 扩大邻域防挑参数 + 分年 + 成本加倍压力测试。
邻域：4h/6h/8h × KDJ周期 14/20/25/30/40 × 回看 10/20/30 × 要不要擠壓 × 多空/只多 = 180组，全部平均(不挑)。"""
import os, sys, itertools, numpy as np, pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import b6
from ov5 import ov, BASE, cur, m4, fF, stats
import lib

if __name__ == "__main__":
    print("新基准", ov(cur * 0, 0), flush=True)
    rows = []; acc = {}
    for tf, kn, lk, ns, sd in itertools.product(("4h", "6h", "8h"), (14, 20, 25, 30, 40), (10, 20, 30), (True, False), (0, 1)):
        kw = dict(kn=kn, look=lk, need_sqz=ns, side=sd)
        sm = lib.summarize(b6.run(b6.zb_sqz, tf, **kw)); d = m4.mtm(b6.zb_sqz, tf, kw)
        acc.setdefault((tf, ns), []).append(d)
        r = dict(tf=tf, kw=str(kw), n=sm.get("n"), Rnet=sm.get("r_net"), r_is=sm.get("r_is"), r_oos=sm.get("r_oos"), ov1=ov(d, 1.0)[:2])
        rows.append(r); print(r, flush=True)
    pd.DataFrame(rows).to_csv(os.path.join(HERE, "b6_nb.csv"), index=False)
    allm = []
    for (tf, ns), lst in sorted(acc.items()):
        a = sum(lst) / len(lst); allm += lst; beat = sum(ov(x, 1.0)[0] > ov(cur * 0, 0)[0] for x in lst)
        yr = ((1 + a).groupby(a.index.year).prod() - 1).round(3).to_dict()
        print(f"== {tf} 擠壓={ns} {len(lst)}组: {beat}组>新基准; 平均 +0.5% {ov(a, 0.5)} +1% {ov(a, 1.0)} +2% {ov(a, 2.0)} 相关F {a.corr(fF):.2f} 各年(1%) {yr}", flush=True)
    A = sum(allm) / len(allm); pd.Series(A).to_pickle(os.path.join(HERE, "zb_sqz_avg.pkl"))
    print(f"==== 全部{len(allm)}组平均 +0.5% {ov(A, 0.5)} +1% {ov(A, 1.0)} +2% {ov(A, 2.0)} 相关F {A.corr(fF):.2f}", flush=True)
    # 成本加倍：吃单+滑点 6.5bp→13bp/边
    lib.FEE, lib.SLIP = 0.001, 0.0003
    A2 = sum(m4.mtm(b6.zb_sqz, tf, dict(kn=kn, look=lk, need_sqz=ns, side=sd)) for tf, kn, lk, ns, sd in
             itertools.product(("4h",), (20, 25, 30), (10, 30), (True, False), (0, 1))) / 24
    print(f"==== 成本加倍(4h 24组) +1% {ov(A2, 1.0)}", flush=True)
