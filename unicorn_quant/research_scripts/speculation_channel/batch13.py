"""批次13 (2025-06..07): 半神MACD究极版(三背离/双背离+半仓保本) / MAK+Andean Oscillator / MAK+半神背离。"""
import itertools, pandas as pd, numpy as np
from lib import *
from batch01 import shift, banshen


# ---- 62 半神究极版: 原批次1信号 + 到 1.3R 挪保本(近似"半仓止盈+保本"), 目标 2R ----
def banshen_ult(b, k=3, diff=0.2, be=1.3, rr=2.0):
    s = banshen(b, k=k, diff=diff, rr=rr); s["be_r"] = be
    return s


# ---- 64 MAK 近似: RSI/MFI/CCI/StochRSI 同长度归一化后平均, >0.5 为多 ----
def mak(b, n=200):
    h, l, c, v = b.h.values, b.l.values, b.c.values, b.v.values
    r = rsi(c, n) / 100
    tp = (h + l + c) / 3; mf = tp * v; up = np.where(tp > shift(tp), mf, 0); dn = np.where(tp < shift(tp), mf, 0)
    mfi = (pd.Series(up).rolling(n).sum() / (pd.Series(up).rolling(n).sum() + pd.Series(dn).rolling(n).sum())).values
    md = pd.Series(tp).rolling(n).std(ddof=0).values * 0.8
    cci = 1 / (1 + np.exp(-((tp - sma(tp, n)) / (0.015 * np.maximum(md, 1e-12))) / 100))
    rr_ = pd.Series(rsi(c, 14)); st = ((rr_ - rr_.rolling(n).min()) / (rr_.rolling(n).max() - rr_.rolling(n).min())).values
    return np.nanmean(np.vstack([r, mfi, cci, st]), axis=0)


def andean(b, n=50, sig_n=9):
    o, c = b.o.values, b.c.values; al = 2 / (n + 1)
    m = len(c); up1 = np.zeros(m); up2 = np.zeros(m); dn1 = np.zeros(m); dn2 = np.zeros(m)
    up1[0] = dn1[0] = c[0]; up2[0] = dn2[0] = c[0] ** 2
    for i in range(1, m):
        up1[i] = max(c[i], o[i], up1[i - 1] - (up1[i - 1] - c[i]) * al)
        up2[i] = max(c[i] ** 2, o[i] ** 2, up2[i - 1] - (up2[i - 1] - c[i] ** 2) * al)
        dn1[i] = min(c[i], o[i], dn1[i - 1] + (c[i] - dn1[i - 1]) * al)
        dn2[i] = min(c[i] ** 2, o[i] ** 2, dn2[i - 1] + (c[i] ** 2 - dn2[i - 1]) * al)
    bull = np.sqrt(np.maximum(dn2 - dn1 ** 2, 0)); bear = np.sqrt(np.maximum(up2 - up1 ** 2, 0))
    return bull, bear, ema(np.maximum(bull, bear), sig_n)


def mak_andean(b, n_mak=200, stop_atr=2.0, exit_mode="flip", rr=3.0):
    c = b.c.values; mk = mak(b, n_mak); bull, bear, sg = andean(b); a = atr(b)
    up = mk > 0.5; dn = mk < 0.5
    # 绿线先上穿红线, 再上穿信号线(两步可以在不同K线完成, 10根内)
    xg = (bull > bear) & (shift(bull) <= shift(bear)); xs = (bull > sg) & (shift(bull) <= shift(sg))
    xr = (bear > bull) & (shift(bear) <= shift(bull)); xs2 = (bear > sg) & (shift(bear) <= shift(sg))
    recent_g = pd.Series(xg).rolling(10).max().values > 0; recent_r = pd.Series(xr).rolling(10).max().values > 0
    sig = np.where(up & xs & recent_g & (bull > bear), 1, np.where(dn & xs2 & recent_r & (bear > bull), -1, 0))
    stop = np.where(sig == 1, c - stop_atr * a, c + stop_atr * a)
    out = dict(sig=sig, stop=stop)
    if exit_mode == "flip": out["exit_sig"] = np.where(bear > bull, 1, np.where(bull > bear, -1, 0))
    else: out["tgt"] = np.where(sig == 1, c + rr * (c - stop), c - rr * (stop - c))
    return out


def mak_banshen(b, rr=1.5, be=1.5):
    s = banshen(b, k=2, diff=0.2, rr=2.0); mk = mak(b, 144); c = b.c.values
    sig = np.where((s["sig"] == 1) & (mk > 0.5), 1, np.where((s["sig"] == -1) & (mk < 0.5), -1, 0))
    tgt = np.where(sig == 1, c + rr * (c - s["stop"]), c - rr * (s["stop"] - c))
    return dict(sig=sig, stop=s["stop"], tgt=np.where(sig == 0, np.nan, s["tgt"]), be_r=be)


if __name__ == "__main__":
    rows = []
    def go(name, fn, tf, **kw):
        s = summarize(run_all(fn, tf, **kw)); s.update(strategy=name, tf=tf, **{k: str(v) for k, v in kw.items()}); rows.append(s)
        print(name, tf, kw, {k: s.get(k) for k in ("n", "win", "r_gross", "r_net", "r_is", "r_oos", "cagr", "mdd", "risk_med")}, flush=True)
    for tf, k in (("15m", 2), ("1h", 2), ("2h", 2), ("4h", 3), ("4h", 2), ("1d", 3)):
        for d in (0.2, 0.4):
            go("半神MACD究极版(保本)", banshen_ult, tf, k=k, diff=d)
    for tf in ("1h", "4h"):
        for em, sa in itertools.product(("flip", "rr"), (1.5, 3.0)):
            go("MAK+Andean", mak_andean, tf, exit_mode=em, stop_atr=sa)
        go("MAK(144)+半神双背离", mak_banshen, tf)
    pd.DataFrame(rows).to_csv("batch13.csv", index=False)
