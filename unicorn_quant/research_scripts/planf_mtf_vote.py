"""2026-10-31 宝贝：方案F 能不能用 MTF "3选2"(像 TV 挂一个图表周期、内部 3 个周期任意 2 个同向就进场)变得更好？
(10-19 mtf_agree.py 测的是"全部同向"，否决；这次是"3选2"投票，宽松得多。)
V = 同一个指标在 3 个周期上投票：
   日线EMA8/30腿：EMA8/30 方向在 {1d, A, B} 上 ≥2 个看多=多头(否则空头)，其余规则(减半周期、BTC同向、盘中突破单、波动调仓)不变。
   8h超级趋势腿：超级趋势(10,3) 方向在 {8h, A, B} 上 ≥2 个同向。
   8h布林突破腿：布林(25,1.25) 状态在 {8h, A, B} 上 ≥2 个同为多/空，否则空仓。
F = 8h 两条腿自己的信号 + 另外两个周期的 EMA8/30 方向，3 个里 ≥2 个同向才持有(进出都看)。
每次只改一条腿，其它腿和美股腿照旧；判定同前：全程、前半(2017-09..2021)、后半(2022..)比值都要超过基准才算过关。
只用每个周期当时已收盘的 K 线。"""
import sys, os, time, json, urllib.request, bisect
os.nice(19)
sys.path.insert(0, "/home/stratroster/unicorn_quant"); sys.path.insert(0, "/home/stratroster/eth-webhook-server")
from engine import optimal as O
CACHE = "/tmp/mtf_ohlc_cache"; os.makedirs(CACHE, exist_ok=True)
HRS = {"2h": 2, "4h": 4, "6h": 6, "8h": 8, "12h": 12, "1d": 24, "3d": 72, "1w": 168}
def kl(sym, iv):
    p = f"{CACHE}/{sym}_{iv}.json"
    if os.path.exists(p) and time.time() - os.path.getmtime(p) < 12 * 3600: return json.load(open(p))
    out = []; s = 1500000000000
    while True:
        r = json.load(urllib.request.urlopen(f"https://api.binance.com/api/v3/klines?symbol={sym}&interval={iv}&startTime={s}&limit=1000", timeout=20))
        if not r: break
        out += [{"t": int(x[0]), "ct": int(x[6]) + 1, "o": float(x[1]), "h": float(x[2]), "l": float(x[3]), "c": float(x[4])} for x in r]
        if len(r) < 1000: break
        s = int(r[-1][0]) + 1; time.sleep(0.25)
    now = int(time.time() * 1000); out = [x for x in out if x["ct"] <= now]
    json.dump(out, open(p, "w")); return out
K = {(s, iv): kl(s, iv) for s in O.M5 for iv in HRS}
print("数据好了", {iv: len(K[("BTCUSDT", iv)]) for iv in HRS}, flush=True)
_orig_ema, _orig_st, _orig_bb = O.ema, O._st_dirs, O._bb_states
def ema_dir(bars):
    c = [x["c"] for x in bars]; a, b = _orig_ema(c, 8), _orig_ema(c, 30)
    return [0 if i < 31 else (1 if a[i] > b[i] else -1) for i in range(len(bars))]
IND = {"ema": ema_dir, "st": lambda b: _orig_st(b), "bb": lambda b: _orig_bb(b)}
_memo = {}
def tf_series(sym, iv, ind):
    k = (sym, iv, ind)
    if k not in _memo:
        bb = K[(sym, iv)]; _memo[k] = ([x["ct"] for x in bb], IND[ind](bb))
    return _memo[k]
def at(sym, iv, ind, T):
    ts, ds = tf_series(sym, iv, ind); j = bisect.bisect_right(ts, T) - 1
    return ds[j] if j >= 0 else 0
def vote(vals):
    up = sum(1 for v in vals if v > 0); dn = sum(1 for v in vals if v < 0)
    return 1 if up >= 2 else (-1 if dn >= 2 else 0)

# ---- 日线腿：拦截 crypto_leg 里的 ema(c,8)/ema(c,30)，把"EMA8>EMA30"换成 3选2 投票结果 ----
DAILY = {"set": None}; DKEY = {}; HITS = [0]
for s in O.M5:
    b = O.spot_daily(s); c = [x["c"] for x in b]; DKEY[(len(c), c[0], c[-1])] = (s, b)
def ema_hook(xs, n):
    S = DAILY["set"]; key = (len(xs), xs[0], xs[-1]) if xs else None
    if not S or n not in (8, 30) or key not in DKEY: return _orig_ema(xs, n)
    e30 = _orig_ema(xs, 30)
    if n == 30: return e30
    sym, b = DKEY[key]; HITS[0] += 1; own = ema_dir(b); out = []
    for i, x in enumerate(b):
        T = x["t"] + O.DAY
        v = vote([own[i]] + [at(sym, iv, "ema", T) for iv in S]) if i >= 31 else own[i]
        out.append(e30[i] * (1 + (1e-9 if v > 0 else -1e-9)))      # 只决定 e8>e30 的方向
    return out
