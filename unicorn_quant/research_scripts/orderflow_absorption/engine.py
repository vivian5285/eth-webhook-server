"""Chris Creamer 订单流"吸收→再砸失败"策略复刻 (油管 fklnLclljFQ)，币安永续 1m 数据。

原文四步 → 本复刻:
1 趋势: 前几天的价值区(当日70%成交量区间)一天比一天高 → 只做多; 一天比一天低 → 只做空。
  GEX(期权伽马)只管波动大小、不管方向，币圈无对应数据，不复刻。
2 位置: 最近波段(24h内)低点→高点画斐波那契，0.705~0.886 是进场区；
  且在前一天价值区外面(做多在 VAL 下方的折扣区)；收盘跌破 0.886 不做。
3 确认(5m 足迹，用 5 根 1m 子线的价位+主动买卖近似):
  B1: 低点进入进场区、下影线里 delta 明显为负(卖单被吸收)、成交量最大的价位在下影线里、收阳；
  B2(下一根): 先回落(低于 B1 收盘)但低点高于 B1 低点(再砸失败)、delta>0(主动买发力)、收阳 → B2 收盘进场。
4 执行: 止损 B2 低点下方；止盈看前一天 POC / 波段高点，或固定 1.5R/2R；
  30 分钟回不到价值区就挪保本；+1R 后按上一根 5m 低点跟踪。
过滤: B1 成交量 ≥ 近20天同时段开盘K中位数的 vol_k 倍；时段只做纽约开盘后 90 分钟；每天最多 2 笔。

无前视: 价值区只用已收完的日；波段只用 B1 之前的 5m；进场价 = B2 收盘(+滑点)；出场在其后 1m 上逐根模拟，
同一根 1m 同时碰止损和止盈按止损算。
"""
import os, numpy as np, pandas as pd
from numba import njit

M1 = 60_000; M5 = 300_000; DAY = 86400_000
DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")


def load(sym):
    d = pd.read_pickle(os.path.join(DATA, f"{sym}_1m.pkl")).reset_index(drop=True)
    d["d"] = 2 * d.tb - d.v
    return d


