"""ETH 1h方向 + 5m平台突破 日内策略回测引擎 (ETHUSDT 永续, Binance 5m 2020-01..2026-09).

无前视约定:
- 1h 指标只用已收盘 1h K; 5m K 收盘时刻 = 小时边界时, 该小时K视为刚收盘可用.
- 平台高低点只取当前 5m K 之前 M 根.
- 信号在 5m 收盘产生, 下一根 5m 开盘成交.
"""
import numpy as np, pandas as pd
from numba import njit

H = 3600_000; M5 = 300_000; DAY = 86400_000
DATA = __import__("os").path.join(__import__("os").path.dirname(__file__), "data")


def ema(x, n):
    return pd.Series(x).ewm(span=n, adjust=False).mean().values


def wilder(x, n):
    return pd.Series(x).ewm(alpha=1 / n, adjust=False).mean().values


def atr_adx(h, l, c, n=14):
    pc = np.r_[c[0], c[:-1]]
    tr = np.maximum(h - l, np.maximum(abs(h - pc), abs(l - pc)))
    atr = wilder(tr, n)
    up = h - np.r_[h[0], h[:-1]]; dn = np.r_[l[0], l[:-1]] - l
    pdm = np.where((up > dn) & (up > 0), up, 0.0); ndm = np.where((dn > up) & (dn > 0), dn, 0.0)
    a = wilder(tr, n)
    pdi = 100 * wilder(pdm, n) / a; ndi = 100 * wilder(ndm, n) / a
    dx = 100 * abs(pdi - ndi) / np.maximum(pdi + ndi, 1e-12)
    return atr, wilder(dx, n)


def resample(df, ms):
    g = df.t // ms * ms
    r = df.groupby(g).agg(o=("o", "first"), h=("h", "max"), l=("l", "min"), c=("c", "last"), v=("v", "sum"))
    r["t"] = r.index.values
    return r.reset_index(drop=True)


@njit(cache=True)
def dir_state(c, ef, es, adx, atr, adx_thr, dist_thr, cooldown):
    n = len(c); out = np.zeros(n, np.int8); st = 0; cd = 0
    for j in range(1, n):
        dist = abs(ef[j] - es[j]) / atr[j] if atr[j] > 0 else 0.0
        rl = c[j] > ef[j] and ef[j] > es[j] and ef[j] > ef[j - 1] and adx[j] > adx_thr and dist > dist_thr
        rs = c[j] < ef[j] and ef[j] < es[j] and ef[j] < ef[j - 1] and adx[j] > adx_thr and dist > dist_thr
        if st == 1 and (c[j] < es[j] or ef[j] < es[j]):
            st = 0; cd = cooldown
        elif st == -1 and (c[j] > es[j] or ef[j] > es[j]):
            st = 0; cd = cooldown
        elif st == 0:
            if cd > 0:
                cd -= 1
            elif rl:
                st = 1
            elif rs:
                st = -1
        out[j] = st
    return out


def map_completed(t5, t_hi, vals, period):
    """把高周期(已收盘)的值映射到每根5m K的收盘时刻."""
    close_t = t5 + M5
    last_open = close_t // period * period - period  # 最近一根已收盘高周期K的开盘时间
    idx = np.searchsorted(t_hi, last_open)
    ok = (idx < len(t_hi)) & (t_hi[np.minimum(idx, len(t_hi) - 1)] == last_open)
    out = np.where(ok, vals[np.minimum(idx, len(t_hi) - 1)], np.nan if vals.dtype.kind == "f" else 0)
    return out


