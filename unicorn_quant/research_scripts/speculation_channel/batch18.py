"""批次18 (2026-07..10): Adhikari 大盘10日线过滤突破 / Ted Zhang 四阶段 / Patrick Nill PBD / Bit浪浪 龙头 / David MACD三背离+斐波那契。"""
import os, glob, itertools, pandas as pd, numpy as np
from lib import *
from batch01 import shift
from batch06 import stock_bars, CRYPTO, qull_trades


# ---- 93 Ted Zhang 四阶段: 周线 SMA10/20/30/40 ≈ 日线 SMA50/100/150/200; 第二阶段(价格在上且多头排列、30周向上)持有 ----
def stage2(b, exit_ma=150):
    c = b.c.values; s10, s20, s30, s40 = (sma(c, n) for n in (50, 100, 150, 200)); a = atr(b)
    st2 = (c > s10) & (s10 > s20) & (s20 > s30) & (s30 > s40) & (s30 > shift(s30, 20))
    sig = np.where(st2 & ~np.r_[False, st2[:-1]], 1, 0)
    ex_ma = {50: s10, 150: s30}[exit_ma]
    return dict(sig=sig, stop=c - 4 * a, exit_sig=np.where(c < ex_ma, 1, 0))


# ---- 111 David: MACD快线顶/底背离(+RSI/量背离计分) → 跌破新高段 23.6% 回撤进场, 止损新高外, 目标 61.8% ----
def david(b, piv=5, min_score=1, tgt_fib=0.618):
    h, l, c, v = b.h.values, b.l.values, b.c.values, b.v.values
    m, _, _ = macd(c); r = rsi(c, 14); a = atr(b); n = len(c)
    sig = np.zeros(n); stop = np.full(n, np.nan); tgt = np.full(n, np.nan)
    PH = []; PL = []; arm = None
    for i in range(2 * piv + 1, n):
        j = i - piv
        if h[j] == h[j - piv:i + 1].max():
            PH.append(j)
            if len(PH) >= 2:
                p, q = PH[-2], PH[-1]
                if h[q] > h[p] and m[q] < m[p]:
                    sc = 1 + (r[q] < r[p]) + (v[q] < v[p])
                    lo = l[p:q + 1].min(); rng_ = h[q] - lo
                    if sc >= min_score and rng_ > 0: arm = (-1, h[q], h[q] - 0.236 * rng_, h[q] - tgt_fib * rng_, i)
        if l[j] == l[j - piv:i + 1].min():
            PL.append(j)
            if len(PL) >= 2:
                p, q = PL[-2], PL[-1]
                if l[q] < l[p] and m[q] > m[p]:
                    sc = 1 + (r[q] > r[p]) + (v[q] < v[p])
                    hi = h[p:q + 1].max(); rng_ = hi - l[q]
                    if sc >= min_score and rng_ > 0: arm = (1, l[q], l[q] + 0.236 * rng_, l[q] + tgt_fib * rng_, i)
        if arm is None: continue
        side, ext, trig, tg, born = arm
        if i - born > 30 or (side == -1 and h[i] > ext) or (side == 1 and l[i] < ext): arm = None; continue
        if (side == -1 and c[i] < trig) or (side == 1 and c[i] > trig):
            st = ext + side * -1 * 0.1 * a[i]
            if (tg - c[i]) * side > 0 and (c[i] - st) * side > 0:
                sig[i] = side; stop[i] = st; tgt[i] = tg
            arm = None
    return dict(sig=sig, stop=stop, tgt=tgt)


# ---- 103 Patrick Nill PBD(简化): 上周价值区边 + 15m 推动后横盘方框, 反向出框做(P下破做空/b上破做多), 止损方框另一边, 目标另一侧价值区边 ----
def pbd(b, box=24, push=4.0, box_w=2.0, near=1.0, min_rr=1.5):
    o, h, l, c, t = b.o.values, b.h.values, b.l.values, b.c.values, b.t.values
    a = atr(b, 96)
    wk = pd.to_datetime(t, unit="ms").to_period("W").astype(str).values
    tp = (h + l + c) / 3; v = b.v.values
    va = {}
    for w, g in pd.DataFrame({"w": wk, "tp": tp, "v": v}).groupby("w", sort=True):
        lo, hi = g.tp.min(), g.tp.max(); nb = 50
        hist, ed = np.histogram(g.tp, bins=nb, range=(lo, hi + 1e-9), weights=g.v)
        k = int(hist.argmax()); L = R = k; acc = hist[k]; tot = hist.sum()
        while acc < 0.7 * tot and (L > 0 or R < nb - 1):
            up = hist[R + 1] if R < nb - 1 else -1; dn = hist[L - 1] if L > 0 else -1
            if up >= dn: R += 1; acc += up
            else: L -= 1; acc += dn
        va[w] = (ed[R + 1], ed[L], (ed[k] + ed[k + 1]) / 2)
    weeks = sorted(va); prev = {weeks[i]: va[weeks[i - 1]] for i in range(1, len(weeks))}
    bt, bb = np.maximum(o, c), np.minimum(o, c)
    n = len(c); sig = np.zeros(n); stop = np.full(n, np.nan); tgt = np.full(n, np.nan)
    for i in range(box + 20, n):
        pv = prev.get(wk[i])
        if pv is None: continue
        VAH, VAL, POC = pv
        btop = bt[i - box:i].max(); bbot = bb[i - box:i].min()
        if btop - bbot > box_w * a[i]: continue
        pre = slice(i - box - 16, i - box)
        pushed_up = btop - l[pre].min() >= push * a[i] and h[pre].max() <= btop + 0.5 * a[i]
        pushed_dn = h[pre].max() - bbot >= push * a[i] and l[pre].min() >= bbot - 0.5 * a[i]
        if pushed_up and abs(btop - VAH) <= near * a[i] * 4 and c[i] < bbot:
            st = btop + 0.2 * a[i]; tg = VAL
            if (c[i] - tg) >= min_rr * (st - c[i]): sig[i] = -1; stop[i] = st; tgt[i] = tg
        elif pushed_dn and abs(bbot - VAL) <= near * a[i] * 4 and c[i] > btop:
            st = bbot - 0.2 * a[i]; tg = VAH
            if (tg - c[i]) >= min_rr * (c[i] - st): sig[i] = 1; stop[i] = st; tgt[i] = tg
    return dict(sig=sig, stop=stop, tgt=tgt)


