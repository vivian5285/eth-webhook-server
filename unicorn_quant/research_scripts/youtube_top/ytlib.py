"""博主策略统一回测：策略函数 fn(b) -> dict(sig, stop, tgt=None, exit_sig=None, max_bars, trail_atr, be_r)
复用 speculation_channel/lib._run(njit，收盘出信号→下一根开盘进场，同根碰止损止盈按止损)。
三种口径：① 每笔 R(含费) ② 单策略复利曲线(仓位=资金×min(1, 3%×√(周期/24h)÷ATR%)，与现行子腿同口径)
③ 接进方案F(币圈第4条子腿 / 美股第2条腿)。"""
import os, sys, math, numpy as np, pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "speculation_channel"))
import lib as L
from lib import ema, sma, rma, atr, rsi, macd, adx, pivots
import base as BS
O = BS.O; DAY = 86400000
from numba import njit


# 复制自 speculation_channel/lib._run(带限价单)，独立一份免得被并行研究改动
@njit(cache=True)
def _run(o, h, l, c, sig, stop, tgt, exit_sig, max_bars, trail_atr, atrv, be_r, lim):
    """sig[i]=±1 在 i 收盘产生 → i+1 开盘进场; stop/tgt[i] 为价格(nan=不用);
    exit_sig[i]=1 表示在 i 收盘平掉多(-1 平空)、下一根开盘成交。一次只持一仓。"""
    n = len(o); out = np.full((n, 6), np.nan); k = 0; i = 0
    while i < n - 1:
        s = sig[i]
        if s == 0 or np.isnan(stop[i]):
            i += 1; continue
        e = o[i + 1]; st = stop[i]; tg = tgt[i]
        islim = not np.isnan(lim[i])
        if islim:  # 限价单只在下一根有效: 碰到才成交(跳空则按开盘价)
            if (s == 1 and l[i + 1] > lim[i]) or (s == -1 and h[i + 1] < lim[i]):
                i += 1; continue
            e = min(o[i + 1], lim[i]) if s == 1 else max(o[i + 1], lim[i])
        R = abs(e - st)
        if R <= 0 or (st - e) * s >= 0:
            i += 1; continue
        j = i + 1; px = np.nan; why = 0; best = e
        while j < n:
            if s == 1:
                if l[j] <= st: px = min(o[j], st) if j > i + 1 else st; why = 1; break
                if not np.isnan(tg) and h[j] >= tg and not (islim and j == i + 1): px = max(o[j], tg) if j > i + 1 else tg; why = 2; break
                best = max(best, h[j])
            else:
                if h[j] >= st: px = max(o[j], st) if j > i + 1 else st; why = 1; break
                if not np.isnan(tg) and l[j] <= tg and not (islim and j == i + 1): px = min(o[j], tg) if j > i + 1 else tg; why = 2; break
                best = min(best, l[j])
            if be_r > 0 and (best - e) * s >= be_r * R:
                st = max(st, e) if s == 1 else min(st, e)
            if trail_atr > 0:
                ts = best - s * trail_atr * atrv[j]
                st = max(st, ts) if s == 1 else min(st, ts)
            if exit_sig[j] == s or exit_sig[j] == 9 or j - i >= max_bars:
                if j + 1 < n: px = o[j + 1]; why = 3; j += 1
                else: px = c[j]; why = 3
                break
            j += 1
        if np.isnan(px):
            px = c[n - 1]; j = n - 1; why = 3
        out[k, 0] = i + 1; out[k, 1] = j; out[k, 2] = s; out[k, 3] = e; out[k, 4] = px; out[k, 5] = why
        k += 1; i = j
    return out[:k]


FEE_SW = 0.0008          # 摆动口径每边 8bp(方案F同口径)


def to_df(bl):
    d = pd.DataFrame(bl)
    if "v" not in d: d["v"] = np.nan
    return d[["t", "o", "h", "l", "c", "v"]].reset_index(drop=True)


def trades(b, spec):
    n = len(b)
    sig = np.nan_to_num(np.asarray(spec["sig"], float)).astype(np.int64)
    stop = np.asarray(spec["stop"], float)
    tgt = spec.get("tgt"); tgt = np.full(n, np.nan) if tgt is None else np.asarray(tgt, float)
    ex = spec.get("exit_sig"); ex = np.zeros(n, np.int64) if ex is None else np.nan_to_num(np.asarray(ex, float)).astype(np.int64)
    lim = spec.get("limit"); lim = np.full(n, np.nan) if lim is None else np.asarray(lim, float)
    r = _run(b.o.values, b.h.values, b.l.values, b.c.values, sig, stop, tgt, ex, int(spec.get("max_bars", 10**9)),
             float(spec.get("trail_atr", 0.0)), atr(b, 14), float(spec.get("be_r", 0.0)), lim)
    if not len(r): return pd.DataFrame(columns=["ii", "jj", "side", "ent", "px", "why", "risk", "t"])
    ii = r[:, 0].astype(int)
    return pd.DataFrame({"ii": ii, "jj": r[:, 1].astype(int), "side": r[:, 2], "ent": r[:, 3], "px": r[:, 4], "why": r[:, 5],
                         "risk": np.abs(r[:, 3] - stop[ii - 1]) / r[:, 3], "t": b.t.values[ii]})


