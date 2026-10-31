"""2026-10-31 宝贝：SNDK单票 v2 发现"ADX逆向仓位"(进场时动能弱→满仓，动能强→减仓，防追高)更好，方案F 会不会也提升？
做法：方案F 每条腿开仓时的仓位系数 alloc(原来 = min(1, 波动目标÷ATR%)) 再乘 ADX 档位倍数，ADX 用该腿自己周期、信号K线(日线腿/美股腿=日线，8h腿=8h)。
其余规则一律不变(减半周期、BTC同向、盘中突破单、行业轮动、组合波动控制、2倍总杠杆)。
分别只改 日线EMA腿 / 8h超级趋势 / 8h布林 / 币圈三腿 / 美股腿 / 全部；档位：
  逆向A 20/30→1/0.75/0.5，逆向B 20/30→1/0.6/0.3，逆向C 25/35→1/0.75/0.5，对照"顺势" 20/30→0.5/0.75/1、固定0.75。
判定同前：全程、前半(2017-09..2021)、后半(2022..)比值都要超过基准才算过关。"""
import sys, os, time, inspect
os.nice(19)
sys.path.insert(0, "/home/stratroster/unicorn_quant"); sys.path.insert(0, "/home/stratroster/eth-webhook-server")
from engine import optimal as O

MODE = {"legs": set(), "f": None}
_adx_cache = {}


def _adx(b, n=14):
    k = (id(b), len(b))
    if k in _adx_cache: return _adx_cache[k]
    tr = [b[0]["h"] - b[0]["l"]]; pdm = [0.0]; mdm = [0.0]
    for i in range(1, len(b)):
        h, l, pc = b[i]["h"], b[i]["l"], b[i - 1]["c"]
        tr.append(max(h - l, abs(h - pc), abs(l - pc))); up, dn = h - b[i - 1]["h"], b[i - 1]["l"] - l
        pdm.append(up if up > dn and up > 0 else 0.0); mdm.append(dn if dn > up and dn > 0 else 0.0)
    A = [tr[0]]; P = [0.0]; M = [0.0]; X = [0.0]
    for i in range(1, len(b)):
        A.append(A[-1] + (tr[i] - A[-1]) / n); P.append(P[-1] + (pdm[i] - P[-1]) / n); M.append(M[-1] + (mdm[i] - M[-1]) / n)
        pdi, mdi = (P[i] / A[i], M[i] / A[i]) if A[i] else (0, 0)
        X.append(X[-1] + ((abs(pdi - mdi) / (pdi + mdi) * 100 if pdi + mdi else 0) - X[-1]) / n)
    _adx_cache[k] = X; return X


def _adxm(leg, b, i):
    if leg not in MODE["legs"] or MODE["f"] is None: return 1.0
    return MODE["f"](_adx(b)[max(i, 0)])


O._adxm = _adxm
HITS = {"d1": 0, "st": 0, "bb": 0, "stk": 0}
def patch(fn, pairs):
    src = inspect.getsource(fn)
    for a, b in pairs:
        n = src.count(a); assert n >= 1, (fn.__name__, a); src = src.replace(a, b)
    exec(compile(src, f"<patched {fn.__name__}>", "exec"), O.__dict__)
patch(O.crypto_leg, [("alloc = min(1.0, VOL_TGT / atrp)", 'alloc = min(1.0, VOL_TGT / atrp) * _adxm("d1", b, i - 1)')])
patch(O.crypto_8h_leg, [("alloc = min(1.0, vt / atrp)", 'alloc = min(1.0, vt / atrp) * _adxm(kind, b, i - 1)')])
patch(O.stock_leg, [('atrp = P["A"][i] / P["b"][i]["c"]; alloc = min(1.0, VOL_TGT / atrp)',
                     'atrp = P["A"][i] / P["b"][i]["c"]; alloc = min(1.0, VOL_TGT / atrp) * _adxm("stk", P["b"], i)')])
H = 18993
def st(cv, a=17410, z=99999):
    ks = [k for k in sorted(cv) if a <= k <= z]; v = [cv[k] for k in ks]; pk = 0; dd = 0
    for x in v: pk = max(pk, x); dd = max(dd, 1 - x / pk)
    yrs = (ks[-1] - ks[0]) / 365.25; cagr = (v[-1] / v[0]) ** (1 / yrs) - 1
    return 100 * cagr, 100 * dd, cagr / dd if dd else 0
def yr(cv):
    by = {}
    for k in sorted(cv):
        if k >= 17410: by.setdefault(time.gmtime(k * 86400).tm_year, []).append(cv[k])
    o = []; pe = None
    for y in sorted(by): xs = by[y]; b0 = pe or xs[0]; o.append(f"{y % 100}:{100 * (xs[-1] / b0 - 1):+.0f}"); pe = xs[-1]
    return " ".join(o)
def build():
    _, _, _, _, CD = O.crypto_leg(); d1 = dict(O.CURVES["日线EMA8/30"])
    O.crypto_8h_leg("st", CD["BTCUSDT"]); stc = dict(O.CURVES["8h超级趋势"]); O.crypto_8h_leg("bb", CD["BTCUSDT"]); bbc = dict(O.CURVES["8h布林突破"])
    sc = O.stock_leg()[0]
    coin = {s: O.combine([d1[s], stc[s], bbc[s]], [1 / 3] * 3) for s in O.M5}
    cB = O.weighted_basket(coin, O.coin_weights(coin, range(2017, 2027)))
    return O.vol_control([cB, sc], [1, 1], 2.0)[0], cB, sc
MODE.update(legs=set(), f=None); BASE, cB0, sc0 = build(); b0 = (st(BASE)[2], st(BASE, 17410, H)[2], st(BASE, H)[2])
def show(lab, F, extra=""):
    a = st(F); s1, s2 = st(F, 17410, H)[2], st(F, H)[2]; ok = a[2] > b0[0] and s1 > b0[1] and s2 > b0[2]
    print(f"{lab:34s} {a[0]:+6.1f}%/{a[1]:4.1f}%/{a[2]:5.2f} 前{s1:.2f}/后{s2:.2f}{' ★过关' if ok else ''} {extra}| {yr(F)}", flush=True)
show("基准 现行方案F", BASE, f"[币圈腿{st(cB0)[2]:.2f} 美股腿{st(sc0)[2]:.2f}]")
T = lambda g1, g2, v: (lambda x: v[0] if x < g1 else (v[1] if x < g2 else v[2]))
FS = {"逆向A 20/30 1/.75/.5": T(20, 30, (1, .75, .5)), "逆向B 20/30 1/.6/.3": T(20, 30, (1, .6, .3)), "逆向C 25/35 1/.75/.5": T(25, 35, (1, .75, .5)),
      "顺势 20/30 .5/.75/1": T(20, 30, (.5, .75, 1)), "固定0.75": lambda x: 0.75}
LEGS = {"日线EMA腿": {"d1"}, "8h超级趋势": {"st"}, "8h布林": {"bb"}, "币圈三腿": {"d1", "st", "bb"}, "美股腿": {"stk"}, "全部": {"d1", "st", "bb", "stk"}}
for ln, legs in LEGS.items():
    print(f"\n-- 只改 {ln} --")
    for fn, f in FS.items():
        MODE.update(legs=legs, f=f); F, cB, sc = build()
        show(f"{ln} {fn}", F, f"[币圈腿{st(cB)[2]:.2f} 美股腿{st(sc)[2]:.2f}]")
MODE.update(legs=set(), f=None)
print("ALL DONE")
