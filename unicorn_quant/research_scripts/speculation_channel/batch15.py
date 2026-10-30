"""批次15 (2025-08..10): CME周末缺口回补 / Marco 流动性陷阱 / Alex Temiz 首阴日做空 / Larry Williams 外包线+FPO。"""
import os, glob, itertools, pandas as pd, numpy as np
from lib import *
from batch01 import shift
from batch06 import stock_bars, CRYPTO
from batch14 import ny_min


# ---- 71 CME 缺口: 周五17:00(纽约)→周日18:00 的跳空, 朝回补方向交易, 目标周五收盘价 ----
def cme_gap(b, min_gap=0.005, stop_mult=1.0, max_days=7):
    o, h, l, c, t = b.o.values, b.h.values, b.l.values, b.c.values, b.t.values
    ts = pd.to_datetime(t, unit="ms", utc=True).tz_convert("America/New_York")
    wd, hr = ts.weekday.values, ts.hour.values
    n = len(c); sig = np.zeros(n); stop = np.full(n, np.nan); tgt = np.full(n, np.nan)
    fri = np.nan; stats = []
    for i in range(1, n):
        if wd[i] == 4 and hr[i] == 16: fri = c[i]  # 周五 16:00-17:00 这根收盘 ≈ CME 收盘
        if wd[i] == 6 and hr[i] == 17 and not np.isnan(fri):  # 周日 17:00-18:00 收盘 ≈ CME 开盘价
            g = c[i] / fri - 1
            if abs(g) >= min_gap:
                fill = (l[i + 1:i + 1 + max_days * 24] <= fri).any() if g > 0 else (h[i + 1:i + 1 + max_days * 24] >= fri).any()
                fill_any = (l[i + 1:] <= fri).any() if g > 0 else (h[i + 1:] >= fri).any()
                stats.append((g, fill, fill_any))
                side = -1 if g > 0 else 1
                st = c[i] + (-side) * stop_mult * abs(c[i] - fri)
                sig[i] = side; stop[i] = st; tgt[i] = fri
            fri = np.nan
    return dict(sig=sig, stop=stop, tgt=tgt, max_bars=max_days * 24), stats


# ---- 72 流动性陷阱: 被尊重过的摆动低点(之后涨过 move×ATR) 被刺破后收回 → 做多, 目标之前的摆动高点 ----
def liq_trap(b, piv=20, move=4.0, min_rr=1.0):
    h, l, c = b.h.values, b.l.values, b.c.values; a = atr(b); n = len(c)
    sig = np.zeros(n); stop = np.full(n, np.nan); tgt = np.full(n, np.nan)
    lows = []; highs = []
    for i in range(2 * piv + 1, n):
        j = i - piv
        if l[j] == l[j - piv:i + 1].min(): lows.append([j, l[j], False])
        if h[j] == h[j - piv:i + 1].max(): highs.append([j, h[j], False])
        lows = lows[-5:]; highs = highs[-5:]
        for L in lows:
            if L[2]: continue
            respected = h[L[0]:i].max() - L[1] >= move * a[i]
            if respected and l[i] < L[1] and c[i] > L[1]:
                hh = [x[1] for x in highs if x[0] > L[0] and x[1] > c[i]]
                st = l[i] - 0.1 * a[i]
                if hh and (min(hh) - c[i]) >= min_rr * (c[i] - st):
                    sig[i] = 1; stop[i] = st; tgt[i] = min(hh)
                L[2] = True; break
            if l[i] < L[1]: L[2] = True
        if sig[i]: continue
        for H in highs:
            if H[2]: continue
            respected = H[1] - l[H[0]:i].min() >= move * a[i]
            if respected and h[i] > H[1] and c[i] < H[1]:
                ll = [x[1] for x in lows if x[0] > H[0] and x[1] < c[i]]
                st = h[i] + 0.1 * a[i]
                if ll and (c[i] - max(ll)) >= min_rr * (st - c[i]):
                    sig[i] = -1; stop[i] = st; tgt[i] = max(ll)
                H[2] = True; break
            if h[i] > H[1]: H[2] = True
    return dict(sig=sig, stop=stop, tgt=tgt)


# ---- 73 首阴日: 连续≥k天大涨(累计≥run) 后第一天跌破前日收盘 → 收盘做空, 止损前日高点, 持有 hold 天 ----
def first_red(b, k=3, run=0.30, hold=3):
    o, h, l, c = b.o.values, b.h.values, b.l.values, b.c.values
    up = c > shift(c)
    streak = pd.Series(up.astype(int)).groupby((~up).cumsum()).cumsum().values
    prev_streak = shift(streak.astype(float)); gain = shift(c) / shift(c, k + 1) - 1
    cond = (prev_streak >= k) & (gain >= run) & (c < shift(c)) & (c < o)
    return dict(sig=np.where(cond, -1, 0), stop=np.maximum(h, shift(h)), max_bars=hold)


