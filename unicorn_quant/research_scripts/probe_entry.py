"""2026-10-19 宝贝：小周期先确认趋势就小仓位先进，等现行方案的信号出来再补到标准仓位，摊低均价。事先定好：
每条子腿、每个币，在 4h 收盘上算仓位：
  主信号有仓(现行回测里这条腿在持仓) → 标准仓位(和现行一样)
  主信号没仓，但小周期 EMA8/30 方向 L 已经出来(且符合减半阶段、山寨跟BTC日线同向) → 先进 p × 标准仓位
  V2 另加：主信号持仓方向和 L 相反时，也提前缩成 p × L(提前反手的小仓)
小周期 L = 4h / 8h / 12h；p = 25% / 50%；只给日线腿 / 三条腿都加。
主信号持仓直接用现行回测的真实成交(进出时间、仓位比例)，所以 p=0 就是现行方案(先核对复刻误差)。
手续费 |Δ仓位|×0.08%；4h 收盘出信号、按收盘价近似成交。"""
import sys, os, time, json, bisect, math
os.nice(19)
sys.path.insert(0, "/home/stratroster/unicorn_quant"); sys.path.insert(0, "/home/stratroster/eth-webhook-server")
from engine import optimal as O
CACHE = "/tmp/mtf_cache"; H4 = 4 * 3600000; DAY = 86400000; FEE = 0.0008
cdaily, ctr, _, _, CD = O.crypto_leg()
_, tst, _ = O.crypto_8h_leg("st", CD["BTCUSDT"]); _, tbb, _ = O.crypto_8h_leg("bb", CD["BTCUSDT"])
LEGS = {"日线EMA8/30": ctr, "8h超级趋势": tst, "8h布林突破": tbb}
sc = O.stock_leg()[0]
def load(s, iv): return json.load(open(f"{CACHE}/{s}_{iv}.json"))   # [(close_ms, close)]
TFD = {}
for s in O.M5:
    for iv in ("4h", "8h", "12h", "1d"):
        b = load(s, iv); c = [x[1] for x in b]; e8, e30 = O.ema(c, 8), O.ema(c, 30)
        TFD[(s, iv)] = ([x[0] for x in b], [0 if i < 31 else (1 if e8[i] > e30[i] else -1) for i in range(len(b))])
def dir_at(s, iv, t):
    ts, ds = TFD[(s, iv)]; j = bisect.bisect_right(ts, t) - 1; return ds[j] if j >= 0 else 0
# 日ATR%(用于试探仓的波动调仓，跟日线腿同口径 min(1, 3%/ATR%))
ATRP = {}
for s in O.M5:
    b = CD[s]; A = O.atr(b); ATRP[s] = ([x["t"] + DAY for x in b], [A[i] / b[i]["c"] for i in range(len(b))])
def atrp(s, t):
    ts, v = ATRP[s]; j = bisect.bisect_right(ts, t) - 1; return v[j] if j >= 0 else 0.05
def intervals(trades, s):
    out = []
    for t in trades:
        if t["symbol"] != s: continue
        out.append((t["entry_t"], t["exit_t"] or 9e15, 1 if t["side"] == "LONG" else -1, t.get("alloc", 1.0)))
    out.sort(); return out
def main_pos(iv, t):
    j = bisect.bisect_right([x[0] for x in iv], t) - 1   # 慢，但够用
    if j >= 0 and iv[j][0] <= t < iv[j][1]: return iv[j][2], iv[j][3]
    return 0, 0.0
class MP:
    def __init__(self, iv): self.iv = iv; self.st = [x[0] for x in iv]
    def at(self, t):
        j = bisect.bisect_right(self.st, t) - 1
        if j >= 0 and self.iv[j][0] <= t < self.iv[j][1]: return self.iv[j][2], self.iv[j][3]
        return 0, 0.0
