"""Fabio Valentini 三套订单流策略复刻 (油管 EucLjn0Wdnw)，币安永续 1m(含主动买卖 → delta)。

"大单气泡"(纳指 ≥30 手市价单)币圈用 1m 代理: 同向 delta ≥ big_k × 近24h 1m 成交量中位数。
"吸收": 1m 主动卖 delta ≤ -big_k×中位数 但收盘 ≥ 开盘(做多)。

1 IVB: 纽约 9:30-10:00 区间高低 + 区间成交量分布(POC/VAH/VAL)。10:00 后第一根 5m 收在区间外=突破，
  不追，等回踩到 [POC, VAH](做多)；触发(trig): touch=碰到 VAH 挂单成交 / body=1m 实体整根收在 VAH 上方 /
  flow=区间内出现卖方大单但收阳。止损 VAL(5m 收回 VAL 下方前作废)，目标 2R/2.5R，纽约 16:00 前平。
2 趋势/AAA: 前一纽约盘(9:30-16:00)价值区；5m 收在外面=失衡，只顺该方向；跳过开盘前 20 分钟。
  这一波(当天纽约盘最低→最高)画成交量分布，位置(pos): lvn=中段最低量档 / val=这一波价值区下沿(AAA)。
  触发: lvn 用同向大单，val 用吸收；止损这根 1m 低点下方；目标 2.5R 或这一波 VAH(AAA)，可跟踪。
3 均值回归(伦敦盘 UTC 7-13): 前一 UTC 日价值区当区间；5m 收出去再收回=假突破；
  回到区间后出现向中间的大单进场，止损在极值里面一点，目标 POC。
"""
import os, pickle, numpy as np, pandas as pd
from numba import njit

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
FEE, SLIP, STOP_SLIP = 0.0005, 0.00015, 0.0002