class Data:
    def __init__(self):
        d = pd.read_pickle(f"{DATA}/ETHUSDT_5m.pkl").reset_index(drop=True)
        self.t = d.t.values.astype(np.int64)
        self.o, self.h, self.l, self.c, self.v = (d[k].values.astype(np.float64) for k in "ohlcv")
        self.atr5, _ = atr_adx(self.h, self.l, self.c)
        self.vma = pd.Series(self.v).rolling(20).mean().shift(1).values
        h1 = resample(d, H); self.h1 = h1
        self.atr1h, self.adx1h = atr_adx(h1.h.values, h1.l.values, h1.c.values)
        self.atr1h_5 = map_completed(self.t, h1.t.values, self.atr1h, H)
        m15 = resample(d, 15 * 60_000)
        a15, _ = atr_adx(m15.h.values, m15.l.values, m15.c.values)
        self.atr15_5 = map_completed(self.t, m15.t.values, a15, 15 * 60_000)
        b = pd.read_pickle(f"{DATA}/BTCUSDT_1h.pkl").reset_index(drop=True); self.b1 = b
        self.batr, self.badx = atr_adx(b.h.values, b.l.values, b.c.values)
        f = pd.read_pickle(f"{DATA}/ETHUSDT_funding.pkl")
        ft = (f.fundingTime.values + M5 // 2) // M5 * M5
        self.fr = np.zeros(len(self.t))
        idx = np.searchsorted(self.t, ft); ok = (idx < len(self.t))
        ok[ok] &= self.t[idx[ok]] == ft[ok]
        self.fr[idx[ok]] = f.fundingRate.values[ok]
        self._plat = {}; self._dir = {}

    def plat(self, M):
        if M not in self._plat:
            ph = pd.Series(self.h).rolling(M).max().shift(1).values
            pl = pd.Series(self.l).rolling(M).min().shift(1).values
            self._plat[M] = (np.nan_to_num(ph), np.nan_to_num(pl))
        return self._plat[M]

    def direction(self, fast, slow, adx_thr, dist_thr, cooldown=1, btc=False):
        k = (fast, slow, adx_thr, dist_thr, cooldown, btc)
        if k not in self._dir:
            src = self.b1 if btc else self.h1
            atr, adx = (self.batr, self.badx) if btc else (self.atr1h, self.adx1h)
            c = src.c.values
            s = dir_state(c, ema(c, fast), ema(c, slow), adx, atr, adx_thr, dist_thr, cooldown)
            self._dir[k] = map_completed(self.t, src.t.values, s, H).astype(np.int8)
        return self._dir[k]


@njit(cache=True)
def run(t, o, h, l, c, v, vma, atr5, atr1h, atr15, fr, dirn, bdir, ph, pl,
        win_s, win_e, plat_k, buf_k, chase_k, gap_k, vol_k, stop_k, smin_k, smax_k,
        trail_k, trail_mode, be_r, chand_r, chand_k, exit_mode, risk, lev, fee, slip, stop_slip,
        max_trades, max_stops, day_loss, sideflag, n_below, t_half, t_flat, t_max,
        fcut, fund_block, i0, i1):
    """返回 (trade记录, 每日权益). sideflag: 1只多 -1只空 0双向 2反向(突破反做,测对照)."""
    n = len(t)
    eq = 10000.0; peak = eq
    pos = 0; qty = 0.0; ent = 0.0; stop = 0.0; trail = 0.0; R = 0.0; hh = 0.0; ll = 0.0
    ent_i = 0; pl_e = 0.0; ph_e = 0.0; halved = False; be_done = False; chand = False
    pend_exit = 0; pend_half = False; pend_dir = 0; pend_reason = 0; below = 0
    tr_pnl = 0.0; tr_fee = 0.0; tr_fund = 0.0; tr_risk = 0.0
    day = -1; day_eq = eq; ntr = 0; nstp = 0; halt = False
    MAXT = 200000
    T = np.zeros((MAXT, 10)); nt = 0
    D = np.zeros((4000, 2)); nd = 0
    for i in range(i0, i1):
        ti = t[i]; tod = ti % 86400000
        d = ti // 86400000
        if d != day:
            if day >= 0 and nd < 4000:
                D[nd, 0] = day; D[nd, 1] = eq; nd += 1
            day = d; day_eq = eq; ntr = 0; nstp = 0; halt = False
        # ---- 资金费 (结算时刻仍持仓) ----
        if pos != 0 and fr[i] != 0.0 and ent_i < i:
            f = fr[i] * qty * o[i] * pos
            eq -= f; tr_fund += f
        # ---- 开盘执行挂起动作 ----
        if pos != 0 and pend_exit != 0:
            px = o[i] * (1 - slip * pos)
            g = (px - ent) * qty * pos; fe = fee * px * qty
            eq += g - fe; tr_pnl += g; tr_fee += fe
            T[nt, 0] = t[ent_i]; T[nt, 1] = ti; T[nt, 2] = pos; T[nt, 3] = tr_pnl - tr_fee - tr_fund
            T[nt, 4] = (tr_pnl - tr_fee - tr_fund) / tr_risk; T[nt, 5] = pend_reason; T[nt, 6] = tr_fee
            T[nt, 7] = tr_fund; T[nt, 8] = (i - ent_i); T[nt, 9] = ent; nt += 1
            if pend_reason == 1 and tr_pnl - tr_fee - tr_fund < 0:
                nstp += 1
            pos = 0; pend_exit = 0; pend_half = False
        elif pos != 0 and pend_half:
            q2 = qty * 0.5; px = o[i] * (1 - slip * pos)
            g = (px - ent) * q2 * pos; fe = fee * px * q2
            eq += g - fe; tr_pnl += g; tr_fee += fe; qty -= q2; pend_half = False
        if pos == 0 and pend_dir != 0:
            dd = pend_dir; pend_dir = 0
            if abs(o[i] - c[i - 1]) <= gap_k * atr5[i - 1]:
                e = o[i] * (1 + slip * dd)
                st = (pl_e - stop_k * atr5[i - 1]) if dd == 1 else (ph_e + stop_k * atr5[i - 1])
                dist = (e - st) * dd
                a1 = atr1h[i - 1]
                if dist < smin_k * a1:
                    dist = smin_k * a1
                if dist <= smax_k * a1 and dist > 0:
                    rk = risk * (0.5 if eq < peak * 0.9 else 1.0)
                    q = eq * rk / dist
                    if q * e > lev * eq:
                        q = lev * eq / e
                    pos = dd; qty = q; ent = e; R = dist; stop = e - dist * dd
                    trail = e - trail_k * atr5[i - 1] * dd if trail_mode == 0 else stop
                    hh = e; ll = e; ent_i = i; halved = False; be_done = False; chand = False; below = 0
                    fe = fee * e * q; eq -= fe
                    tr_pnl = 0.0; tr_fee = fe; tr_fund = 0.0; tr_risk = eq * rk if eq * rk > 0 else 1.0
                    ntr += 1
        # ---- 盘中: 交易所止损单 (exit_mode 0) ----
        if pos != 0:
            es = stop
            if pos == 1 and trail > es:
                es = trail
            if pos == -1 and trail < es:
                es = trail
            hit = (l[i] <= es) if pos == 1 else (h[i] >= es)
            if exit_mode == 0 and hit:
                px = (min(o[i], es) if pos == 1 else max(o[i], es)) * (1 - (slip + stop_slip) * pos)
                g = (px - ent) * qty * pos; fe = fee * px * qty
                eq += g - fe; tr_pnl += g; tr_fee += fe
                T[nt, 0] = t[ent_i]; T[nt, 1] = ti; T[nt, 2] = pos; T[nt, 3] = tr_pnl - tr_fee - tr_fund
                T[nt, 4] = (tr_pnl - tr_fee - tr_fund) / tr_risk; T[nt, 5] = 1; T[nt, 6] = tr_fee
                T[nt, 7] = tr_fund; T[nt, 8] = (i - ent_i); T[nt, 9] = ent; nt += 1
                if tr_pnl - tr_fee - tr_fund < 0:
                    nstp += 1
                pos = 0; pend_half = False
            elif exit_mode == 1 and hit:
                pend_exit = 1; pend_reason = 1
            elif exit_mode == 2 and ((c[i] <= es) if pos == 1 else (c[i] >= es)):
                pend_exit = 1; pend_reason = 1
        if eq > peak:
            peak = eq
        if eq <= day_eq * (1 - day_loss):
            halt = True
        # ---- 收盘: 持仓管理 ----
        if pos != 0 and pend_exit == 0:
            if h[i] > hh:
                hh = h[i]
            if l[i] < ll:
                ll = l[i]
            prof = (c[i] - ent) * pos / R
            if trail_mode == 0 or (trail_mode == 1 and prof >= 1.0):
                nt_ = c[i] - trail_k * atr5[i] * pos
                if (pos == 1 and nt_ > trail) or (pos == -1 and nt_ < trail):
                    trail = nt_
            if be_r > 0 and not be_done and prof >= be_r:
                be_done = True
                if (ent - stop) * pos > 0:
                    stop = ent
            if chand_r > 0 and prof >= chand_r:
                chand = True
            if chand:
                cs = hh - chand_k * atr15[i] if pos == 1 else ll + chand_k * atr15[i]
                if (pos == 1 and cs > stop) or (pos == -1 and cs < stop):
                    stop = cs
            # 平台下轨连续收破
            if (pos == 1 and c[i] < pl_e) or (pos == -1 and c[i] > ph_e):
                below += 1
            else:
                below = 0
            held = i + 1 - ent_i
            ntod = (ti + 300000) % 86400000
            r = 0
            if dirn[i] != pos and sideflag != 2:
                r = 2
            elif sideflag == 2 and dirn[i] != -pos:
                r = 2
            elif n_below > 0 and below >= n_below:
                r = 3
            elif held >= t_max:
                r = 4
            elif t_flat > 0 and held >= t_flat and prof <= 0:
                r = 4
            elif ntod >= fcut and ntod < fcut + 300000 * 6:
                r = 5
            if r > 0:
                pend_exit = 1; pend_reason = r
            elif t_half > 0 and not halved and held >= t_half and prof < 0.5:
                halved = True; pend_half = True
        # ---- 收盘: 开仓信号 ----
        if pos == 0 and pend_dir == 0 and not halt and ntr < max_trades and nstp < max_stops and i + 1 < i1:
            dd0 = dirn[i]
            if dd0 != 0 and atr5[i] > 0 and bdir[i] == dd0:
                etod = (ti + 300000) % 86400000; eh = etod // 3600000
                inwin = (eh >= win_s and eh < win_e) if win_s < win_e else (eh >= win_s or eh < win_e)
                fb = False
                if fund_block > 0:
                    m = etod % 28800000
                    fb = m >= 28800000 - fund_block * 60000
                if inwin and not fb and etod < fcut - 1800000:
                    a = atr5[i]
                    if ph[i] - pl[i] <= plat_k * a and (vol_k <= 0 or v[i] > vol_k * vma[i]):
                        up = c[i] > ph[i] + buf_k * a and c[i] - ph[i] <= chase_k * a
                        dn = c[i] < pl[i] - buf_k * a and pl[i] - c[i] <= chase_k * a
                        sig = 0
                        if dd0 == 1 and up:
                            sig = 1
                        elif dd0 == -1 and dn:
                            sig = -1
                        if sideflag == 2:  # 反向对照: 顺1h方向的突破反着做
                            sig = -sig
                        elif sideflag != 0 and sig != sideflag:
                            sig = 0
                        if sig != 0:
                            pend_dir = sig; pl_e = pl[i]; ph_e = ph[i]
    D[nd, 0] = day; D[nd, 1] = eq; nd += 1
    return T[:nt], D[:nd]


DEFAULT = dict(fast=8, slow=30, adx_thr=20, dist_thr=0.2, cooldown=1, M=20, btc=False,
               win_s=7, win_e=20, plat_k=1.5, buf_k=0.1, chase_k=1.0, gap_k=0.5, vol_k=0.0,
               stop_k=0.5, smin_k=0.3, smax_k=1.0, trail_k=1.2, trail_mode=0, be_r=1.0, chand_r=2.0,
               chand_k=1.5, exit_mode=0, risk=0.01, lev=2.0, fee=0.0005, slip=0.00015, stop_slip=0.0002,
               max_trades=3, max_stops=2, day_loss=0.02, sideflag=0, n_below=6, t_half=24, t_flat=48,
               t_max=96, fcut=23 * 3600_000 + 1800_000, fund_block=15)


def backtest(D, start="2020-02-01", end="2026-10-01", **kw):
    p = dict(DEFAULT); p.update(kw)
    i0 = int(np.searchsorted(D.t, pd.Timestamp(start).value // 10**6))
    i1 = int(np.searchsorted(D.t, pd.Timestamp(end).value // 10**6))
    dirn = D.direction(p["fast"], p["slow"], p["adx_thr"], p["dist_thr"], p["cooldown"])
    bdir = D.direction(p["fast"], p["slow"], p["adx_thr"], p["dist_thr"], p["cooldown"], btc=True) if p["btc"] else dirn
    ph, pl = D.plat(p["M"])
    T, E = run(D.t, D.o, D.h, D.l, D.c, D.v, D.vma, D.atr5, D.atr1h_5, D.atr15_5, D.fr, dirn, bdir, ph, pl,
               p["win_s"], p["win_e"], p["plat_k"], p["buf_k"], p["chase_k"], p["gap_k"], p["vol_k"],
               p["stop_k"], p["smin_k"], p["smax_k"], p["trail_k"], p["trail_mode"], p["be_r"], p["chand_r"],
               p["chand_k"], p["exit_mode"], p["risk"], p["lev"], p["fee"], p["slip"], p["stop_slip"],
               p["max_trades"], p["max_stops"], p["day_loss"], p["sideflag"], p["n_below"], p["t_half"],
               p["t_flat"], p["t_max"], p["fcut"], p["fund_block"], i0, i1)
    tr = pd.DataFrame(T, columns=["et", "xt", "dir", "pnl", "R", "reason", "fee", "fund", "bars", "ent"])
    eqd = pd.Series(E[:, 1], index=pd.to_datetime(E[:, 0] * DAY, unit="ms"))
    return tr, eqd


def stats(tr, eqd):
    if len(eqd) < 2:
        return {}
    yrs = (eqd.index[-1] - eqd.index[0]).days / 365.25
    cagr = (eqd.iloc[-1] / eqd.iloc[0]) ** (1 / max(yrs, 1e-9)) - 1
    mdd = (1 - eqd / eqd.cummax()).max()
    r = eqd.pct_change().dropna()
    sh = r.mean() / r.std() * np.sqrt(365) if r.std() > 0 else 0
    w = tr[tr.pnl > 0]; lo = tr[tr.pnl <= 0]
    pf = w.pnl.sum() / -lo.pnl.sum() if len(lo) and lo.pnl.sum() < 0 else np.nan
    payoff = (w.pnl.mean() / -lo.pnl.mean()) if len(w) and len(lo) else np.nan
    return dict(n=len(tr), cagr=cagr, mdd=mdd, ratio=cagr / mdd if mdd > 0 else np.nan, sharpe=sh,
                pf=pf, win=len(w) / len(tr) if len(tr) else np.nan, payoff=payoff,
                avgR=tr.R.mean() if len(tr) else np.nan,
                fee_share=tr.fee.sum() / eqd.iloc[0], fund_share=tr.fund.sum() / eqd.iloc[0])