bars4 = {s: load(s, "4h") for s in O.M5}
def coin_curve(s, leg, tf, p, v2):
    mp = MP(intervals(LEGS[leg], s)); b = bars4[s]; eq = 1.0; e_prev = 0.0; out = {}
    for i in range(1, len(b)):
        t, c = b[i]; pc = b[i - 1][1]
        eq *= 1 + e_prev * (c / pc - 1)
        # 在这根4h收盘(t)决定下一段仓位
        side, al = mp.at(t + 1)
        e = side * al
        if tf and p > 0:
            L = dir_at(s, tf, t)
            ph, _ = O.phase_of(t)
            ok = L != 0 and not ((ph == "牛" and L < 0) or (ph == "熊" and L > 0)) and (s == "BTCUSDT" or dir_at("BTCUSDT", "1d", t) == L)
            if ok and (side == 0 or (v2 and side == -L)):
                e = p * L * min(1.0, O.VOL_TGT / max(atrp(s, t), 1e-4))
        eq -= abs(e - e_prev) * FEE; e_prev = e; eq = max(eq, 0.0)
        out[t // DAY] = eq
    return out
def st(cv, a=17410, z=99999):
    ks = [k for k in sorted(cv) if a <= k <= z]; v = [cv[k] for k in ks]; pk = 0; dd = 0
    for x in v: pk = max(pk, x); dd = max(dd, 1 - x / pk)
    yrs = (ks[-1] - ks[0]) / 365.25; cagr = (v[-1] / v[0]) ** (1 / yrs) - 1
    return 100 * cagr, 100 * dd, cagr / dd if dd else 0
H = 18993
def yr(cv):
    by = {}
    for k in sorted(cv):
        if k >= 17410: by.setdefault(time.gmtime(k * 86400).tm_year, []).append(cv[k])
    o = []; pe = None
    for y in sorted(by): xs = by[y]; b0 = pe or xs[0]; o.append(f"{y%100}:{100*(xs[-1]/b0-1):+.0f}"); pe = xs[-1]
    return " ".join(o)
def plan(cfg):
    coin = {}
    for s in O.M5:
        cs = [coin_curve(s, leg, *cfg.get(leg, (None, 0, False))) for leg in LEGS]
        coin[s] = O.combine(cs, [1/3] * 3)
    cB = O.weighted_basket(coin, O.coin_weights(coin, range(2017, 2027)))
    return O.vol_control([cB, sc], [1, 1], 2.0)[0], cB
REF = O.vol_control([O.weighted_basket(*(lambda c: (c, O.coin_weights(c, range(2017, 2027))))({s: O.combine([O.CURVES["日线EMA8/30"][s], O.CURVES["8h超级趋势"][s], O.CURVES["8h布林突破"][s]], [1/3]*3) for s in O.M5})), sc], [1, 1], 2.0)[0]
BASE, cb0 = plan({})
b0 = (st(BASE)[2], st(BASE, 17410, H)[2], st(BASE, H)[2])
def show(lab, F):
    a = st(F); s1, s2 = st(F, 17410, H)[2], st(F, H)[2]; tag = "★" if a[2] > b0[0] and s1 > b0[1] and s2 > b0[2] else ""
    print(f"{lab:34s} {a[0]:+6.1f}%/{a[1]:4.1f}%/{a[2]:5.2f} 前{s1:.2f}/后{s2:.2f}{tag} | {yr(F)}", flush=True)
show("正式回测(参照)", REF)
show("复刻 p=0(4h近似，比较基准)", BASE)
for v2 in (False, True):
    for tf in ("4h", "8h", "12h"):
        for p in (0.25, 0.5):
            lab = f"{'V2提前反手' if v2 else 'V1空仓时先进'} {tf} p={int(p*100)}%"
            show(lab + " 只日线腿", plan({"日线EMA8/30": (tf, p, v2)})[0])
            show(lab + " 三条腿", plan({k: (tf, p, v2) for k in LEGS})[0])
print("ALL DONE")
