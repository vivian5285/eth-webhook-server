"""批次19 (宝贝 10-30 发的4个视频): 交易伦 1h结构+5m EMA21/50/200 强动能K / 已泰 EMA20/50/100+威廉分型回调 1.5R /
币哥 MA+EMA 20/60/120 '均线密集'突破+首次回踩20线。第4个(mio)= Fabio 订单流，10-30 已测否决，不重复。"""
import itertools, pandas as pd, numpy as np
from lib import *
from batch01 import shift


def fractals(h, l, n=2):
    """威廉分型(周期n): 第 j 根高/低是前后各 n 根的极值; 第 j+n 根收盘才确认 → 在 i=j+n 处给信号。"""
    N = len(h); up = np.zeros(N, bool); dn = np.zeros(N, bool)
    hs = pd.Series(h).rolling(2 * n + 1).max().values; ls = pd.Series(l).rolling(2 * n + 1).min().values
    for i in range(2 * n, N):
        j = i - n
        dn[i] = l[j] == ls[i]   # 绿箭头(底分型) → 做多信号
        up[i] = h[j] == hs[i]   # 红箭头(顶分型) → 做空信号
    return up, dn


# ---- 已泰: EMA20>50>100 多头排列, 回调到 EMA20 下方(未收破 EMA100), 出现底分型 → 收盘进场; 止损 EMA50(跌破50则 EMA100)下方一点; 目标 1.5R ----
def yitai(b, rr=1.5, n=2, pad=0.1):
    h, l, c = b.h.values, b.l.values, b.c.values
    e20, e50, e100 = ema(c, 20), ema(c, 50), ema(c, 100); a = atr(b)
    up, dn = fractals(h, l, n)
    lw = pd.Series(l).rolling(n + 1).min().values; hg = pd.Series(h).rolling(n + 1).max().values
    bull = (e20 > e50) & (e50 > e100) & (lw < e20) & (c > e100) & dn
    bear = (e20 < e50) & (e50 < e100) & (hg > e20) & (c < e100) & up
    sl_long = np.where(lw < e50, e100, e50) - pad * a
    sl_short = np.where(hg > e50, e100, e50) + pad * a
    sig = np.where(bull, 1, np.where(bear, -1, 0))
    stop = np.where(bull, sl_long, np.where(bear, sl_short, np.nan))
    tgt = c + sig * rr * np.abs(c - stop)
    return dict(sig=sig, stop=stop, tgt=tgt)


# ---- 交易伦: 1h 定偏向(1h 收盘 vs 1h EMA50) + 关键位(1h 摆动高低点); 5m: EMA50 vs EMA200 定趋势,
#      强动能K(实体 ≥ k×ATR, 一根收盘同时穿过 EMA21 和 EMA50) + RSI 穿 50 → 进场; 止损动能K另一端; 目标下一个1h关键位或固定R ----
def lun(b, k=1.0, tgt_mode="pivot", rr=2.0, piv=5):
    o, h, l, c, t = b.o.values, b.h.values, b.l.values, b.c.values, b.t.values
    e21, e50, e200 = ema(c, 21), ema(c, 50), ema(c, 200); a = atr(b); r = rsi(c, 14)
    sym = b.attrs.get("sym")
    H = bars(sym, "1h"); hc = H.c.values; he50 = ema(hc, 50)
    ph, pl = pivots(H.h.values, H.l.values, piv, piv)
    # 1h 第 j 根收盘时刻 = t+1h; 5m 第 i 根收盘 = t+5m; 用 searchsorted 只取已收盘的 1h
    idx = np.searchsorted(H.t.values + 3600_000, t + 300_000, side="right") - 1
    ok = idx >= 0; idx = np.clip(idx, 0, len(H) - 1)
    bias = np.where(ok, np.sign(hc[idx] - he50[idx]), 0)
    res, sup = ph[idx], pl[idx]
    body = np.abs(c - o)
    strong_dn = (c < o) & (body >= k * a) & (o > np.minimum(e21, e50)) & (c < np.minimum(e21, e50))
    strong_up = (c > o) & (body >= k * a) & (o < np.maximum(e21, e50)) & (c > np.maximum(e21, e50))
    rdn = (r < 50) & (shift(r, 1) >= 50) | (r < 50) & (shift(r, 2) >= 50)
    rup = (r > 50) & (shift(r, 1) <= 50) | (r > 50) & (shift(r, 2) <= 50)
    short = (bias < 0) & (e50 < e200) & strong_dn & rdn
    long_ = (bias > 0) & (e50 > e200) & strong_up & rup
    sig = np.where(long_, 1, np.where(short, -1, 0))
    stop = np.where(long_, l - 0.1 * a, np.where(short, h + 0.1 * a, np.nan))
    R = np.abs(c - stop)
    if tgt_mode == "pivot":
        # 目标: 1h 下一个关键位; 关键位离得太近(<1R)或在反方向就不做
        tg = np.where(long_, res, np.where(short, sup, np.nan))
        good = ((tg - c) * sig) >= 1.0 * R
        sig = np.where(good, sig, 0)
    else:
        tg = c + sig * rr * R
    return dict(sig=sig, stop=stop, tgt=tg)


