"""2026-10-19 宝贝看 2026-04 BTC：04-08 均线已金叉、空单已平，但多单拖到 04-15 才开(74131，晚了约4%)。
原因：熊市段开多要过"底部反转例外"，其他条件 04-08 前都满足，只差放量(近3天量>20日均量×1.5)，04-14 才到 1.5 倍。
对照(事先定好)：放量门槛 1.5(现行)/1.3/1.2/1.0/不要求；放量看近 3(现行)/7/10 天；超卖 3 项里 2 项(现行)/1 项。只改这一个例外，其余不动。"""
import sys, os, time, inspect
os.nice(19)
sys.path.insert(0, "/home/stratroster/unicorn_quant"); sys.path.insert(0, "/home/stratroster/eth-webhook-server")
from engine import optimal as O, bottom
SRC = inspect.getsource(bottom.check)
def make(vt=1.5, vw=3, osc_n=2, novol=False):
    s = SRC.replace("for k in range(3):", f"for k in range({vw}):").replace("ok = osc >= 2 and macd_up and vr > 1.5 and off >= 0.35",
        f"ok = osc >= {osc_n} and macd_up and ({'True' if novol else f'vr > {vt}'}) and off >= 0.35")
    assert s != SRC or (vt == 1.5 and vw == 3 and osc_n == 2 and not novol)
    ns = dict(bottom.__dict__); exec(s, ns); return ns["check"]
ORIG = bottom.check
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
sc = O.stock_leg()[0]
_, _, _, _, CD = O.crypto_leg(); O.crypto_8h_leg("st", CD["BTCUSDT"]); S8 = dict(O.CURVES["8h超级趋势"]); O.crypto_8h_leg("bb", CD["BTCUSDT"]); B8 = dict(O.CURVES["8h布林突破"])
def run(chk):
    bottom.check = chk
    _, tr, _, _, _ = O.crypto_leg(); D1 = dict(O.CURVES["日线EMA8/30"])
    coin = {s: O.combine([D1[s], S8[s], B8[s]], [1/3] * 3) for s in O.M5}
    cB = O.weighted_basket(coin, O.coin_weights(coin, range(2017, 2027)))
    n = sum(1 for t in tr if t.get("counter"))
    bt = [t for t in tr if t["symbol"] == "BTCUSDT" and t["side"] == "LONG" and 1775000000000 < t["entry_t"] < 1777000000000]
    return O.vol_control([cB, sc], [1, 1], 2.0)[0], n, bt
F0, n0, bt0 = run(ORIG); b0 = (st(F0)[2], st(F0, 17410, H)[2], st(F0, H)[2])
D = lambda t: time.strftime("%m-%d", time.gmtime(t / 1000))
def show(lab, r):
    F, n, bt = r; a = st(F); s1, s2 = st(F, 17410, H)[2], st(F, H)[2]; tag = "★" if a[2] > b0[0] and s1 > b0[1] and s2 > b0[2] else ""
    btx = " ".join(f"{D(t['entry_t'])}@{t['entry']:.0f}" for t in bt)
    print(f"{lab:26s} {a[0]:+6.1f}%/{a[1]:4.1f}%/{a[2]:5.2f} 前{s1:.2f}/后{s2:.2f}{tag} | 底部例外多单{n}笔 | 26年4月BTC多 {btx} | {yr(F)}", flush=True)
show("现行(量>1.5倍 近3天 超卖2项)", (F0, n0, bt0))
for vt in (1.3, 1.2, 1.0): show(f"放量门槛 {vt}倍", run(make(vt=vt)))
show("不要求放量", run(make(novol=True)))
for vw in (7, 10): show(f"放量看近{vw}天", run(make(vw=vw)))
show("超卖只要1项", run(make(osc_n=1)))
bottom.check = ORIG
print("ALL DONE")