def rstats(tr, fee=FEE_SW, oos_ms=None):
    if tr is None or not len(tr): return dict(n=0)
    g = tr.side * (tr.px - tr.ent) / tr.ent; net = g - 2 * fee
    rk = tr.risk.clip(lower=max(1e-4, 0.3 * tr.risk.median()))   # 极小止损(跳空贴近止损)会把 R 放大成离群值
    rn = (net / rk).clip(-3, 30)
    d = dict(n=len(tr), win=round((net > 0).mean(), 3), r_g=round((g / rk).clip(-3, 30).mean(), 3), r=round(rn.mean(), 3),
             risk=round(tr.risk.median(), 4), ret=round(net.mean() * 100, 2))
    if oos_ms:
        d["r_is"] = round(rn[tr.t < oos_ms].mean(), 3); d["r_oos"] = round(rn[tr.t >= oos_ms].mean(), 3)
    return d


def curve(b, tr, hrs, vt=0.03, fee=FEE_SW):
    """逐根盯市的单策略复利曲线 {日: 净值}"""
    A = atr(b, 14); o, c = b.o.values, b.c.values; t = b.t.values
    v = vt * math.sqrt(hrs / 24); eq = 1.0; out = {}
    ent = {int(r.ii): r for r in tr.itertuples()}; cur = None; q = 0.0; pc = 0.0
    for i in range(1, len(b)):
        if cur is None and i in ent:
            cur = ent[i]; al = min(1.0, v / max(A[i - 1] / c[i - 1], 1e-6))
            q = cur.side * eq * al / cur.ent; eq -= abs(q) * cur.ent * fee; pc = cur.ent
        if cur is not None:
            if i == cur.jj:
                eq += q * (cur.px - pc) - abs(q) * cur.px * fee; cur = None; q = 0.0
            else:
                eq += q * (c[i] - pc); pc = c[i]
        out[int(t[i] // DAY)] = max(eq, 1e-9)
    return out


def phase_filt(b, sig, hrs):
    s = np.asarray(sig, float).copy()
    for i, t in enumerate(b.t.values):
        if s[i]:
            ph, _ = O.phase_of(int(t) + int(hrs * 3600000))
            if (ph == "牛" and s[i] < 0) or (ph == "熊" and s[i] > 0): s[i] = 0
    return s


def cross_up(a, b):
    a = np.asarray(a); b = np.asarray(b)
    return (a > b) & (np.r_[np.nan, a[:-1]] <= np.r_[np.nan, b[:-1]])


def shift(x, k=1, fill=np.nan):
    x = np.asarray(x, float)
    return np.r_[np.full(k, fill), x[:-k]] if k > 0 else x


_DATA = {}
def crypto(tf):
    if tf not in _DATA:
        B = BS.base()
        _DATA[tf] = {s: to_df(B["CD"][s] if tf == "1d" else B["B8"][s]) for s in O.M5}
    return _DATA[tf]


def fmtR(R):
    return (f"{R.get('n', 0):5d}笔 胜{R.get('win', 0):.0%} R{R.get('r', 0):+.2f}(前{R.get('r_is', 0):+.2f}/后{R.get('r_oos', 0):+.2f}) "
            f"止损{R.get('risk', 0):.1%}")


def eval_crypto(name, fn, tfs=("1d", "8h"), phase=(False, True), show_yr=False, **kw):
    """逐周期×(原始/减半过滤)：R 统计 + 腿自己 + 作第4腿"""
    rows = []; B = BS.base(); b0 = B["b"]
    for tf in tfs:
        hrs = 24 if tf == "1d" else 8; D = crypto(tf)
        for ph in phase:
            cvs = {}; allt = []
            for s in O.M5:
                b = D[s]; sp = fn(b, **kw)
                if ph: sp = dict(sp, sig=phase_filt(b, sp["sig"], hrs))
                tr = trades(b, sp); allt.append(tr); cvs[s] = curve(b, tr, hrs)
            T = pd.concat(allt); R = rstats(T, oos_ms=BS.H * DAY)
            leg = O.combine([cvs[s] for s in O.M5], [0.2] * 5); a = BS.three(leg)
            F = BS.three(BS.as_4th_leg(cvs))
            line = f"{name:24s} {tf:3s} {'减半' if ph else '原始'} | {fmtR(R)} | 腿 {BS.fmt(a)} | 第4腿 {BS.fmt(F)} {BS.verdict(F, b0)}"
            if show_yr: line += " | " + BS.yr(leg)
            print(line, flush=True)
            rows.append(dict(name=name, tf=tf, phase=ph, R=R, leg=a, F=F, cvs=cvs))
    return rows


# ---- 美股 ----
_ST = {}
def stocks():
    if not _ST:
        for tk in sorted(O.TRADABLE_FALLBACK):
            try: b = to_df(O.stock_daily(tk))
            except Exception: continue
            if len(b) > 60: _ST[tk] = b
    return _ST


def stock_portfolio(trs, K=5, vt=0.03, fee=FEE_SW):
    """共用账户：单只名义 = 净值×min(1, 3%÷ATR%)÷K，总名义≤1倍；逐日盯市。trs={tk: (b, trades)}"""
    ev = {}
    for tk, (b, tr) in trs.items():
        A = atr(b, 14)
        for r in tr.itertuples():
            ev.setdefault(int(b.t.values[r.ii] // DAY), []).append((tk, r, A[r.ii - 1] / b.c.values[r.ii - 1]))
    closes = {tk: dict(zip((b.t.values // DAY).astype(int), b.c.values)) for tk, (b, _) in trs.items()}
    idx = {tk: dict(zip((b.t.values // DAY).astype(int), range(len(b)))) for tk, (b, _) in trs.items()}
    days = sorted(set().union(*[set(c) for c in closes.values()])); eq = 1.0; op = {}; out = {}
    for d in days:
        for tk in list(op):                      # 平仓(出场那根按成交价)
            q, pc, r = op[tk]; i = idx[tk].get(d)
            if i is not None and i >= r.jj:
                eq += q * (r.px - pc) - abs(q) * r.px * fee; del op[tk]
        for tk, r, ap in ev.get(d, []):          # 开仓
            if tk in op: continue
            gross = sum(abs(q) * pc for (q, pc, _) in op.values())
            nominal = min(eq * min(1.0, vt / max(ap, 1e-6)) / K, max(0.0, eq - gross))
            if nominal <= 0: continue
            q = r.side * nominal / r.ent; eq -= nominal * fee
            if r.jj <= r.ii: eq += q * (r.px - r.ent) - abs(q) * r.px * fee
            else: op[tk] = (q, r.ent, r)
        for tk in list(op):                      # 盯市
            q, pc, r = op[tk]; c = closes[tk].get(d)
            if c is not None: eq += q * (c - pc); op[tk] = (q, c, r)
        out[d] = max(eq, 1e-9)
    return out


def eval_stock(name, fn, **kw):
    B = BS.base(); b0 = B["b"]; trs = {}; allt = []
    for tk, b in stocks().items():
        tr = trades(b, fn(b, **kw)); trs[tk] = (b, tr); allt.append(tr)
    T = pd.concat(allt); R = rstats(T, oos_ms=BS.H * DAY)
    cv = stock_portfolio(trs); a = BS.three(cv); F = BS.three(BS.as_stock_leg2(cv)); ref = BS.three(B["sc"])
    print(f"{name:24s} 美股    | {fmtR(R)} | 腿 {BS.fmt(a)} (现行美股腿{ref[2]:.2f}) | 美股第2腿 {BS.fmt(F)} {BS.verdict(F, b0)}", flush=True)
    return dict(name=name, tf="1d美股", R=R, leg=a, F=F, cv=cv)


# ---- 日内(币安永续 1m 合成，2022-01..2026-09) ----
def _flip(b, sp, seed):
    """随机方向对照：同样的进场时点/止损距离/出场规则，方向抛硬币(止损/止盈以信号收盘价镜像)"""
    c = b.c.values; rng = np.random.default_rng(seed); f = rng.choice([-1, 1], len(c))
    sig = np.asarray(sp["sig"], float); fl = (sig != 0) & (f < 0)
    out = dict(sp, sig=np.where(fl, -sig, sig), stop=np.where(fl, 2 * c - np.asarray(sp["stop"], float), sp["stop"]))
    if sp.get("tgt") is not None: out["tgt"] = np.where(fl, 2 * c - np.asarray(sp["tgt"], float), sp["tgt"])
    return out


def eval_intraday(name, fn, tf, syms=L.SYMS, control=True, **kw):
    allt = []; ctl = []
    for s in syms:
        b = L.bars(s, tf); sp = fn(b, **kw); allt.append(trades(b, sp))
        ex = sp.get("exit_sig")
        if control and sp.get("limit") is None and (ex is None or set(np.unique(ex)) <= {0, 9}):
            for seed in range(5): ctl.append(trades(b, _flip(b, sp, seed)))
    T = pd.concat(allt)
    z = rstats(T, fee=0.0); f = rstats(T, fee=L.FEE + L.SLIP, oos_ms=L.OOS); m = rstats(T, fee=0.0002)
    cz = rstats(pd.concat(ctl), fee=0.0).get("r", np.nan) if ctl else np.nan
    print(f"{name:24s} {tf:4s} | {f.get('n', 0):6d}笔 胜{f.get('win', 0):.0%} 零成本R{z.get('r', 0):+.3f} (随机方向{cz:+.3f}) 挂单R{m.get('r', 0):+.3f} "
          f"含费R{f.get('r', 0):+.3f}(前{f.get('r_is', 0):+.2f}/后{f.get('r_oos', 0):+.2f}) 止损{f.get('risk', 0):.2%}", flush=True)
    return dict(name=name, tf=tf, zero=z, maker=m, R=f, ctl=cz, T=T)
