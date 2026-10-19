"""2026-10-19 美股腿剔除表现差的股票：事后版(全部历史挑，作弊上限) vs 滚动版(每年1月1日只用过去2年的单股表现)。
只在 optimal.stock_leg 的"当月可做的股票"里加一条排除名单，其余规则一字不改。"""
import sys, os, time, inspect
os.nice(19)
sys.path.insert(0, "/home/stratroster/unicorn_quant"); sys.path.insert(0, "/home/stratroster/eth-webhook-server")
from engine import optimal as O
from engine.universe import solo_stock
DAY = 86400000
src = inspect.getsource(O.stock_leg).replace("def stock_leg():", "def stock_leg_x():")
old = """        return {s for s in PRE if SEC_OF[s] in secs and PRE[s]["first"] <= d and s in TR}"""
assert src.count(old) == 1
src = src.replace(old, """        return {s for s in PRE if SEC_OF[s] in secs and PRE[s]["first"] <= d and s in TR and s not in EXCL.get(time.gmtime(d * 86400).tm_year, ())}""")
O.__dict__["EXCL"] = {}
exec(src, O.__dict__)
cd, _, _, _, CD = O.crypto_leg(); s8, _, _ = O.crypto_8h_leg("st", CD["BTCUSDT"]); b8, _, _ = O.crypto_8h_leg("bb", CD["BTCUSDT"])
cB = O.combine([cd, s8, b8], [.5, .25, .25])
def st(cv, a=17410, z=99999):
    ks = [k for k in sorted(cv) if a <= k <= z]; v = [cv[k] for k in ks]; pk = 0; dd = 0
    for x in v: pk = max(pk, x); dd = max(dd, 1 - x / pk)
    yrs = (ks[-1] - ks[0]) / 365.25; cagr = (v[-1] / v[0]) ** (1 / yrs) - 1
    return 100 * cagr, 100 * dd, cagr / dd if dd else 0
f = lambda a: f"{a[0]:+6.1f}%/{a[1]:4.1f}%/{a[2]:5.2f}"
H = 18993
# 单股曲线(同一套规则，不看行业)
SOLO = {}
for tk in O.SEC_OF:
    try:
        b = O.stock_daily(tk)
        if len(b) < 300: continue
        c = [x["c"] for x in b]; e8, e30 = O.ema(c, 8), O.ema(c, 30)
        rows = [[x["t"], x["o"], x["h"], x["l"], x["c"], e8[i], e30[i]] for i, x in enumerate(b)]
        cv, _ = solo_stock(rows); SOLO[tk] = dict((d, v) for d, v in cv)
    except Exception as e:
        print("skip", tk, e)
def solo_stat(tk, a, z):
    cv = SOLO.get(tk); ks = [k for k in sorted(cv or {}) if a <= k <= z]
    if len(ks) < 250: return None
    return st(cv, a, z)
def run(excl):
    O.EXCL = excl; sc, tr, _, _ = O.stock_leg_x(); F = O.vol_control([cB, sc], [1, 1], 2.0)[0]
    return sc, tr, F
def line(nm, excl):
    sc, tr, F = run(excl)
    print(f"{nm:40s} 美股腿 {f(st(sc, 17167))} 前{st(sc,17167,H)[2]:.2f}/后{st(sc,H)[2]:.2f} {len(tr)}笔 || 主方案 {f(st(F))} 前{st(F,17410,H)[2]:.2f}/后{st(F,H)[2]:.2f}", flush=True)
YRS = range(2017, 2027)
line("现行(不剔除)", {})
full = {tk: st(cv, 17167) for tk, cv in SOLO.items()}
bad_full = {tk for tk, a in full.items() if a[2] < 0.3}
line(f"事后版：剔除全程单股比<0.3的 {len(bad_full)} 只", {y: bad_full for y in YRS})
bad_full2 = {tk for tk, a in full.items() if a[0] < 10}
line(f"事后版：剔除全程单股年化<10%的 {len(bad_full2)} 只", {y: bad_full2 for y in YRS})
def yr0(y): return int(time.mktime((y, 1, 1, 0, 0, 0, 0, 0, 0)) // 86400)
ex_neg, ex_q = {}, {}
for y in YRS:
    a, z = yr0(y) - 730, yr0(y) - 1
    sc_ = {tk: solo_stat(tk, a, z) for tk in SOLO}; sc_ = {k: v for k, v in sc_.items() if v}
    ex_neg[y] = {tk for tk, v in sc_.items() if v[0] < 0}
    rk = sorted(sc_, key=lambda k: sc_[k][2]); ex_q[y] = set(rk[:len(rk) // 4])
line("滚动版：剔除过去2年单股收益为负的", ex_neg)
print("      每年剔除数：" + " ".join(f"{y}:{len(ex_neg[y])}" for y in YRS), flush=True)
line("滚动版：剔除过去2年单股比值最差的1/4", ex_q)
print("ALL DONE", flush=True)