# ---- 币哥 双均线(MA+EMA 20/60/120 共6条): 均线密集 = 6线宽度 < w×ATR 持续 ≥ m 根; 收盘突破密集区 → 进场, 止损密集区另一侧;
#      离场: 固定 R / 下一次密集出现时 / 收破 20 线 ----
def ribbon(b, w=1.5, m=10, exit_mode="rr", rr=3.0, look=20, retest=False):
    h, l, c = b.h.values, b.l.values, b.c.values
    L = np.vstack([sma(c, 20), sma(c, 60), sma(c, 120), ema(c, 20), ema(c, 60), ema(c, 120)])
    top, bot = np.nanmax(L, 0), np.nanmin(L, 0); a = atr(b)
    dense = (top - bot) < w * a
    run = pd.Series(dense.astype(int)).groupby((~dense).cumsum()).cumsum().values
    # 最近 look 根内曾有过 ≥m 根的密集, 记下那次密集区的上下沿
    zt = np.full(len(c), np.nan); zb = np.full(len(c), np.nan); last = -10**9; zt_, zb_ = np.nan, np.nan
    for i in range(len(c)):
        if dense[i] and run[i] >= m: last = i; zt_, zb_ = top[i], bot[i]
        if i - last <= look: zt[i], zb[i] = zt_, zb_
    m20 = np.minimum(L[0], L[3]); M20 = np.maximum(L[0], L[3])
    if not retest:
        long_ = (c > zt) & (shift(c, 1) <= shift(zt, 1)) & ~dense
        short = (c < zb) & (shift(c, 1) >= shift(zb, 1)) & ~dense
        stop = np.where(long_, zb - 0.1 * a, np.where(short, zt + 0.1 * a, np.nan))
    else:
        # 首次回踩20线不破: 突破后价格回到20线(低点触及)但收盘仍在20线外
        brk_up = (c > zt) & ~dense; brk_dn = (c < zb) & ~dense
        long_ = np.zeros(len(c), bool); short = np.zeros(len(c), bool); st = 0; used = False
        for i in range(1, len(c)):
            if brk_up[i] and st != 1: st, used = 1, False
            elif brk_dn[i] and st != -1: st, used = -1, False
            if dense[i]: st = 0
            if st == 1 and not used and l[i] <= M20[i] and c[i] > M20[i]: long_[i] = True; used = True
            if st == -1 and not used and h[i] >= m20[i] and c[i] < m20[i]: short[i] = True; used = True
        stop = np.where(long_, m20 - 0.5 * a, np.where(short, M20 + 0.5 * a, np.nan))
    sig = np.where(long_, 1, np.where(short, -1, 0))
    tgt = None; exit_sig = None
    if exit_mode == "rr":
        tgt = c + sig * rr * np.abs(c - stop)
    elif exit_mode == "ma20":
        exit_sig = np.where(c < m20, 1, np.where(c > M20, -1, 0))
    elif exit_mode == "dense":
        exit_sig = np.where(dense & (run == 1), 1, 0) + np.where(dense & (run == 1), -1, 0) * 0  # 多空都平: 下面单独处理
        exit_sig = np.where(dense & (run == 1), 9, 0)
    return dict(sig=sig, stop=stop, tgt=tgt, exit_sig=exit_sig)


def run(fn, tf, **kw):
    out = []
    for s in SYMS:
        b = bars(s, tf); b.attrs["sym"] = s; spec = fn(b, **kw)
        if spec.get("exit_sig") is not None and (np.asarray(spec["exit_sig"]) == 9).any():
            # '下一次密集出现'对多空都是离场信号: 拆成两次回测各管一边
            e9 = np.asarray(spec["exit_sig"]) == 9; sig = np.asarray(spec["sig"])
            for side in (1, -1):
                sp = dict(spec, sig=np.where(sig == side, side, 0), exit_sig=np.where(e9, side, 0))
                tr = backtest(b, **sp); tr["sym"] = s; out.append(tr)
            continue
        tr = backtest(b, **spec); tr["sym"] = s; out.append(tr)
    return pd.concat(out, ignore_index=True).sort_values("t")


if __name__ == "__main__":
    rows = []
    def rec(name, tf, tr, **kw):
        d = summarize(tr); d.update(name=name, tf=tf, **{k: str(v) for k, v in kw.items()}); rows.append(d)
        print(name, tf, kw, {k: d.get(k) for k in ("n", "win", "r_gross", "r_net", "r_is", "r_oos", "cagr", "mdd")}, flush=True)
    for tf in ("5m", "15m", "1h", "4h"):
        for rr in (1.5, 2.0):
            rec("yitai", tf, run(yitai, tf, rr=rr), rr=rr)
    for tf in ("5m", "15m"):
        for k, tm in itertools.product((0.8, 1.2), ("pivot", "rr")):
            rec("lun", tf, run(lun, tf, k=k, tgt_mode=tm), k=k, tgt=tm)
    for tf in ("1h", "4h", "1d"):
        for w, ex in itertools.product((1.0, 2.0), ("rr", "ma20", "dense")):
            rec("ribbon", tf, run(ribbon, tf, w=w, exit_mode=ex, rr=4.0), w=w, exit=ex)
        for w in (1.0, 2.0):
            rec("ribbon_retest", tf, run(ribbon, tf, w=w, exit_mode="ma20", retest=True), w=w, exit="ma20")
    pd.DataFrame(rows).to_csv("batch19.csv", index=False)
