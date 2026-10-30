"""批次14 (2025-08): Kane PO3+SMT背离 / Tyler 三叉戟 / Victoria 趋势线反弹 / JadeCap 纽约盘扫亚欧盘流动性。"""
import itertools, pandas as pd, numpy as np
from lib import *
from batch01 import shift


def ny_min(t):
    ts = pd.to_datetime(t, unit="ms", utc=True).tz_convert("America/New_York")
    return (ts.hour * 60 + ts.minute).values, ts.normalize().tz_localize(None).values.astype("datetime64[D]").astype(np.int64)


# ---- 66 PO3 + SMT: 扫前日高点后收回, 且配对币当天没破自己的前日高点 → 做空, 目标前日区间50% ----
def po3_smt(b, pair, window=None):
    h, l, c, t = b.h.values, b.l.values, b.c.values, b.t.values
    p = pair.set_index("t").reindex(t)
    ph_, pl_ = p.h.values, p.l.values
    day = t // 86400000; a = atr(b)
    def daily(hh, ll, cc):
        d = pd.DataFrame({"d": day, "h": hh, "l": ll}).groupby("d").agg(H=("h", "max"), L=("l", "min"))
        return d.H.shift(1).reindex(day).values, d.L.shift(1).reindex(day).values
    PDH, PDL = daily(h, l, c); QDH, QDL = daily(ph_, pl_, None)
    dh = pd.Series(h).groupby(day).cummax().values; dl = pd.Series(l).groupby(day).cummin().values
    qh = pd.Series(ph_).groupby(day).cummax().values; ql = pd.Series(pl_).groupby(day).cummin().values
    mid = (PDH + PDL) / 2
    nym, _ = ny_min(t); win = np.ones(len(c), bool) if window is None else (nym >= window[0]) & (nym < window[1])
    short = win & (h > PDH) & (c < PDH) & (qh <= QDH) & (c > mid)
    long_ = win & (l < PDL) & (c > PDL) & (ql >= QDL) & (c < mid)
    sig = np.where(short, -1, np.where(long_, 1, 0))
    stop = np.where(sig == -1, dh + 0.1 * a, dl - 0.1 * a)
    return dict(sig=sig, stop=stop, tgt=mid, be_r=1.0)


def run_po3(window=None):
    out = []
    for s, q in (("BTCUSDT", "ETHUSDT"), ("ETHUSDT", "BTCUSDT"), ("SOLUSDT", "ETHUSDT"), ("BNBUSDT", "BTCUSDT"), ("XRPUSDT", "BTCUSDT")):
        b = bars(s, "1h"); tr = backtest(b, **po3_smt(b, bars(q, "1h"), window)); tr["sym"] = s; out.append(tr)
    return pd.concat(out, ignore_index=True)


# ---- 67 Tyler 三叉戟 (30m, 只做多) ----
def trident(b, window=(150, 240), exit_mode="ema", rr=10.0):
    o, h, l, c, t = b.o.values, b.h.values, b.l.values, b.c.values, b.t.values
    e5, e9, e13, e21 = (ema(c, n) for n in (5, 9, 13, 21)); d200 = ema(c, 200 * 48)
    stack = (e5 > e9) & (e9 > e13) & (e13 > e21) & (c > d200)
    nym, _ = ny_min(t); inw = (nym >= window[0]) & (nym < window[1]) if window else np.ones(len(c), bool)
    rng = np.maximum(h - l, 1e-12); doji = np.abs(c - o) <= 0.25 * rng
    n = len(c); sig = np.zeros(n); stop = np.full(n, np.nan); tgt = np.full(n, np.nan)
    fvg = None
    for i in range(3, n):
        if stack[i] and inw[i] and l[i] > h[i - 2] and l[i - 2] > e5[i - 2]:
            fvg = (h[i - 2], l[i], l[i - 1], i)  # 缺口下沿/上沿/中间K低点
        if fvg is None: continue
        if i - fvg[3] > 24 or c[i] < fvg[0]: fvg = None; continue
        midg = (fvg[0] + fvg[1]) / 2; d = i - 1
        if d > fvg[3] and doji[d] and l[d] <= midg and c[i] < h[d] and stack[i]:
            st = min(fvg[2], l[d]) - 0.1 * atr(b)[i] if False else min(fvg[2], l[d])
            if st < c[i]:
                sig[i] = 1; stop[i] = st; tgt[i] = c[i] + rr * (c[i] - st); fvg = None
    out = dict(sig=sig, stop=stop)
    if exit_mode == "ema": out["exit_sig"] = np.where(e5 < e21, 1, 0)
    else: out["tgt"] = tgt
    return out


