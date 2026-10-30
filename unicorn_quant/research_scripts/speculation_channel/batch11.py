"""批次11 (2024-10..12): Hong Inki 放量长阳突破新高 / VSA 超高量能反向。"""
import itertools, pandas as pd, numpy as np
from lib import *
from batch01 import shift
from batch06 import stock_bars, CRYPTO


# ---- 51 Hong Inki: 日涨幅≥ret 且收在 N 日新高(可选放量) → 收盘买, 止损长阳低点, 破 EMA 离场 ----
def hong(b, ret=0.10, look=126, vk=2.0, exit_ema=10):
    o, h, l, c = b.o.values, b.h.values, b.l.values, b.c.values
    top = pd.Series(h).rolling(look).max().shift(1).values
    cond = (c / shift(c) - 1 >= ret) & (c > top) & (c > o)
    if vk > 0 and "v" in b:
        v = b.v.values; cond &= v >= vk * shift(sma(v, 20))
    return dict(sig=np.where(cond, 1, 0), stop=l, exit_sig=np.where(c < ema(c, exit_ema), 1, 0))


# ---- 54 VSA: 超高量能(≥k×均量)的大阴线收盘做多 / 大阳线收盘做空; 止损 ATR×1.7; 可选只顺大趋势 ----
def vsa(b, vk=3.0, body_atr=1.0, rr=1.0, trend=False, big=48):
    o, h, l, c, v = b.o.values, b.h.values, b.l.values, b.c.values, b.v.values
    a = atr(b, 14); vm = shift(sma(v, 20)); ultra = v >= vk * vm
    first = ultra & ~shift(ultra).astype(bool)
    bigdn = (c < o) & (o - c >= body_atr * a); bigup = (c > o) & (c - o >= body_atr * a)
    e = ema(c, 200 * big) if trend else None
    lo_ok = (c > e) if trend else True; sh_ok = (c < e) if trend else True
    sig = np.where(first & bigdn & lo_ok, 1, np.where(first & bigup & sh_ok, -1, 0))
    stop = np.where(sig == 1, l - 1.7 * a, h + 1.7 * a)
    return dict(sig=sig, stop=stop, tgt=np.where(sig == 1, c + rr * (c - stop), c - rr * (stop - c)))


if __name__ == "__main__":
    rows = []
    def go(name, fn, tf, **kw):
        s = summarize(run_all(fn, tf, **kw)); s.update(strategy=name, tf=tf, **{k: str(v) for k, v in kw.items()}); rows.append(s)
        print(name, tf, kw, {k: s.get(k) for k in ("n", "win", "r_gross", "r_net", "r_is", "r_oos", "cagr", "mdd", "risk_med")}, flush=True)
    for tf, big in (("5m", 48), ("15m", 16), ("1h", 4)):
        for vk, rr, tr in itertools.product((3.0, 5.0), (1.0, 2.0), (False, True)):
            go("VSA超高量反向", vsa, tf, vk=vk, rr=rr, trend=tr, big=big)
    for ret, vk, ee in itertools.product((0.08, 0.12), (0, 2.0), (10, 20)):
        go("Hong Inki 长阳新高(币圈日线2022起)", hong, "1d", ret=ret, vk=vk, exit_ema=ee)
    SB = stock_bars(); stocks = {s: b for s, b in SB.items() if s not in CRYPTO}
    for ret, ee in itertools.product((0.07, 0.10), (10, 20)):
        tr = pd.concat([backtest(b, **hong(b, ret=ret, vk=0, exit_ema=ee)).assign(sym=s) for s, b in stocks.items()], ignore_index=True)
        s = summarize(tr); s.update(strategy="Hong Inki 长阳新高(美股78,无量)", tf="1d", ret=ret, exit_ema=ee); rows.append(s)
        print("Hong美股", ret, ee, {k: s.get(k) for k in ("n", "win", "r_gross", "r_net", "r_is", "r_oos", "cagr", "mdd")}, flush=True)
    pd.DataFrame(rows).to_csv("batch11.csv", index=False)