def prep(sym):
    f = os.path.join(DATA, f"{sym}_fabio.pkl")
    if os.path.exists(f):
        return pickle.load(open(f, "rb"))
    d = pd.read_pickle(os.path.join(DATA, f"{sym}_1m.pkl")).reset_index(drop=True)
    d["d"] = 2 * d.tb - d.v
    ts = pd.to_datetime(d.t.values, unit="ms", utc=True).tz_convert("America/New_York")
    d["nym"] = ts.hour * 60 + ts.minute
    d["nyday"] = ts.normalize().tz_localize(None).values.astype("datetime64[D]").astype(np.int64)
    d["uday"] = d.t // 86400_000
    d["umin"] = (d.t // 60_000) % 1440
    d["medv"] = d.v.rolling(1440, min_periods=200).median().shift(1)
    pickle.dump(d, open(f, "wb"))
    return d


def profile(tp, v, ref, bin_frac=0.0005, va=0.70):
    lo, hi = tp.min(), tp.max()
    nb = max(10, int((hi - lo) / (ref * bin_frac)) + 1)
    hist, edges = np.histogram(tp, bins=nb, range=(lo, hi + 1e-12), weights=v)
    k = int(hist.argmax()); L = R = k; tot = hist.sum(); acc = hist[k]
    while acc < va * tot and (L > 0 or R < nb - 1):
        up = hist[R + 1] if R < nb - 1 else -1; dn = hist[L - 1] if L > 0 else -1
        if up >= dn: R += 1; acc += up
        else: L -= 1; acc += dn
    return (edges[k] + edges[k + 1]) / 2, edges[R + 1], edges[L], hist, edges


@njit(cache=True)
def sim(h1, l1, c1, i0, side, ent, stop, tgt, max_bars, be_r, trail):
    st = stop; R = abs(ent - stop); lo5 = 1e18; hi5 = -1e18; n = len(h1)
    for k in range(max_bars):
        i = i0 + k
        if i >= n: return c1[n - 1], 0
        if side == 1:
            if l1[i] <= st: return st, 1
            if h1[i] >= tgt: return tgt, 2
            if be_r > 0 and h1[i] - ent >= be_r * R: st = max(st, ent)
        else:
            if h1[i] >= st: return st, 1
            if l1[i] <= tgt: return tgt, 2
            if be_r > 0 and ent - l1[i] >= be_r * R: st = min(st, ent)
        lo5 = min(lo5, l1[i]); hi5 = max(hi5, h1[i])
        if (k + 1) % 5 == 0:
            if trail:
                if side == 1 and c1[i] - ent >= R: st = max(st, lo5)
                if side == -1 and ent - c1[i] >= R: st = min(st, hi5)
            lo5 = 1e18; hi5 = -1e18
    return c1[min(i0 + max_bars - 1, n - 1)], 0


def close_trade(d, i_ent, side, ent, stop, tgt, end_i, be_r=0.0, trail=False):
    """i_ent: 进场所在 1m 的下一根开始模拟; end_i: 强平的 1m 索引(不含)。"""
    if end_i <= i_ent + 1:
        return None
    px, why = sim(d.h.values, d.l.values, d.c.values, i_ent + 1, side, ent, stop, tgt, end_i - i_ent - 1, be_r, trail)
    rp = abs(ent - stop) / ent
    if rp <= 0:
        return None
    gross = side * (px - ent) / ent
    cost = 2 * (FEE + SLIP) + (STOP_SLIP if why == 1 else 0)
    return dict(t=int(d.t.values[i_ent]), side=side, r_gross=gross / rp, r_net=(gross - cost) / rp,
                r_maker=(gross - 0.0004 - (0.0002 if why == 1 else 0)) / rp, risk_pct=rp, why=why)


def days(d, key):
    k = d[key].values
    s = np.r_[0, np.flatnonzero(np.diff(k)) + 1, len(k)]
    return [(int(k[a]), a, b) for a, b in zip(s[:-1], s[1:])]


# ---------------- 1 IVB ----------------
def ivb(d, trig="body", rr=2.0, big_k=1.0, last_entry=780, be_r=0.0, trail=False, short=True):
    h, l, c, o, v, dl, nym, medv = (d[x].values for x in ("h", "l", "c", "o", "v", "d", "nym", "medv"))
    tp = (h + l + c) / 3
    out = []
    for _, a, b in days(d, "nyday"):
        m = nym[a:b]
        ib = np.flatnonzero((m >= 570) & (m < 600)) + a
        if len(ib) < 25: continue
        H, L = h[ib].max(), l[ib].min()
        poc, vah, val, _, _ = profile(tp[ib], v[ib], c[ib[0]])
        aft = np.flatnonzero((m >= 600) & (m < 960)) + a
        if len(aft) < 30: continue
        end_i = aft[-1] + 1
        side = 0; brk = None
        for i in aft:
            if (nym[i] + 1) % 5 == 0:  # 5m 收盘
                if c[i] > H: side = 1; brk = i; break
                if c[i] < L: side = -1; brk = i; break
        if not side or (side == -1 and not short): continue
        top, bot, stop = (vah, poc, val) if side == 1 else (val, poc, vah)
        touched = False
        for i in range(brk + 1, end_i):
            if nym[i] >= last_entry: break
            if (nym[i] + 1) % 5 == 0 and (c[i] - stop) * side < 0: break  # 收回区间另一边: 作废
            if side == 1:
                if l[i] <= top: touched = True
                if not touched: continue
                if trig == "touch":
                    ent = min(top, o[i]); i_e = i
                    if l[i] <= stop: break
                elif trig == "body":
                    if min(o[i], c[i]) <= top: continue
                    ent = c[i]; i_e = i
                else:
                    if not (l[i] <= top and l[i] > stop and dl[i] <= -big_k * medv[i] and c[i] >= o[i]): continue
                    ent = c[i]; i_e = i
            else:
                if h[i] >= top: touched = True
                if not touched: continue
                if trig == "touch":
                    ent = max(top, o[i]); i_e = i
                    if h[i] >= stop: break
                elif trig == "body":
                    if max(o[i], c[i]) >= top: continue
                    ent = c[i]; i_e = i
                else:
                    if not (h[i] >= top and h[i] < stop and dl[i] >= big_k * medv[i] and c[i] <= o[i]): continue
                    ent = c[i]; i_e = i
            if (ent - stop) * side <= 0: break
            tgt = ent + side * rr * abs(ent - stop)
            r = close_trade(d, i_e, side, ent, stop, tgt, end_i, be_r, trail)
            if r: r["s"] = "IVB"; out.append(r)
            break
    return out


# ---------------- 2 趋势 / AAA ----------------
def trend(d, pos="lvn", big_k=1.0, rr=2.5, tgt_mode="rr", be_r=0.0, trail=False, start=590, max_trades=2):
    h, l, c, o, v, dl, nym, medv = (d[x].values for x in ("h", "l", "c", "o", "v", "d", "nym", "medv"))
    tp = (h + l + c) / 3
    out = []; prev = None
    for _, a, b in days(d, "nyday"):
        m = nym[a:b]
        rth = np.flatnonzero((m >= 570) & (m < 960)) + a
        if len(rth) < 300:
            prev = None; continue
        if prev is None:
            prev = profile(tp[rth], v[rth], c[rth[0]])[:3]; continue
        ppoc, pvah, pval = prev
        end_i = rth[-1] + 1
        bias = 0; n_tr = 0; nxt = 0
        for i in rth:
            if (nym[i] + 1) % 5 == 0:
                if c[i] > pvah: bias = 1
                elif c[i] < pval: bias = -1
            if nym[i] < start or i < nxt or bias == 0 or n_tr >= max_trades or nym[i] >= 930:
                continue
            seg = slice(rth[0], i)  # 这一波: 当天纽约盘至今
            if bias == 1:
                il = rth[0] + int(l[seg].argmin()); ih = il + int(h[il:i].argmax()) if i > il else il
                lo, hi = l[il], h[ih]
            else:
                ih = rth[0] + int(h[seg].argmax()); il = ih + int(l[ih:i].argmin()) if i > ih else ih
                lo, hi = l[il], h[ih]
            a0, a1 = (il, ih + 1) if bias == 1 else (ih, il + 1)
            if a1 - a0 < 15 or (hi - lo) / lo < 0.004: continue
            lpoc, lvah, lval, hist, edges = profile(tp[a0:a1], v[a0:a1], c[a0])
            if pos == "lvn":
                nb = len(hist); q0, q1 = int(nb * 0.25), int(nb * 0.75)
                if q1 - q0 < 3: continue
                k = q0 + int(hist[q0:q1].argmin()); lvl = (edges[k] + edges[k + 1]) / 2
            else:
                lvl = lval if bias == 1 else lvah
            if bias == 1:
                if not (l[i] <= lvl <= h[i] or (l[i] <= lvl and c[i] > lvl * 0.999)): continue
                if pos == "lvn" and not (dl[i] >= big_k * medv[i] and c[i] > o[i]): continue
                if pos == "val" and not (dl[i] <= -big_k * medv[i] and c[i] >= o[i]): continue
                ent = c[i]; stop = l[i] * 0.9999
                tgt = lvah if tgt_mode == "va" else ent + rr * (ent - stop)
            else:
                if not (l[i] <= lvl <= h[i] or (h[i] >= lvl and c[i] < lvl * 1.001)): continue
                if pos == "lvn" and not (dl[i] <= -big_k * medv[i] and c[i] < o[i]): continue
                if pos == "val" and not (dl[i] >= big_k * medv[i] and c[i] <= o[i]): continue
                ent = c[i]; stop = h[i] * 1.0001
                tgt = lval if tgt_mode == "va" else ent - rr * (stop - ent)
            if (tgt - ent) * bias <= abs(ent - stop): continue  # 目标至少 1R
            r = close_trade(d, i, bias, ent, stop, tgt, end_i, be_r, trail)
            if r:
                r["s"] = "TREND_" + pos; out.append(r); n_tr += 1; nxt = i + 15
        prev = profile(tp[rth], v[rth], c[rth[0]])[:3]
    return out


# ---------------- 3 均值回归(伦敦盘) ----------------
def meanrev(d, big_k=1.0, win=(420, 780), min_rr=1.0):
    h, l, c, o, v, dl, umin, medv = (d[x].values for x in ("h", "l", "c", "o", "v", "d", "umin", "medv"))
    tp = (h + l + c) / 3
    out = []; prev = None
    for _, a, b in days(d, "uday"):
        if prev is not None:
            poc, vah, val = prev
            idx = np.flatnonzero((umin[a:b] >= win[0]) & (umin[a:b] < win[1])) + a
            state = 0; ext = 0.0; done = 0
            for i in idx:
                if done >= 2: break
                if (umin[i] + 1) % 5 == 0:
                    if state == 0 and c[i] > vah: state = 2; ext = h[i]
                    elif state == 0 and c[i] < val: state = -2; ext = l[i]
                    elif state == 2 and c[i] < vah: state = -1  # 上破失败 → 做空
                    elif state == -2 and c[i] > val: state = 1
                if state == 2: ext = max(ext, h[i])
                if state == -2: ext = min(ext, l[i])
                if state == -1 and dl[i] <= -big_k * medv[i] and c[i] < o[i] and c[i] > poc:
                    ent = c[i]; stop = ext * 0.9998
                    if stop > ent and (ent - poc) >= min_rr * (stop - ent):
                        r = close_trade(d, i, -1, ent, stop, poc, b)
                        if r: r["s"] = "MR"; out.append(r); done += 1
                    state = 0
                if state == 1 and dl[i] >= big_k * medv[i] and c[i] > o[i] and c[i] < poc:
                    ent = c[i]; stop = ext * 1.0002
                    if stop < ent and (poc - ent) >= min_rr * (ent - stop):
                        r = close_trade(d, i, 1, ent, stop, poc, b)
                        if r: r["s"] = "MR"; out.append(r); done += 1
                    state = 0
        prev = profile(tp[a:b], v[a:b], c[a])[:3]
    return out
