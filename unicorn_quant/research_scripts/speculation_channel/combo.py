"""候选策略 × 方案F 组合检验: 相关性 + 按比例加入后的 年化/回撤/比值 变化(2022-01 起, 与候选数据同窗口)。
候选各自作为独立子账户: 每笔冒 1% 风险, 盈亏记在平仓日。"""
import json, os, numpy as np, pandas as pd
from lib import *
from batch01 import hoffman
from batch02 import adx_stoch
from batch07 import knife, ouyang_long
from batch09 import chuanmu
from batch13 import mak_andean
from batch16 import martin
from batch17 import tony

TFMS = {"15m": 900e3, "1h": 3600e3, "4h": 14400e3, "1d": 86400e3}
C = [
    ("ADX50+StochRSI 4h 2R", adx_stoch, "4h", dict(adx_thr=50, rr=2.0, ema_tf_mult=1)),
    ("ADX40+StochRSI 4h 3R", adx_stoch, "4h", dict(adx_thr=40, rr=3.0, ema_tf_mult=1)),
    ("Hoffman IRB 1h 3R", hoffman, "1h", dict(rr=3.0, entry="break", stop_mode="white")),
    ("MAK+Andean 4h 1.5ATR", mak_andean, "4h", dict(exit_mode="rr", stop_atr=1.5)),
    ("欧阳真突破 1d", ouyang_long, "1d", dict(cons=60, rng_max=0.40, vk=1.5)),
    ("Tony MA20+55日突破 1d", tony, "1d", dict(n=55)),
    ("Martin EMA9/21 1d", martin, "1d", dict(exit_mode="ema9", max_stop=0.06)),
    ("Flight接刀 15m", knife, "15m", dict(drop=0.12, look=32, vk=4.0, rr=2.0)),
    ("川沐V字针底 15m", chuanmu, "15m", dict(d5=0.04, u2=0.02, rr=3.0)),
]


def daily_pnl(tr, tf, risk=0.01):
    ex = pd.to_datetime((tr.t + tr.bars * TFMS[tf]).astype("int64"), unit="ms").dt.normalize()
    return (risk * tr.r_net.clip(-5, 50)).groupby(ex).sum()


def stats(r):
    eq = (1 + r).cumprod(); yrs = len(r) / 365.25
    cagr = eq.iloc[-1] ** (1 / yrs) - 1; mdd = (1 - eq / eq.cummax()).max()
    return cagr, mdd, cagr / mdd if mdd > 0 else np.nan


if __name__ == "__main__":
    F = json.load(open(os.path.join(HERE, "planF_series.json")))
    def ser(k):
        a = np.array(F[k]); s = pd.Series(a[:, 1], index=pd.to_datetime(a[:, 0], unit="ms").normalize())
        return s[~s.index.duplicated()].asfreq("D").ffill().pct_change().fillna(0)
    fF, fC = ser("F"), ser("crypto")
    start, end = pd.Timestamp("2022-03-01"), min(fF.index[-1], pd.Timestamp("2026-09-30"))
    fF, fC = fF[start:end], fC[start:end]
    base = stats(fF); basec = stats(fC)
    print(f"方案F 同窗口 年化{base[0]:.1%} 回撤{base[1]:.1%} 比值{base[2]:.2f} | 币圈腿 {basec[0]:.1%}/{basec[1]:.1%}/{basec[2]:.2f}")
    rows = []; pn = {}
    for name, fn, tf, kw in C:
        tr = run_all(fn, tf, **kw)
        d = daily_pnl(tr, tf).reindex(fF.index).fillna(0); pn[name] = d
        cs = stats(d); corrF = d.corr(fF); corrC = d.corr(fC)
        wk = pd.DataFrame({"c": d, "f": fF}).resample("W").sum(); corrW = wk.c.corr(wk.f)
        row = dict(候选=name, 笔数=len(tr), 单独年化=round(cs[0], 3), 单独回撤=round(cs[1], 3), 单独比值=round(cs[2], 2),
                   日相关F=round(corrF, 2), 周相关F=round(corrW, 2), 日相关币圈腿=round(corrC, 2))
        for w in (0.1, 0.2, 0.3):
            bl = stats((1 - w) * fF + w * d); row[f"加{int(w*100)}%比值"] = round(bl[2], 2); row[f"加{int(w*100)}%年化"] = round(bl[0], 3)
        rows.append(row); print(row, flush=True)
    # 急跌反弹两种合并(等权)
    mr = (pn["Flight接刀 15m"] + pn["川沐V字针底 15m"]) / 2
    cs = stats(mr); row = dict(候选="急跌反弹(接刀+V底 等权)", 单独年化=round(cs[0], 3), 单独回撤=round(cs[1], 3), 单独比值=round(cs[2], 2), 日相关F=round(mr.corr(fF), 2))
    for w in (0.1, 0.2, 0.3):
        bl = stats((1 - w) * fF + w * mr); row[f"加{int(w*100)}%比值"] = round(bl[2], 2); row[f"加{int(w*100)}%年化"] = round(bl[0], 3)
    rows.append(row); print(row)
    out = pd.DataFrame(rows); out.to_csv("combo.csv", index=False)
    print(f"\n基准 方案F 比值 {base[2]:.2f}")
    print(out.to_string(index=False))