def value_areas(d, bin_frac=0.0005, va=0.70):
    """每个 UTC 日的 POC/VAH/VAL (成交量按 1m 典型价分箱)。"""
    day = (d.t.values // DAY)
    tp = ((d.h + d.l + d.c) / 3).values; v = d.v.values
    out = {}
    starts = np.r_[0, np.flatnonzero(np.diff(day)) + 1, len(day)]
    for a, b in zip(starts[:-1], starts[1:]):
        p = tp[a:b]; w = v[a:b]
        lo, hi = p.min(), p.max()
        nb = max(20, int((hi - lo) / (p[0] * bin_frac)) + 1)
        hist, edges = np.histogram(p, bins=nb, range=(lo, hi + 1e-12), weights=w)
        k = int(hist.argmax()); L = R = k; tot = hist.sum(); acc = hist[k]
        while acc < va * tot and (L > 0 or R < nb - 1):
            up = hist[R + 1] if R < nb - 1 else -1; dn = hist[L - 1] if L > 0 else -1
            if up >= dn: R += 1; acc += up
            else: L -= 1; acc += dn
        out[int(day[a])] = ((edges[k] + edges[k + 1]) / 2, edges[R + 1], edges[L])
    days = np.array(sorted(out))
    vals = np.array([out[x] for x in days])
    return pd.DataFrame({"day": days, "poc": vals[:, 0], "vah": vals[:, 1], "val": vals[:, 2]}).set_index("day")


def bars5(d):
    """5m K线 + 足迹近似特征。"""
    g = d.t.values // M5
    agg = d.groupby(g).agg(t=("t", "first"), o=("o", "first"), h=("h", "max"), l=("l", "min"), c=("c", "last"),
                           v=("v", "sum"), dl=("d", "sum"))
    agg["t"] = agg.index.values * M5
    bb = np.minimum(agg.o, agg.c); bt = np.maximum(agg.o, agg.c)
    gi = pd.Series(g).map(pd.Series(np.arange(len(agg)), index=agg.index)).values
    h1, l1, d1, v1 = d.h.values, d.l.values, d.d.values, d.v.values
    rng = np.maximum(h1 - l1, 1e-12)
    fb = np.clip((bb.values[gi] - l1) / rng, 0, 1); fa = np.clip((h1 - bt.values[gi]) / rng, 0, 1)
    agg["wd_lo"] = np.bincount(gi, d1 * fb, len(agg))     # 下影线里的 delta
    agg["wd_hi"] = np.bincount(gi, d1 * fa, len(agg))     # 上影线里的 delta
    tp = (h1 + l1 + d.c.values) / 3
    idx = pd.Series(v1).groupby(gi).idxmax().values      # 每根 5m 里成交量最大的 1m
    agg["poc"] = tp[idx]
    agg = agg.reset_index(drop=True)
    agg["i1"] = np.searchsorted(d.t.values, agg.t.values)  # 该 5m 在 1m 序列里的起点
    return agg


def ny_minutes(t_ms):
    ts = pd.to_datetime(t_ms, unit="ms", utc=True).tz_convert("America/New_York")
    return (ts.hour * 60 + ts.minute).values, ts.normalize().tz_localize(None).values.astype("datetime64[D]").astype(np.int64)


def annotate(b):
    """纽约时刻 + 量能参照(只用之前的日子)，算一次缓存在 b 上。"""
    nym, nyday = ny_minutes(b.t.values)
    b["nym"] = nym; b["nyday"] = nyday
    s = pd.DataFrame({"m": nym, "v": b.v.values})
    b["ref_same"] = s.groupby("m").v.transform(lambda x: x.shift(1).rolling(20, min_periods=10).median()).values
    b["ref_open"] = pd.Series(np.where(nym == 570, b.v.values, np.nan)).shift(1).ffill() \
        .rolling(20 * 288, min_periods=1).median().values


def candidates(b, vaf, P):
    """返回信号列表: (B2 索引, 方向, 止损, 止盈结构价, 前日VAL/VAH, 波段)。"""
    n = len(b)
    t = b.t.values; o = b.o.values; h = b.h.values; l = b.l.values; c = b.c.values; v = b.v.values
    dl = b.dl.values; wlo = b.wd_lo.values; whi = b.wd_hi.values; poc5 = b.poc.values
    if "nym" not in b:
        annotate(b)
    nym, nyday = b.nym.values, b.nyday.values
    day = t // DAY
    # 前一 UTC 日价值区 + 趋势
    vd = vaf.reindex(np.arange(day.min() - 5, day.max() + 1))
    mid = ((vd.vah + vd.val) / 2).values; off = day.min() - 5
    vah = vd.vah.values; val = vd.val.values; vpoc = vd.poc.values

    def prev(arr, k):
        return arr[day - k - off]
    tr = np.zeros(n, np.int8)
    if P["trend_days"] == 0:
        tr[:] = 2  # 不过滤: 两个方向都允许
    else:
        up = np.ones(n, bool); dn = np.ones(n, bool)
        for k in range(1, P["trend_days"] + 1):
            up &= prev(mid, k) > prev(mid, k + 1); dn &= prev(mid, k) < prev(mid, k + 1)
            if P.get("strict_va"):
                up &= (prev(val, k) > prev(val, k + 1)) & (prev(vah, k) > prev(vah, k + 1))
                dn &= (prev(val, k) < prev(val, k + 1)) & (prev(vah, k) < prev(vah, k + 1))
        tr[up] = 1; tr[dn] = -1
    pval, pvah, ppoc = prev(val, 1), prev(vah, 1), prev(vpoc, 1)
    # 时段
    w0, w1 = P["win"]
    inwin = (nym >= w0) & (nym < w1)
    # 量能: 近20天同一 NY 时刻 5m 成交量中位数(只用之前的日子)
    volref = b.ref_same.values if P.get("vol_ref") == "same_time" else b.ref_open.values
    volok = v >= P["vol_k"] * volref if P["vol_k"] > 0 else np.ones(n, bool)
    rng = np.maximum(h - l, 1e-12)
    bb = np.minimum(o, c); bt = np.maximum(o, c)
    W = P["swing_w"]
    out = []
    lastday = -1; cnt = 0; nxt = 0
    # 先向量化粗筛: B1 收阳/收阴 且 B2 再砸失败并翻色
    o2, h2, l2, c2 = np.r_[o[1:], 0], np.r_[h[1:], 0], np.r_[l[1:], 0], np.r_[c[1:], 0]
    pat = ((c > o) & (l2 < c) & (l2 > l) & (c2 > o2)) | ((c < o) & (h2 > c) & (h2 < h) & (c2 < o2))
    pat &= inwin & volok
    pat[:W + 1] = False; pat[n - 2:] = False
    for i in np.flatnonzero(pat):
        if i < nxt:
            continue
        for side in (1, -1):
            if tr[i] != 2 and tr[i] != side:
                continue
            j = i + 1  # B2
            if side == 1:
                # B1 收阳 + 足迹吸收
                if c[i] <= o[i]: continue
                if P["fp"]:
                    if wlo[i] > -P["wick_k"] * v[i] or poc5[i] >= bb[i]: continue
                else:
                    if (bb[i] - l[i]) < 0.3 * rng[i]: continue  # 纯价格对照: 只要求明显下影线
                # B2 再砸失败
                if not (l[j] < c[i] and l[j] > l[i] and c[j] > o[j]): continue
                if P["fp"] and dl[j] <= P["b2_k"] * v[j]: continue
                # 位置
                hs = h[i - W:i]; ih = int(hs.argmax()); H = hs[ih]; L = l[i - W:i - W + ih + 1].min()
                if P["loc"] >= 1:
                    f705 = H - 0.705 * (H - L); f886 = H - 0.886 * (H - L)
                    if not (l[i] <= f705 and c[i] >= f886): continue
                if P["loc"] >= 2 and not (l[i] < pval[i]): continue
                ent = c[j]; stop = l[j] * (1 - 0.0001)
                tgt_struct = [x for x in (ppoc[i], H) if x > ent]
            else:
                if c[i] >= o[i]: continue
                if P["fp"]:
                    if whi[i] < P["wick_k"] * v[i] or poc5[i] <= bt[i]: continue
                else:
                    if (h[i] - bt[i]) < 0.3 * rng[i]: continue
                if not (h[j] > c[i] and h[j] < h[i] and c[j] < o[j]): continue
                if P["fp"] and dl[j] >= -P["b2_k"] * v[j]: continue
                ls = l[i - W:i]; il = int(ls.argmin()); L = ls[il]; H = h[i - W:i - W + il + 1].max()
                if P["loc"] >= 1:
                    f705 = L + 0.705 * (H - L); f886 = L + 0.886 * (H - L)
                    if not (h[i] >= f705 and c[i] <= f886): continue
                if P["loc"] >= 2 and not (h[i] > pvah[i]): continue
                ent = c[j]; stop = h[j] * (1 + 0.0001)
                tgt_struct = [x for x in (ppoc[i], L) if x < ent]
            if nyday[i] != lastday:
                lastday = nyday[i]; cnt = 0
            if cnt >= P["max_day"]:
                continue
            cnt += 1
            R = abs(ent - stop)
            ts = sorted(tgt_struct, key=lambda x: abs(x - ent))
            ts = [x for x in ts if abs(x - ent) >= R] or [ent + side * 1.5 * R]
            out.append((j, side, ent, stop, ts[0], pval[i] if side == 1 else pvah[i], t[j] + M5))
            nxt = j + 1
            break
    return out


@njit(cache=True)
def sim(h1, l1, c1, i0, side, ent, stop, tgt, va_edge, max_bars, be_bars, trail):
    """1m 逐根模拟。返回 (出场价, 原因 0=时间 1=止损 2=止盈, 持有分钟)。"""
    st = stop; R = abs(ent - stop); back = False; lo5 = 1e18; hi5 = -1e18
    n = len(h1)
    for k in range(max_bars):
        i = i0 + k
        if i >= n: return c1[n - 1], 0, k
        if side == 1:
            if l1[i] <= st: return st, 1, k
            if h1[i] >= tgt: return tgt, 2, k
            if c1[i] > va_edge: back = True
        else:
            if h1[i] >= st: return st, 1, k
            if l1[i] <= tgt: return tgt, 2, k
            if c1[i] < va_edge: back = True
        lo5 = min(lo5, l1[i]); hi5 = max(hi5, h1[i])
        if (k + 1) % 5 == 0:
            if be_bars > 0 and k + 1 >= be_bars and not back:
                st = max(st, ent) if side == 1 else min(st, ent)
            if trail:
                if side == 1 and c1[i] - ent >= R: st = max(st, lo5)
                if side == -1 and ent - c1[i] >= R: st = min(st, hi5)
            lo5 = 1e18; hi5 = -1e18
    return c1[min(i0 + max_bars - 1, n - 1)], 0, max_bars


def run_exits(d, sigs, E, fee=0.0005, slip=0.00015, stop_slip=0.0002):
    h1, l1, c1, t1 = d.h.values, d.l.values, d.c.values, d.t.values
    rows = []
    for (j, side, ent, stop, tstruct, edge, te) in sigs:
        i0 = int(np.searchsorted(t1, te))
        R = abs(ent - stop)
        tgt = tstruct if E["tp"] == "struct" else ent + side * E["tp"] * R
        px, why, held = sim(h1, l1, c1, i0, side, ent, stop, tgt, edge, E["max_min"], E["be"], E["trail"])
        gross = side * (px - ent) / ent
        cost = 2 * (fee + slip) + (stop_slip if why == 1 else 0)
        rp = R / ent
        rows.append((te, side, gross / rp, (gross - cost) / rp, rp, why, held, gross - cost))
    return pd.DataFrame(rows, columns=["t", "side", "r_gross", "r_net", "risk_pct", "why", "held", "ret_net"])
