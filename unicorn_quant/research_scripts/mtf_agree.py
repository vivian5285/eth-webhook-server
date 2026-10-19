"""2026-10-19 宝贝：多周期(4h/6h/8h/12h/1d)同向能不能增加收益？基准 = 新的等权方案(5.39)。事先定好：
同向 = 各周期 EMA8/30 方向一致(只用当时已收盘的K线)。
X) 8h两条子腿加"同向过滤"：
   X1 进出都过滤：周期不一致就空仓
   X2 只过滤进场：信号出现后等周期一致才进，持仓中不因不一致离场
   周期组合：{4h} {12h} {4h,12h} {6h,12h} {4h,6h,12h} {4h,12h,1d}
Y) 新增一条"全周期同向"子腿(8h收盘判断，组合一致就持有，不一致空仓；减半周期+BTC同向照旧)，
   Y1 作为第4条腿(四条各1/4)  Y2 替换8h布林(三条各1/3)
   组合：{4h,8h,12h} {4h,8h,12h,1d} {6h,12h,1d} {4h,6h,8h,12h}"""
import sys, os, time, json, urllib.request, bisect
os.nice(19)
sys.path.insert(0, "/home/stratroster/unicorn_quant"); sys.path.insert(0, "/home/stratroster/eth-webhook-server")
from engine import optimal as O
CACHE = "/tmp/mtf_cache"; os.makedirs(CACHE, exist_ok=True)
MS = {"4h": 4, "6h": 6, "8h": 8, "12h": 12, "1d": 24}
def kl(sym, iv):
    p = f"{CACHE}/{sym}_{iv}.json"
    if os.path.exists(p): return json.load(open(p))
    out = []; s = 1500000000000
    while True:
        r = json.load(urllib.request.urlopen(f"https://api.binance.com/api/v3/klines?symbol={sym}&interval={iv}&startTime={s}&limit=1000", timeout=20))
        if not r: break
        out += [(int(x[0]) + MS[iv] * 3600000, float(x[4])) for x in r]
        if len(r) < 1000: break
        s = int(r[-1][0]) + 1; time.sleep(0.3)
    now = int(time.time() * 1000); out = [x for x in out if x[0] <= now]
    json.dump(out, open(p, "w")); return out
DIR = {}
for s in O.M5:
    for iv in MS:
        b = kl(s, iv); c = [x[1] for x in b]; e8, e30 = O.ema(c, 8), O.ema(c, 30)
        DIR[(s, iv)] = ([x[0] for x in b], [0 if i < 31 else (1 if e8[i] > e30[i] else -1) for i in range(len(b))])
print("数据好了", {iv: len(DIR[("BTCUSDT", iv)][0]) for iv in MS}, flush=True)
def dir_at(s, iv, close_ms):
    ts, ds = DIR[(s, iv)]; j = bisect.bisect_right(ts, close_ms) - 1
    return ds[j] if j >= 0 else 0
CUR = {"sym": None}
_orig_spot8 = O.spot_8h
def spot8(sym): CUR["sym"] = sym; return _orig_spot8(sym)
O.spot_8h = spot8
_orig_st, _orig_bb = O._st_dirs, O._bb_states
MODE = {"set": None, "how": None, "leg": None}
def agree(sym, close, tfs):
    ds = {dir_at(sym, iv, close) for iv in tfs}
    return ds.pop() if len(ds) == 1 else 0
def wrap(fn):
    def g(b, *a, **k):
        raw = fn(b, *a, **k); S = MODE["set"]; sym = CUR["sym"]
        if MODE["leg"]:                      # Y：全周期同向本身作为信号
            return [agree(sym, x["t"] + 8 * 3600000, S) for x in b]
        if not S: return raw
        out = [0] * len(raw); prev = 0
        for i, x in enumerate(b):
            r = raw[i]; close = x["t"] + 8 * 3600000
            if r == 0: o = 0
            elif MODE["how"] == "both": o = r if agree(sym, close, S) == r else 0
            else: o = r if (prev == r or agree(sym, close, S) == r) else 0
            out[i] = o; prev = o
        return out
    return g
O._st_dirs, O._bb_states = wrap(_orig_st), wrap(_orig_bb)
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
cd, _, _, _, CD = O.crypto_leg(); D1 = dict(O.CURVES["日线EMA8/30"]); sc = O.stock_leg()[0]
def legs8():
    O.crypto_8h_leg("st", CD["BTCUSDT"]); stc = dict(O.CURVES["8h超级趋势"]); O.crypto_8h_leg("bb", CD["BTCUSDT"]); return stc, dict(O.CURVES["8h布林突破"])
def plan(curve_sets, w):
    coin = {s: O.combine([c[s] for c in curve_sets], w) for s in O.M5}
    cB = O.weighted_basket(coin, O.coin_weights(coin, range(2017, 2027)))
    return O.vol_control([cB, sc], [1, 1], 2.0)[0]
ST0, BB0 = legs8(); BASE = plan([D1, ST0, BB0], [1/3] * 3); b0 = (st(BASE)[2], st(BASE, 17410, H)[2], st(BASE, H)[2])
def show(lab, F):
    a = st(F); s1, s2 = st(F, 17410, H)[2], st(F, H)[2]; tag = "★" if a[2] > b0[0] and s1 > b0[1] and s2 > b0[2] else ""
    print(f"{lab:34s} {a[0]:+6.1f}%/{a[1]:4.1f}%/{a[2]:5.2f} 前{s1:.2f}/后{s2:.2f}{tag} | {yr(F)}", flush=True)
show("基准 等权三腿", BASE)
for how in ("both", "entry"):
    for S in (("4h",), ("12h",), ("4h", "12h"), ("6h", "12h"), ("4h", "6h", "12h"), ("4h", "12h", "1d")):
        MODE.update(set=S, how=how, leg=None); stc, bbc = legs8()
        show(f"X{'1进出' if how=='both' else '2只进场'} 8h腿+{'/'.join(S)}同向", plan([D1, stc, bbc], [1/3] * 3))
for S in (("4h", "8h", "12h"), ("4h", "8h", "12h", "1d"), ("6h", "12h", "1d"), ("4h", "6h", "8h", "12h")):
    MODE.update(set=S, how=None, leg=True); O.crypto_8h_leg("st", CD["BTCUSDT"]); M = dict(O.CURVES["8h超级趋势"])
    ms = st(O.weighted_basket(M, O.coin_weights(M, range(2017, 2027))))
    print(f"   [{'/'.join(S)}全同向腿自己 {ms[0]:+.1f}%/{ms[1]:.1f}%/{ms[2]:.2f}]", flush=True)
    show(f"Y1 第4腿 {'/'.join(S)}", plan([D1, ST0, BB0, M], [.25] * 4))
    show(f"Y2 替布林 {'/'.join(S)}", plan([D1, ST0, M], [1/3] * 3))
MODE.update(set=None, how=None, leg=None)
print("ALL DONE")