# ---- 105 Bit浪浪 龙头: BTC 日线在 EMA50 上方(春夏)时, 每周买过去 look 天涨幅最大且 BTC 回调期间抗跌的 k 个山寨, 持有一周 ----
def leader_rotation(k=3, look=30, regime=50, fee=0.0013):
    A = {os.path.basename(f)[:-4]: pd.read_pickle(f) for f in glob.glob(os.path.join(HERE, "alts1d", "*.pkl"))}
    btc = A.pop("BTCUSDT"); A.pop("ETHUSDT", None)
    idx = btc.t.values; C = pd.DataFrame({s: pd.Series(d.c.values, index=d.t.values) for s, d in A.items()}).reindex(idx)
    bc = pd.Series(btc.c.values, index=idx); bull = bc > bc.ewm(span=regime, adjust=False).mean()
    mom = C / C.shift(look) - 1; dd = C / C.rolling(look).max() - 1
    eq = 1.0; curve = []; hold = []
    for k_ in range(look + 1, len(idx)):
        if hold:
            r = (C.iloc[k_][hold] / C.iloc[k_ - 1][hold] - 1).fillna(0).mean(); eq *= 1 + r
        if (k_ % 7) == 0:
            new = []
            if bull.iloc[k_]:
                cand = mom.iloc[k_].dropna()
                cand = cand[(dd.iloc[k_][cand.index] > -0.15) & (C.iloc[:k_].notna().sum()[cand.index] > 90)]
                new = list(cand.sort_values(ascending=False).index[:k])
            turn = len(set(new) ^ set(hold)) / max(1, 2 * k)
            eq *= 1 - fee * turn; hold = new
        curve.append(eq)
    cv = np.array(curve); yrs = len(cv) / 365.25
    bh = (bc.iloc[-1] / bc.iloc[look]) ** (1 / yrs) - 1
    tt = pd.to_datetime(idx[look + 1:], unit="ms"); s = pd.Series(cv, index=tt).resample("YE").last()
    return dict(cagr=round(cv[-1] ** (1 / yrs) - 1, 3), mdd=round((1 - cv / np.maximum.accumulate(cv)).max(), 3), btc_cagr=round(bh, 3),
                yearly={str(x.year): round(y, 2) for x, y in s.pct_change().fillna(s.iloc[0] - 1).items()})


if __name__ == "__main__":
    rows = []
    def rec(name, tf, tr, **kw):
        s = summarize(tr); s.update(strategy=name, tf=tf, **{k: str(v) for k, v in kw.items()}); rows.append(s)
        print(name, tf, kw, {k: s.get(k) for k in ("n", "win", "r_gross", "r_net", "r_is", "r_oos", "cagr", "mdd", "risk_med")}, flush=True)
    SB = stock_bars(); stk = {s: b for s, b in SB.items() if s not in CRYPTO}; cry = {s: b for s, b in SB.items() if s in CRYPTO}
    # 89 Adhikari: Qullamaggie 突破 + 大盘(78只等权指数)在10日线上
    px = pd.DataFrame({s: pd.Series(b.c.values, index=b.t.values) for s, b in stk.items()}).sort_index()
    idxp = (px / px.bfill().iloc[0]).mean(axis=1); ok = (idxp > idxp.rolling(10).mean())
    for flt in (False, True):
        trs = []
        for s, b in stk.items():
            tr = qull_trades(b, gain=0.3, rng_max=0.25).assign(sym=s)
            if flt and len(tr): tr = tr[ok.reindex(tr.t.values - 0).fillna(False).values]
            trs.append(tr)
        rec("Adhikari 突破(+大盘10日线过滤)" if flt else "Adhikari 突破(无过滤,对照)", "1d", pd.concat(trs), market_filter=flt)
    for univ, U in (("美股78", stk), ("币圈5币2017起", cry)):
        for em in (50, 150):
            rec("Ted Zhang 第二阶段", "1d", pd.concat([backtest(b, **stage2(b, em)).assign(sym=s) for s, b in U.items()]), univ=univ, exit_ma=em)
    for tf in ("5m", "15m", "1h"):
        for ms, tfib in itertools.product((1, 2, 3), (0.618,)):
            rec("David MACD三背离+斐波那契", tf, run_all(david, tf, min_score=ms, tgt_fib=tfib), min_score=ms)
    for push, bw in itertools.product((3.0, 5.0), (1.5, 2.5)):
        rec("Patrick Nill PBD(简化)", "15m", run_all(pbd, "15m", push=push, box_w=bw), push=push, box_w=bw)
    pd.DataFrame(rows).to_csv("batch18.csv", index=False)
    for k, look in itertools.product((1, 3, 5), (14, 30)):
        r = leader_rotation(k, look); r.update(k=k, look=look); print("龙头轮动", r, flush=True)