O.ema = ema_hook
# ---- 8h 腿 ----
CUR = {"sym": None}; _orig_spot8 = O.spot_8h
def spot8(sym): CUR["sym"] = sym; return _orig_spot8(sym)
O.spot_8h = spot8
MODE = {"kind": None, "set": None}
def wrap(fn, ind):
    def g(b, *a, **k):
        raw = fn(b, *a, **k); m = MODE["kind"]; S = MODE["set"]; sym = CUR["sym"]
        if not m: return raw
        out = []
        for i, x in enumerate(b):
            T = x["t"] + 8 * 3600000
            if i < 60: out.append(raw[i]); continue
            if m == "V": out.append(vote([raw[i]] + [at(sym, iv, ind, T) for iv in S]))
            else:   # F：自己信号 + 另外两个周期 EMA 方向，≥2 同向
                r = raw[i]
                out.append(r if r and sum(1 for iv in S if at(sym, iv, "ema", T) == r) >= 1 else 0)
        return out
    return g
O._st_dirs, O._bb_states = wrap(_orig_st, "st"), wrap(_orig_bb, "bb")
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
def daily_leg():
    _, _, _, _, CD = O.crypto_leg(); return dict(O.CURVES["日线EMA8/30"]), CD
def leg8(kind, CD):
    O.crypto_8h_leg(kind, CD["BTCUSDT"]); return dict(O.CURVES["8h超级趋势" if kind == "st" else "8h布林突破"])
sc = O.stock_leg()[0]
D1, CD = daily_leg(); ST0, BB0 = leg8("st", CD), leg8("bb", CD)
def plan(d1, stc, bbc):
    coin = {s: O.combine([d1[s], stc[s], bbc[s]], [1 / 3] * 3) for s in O.M5}
    cB = O.weighted_basket(coin, O.coin_weights(coin, range(2017, 2027)))
    return O.vol_control([cB, sc], [1, 1], 2.0)[0]
BASE = plan(D1, ST0, BB0); b0 = (st(BASE)[2], st(BASE, 17410, H)[2], st(BASE, H)[2])
RES = []
def show(lab, F):
    a = st(F); s1, s2 = st(F, 17410, H)[2], st(F, H)[2]; ok = a[2] > b0[0] and s1 > b0[1] and s2 > b0[2]
    RES.append((lab, round(a[0], 1), round(a[1], 1), round(a[2], 2), round(s1, 2), round(s2, 2), ok))
    print(f"{lab:40s} {a[0]:+6.1f}%/{a[1]:4.1f}%/{a[2]:5.2f} 前{s1:.2f}/后{s2:.2f}{' ★过关' if ok else ''} | {yr(F)}", flush=True)
show("基准 现行方案F", BASE)
print("\n-- 日线EMA8/30腿 3选2 投票 {1d + A + B} --")
for S in (("12h", "3d"), ("8h", "3d"), ("12h", "1w"), ("3d", "1w"), ("8h", "12h"), ("4h", "12h"), ("6h", "3d")):
    HITS[0] = 0; DAILY["set"] = S; d1, _ = daily_leg(); DAILY["set"] = None; assert HITS[0] >= 5, "日线拦截没生效"
    show(f"V 日线腿 1d+{'+'.join(S)}", plan(d1, ST0, BB0))
for kind in ("st", "bb"):
    nm = "8h超级趋势" if kind == "st" else "8h布林"
    print(f"\n-- {nm}腿 --")
    for S in (("4h", "12h"), ("4h", "1d"), ("12h", "1d"), ("2h", "4h"), ("6h", "12h")):
        for m in ("V", "F"):
            MODE.update(kind=m, set=S); x = leg8(kind, CD); MODE.update(kind=None, set=None)
            lab = f"{m} {nm} 8h+{'+'.join(S)}" + (" 同指标投票" if m == "V" else " +EMA确认")
            show(lab, plan(D1, x, BB0) if kind == "st" else plan(D1, ST0, x))
print("\n-- 三条腿一起换成各自最好的 V 版本(事后挑，仅参考上限) --")
best = {}
for lab, *v in RES[1:]:
    leg = "日线" if "日线腿" in lab else ("st" if "超级趋势" in lab else "bb")
    if lab.startswith("V") and (leg not in best or v[2] > best[leg][1][2]): best[leg] = (lab, v)
print("  ", {k: v[0] for k, v in best.items()})
def parse(lab): return tuple(lab.split("+")[1:]) if "日线" in lab else tuple(lab.split(" ")[2].split("+")[1:])
DAILY["set"] = parse(best["日线"][0]); d1, _ = daily_leg(); DAILY["set"] = None
MODE.update(kind="V", set=tuple(best["st"][0].split(" ")[2].split("+")[1:])); stx = leg8("st", CD)
MODE.update(kind="V", set=tuple(best["bb"][0].split(" ")[2].split("+")[1:])); bbx = leg8("bb", CD); MODE.update(kind=None, set=None)
show("V 三腿各自最好(上限)", plan(d1, stx, bbx))
json.dump(RES, open("/tmp/planf_mtf_vote_result.json", "w"), ensure_ascii=False)
print("ALL DONE")
