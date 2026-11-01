"""JiaSheng 真假突破(brk)：4h/8h/1d 单独为正且两段都正 → 接进方案F + 参数邻域。
每组 = 一半 1.5R 止盈 + 一半吊灯追踪(他说的分批)，两笔序列各半相加。邻域：箱长 30/40/50 × 宽 3.5/4/4.5ATR × 大K 1.5/2.5。"""
import os, sys, itertools, numpy as np, pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE); sys.path.insert(0, os.path.join(HERE, "..", "youtube_b3"))
import b4, b4c
import combo_mtm
combo_mtm.TFMS.update(b4.TFMS)
from combo import stats
from combo_mtm import mtm_daily
from lib import summarize
from b3_overlay import fF
from b4_overlay import ov

if __name__ == "__main__":
    b0 = stats(fF); print(f"方案F {b0[2]:.2f}", flush=True)
    rows = []; acc = {}
    for tf, nb, w, bg in itertools.product(("4h", "8h", "1d"), (30, 40, 50), (3.5, 4.0, 4.5), (1.5, 2.5)):
        kw = dict(n_box=nb, wmax=w, big=bg)
        s1 = summarize(b4.run(b4c.brk, tf, half="tp", **kw)); s2 = summarize(b4.run(b4c.brk, tf, half="trail", **kw))
        d = 0.5 * mtm_daily(b4c.brk, tf, dict(kw, half="tp"), fF.index) + 0.5 * mtm_daily(b4c.brk, tf, dict(kw, half="trail"), fF.index)
        acc.setdefault(tf, []).append(d)
        r = dict(tf=tf, kw=str(kw), n=s1.get("n", 0), R_tp=s1.get("r_net"), R_trail=s2.get("r_net"), is_tp=s1.get("r_is"), oos_tp=s1.get("r_oos"),
                 ov05=ov(d, 0.5)[:2], ov10=ov(d, 1.0)[:2], ov20=ov(d, 2.0)[:2], corr=round(d.corr(fF), 2))
        rows.append(r); print(r, flush=True)
    for tf, lst in acc.items():
        a = sum(lst) / len(lst); beat = sum(ov(x, 1.0)[0] > b0[2] for x in lst)
        print(f"== brk {tf} 邻域{len(lst)}组: 过方案F {beat}/{len(lst)}；平均 +0.5% {ov(a, 0.5)} +1% {ov(a, 1.0)} +2% {ov(a, 2.0)} 相关F {a.corr(fF):.2f}", flush=True)
    pd.DataFrame(rows).to_csv(os.path.join(HERE, "b4c_nb.csv"), index=False)