# ---- 68 Victoria 趋势线: 最近两个上升摆动低点连线(间隔≥1周), 第三次触线收在线上做多, 收盘破线离场 ----
def trendline(b, piv=10, min_gap=42, tol=0.2, stop_atr=1.0, rr=0.0):
    h, l, c = b.h.values, b.l.values, b.c.values; a = atr(b); n = len(c)
    big = ema(c, 300)  # ≈ 日线 EMA50
    sig = np.zeros(n); stop = np.full(n, np.nan); tgt = np.full(n, np.nan); ex = np.zeros(n)
    lows = []; lowsH = []
    for i in range(2 * piv + 1, n):
        j = i - piv
        if l[j] == l[j - piv:i + 1].min(): lows.append((j, l[j]))
        if h[j] == h[j - piv:i + 1].max(): lowsH.append((j, h[j]))
        if len(lows) >= 2:
            (ia, pa), (ib, pb) = lows[-2], lows[-1]
            if pb > pa and ib - ia >= min_gap:
                k = (pb - pa) / (ib - ia); line = pb + k * (i - ib)
                ex[i] = 1 if c[i] < line else 0
                broken = (c[ib + 1:i] < pb + k * (np.arange(ib + 1, i) - ib)).any()
                if not broken and i - ib >= 3 and l[i] <= line + tol * a[i] and c[i] > line and c[i] > big[i]:
                    sig[i] = 1; stop[i] = line - stop_atr * a[i]
                    if rr: tgt[i] = c[i] + rr * (c[i] - stop[i])
        if len(lowsH) >= 2:
            (ia, pa), (ib, pb) = lowsH[-2], lowsH[-1]
            if pb < pa and ib - ia >= min_gap:
                k = (pb - pa) / (ib - ia); line = pb + k * (i - ib)
                if ex[i] == 0: ex[i] = -1 if c[i] > line else 0
                broken = (c[ib + 1:i] > pb + k * (np.arange(ib + 1, i) - ib)).any()
                if sig[i] == 0 and not broken and i - ib >= 3 and h[i] >= line - tol * a[i] and c[i] < line and c[i] < big[i]:
                    sig[i] = -1; stop[i] = line + stop_atr * a[i]
                    if rr: tgt[i] = c[i] - rr * (stop[i] - c[i])
    out = dict(sig=sig, stop=stop, exit_sig=ex)
    if rr: out["tgt"] = tgt
    return out


# ---- 69 JadeCap: 纽约 9:30-11:30 扫掉亚洲/伦敦时段低(高)点后 5m 收回 → 反向, 目标前一纽约日高(低) ----
def jadecap(b, min_rr=1.0):
    h, l, c, t = b.h.values, b.l.values, b.c.values, b.t.values
    nym, nyd = ny_min(t); a = atr(b)
    df = pd.DataFrame({"d": nyd, "m": nym, "h": h, "l": l})
    pre = df[(df.m < 300)].groupby("d").agg(PL=("l", "min"), PH=("h", "max"))  # 当天 0:00-5:00 NY(亚洲尾+伦敦)
    eve = df[(df.m >= 1200)].groupby("d").agg(EL=("l", "min"), EH=("h", "max"))  # 前一晚 20:00-24:00 NY(亚洲)
    eve.index = eve.index + 1
    full = df.groupby("d").agg(DH=("h", "max"), DL=("l", "min")); full.index = full.index + 1
    J = pd.DataFrame(index=np.unique(nyd)).join(pre).join(eve).join(full)
    PLv = np.fmin(J.PL, J.EL).reindex(nyd).values; PHv = np.fmax(J.PH, J.EH).reindex(nyd).values
    DH = J.DH.reindex(nyd).values; DL = J.DL.reindex(nyd).values
    inw = (nym >= 570) & (nym < 690)
    dl_ = pd.Series(np.where(inw, l, np.nan)).groupby(nyd).cummin().values; dh_ = pd.Series(np.where(inw, h, np.nan)).groupby(nyd).cummax().values
    n = len(c); sig = np.zeros(n); stop = np.full(n, np.nan); tgt = np.full(n, np.nan); done = -1
    for i in range(1, n):
        if not inw[i] or nyd[i] == done: continue
        if dl_[i] < PLv[i] and c[i] > PLv[i] and c[i - 1] <= PLv[i]:
            st = dl_[i] - 0.1 * a[i]
            if DH[i] - c[i] >= min_rr * (c[i] - st): sig[i] = 1; stop[i] = st; tgt[i] = DH[i]; done = nyd[i]
        elif dh_[i] > PHv[i] and c[i] < PHv[i] and c[i - 1] >= PHv[i]:
            st = dh_[i] + 0.1 * a[i]
            if c[i] - DL[i] >= min_rr * (st - c[i]): sig[i] = -1; stop[i] = st; tgt[i] = DL[i]; done = nyd[i]
    return dict(sig=sig, stop=stop, tgt=tgt, max_bars=78)


if __name__ == "__main__":
    rows = []
    def rec(name, tf, tr, **kw):
        s = summarize(tr); s.update(strategy=name, tf=tf, **{k: str(v) for k, v in kw.items()}); rows.append(s)
        print(name, tf, kw, {k: s.get(k) for k in ("n", "win", "r_gross", "r_net", "r_is", "r_oos", "cagr", "mdd", "risk_med")}, flush=True)
    for w in (None, (570, 690), (600, 960)):
        rec("Kane PO3+SMT背离", "1h", run_po3(w), window=w)
    for w, em in itertools.product(((150, 240), (600, 810), None), ("ema", "rr")):
        rec("Tyler三叉戟", "30m", run_all(trident, "30m", window=w, exit_mode=em), window=w, exit=em)
    for tf in ("4h", "1h"):
        for sa, rr in itertools.product((0.5, 1.0), (0.0, 3.0)):
            rec("Victoria趋势线反弹", tf, run_all(trendline, tf, stop_atr=sa, rr=rr), stop_atr=sa, rr=rr)
    for mr in (1.0, 2.0):
        rec("JadeCap纽约盘扫亚欧流动性", "5m", run_all(jadecap, "5m", min_rr=mr), min_rr=mr)
    pd.DataFrame(rows).to_csv("batch14.csv", index=False)
