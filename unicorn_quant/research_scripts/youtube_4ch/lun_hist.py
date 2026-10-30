"""交易伦 历史视频(语音转写)里的规则，同 speculation_channel/lib 口径。
fTRv2HhTJUk '破位交易完整清单'：强动能突破EMA50定偏向 → 回调时 RSI14 不跌破50(做空不升破50) → 再突破回调高点且 RSI≥68(做空≤32) → 进场，
  止损=进场K线另一端 或 回调结构低点，目标 2R/3R；他偏好4h，也用1h/15m。
QZNzq-BunA4 '伦敦破位'：亚洲盘(00:00-07:00 UTC)区间；区间窄(<k×日ATR)→伦敦/纽约时段(07:00-16:00 UTC)收盘破区间顺势进；区间宽→等假突破(刺破后收回区间)反向进；2R，当日16:00 UTC未了结平仓。"""
import os, sys, itertools, numpy as np, pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "speculation_channel")); sys.path.insert(0, HERE)
from lib import *
from batch01 import shift
from yitai_hist import run


def lun_brk(b, rr=2.0, stop_mode="bar", look=20, hi_rsi=68):
    h, l, c = b.h.values, b.l.values, b.c.values; N = len(c)
    e50 = ema(c, 50); r = rsi(c, 14); a = atr(b)
    sig = np.zeros(N); stop = np.full(N, np.nan)
    side = 0; born = -1; ph = pl = np.nan; pull_lo = pull_hi = np.nan; pulled = False
    for i in range(51, N):
        # 1 强动能突破 EMA50 定偏向(实体≥1ATR 且收盘穿越 EMA50)
        if c[i] > e50[i] and c[i - 1] <= e50[i - 1] and c[i] - b.o.values[i] >= a[i]:
            side, born, ph, pulled, pull_lo = 1, i, h[i], False, np.nan
            continue
        if c[i] < e50[i] and c[i - 1] >= e50[i - 1] and b.o.values[i] - c[i] >= a[i]:
            side, born, pl, pulled, pull_hi = -1, i, l[i], False, np.nan
            continue
        if side == 0 or i - born > look:
            side = 0; continue
        if side == 1:
            if r[i] < 50: side = 0; continue                       # 回调太强，放弃
            if c[i] < c[i - 1]: pulled = True; pull_lo = np.nanmin([pull_lo, l[i]])
            ph = max(ph, h[i - 1]) if not pulled else ph
            if pulled and c[i] > ph and r[i] >= hi_rsi:
                sig[i] = 1; stop[i] = (l[i] if stop_mode == "bar" else pull_lo) - 0.1 * a[i]; side = 0
        else:
            if r[i] > 50: side = 0; continue
            if c[i] > c[i - 1]: pulled = True; pull_hi = np.nanmax([pull_hi, h[i]])
            pl = min(pl, l[i - 1]) if not pulled else pl
            if pulled and c[i] < pl and r[i] <= 100 - hi_rsi:
                sig[i] = -1; stop[i] = (h[i] if stop_mode == "bar" else pull_hi) + 0.1 * a[i]; side = 0
    return dict(sig=sig, stop=stop, tgt=c + sig * rr * np.abs(c - stop))


def london(b, k=0.5, rr=2.0):
    """b 为 15m。亚洲盘 00:00-07:00 UTC 区间，日 ATR 用前一日。"""
    t = pd.to_datetime(b.t.values, unit="ms"); o, h, l, c = b.o.values, b.h.values, b.l.values, b.c.values; N = len(c)
    day = t.normalize(); hr = t.hour.values
    df = pd.DataFrame({"d": day, "h": h, "l": l, "c": c, "hr": hr})
    asia = df[df.hr < 7].groupby("d").agg(ah=("h", "max"), al=("l", "min"))
    dd = df.groupby("d").agg(H=("h", "max"), L=("l", "min"), C=("c", "last"))
    tr = np.maximum(dd.H - dd.L, np.maximum((dd.H - dd.C.shift()).abs(), (dd.L - dd.C.shift()).abs()))
    datr = tr.ewm(alpha=1 / 14, adjust=False).mean().shift(1)
    A = asia.join(datr.rename("atr")); idx = A.reindex(day)
    ah, al, da = idx.ah.values, idx.al.values, idx.atr.values
    narrow = (ah - al) < k * da; sess = (hr >= 7) & (hr < 16)
    sig = np.zeros(N); stop = np.full(N, np.nan); ex = np.zeros(N)
    done = {}
    for i in range(1, N):
        if hr[i] == 16 and t[i].minute == 0: ex[i] = 9
        if not sess[i] or np.isnan(ah[i]) or done.get(day[i]): continue
        rng = ah[i] - al[i]
        if narrow[i]:
            if c[i] > ah[i] and c[i - 1] <= ah[i]: sig[i] = 1; stop[i] = al[i] if rng < 1.5 * da[i] else c[i] - rng; done[day[i]] = 1
            elif c[i] < al[i] and c[i - 1] >= al[i]: sig[i] = -1; stop[i] = ah[i]; done[day[i]] = 1
        else:
            if h[i] > ah[i] and c[i] < ah[i]: sig[i] = -1; stop[i] = h[i] + 0.1 * rng; done[day[i]] = 1
            elif l[i] < al[i] and c[i] > al[i]: sig[i] = 1; stop[i] = l[i] - 0.1 * rng; done[day[i]] = 1
    tgt = c + sig * rr * np.abs(c - stop)
    return dict(sig=sig, stop=stop, tgt=tgt, exit_sig=np.where(ex == 9, 1, 0))


def run_london(k, rr):
    out = []
    for s in SYMS:
        b = bars(s, "15m"); sp = london(b, k, rr); e9 = sp["exit_sig"] == 1; sig = sp["sig"]
        for side in (1, -1):   # 16:00 UTC 收盘平仓对多空都生效：拆两次回测
            tr = backtest(b, sig=np.where(sig == side, side, 0), stop=sp["stop"], tgt=sp["tgt"], exit_sig=np.where(e9, side, 0)); tr["sym"] = s; out.append(tr)
    return pd.concat(out, ignore_index=True).sort_values("t")


if __name__ == "__main__":
    rows = []
    def rec(name, tf, tr, **kw):
        d = summarize(tr); d.update(name=name, tf=tf, kw=str(kw)); rows.append(d)
        print(f"{name:8s} {tf:4s} {str(kw):34s} n={d.get('n')} win={d.get('win')} R0={d.get('r_gross')} Rnet={d.get('r_net')} "
              f"is={d.get('r_is')} oos={d.get('r_oos')} cagr={d.get('cagr')} mdd={d.get('mdd')}", flush=True)
    for tf, rr, sm, hr_ in itertools.product(("15m", "1h", "4h"), (2.0, 3.0), ("bar", "struct"), (65, 70)):
        rec("lun_brk", tf, run(lun_brk, tf, rr=rr, stop_mode=sm, hi_rsi=hr_), rr=rr, stop=sm, rsi=hr_)
    for k, rr in itertools.product((0.3, 0.5, 0.8), (1.5, 2.0)):
        rec("london", "15m", run_london(k, rr), k=k, rr=rr)
    pd.DataFrame(rows).to_csv(os.path.join(HERE, "lun_hist.csv"), index=False)
