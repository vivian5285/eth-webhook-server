"""批次12 (2024-12..2025-06): Pradeep Bonde 本垒打动量(2Lynch) / 川沐 批量做空山寨(偏空环境)。"""
import os, glob, itertools, pandas as pd, numpy as np
from lib import *
from batch01 import shift
from batch06 import stock_bars, CRYPTO


# ---- 57 本垒打: 当日涨≥ret, 前一日没涨, 前5日窄幅, 收在高位(可选放量) → 收盘买, 止损当日低, 持有 hold 天或目标 tgt ----
def burst(b, ret=0.04, hold=5, tgt_pct=0.12, vk=0.0, narrow=0.10):
    o, h, l, c = b.o.values, b.h.values, b.l.values, b.c.values
    r1 = c / shift(c) - 1; r0 = shift(c) / shift(c, 2) - 1
    rng5 = (pd.Series(h).rolling(5).max().shift(1) - pd.Series(l).rolling(5).min().shift(1)).values / shift(c)
    cond = (r1 >= ret) & (r0 <= 0.0) & (rng5 <= narrow) & ((c - l) >= 0.7 * (h - l))
    if vk > 0 and "v" in b: cond &= b.v.values >= vk * shift(sma(b.v.values, 50))
    return dict(sig=np.where(cond, 1, 0), stop=l, tgt=c * (1 + tgt_pct), max_bars=hold)


# ---- 59 批量做空山寨: BTC 偏空(收盘<SMA)时等权做空全部山寨, 单币+40%止损/-50%止盈, 转多全平 ----
def alt_basket_short(regime_ma=50, sl=0.40, tp=0.50, min_hist=60, fee=0.0013, use_regime=True):
    A = {os.path.basename(f)[:-4]: pd.read_pickle(f) for f in glob.glob(os.path.join(HERE, "alts1d", "*.pkl"))}
    btc = A.pop("BTCUSDT"); A.pop("ETHUSDT", None)
    idx = btc.t.values; bc = pd.Series(btc.c.values, index=idx)
    bear = (bc < bc.rolling(regime_ma).mean()) if use_regime else pd.Series(True, index=idx)
    C = pd.DataFrame({s: pd.Series(d.c.values, index=d.t.values) for s, d in A.items()}).reindex(idx)
    H = pd.DataFrame({s: pd.Series(d.h.values, index=d.t.values) for s, d in A.items()}).reindex(idx)
    L = pd.DataFrame({s: pd.Series(d.l.values, index=d.t.values) for s, d in A.items()}).reindex(idx)
    age = C.notna().cumsum()
    eq = 1.0; curve = []; pos = {}  # sym -> (entry, weight)
    for k in range(1, len(idx)):
        day_ret = 0.0
        for s, (e, w) in list(pos.items()):
            hi, lo, cl, pc = H[s].iloc[k], L[s].iloc[k], C[s].iloc[k], C[s].iloc[k - 1]
            if np.isnan(cl): continue
            if hi >= e * (1 + sl):
                day_ret += w * -(e * (1 + sl) / pc - 1) - w * fee / 2; del pos[s]; continue
            if lo <= e * (1 - tp):
                day_ret += w * -(e * (1 - tp) / pc - 1) - w * fee / 2; del pos[s]; continue
            day_ret += w * -(cl / pc - 1)
        eq *= 1 + day_ret
        if not bear.iloc[k - 1] and pos:  # 转多: 全平(按今天收盘近似)
            eq *= 1 - fee / 2 * sum(w for _, w in pos.values()); pos = {}
        elif bear.iloc[k] and not pos:
            elig = [s for s in C.columns if age[s].iloc[k] >= min_hist and not np.isnan(C[s].iloc[k])]
            if elig:
                w = 1.0 / len(elig); pos = {s: (C[s].iloc[k], w) for s in elig}; eq *= 1 - fee / 2
        curve.append(eq)
    cv = np.array(curve); yrs = len(cv) / 365.25
    tt = pd.to_datetime(idx[1:], unit="ms")
    yearly = pd.Series(cv, index=tt).resample("YE").last().pct_change().fillna(pd.Series(cv, index=tt).resample("YE").last().iloc[0] - 1)
    return dict(cagr=round(cv[-1] ** (1 / yrs) - 1, 3), mdd=round((1 - cv / np.maximum.accumulate(cv)).max(), 3),
                expo=round(bear.mean(), 2), n_alts=len(A), yearly={str(k.year): round(v, 3) for k, v in yearly.items()})


if __name__ == "__main__":
    rows = []
    SB = stock_bars()
    for univ, U in (("美股78", {s: b for s, b in SB.items() if s not in CRYPTO}),):
        for ret, hold, tg in itertools.product((0.04, 0.06), (3, 5), (0.08, 0.20)):
            tr = pd.concat([backtest(b, **burst(b, ret=ret, hold=hold, tgt_pct=tg)).assign(sym=s) for s, b in U.items()], ignore_index=True)
            s = summarize(tr); s.update(strategy="本垒打动量(2Lynch)", univ=univ, ret=ret, hold=hold, tgt=tg); rows.append(s)
            print("本垒打", univ, ret, hold, tg, {k: s.get(k) for k in ("n", "win", "r_gross", "r_net", "r_is", "r_oos", "cagr", "mdd")}, flush=True)
    for ret, hold, tg, vk in itertools.product((0.06, 0.10), (3, 5), (0.12, 0.25), (0, 2.0)):
        s = summarize(run_all(burst, "1d", ret=ret, hold=hold, tgt_pct=tg, vk=vk, narrow=0.20))
        s.update(strategy="本垒打动量(2Lynch)", univ="币圈5币日线2022起", ret=ret, hold=hold, tgt=tg, vk=vk); rows.append(s)
        print("本垒打 币", ret, hold, tg, vk, {k: s.get(k) for k in ("n", "win", "r_gross", "r_net", "r_is", "r_oos")}, flush=True)
    pd.DataFrame(rows).to_csv("batch12.csv", index=False)
    out = []
    for ma, sl, tp, ur in itertools.product((50, 100), (0.4, 0.2), (0.5, 0.3), (True, False)):
        r = alt_basket_short(regime_ma=ma, sl=sl, tp=tp, use_regime=ur); r.update(ma=ma, sl=sl, tp=tp, regime=ur); out.append(r); print("批量空山寨", r, flush=True)
    pd.DataFrame(out).to_csv("batch12_altshort.csv", index=False)
