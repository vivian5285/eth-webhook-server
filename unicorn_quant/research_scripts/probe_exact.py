"""2026-10-19 精确版：直接在正式引擎 crypto_8h_leg 里做"先小仓"：
8h 子腿自己的信号是空仓(布林回到中轨后、或超趋势被减半阶段挡住)时，若小周期 EMA8/30 方向 L 已出来，先开 p×标准仓位；
等子腿自己信号出来 → 换成标准仓位；L 反过来 → 小仓平掉。减半阶段/山寨跟BTC同向照旧(引擎自己判断)。
L = 4h / 8h；p = 25/50/75%；只布林腿 / 只超趋势腿 / 两条都加。基准 = 正式等权方案 5.39。"""
import sys, os, time, json, bisect
os.nice(19)
sys.path.insert(0, "/home/stratroster/unicorn_quant"); sys.path.insert(0, "/home/stratroster/eth-webhook-server")
from engine import optimal as O
CACHE = "/tmp/mtf_cache"
class P(float):
    """小仓位方向：数值是 ±p；跟 BTC 方向比较(!=)时只看正负号"""
    def __ne__(self, o): return (self > 0) != (o > 0) if o else True
    def __eq__(self, o): return float(self) == float(o)
    __hash__ = float.__hash__
TFD = {}
for s in O.M5:
    for iv in ("4h", "8h"):
        b = json.load(open(f"{CACHE}/{s}_{iv}.json")); c = [x[1] for x in b]; e8, e30 = O.ema(c, 8), O.ema(c, 30)
        TFD[(s, iv)] = ([x[0] for x in b], [0 if i < 31 else (1 if e8[i] > e30[i] else -1) for i in range(len(b))])
def dir_at(s, iv, t):
    ts, ds = TFD[(s, iv)]; j = bisect.bisect_right(ts, t) - 1; return ds[j] if j >= 0 else 0
CUR = {"sym": None}; _sp = O.spot_8h
def spot8(sym): CUR["sym"] = sym; return _sp(sym)
O.spot_8h = spot8
CFG = {"st": None, "bb": None}; KIND = {"k": None}
_st, _bb = O._st_dirs, O._bb_states
def wrap(fn, kind):
    def g(b, *a, **k):
        raw = fn(b, *a, **k); c = CFG[kind]
        if not c: return raw
        tf, p = c; s = CUR["sym"]; out = []
        for i, x in enumerate(b):
            if raw[i]: out.append(raw[i]); continue
            L = dir_at(s, tf, x["t"] + 8 * 3600000)
            out.append(P(p * L) if L else 0)
        return out
    return g
O._st_dirs, O._bb_states = wrap(_st, "st"), wrap(_bb, "bb")
import inspect
_src = inspect.getsource(O.crypto_8h_leg).replace('"回到布林中轨"}[D[i]]', '"回到布林中轨"}[(1 if D[i] > 0 else -1 if D[i] < 0 else 0)]')
assert "if D[i] > 0 else -1" in _src
exec(_src, O.__dict__)
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
def run():
    _, tst, _ = O.crypto_8h_leg("st", CD["BTCUSDT"]); S = dict(O.CURVES["8h超级趋势"])
    _, tbb, _ = O.crypto_8h_leg("bb", CD["BTCUSDT"]); B = dict(O.CURVES["8h布林突破"])
    coin = {s: O.combine([D1[s], S[s], B[s]], [1/3] * 3) for s in O.M5}
    cB = O.weighted_basket(coin, O.coin_weights(coin, range(2017, 2027)))
    nprobe = sum(1 for t in tst + tbb if abs(t.get("alloc", 1)) and isinstance(t.get("_p"), float))
    return O.vol_control([cB, sc], [1, 1], 2.0)[0], len(tst), len(tbb)
F0, n1, n2 = run(); b0 = (st(F0)[2], st(F0, 17410, H)[2], st(F0, H)[2])
def show(lab, r):
    F, a1, a2 = r; a = st(F); s1, s2 = st(F, 17410, H)[2], st(F, H)[2]; tag = "★" if a[2] > b0[0] and s1 > b0[1] and s2 > b0[2] else ""
    print(f"{lab:28s} {a[0]:+6.1f}%/{a[1]:4.1f}%/{a[2]:5.2f} 前{s1:.2f}/后{s2:.2f}{tag} | 笔数 超趋{a1} 布林{a2} | {yr(F)}", flush=True)
show("基准(正式等权)", (F0, n1, n2))
for tf in ("4h", "8h"):
    for p in (0.25, 0.5, 0.75):
        for which in ("bb", "st", "both"):
            CFG.update(st=(tf, p) if which in ("st", "both") else None, bb=(tf, p) if which in ("bb", "both") else None)
            show(f"{tf} 先进{int(p*100)}% {'布林腿' if which=='bb' else '超趋腿' if which=='st' else '两条腿'}", run())
CFG.update(st=None, bb=None)
print("ALL DONE")
