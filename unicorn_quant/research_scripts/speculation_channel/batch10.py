"""批次10 (2024-08..09): AHR999 囤币 / 洛伦兹分类(机器学习) / 2B 假突破 / 加特利谐波。"""
import itertools, pandas as pd, numpy as np
from numba import njit
from lib import *
from batch01 import shift
from batch06 import stock_bars


# ---- 46 AHR999 (BTC 日线) ----
def ahr999(b):
    c, t = b.c.values, b.t.values
    age = (t - pd.Timestamp("2009-01-03").value // 10**6) / 86400e3
    growth = 10 ** (5.84 * np.log10(age) - 17.01)
    dca = 200 / pd.Series(1 / c).rolling(200).sum().values  # 定额定投成本 = 调和平均
    return (c / dca) * (c / growth)


def ahr_study(b):
    c = b.c.values; a = ahr999(b); ok = ~np.isnan(a); c, a = c[ok], a[ok]
    res = {}
    # 每日定投 1 元: 全程 / 只在 ahr<1.2 / <0.45 时 3 倍
    for name, w in {"每日定投": np.ones(len(c)), "AHR<1.2才投": (a < 1.2) * 1.0,
                    "AHR<1.2投,<0.45投3倍": np.where(a < 0.45, 3.0, np.where(a < 1.2, 1.0, 0.0))}.items():
        coins = (w / c).sum(); cost = w.sum()
        res[name] = dict(投入=round(cost), 终值=round(coins * c[-1]), 倍数=round(coins * c[-1] / cost, 2), 平均成本=round(cost / coins))
    # 择时: <0.45 满仓, >thr 清仓
    for thr in (1.2, 2.0, 4.0):
        pos = 0; eq = 1.0; curve = []
        for i in range(1, len(c)):
            eq *= (c[i] / c[i - 1]) if pos else 1
            if pos == 0 and a[i] < 0.45: pos = 1
            elif pos == 1 and a[i] > thr: pos = 0
            curve.append(eq)
        cv = np.array(curve); yrs = len(c) / 365.25
        res[f"<0.45买 >{thr}卖"] = dict(年化=round(eq ** (1 / yrs) - 1, 3), 回撤=round((1 - cv / np.maximum.accumulate(cv)).max(), 3))
    yrs = len(c) / 365.25
    res["买入持有"] = dict(年化=round((c[-1] / c[0]) ** (1 / yrs) - 1, 3), 回撤=round((1 - c / np.maximum.accumulate(c)).max(), 3))
    return res, (a < 0.45).mean(), (a > 1.2).mean()


# ---- 47 洛伦兹分类 (jdehorty 简化复刻) ----
def _norm(x, n=200):
    lo = pd.Series(x).rolling(n, min_periods=20).min(); hi = pd.Series(x).rolling(n, min_periods=20).max()
    return ((pd.Series(x) - lo) / (hi - lo).replace(0, np.nan)).fillna(0.5).values


def features(b):
    h, l, c = b.h.values, b.l.values, b.c.values
    hlc3 = (h + l + c) / 3
    esa = ema(hlc3, 10); d = ema(np.abs(hlc3 - esa), 10); ci = (hlc3 - esa) / (0.015 * np.maximum(d, 1e-12)); wt1 = ema(ci, 11); wt2 = sma(wt1, 4)
    tp = hlc3; cci = (tp - sma(tp, 20)) / (0.015 * pd.Series(tp).rolling(20).apply(lambda x: np.abs(x - x.mean()).mean(), raw=True).values)
    ad, _, _ = adx(b, 20)
    F = np.vstack([ema(rsi(c, 14), 1) / 100, _norm(wt1 - wt2), _norm(ema(cci, 1)), ad / 100, ema(rsi(c, 9), 1) / 100]).T
    return np.nan_to_num(F, nan=0.5)


@njit(cache=True)
def _ann(F, y, k, maxback):
    n = F.shape[0]; pred = np.zeros(n)
    for i in range(maxback, n):
        dist_list = np.full(k, 1e18); lab = np.zeros(k); cnt = 0; last = -1.0
        for j in range(i - maxback, i - 4):
            if (i - j) % 4 != 0: continue
            dd = 0.0
            for f in range(F.shape[1]):
                dd += np.log(1 + abs(F[i, f] - F[j, f]))
            if dd >= last:
                # 原脚本: 距离不小于上一个入选距离才进入, 满了按 3/4 分位更新 lastDistance
                if cnt < k:
                    dist_list[cnt] = dd; lab[cnt] = y[j]; cnt += 1
                else:
                    dist_list[:-1] = dist_list[1:]; lab[:-1] = lab[1:]; dist_list[-1] = dd; lab[-1] = y[j]
                    srt = np.sort(dist_list); last = srt[int(k * 3 / 4)]
                if cnt < k: last = dd
        pred[i] = lab[:cnt].sum()
    return pred


def kernel_rq(c, h=18, r=14.0, x0=35):
    n = len(c); out = np.full(n, np.nan)
    w = np.array([(1 + (i ** 2) / (h ** 2 * 2 * r)) ** (-r) for i in range(x0 + 1)])
    for t in range(x0 + 1, n):
        seg = c[t - x0:t + 1][::-1]; out[t] = (seg * w[:len(seg)]).sum() / w[:len(seg)].sum()
    return out


def lorentzian(b, k=6, rr=2.0, maxback=2000, use_kernel=True):
    o, h, l, c = b.o.values, b.h.values, b.l.values, b.c.values
    F = features(b)
    fut = np.r_[c[4:], np.full(4, np.nan)]
    y = np.where(fut > c, 1.0, np.where(fut < c, -1.0, 0.0))  # 训练标签: 未来4根方向(只在 j<i-4 使用, 无前视)
    pred = _ann(F, y, k, maxback)
    e200 = ema(c, 200); kr = kernel_rq(c)
    sgn = np.sign(pred); newb = (sgn > 0) & (shift(sgn) <= 0); news = (sgn < 0) & (shift(sgn) >= 0)
    kb = (kr > shift(kr)) if use_kernel else np.ones(len(c), bool); ks = (kr < shift(kr)) if use_kernel else np.ones(len(c), bool)
    sig = np.where(newb & (c > o) & (c > e200) & kb, 1, np.where(news & (c < o) & (c < e200) & ks, -1, 0))
    lo = pd.Series(l).rolling(10).min().values; hi = pd.Series(h).rolling(10).max().values
    stop = np.where(sig == 1, lo, hi)
    return dict(sig=sig, stop=stop, tgt=np.where(sig == 1, c + rr * (c - lo), c - rr * (hi - c)))


# ---- 50a 2B: 突破前一摆动高点后 1~2 根内收回 → 反向(要求大周期同向) ----
def twob(b, piv=10, rr=2.0, trend=True, big=4):
    h, l, c = b.h.values, b.l.values, b.c.values; a = atr(b)
    ph, pl = pivots(h, l, piv, piv); e = ema(c, 200 * big)
    hh2 = np.maximum(h, shift(h)); ll2 = np.minimum(l, shift(l))
    php, plp = shift(ph), shift(pl)
    s_ok = (c < e) if trend else np.ones(len(c), bool); l_ok = (c > e) if trend else np.ones(len(c), bool)
    short = (hh2 > php) & (c < php) & s_ok; long_ = (ll2 < plp) & (c > plp) & l_ok
    sig = np.where(short, -1, np.where(long_, 1, 0))
    stop = np.where(sig == -1, hh2 + 0.2 * a, ll2 - 0.2 * a)
    return dict(sig=sig, stop=stop, tgt=np.where(sig == -1, c - rr * (stop - c), c + rr * (c - stop)))


# ---- 50b 加特利: XABCD, B≈0.618XA(<0.786), C 在 0.382~0.886AB, D=0.786XA 挂单, 止损 X 外, 目标 0.382/0.618AD ----
def gartley(b, piv=5, tgt_fib=0.618):
    h, l, c = b.h.values, b.l.values, b.c.values; a = atr(b); n = len(c)
    sig = np.zeros(n); stop = np.full(n, np.nan); tgt = np.full(n, np.nan); lim = np.full(n, np.nan)
    pts = []  # (idx, price, type +1 高 / -1 低)
    active = None
    for i in range(2 * piv + 1, n):
        j = i - piv
        if h[j] == h[j - piv:i + 1].max():
            if pts and pts[-1][2] == 1: pts[-1] = (j, max(h[j], pts[-1][1]), 1) if h[j] >= pts[-1][1] else pts[-1]
            else: pts.append((j, h[j], 1))
        if l[j] == l[j - piv:i + 1].min():
            if pts and pts[-1][2] == -1: pts[-1] = (j, min(l[j], pts[-1][1]), -1) if l[j] <= pts[-1][1] else pts[-1]
            else: pts.append((j, l[j], -1))
        if len(pts) >= 4:
            X, A, B, C = pts[-4:]
            if X[2] == -1:  # 看涨: X低 A高 B低 C高
                xa = A[1] - X[1]; rb = (A[1] - B[1]) / xa if xa > 0 else 0; ab = A[1] - B[1]
                rc = (C[1] - B[1]) / ab if ab > 0 else 0
                if 0.58 <= rb <= 0.70 and 0.382 <= rc <= 0.886 and C[1] < A[1]:
                    D = A[1] - 0.786 * xa; active = (1, D, X[1] - 0.2 * a[i], A[1], C[0])
            else:
                xa = X[1] - A[1]; rb = (B[1] - A[1]) / xa if xa > 0 else 0; ab = B[1] - A[1]
                rc = (B[1] - C[1]) / ab if ab > 0 else 0
                if 0.58 <= rb <= 0.70 and 0.382 <= rc <= 0.886 and C[1] > A[1]:
                    D = A[1] + 0.786 * xa; active = (-1, D, X[1] + 0.2 * a[i], A[1], C[0])
        if active is not None:
            side, D, st, Ap, ci = active
            if i - ci > 100 or (side == 1 and l[i] < st) or (side == -1 and h[i] > st): active = None; continue
            if (side == 1 and l[i] > D) or (side == -1 and h[i] < D):
                sig[i] = side; lim[i] = D; stop[i] = st; tgt[i] = D + tgt_fib * (Ap - D)
            else:
                active = None  # 已经触及(下一根成交或错过)
    return dict(sig=sig, stop=stop, tgt=tgt, limit=lim)


if __name__ == "__main__":
    SB = stock_bars(); btc = SB["BTCUSDT"]
    res, p_low, p_high = ahr_study(btc)
    print("AHR999 BTC", pd.to_datetime(btc.t.iloc[200], unit="ms").date(), "起: <0.45 天数占比", round(p_low, 3), ">1.2 占比", round(p_high, 3))
    for k, v in res.items(): print(" ", k, v)
    pd.DataFrame(res).T.to_csv("batch10_ahr.csv")
    rows = []
    def go(name, fn, tf, **kw):
        s = summarize(run_all(fn, tf, **kw)); s.update(strategy=name, tf=tf, **{k: str(v) for k, v in kw.items()}); rows.append(s)
        print(name, tf, kw, {k: s.get(k) for k in ("n", "win", "r_gross", "r_net", "r_is", "r_oos", "cagr", "mdd", "risk_med")}, flush=True)
    for tf in ("1h", "4h"):
        for rr, piv in itertools.product((1.0, 2.0, 3.0), (5, 10)):
            go("2B假突破(大周期同向)", twob, tf, rr=rr, piv=piv)
        go("2B假突破(不过滤)", twob, tf, rr=2.0, piv=10, trend=False)
        for tf_ in (0.382, 0.618):
            go("加特利谐波", gartley, tf, tgt_fib=tf_)
    for tf in ("1h", "4h"):
        for rr in (1.5, 2.0, 3.0):
            go("洛伦兹分类", lorentzian, tf, rr=rr)
    pd.DataFrame(rows).to_csv("batch10.csv", index=False)