# ---- 75 Larry Williams 外包线 + FPO(第一个盈利开盘价离场) ----
def larry(b, min_ratio=2.0, buf=1.0, max_days=10, fee=FEE + SLIP):
    o, h, l, c, t = b.o.values, b.h.values, b.l.values, b.c.values, b.t.values; a = atr(b)
    body = np.abs(c - o); out = []; i = 2; n = len(c)
    while i < n - 2:
        ob = h[i] > h[i - 1] and l[i] < l[i - 1] and body[i] >= min_ratio * body[i - 1]
        side = 1 if (ob and c[i] < l[i - 1]) else (-1 if (ob and c[i] > h[i - 1]) else 0)
        if side == 0: i += 1; continue
        e = o[i + 1]; st = (l[i] - buf * 0.2 * a[i]) if side == 1 else (h[i] + buf * 0.2 * a[i])
        if (e - st) * side <= 0: i += 1; continue
        R = abs(e - st); px = np.nan; why = 3; j = i + 1
        while j < n:
            if side == 1 and l[j] <= st: px = min(o[j], st) if j > i + 1 else st; why = 1; break
            if side == -1 and h[j] >= st: px = max(o[j], st) if j > i + 1 else st; why = 1; break
            if j + 1 < n and (o[j + 1] - e) * side > 0: px = o[j + 1]; j += 1; break
            if j - i >= max_days and j + 1 < n: px = o[j + 1]; j += 1; break
            j += 1
        if np.isnan(px): px = c[-1]
        g = side * (px - e) / e; net = g - 2 * fee - (STOP_SLIP if why == 1 else 0)
        out.append(dict(t=t[i + 1], side=side, risk=R / e, r_gross=g / (R / e), r_net=net / (R / e), ret=net))
        i = j
    return pd.DataFrame(out)


if __name__ == "__main__":
    rows = []
    def rec(name, tf, tr, **kw):
        s = summarize(tr); s.update(strategy=name, tf=tf, **{k: str(v) for k, v in kw.items()}); rows.append(s)
        extra = f" 胜率{(tr.r_net > 0).mean():.2f} 平均每笔{tr.ret.mean() * 100:.2f}%" if "ret" in tr else ""
        print(name, tf, kw, {k: s.get(k) for k in ("n", "win", "r_gross", "r_net", "r_is", "r_oos", "cagr", "mdd", "risk_med")}, extra, flush=True)
    # 71
    allst = []
    for mg, sm in itertools.product((0.005, 0.01), (1.0, 2.0)):
        trs = []
        for s in ("BTCUSDT", "ETHUSDT"):
            b = bars(s, "1h"); spec, st = cme_gap(b, mg, sm); trs.append(backtest(b, **spec).assign(sym=s))
            if sm == 1.0: allst += [(s, mg) + x for x in st]
        rec("CME周末缺口回补", "1h", pd.concat(trs), min_gap=mg, stop_mult=sm)
    g = pd.DataFrame(allst, columns=["sym", "mg", "gap", "fill7d", "fill_ever"])
    print("缺口回补率\n", g.groupby(["sym", "mg"])[["fill7d", "fill_ever"]].mean().round(3), flush=True)
    # 72
    for tf in ("1h", "4h"):
        for piv, mv in itertools.product((10, 20), (3.0, 6.0)):
            rec("Marco流动性陷阱", tf, run_all(liq_trap, tf, piv=piv, move=mv), piv=piv, move=mv)
    # 73
    A = {os.path.basename(f)[:-4]: pd.read_pickle(f) for f in glob.glob(os.path.join(HERE, "alts1d", "*.pkl"))}
    for k, run, hold in itertools.product((3, 5), (0.3, 0.6), (1, 3, 5)):
        tr = pd.concat([backtest(b, **first_red(b, k, run, hold)).assign(sym=s) for s, b in A.items()], ignore_index=True)
        rec("Alex首阴日做空(山寨84币)", "1d", tr, k=k, run=run, hold=hold)
    # 75
    SB = stock_bars()
    for univ, U in (("币圈5币2017起", {s: b for s, b in SB.items() if s in CRYPTO}), ("美股78", {s: b for s, b in SB.items() if s not in CRYPTO}),
                    ("山寨84币", A)):
        for mr in (1.0, 2.0):
            tr = pd.concat([larry(b, min_ratio=mr).assign(sym=s) for s, b in U.items()], ignore_index=True)
            rec("Larry外包线+FPO", "1d", tr, univ=univ, min_ratio=mr)
            if univ.startswith("币圈"):
                print(tr.groupby("sym").apply(lambda x: pd.Series(dict(n=len(x), win=round((x.r_net > 0).mean(), 3), avg=round(x.ret.mean() * 100, 2)))).to_string(), flush=True)
    pd.DataFrame(rows).to_csv("batch15.csv", index=False)
