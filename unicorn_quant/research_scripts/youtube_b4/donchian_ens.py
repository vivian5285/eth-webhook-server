"""Zarattini/Pagani/Barbon 2025「Catching Crypto Trends」唐奇安集成(网上找的新来源，2026-11-01)。
规则(CXO Advisory 摘要)：日线收盘创 L 日新高 → 开多；追踪止损 = max(昨日止损, L日最高收盘与最低收盘的中点)，收盘跌破离场；
L ∈ {5,10,20,30,60,90,150,250,360}，各 L 等权；每币仓位 = 开着的 L 占比 × min(25%/年化90日波动, 2)，每日调仓；只做多。
我们的口径：方案F主流5币(BTC/ETH/SOL/BNB/XRP)等权，币安现货日线；换手成本 6.5bp/边(吃单5+滑点1.5)；
收盘出信号、次日收盘收益起算(无前视)。比较：① 单独 ② 替换方案F币圈腿 ③ 叠加到方案F。"""
import json, os, sys, time, urllib.request
import numpy as np, pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__)); SC = os.path.join(HERE, "..", "speculation_channel")
sys.path.insert(0, SC)
from combo import stats

SYMS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT"]
LB = (5, 10, 20, 30, 60, 90, 150, 250, 360)
COST = 0.00065


def daily(sym):
    f = os.path.join(HERE, f"{sym}.csv")              # 本机连不上币安 API，从 VPS 拉好的(ts_ms,close)
    if os.path.exists(f):
        a = pd.read_csv(f, header=None)
        return pd.Series(a[1].values.astype(float), index=pd.to_datetime(a[0], unit="ms"))
    rows, start = [], 1500000000000
    while True:
        u = f"https://api.binance.com/api/v3/klines?symbol={sym}&interval=1d&startTime={start}&limit=1000"
        a = json.load(urllib.request.urlopen(u, timeout=30))
        if not a: break
        rows += a; start = a[-1][0] + 86400000
        if len(a) < 1000: break
        time.sleep(0.3)
    s = pd.Series([float(r[4]) for r in rows], index=pd.to_datetime([r[0] for r in rows], unit="ms"))
    s.to_pickle(f); return s


def coin_pos(c, lbs=LB, vt=0.25, vlb=90, cap=2.0, mid_frac=0.5):
    """返回每天收盘后定下的目标仓位(次日生效)。mid_frac=0.5 即中点；<0.5 止损更松。"""
    x = c.values; n = len(x); on = np.zeros((n, len(lbs)))
    for j, L in enumerate(lbs):
        hi = pd.Series(x).rolling(L).max().shift(1).values; mx = pd.Series(x).rolling(L).max().values; mn = pd.Series(x).rolling(L).min().values
        pos, stop = 0, np.nan
        for i in range(L, n):
            lvl = mn[i] + mid_frac * (mx[i] - mn[i])
            if pos:
                stop = max(stop, lvl)
                if x[i] < stop: pos = 0
            elif x[i] > hi[i]:
                pos, stop = 1, lvl
            on[i, j] = pos
    vol = pd.Series(x).pct_change().rolling(vlb).std().values * np.sqrt(365)
    lev = np.minimum(vt / vol, cap)
    return pd.Series(np.nan_to_num(on.mean(1) * lev), index=c.index)


def strat(**kw):
    rets, ws = [], []
    for s in SYMS:
        c = daily(s); w = coin_pos(c, **kw); r = c.pct_change().fillna(0)
        pnl = w.shift(1).fillna(0) * r - COST * w.diff().abs().fillna(0)
        rets.append(pnl.rename(s)); ws.append(w.rename(s))
    R = pd.concat(rets, axis=1).fillna(0); W = pd.concat(ws, axis=1).fillna(0)
    live = R.notna() & (W.index.to_series().notna().values[:, None])
    return (R.sum(1) / len(SYMS)), W / len(SYMS)


def F_series(k):
    F = json.load(open(os.path.join(SC, "planF_series.json")))
    a = np.array(F[k]); s = pd.Series(a[:, 1], index=pd.to_datetime(a[:, 0], unit="ms").normalize())
    return s[~s.index.duplicated()].asfreq("D").ffill().pct_change().fillna(0)


def fmt(r):
    a, i, o = stats(r), stats(r[:"2024-12-31"]), stats(r["2025-01-01":])
    return f"{a[0]:.1%}/{a[1]:.1%}/{a[2]:.2f} (前{i[2]:.2f} 后{o[2]:.2f})"


if __name__ == "__main__":
    fF, fC = F_series("F"), F_series("crypto")
    t0, t1 = max(fF.index[0], pd.Timestamp("2019-01-01")), fF.index[-1]
    print(f"方案F 数据 {fF.index[0].date()}~{t1.date()}", flush=True)
    for t0 in (pd.Timestamp("2019-01-01"), pd.Timestamp("2022-03-01")):
        print(f"\n==== 窗口 {t0.date()} ~ {t1.date()} ====", flush=True)
        F, C = fF[t0:t1], fC[t0:t1]
        print("方案F        ", fmt(F)); print("方案F币圈腿  ", fmt(C))
        grid = [dict(), dict(vt=0.40), dict(vt=0.60), dict(mid_frac=0.3), dict(lbs=(20, 30, 60, 90, 150, 250, 360)), dict(lbs=(5, 10, 20, 30, 60))]
        for kw in grid:
            d, W = strat(**kw); d = d.reindex(F.index).fillna(0)
            row = f"{str(kw):45s} 单独 {fmt(d)}  均仓{W.sum(1)[t0:].mean():.2f}  相关币圈腿{d.corr(C):.2f}"
            # 替换币圈腿：方案F - 币圈腿 + 唐奇安(把币圈腿的风险换成唐奇安，按波动率匹配)
            k = C.std() / d.std() if d.std() > 0 else 0
            row += f"\n      换掉币圈腿(等波动 ×{k:.2f}) {fmt(F - C + k * d)}"
            for m in (0.25, 0.5, 1.0):
                row += f"\n      叠加 ×{m} {fmt(F + m * d)}"
            print(row, flush=True)
