"""JiaSheng nBooQWVjTw0「TSS 趋势策略」(自称5年32倍、回撤26%)：日线 EMA12/24。
K线整根脱离两条均线之上(最低价>两线) → 次日开盘做多；整根在两线之下 → 做空；两线之间=不交易区(持仓不动)；
单日大阳(≥8%)/大阴(≤-5%)直接算趋势成立；趋势反向成立时次日开盘离场/反手；不设止损，1倍。
牛熊过滤：减半周期(减半前180天~减半后540天=牛市只做多，其余=熊市只做空)。
和方案F币圈日线腿(EMA8/30)是同一家族，所以直接跟方案F币圈腿比，并测"换掉币圈腿"。数据=币安现货日线(donchian_ens 同一份)。"""
import os, sys, numpy as np, pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
from donchian_ens import daily, F_series, fmt, SYMS, COST

HALV = [pd.Timestamp(x) for x in ("2016-07-09", "2020-05-11", "2024-04-20", "2028-04-15")]


def regime(idx):
    bull = np.zeros(len(idx), bool)
    for h in HALV:
        bull |= (idx >= h - pd.Timedelta(days=180)) & (idx <= h + pd.Timedelta(days=540))
    return bull


def ohlc(sym):
    """donchian_ens 只存了收盘；这里用 VPS 拉的同一份 K 线的高低价(若无则退化成收盘)。"""
    f = os.path.join(HERE, f"{sym}_ohlc.csv")
    if os.path.exists(f):
        a = pd.read_csv(f, header=None, names=["t", "o", "h", "l", "c"])
        a.index = pd.to_datetime(a.t, unit="ms"); return a
    c = daily(sym); return pd.DataFrame({"o": c.shift(1).fillna(c), "h": c, "l": c, "c": c})


def tss_pos(d, fast=12, slow=24, big_up=0.08, big_dn=-0.05, mode="both", touch="candle"):
    c, h, l = d.c.values, d.h.values, d.l.values
    ef = pd.Series(c).ewm(span=fast, adjust=False).mean().values; es = pd.Series(c).ewm(span=slow, adjust=False).mean().values
    hi_l, lo_l = np.maximum(ef, es), np.minimum(ef, es)
    up = (l > hi_l) if touch == "candle" else (c > hi_l); dn = (h < lo_l) if touch == "candle" else (c < lo_l)
    ret = pd.Series(c).pct_change().values
    up |= ret >= big_up; dn |= ret <= big_dn
    bull = regime(d.index)
    pos = np.zeros(len(c)); p = 0
    for i in range(slow, len(c)):
        if up[i]: p = 1
        elif dn[i]: p = -1
        q = p
        if mode == "long": q = max(p, 0)
        elif mode == "halving": q = p if (p == 1 and bull[i]) or (p == -1 and not bull[i]) else 0
        pos[i] = q
    return pd.Series(pos, index=d.index)


def strat(**kw):
    out = []
    for s in SYMS:
        d = ohlc(s); w = tss_pos(d, **kw); r = d.c.pct_change().fillna(0)
        out.append((w.shift(1).fillna(0) * r - COST * w.diff().abs().fillna(0)).rename(s))
    return pd.concat(out, axis=1).fillna(0).mean(axis=1)


if __name__ == "__main__":
    fF, fC = F_series("F"), F_series("crypto")
    for t0 in (pd.Timestamp("2019-01-01"), pd.Timestamp("2022-03-01")):
        t1 = fF.index[-1]; F, C = fF[t0:t1], fC[t0:t1]
        print(f"\n==== 窗口 {t0.date()} ~ {t1.date()} ====\n方案F        {fmt(F)}\n方案F币圈腿  {fmt(C)}", flush=True)
        for kw in (dict(), dict(mode="long"), dict(mode="halving"), dict(touch="close"), dict(touch="close", mode="halving"),
                   dict(fast=8, slow=30), dict(fast=8, slow=30, mode="halving"), dict(fast=10, slow=20), dict(fast=15, slow=30)):
            d = strat(**kw).reindex(F.index).fillna(0); k = C.std() / d.std()
            print(f"{str(kw):45s} 单独(1倍等权) {fmt(d)}  相关币圈腿{d.corr(C):.2f}\n"
                  f"      换掉币圈腿(等波动×{k:.2f}) {fmt(F - C + k * d)}\n      各半(币圈腿/2+TSS/2) {fmt(F - C / 2 + k * d / 2)}", flush=True)
