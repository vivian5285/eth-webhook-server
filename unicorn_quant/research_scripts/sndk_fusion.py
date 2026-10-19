"""2026-10-19 宝贝：SNDK 单独做 1 倍 + 现行方案F 融合；美股轮动腿里剔除 SNDK(它的份额自动分给其余美股)。
SNDK 腿规则 = 现行美股腿同一套：日线 EMA8>30 做多、<30 平，3×ATR 止损(止损后等重新金叉)，下一根开盘成交，手续费 0.08%/边。
仓位两种：a) 同美股腿 min(1, 3%÷ATR%)  b) 满 1 倍名义。
组合：波动率控制 40%(同主方案)。SNDK 上市前 SNDK 腿空仓；另用 MU 代替 SNDK 做 2017 起的压力测试(换一只'看好的强势股'会怎样)。"""
import sys, os, time
os.nice(19)
sys.path.insert(0, "/home/stratroster/unicorn_quant"); sys.path.insert(0, "/home/stratroster/eth-webhook-server")
from engine import optimal as O
DAY = 86400000; FEE = 0.0008
def sim(b, full=False):
    c = [x["c"] for x in b]; e8, e30 = O.ema(c, 8), O.ema(c, 30); A = O.atr(b)
    eq = 1.0; q = 0.0; pend = None; stop = None; lock = 0; out = {}; n = 0
    for i in range(1, len(b)):
        op, hi, lo, cl, pc = b[i]["o"], b[i]["h"], b[i]["l"], b[i]["c"], b[i - 1]["c"]
        if pend is not None:
            tgt = pend; pend = None
            if q and tgt == 0:
                eq += q * (op - pc) - abs(q) * op * FEE; q = 0.0; pc = op
            if tgt and not q:
                al = 1.0 if full else min(1.0, 0.03 / (A[i - 1] / b[i - 1]["c"])); q = eq * al / op; eq -= abs(q) * op * FEE; pc = op; n += 1
                stop = op - 3 * A[i - 1]
        if q and lo <= stop:
            px = min(op, stop); eq += q * (px - pc) - abs(q) * px * FEE; lock = 1; q = 0.0
        elif q:
            eq += q * (cl - pc)
        out[b[i]["t"] // DAY] = max(eq, 0)
        if i < 31: continue
        want = 1 if e8[i] > e30[i] else 0
        if lock and want != lock: lock = 0
        if lock: want = 0
        cur = 1 if q else 0
        if want != cur: pend = want
    return out, n
def st(cv, a, z=99999):
    ks = [k for k in sorted(cv) if a <= k <= z]; v = [cv[k] for k in ks]; pk = 0; dd = 0
    for x in v: pk = max(pk, x); dd = max(dd, 1 - x / pk)
    yrs = (ks[-1] - ks[0]) / 365.25; cagr = (v[-1] / v[0]) ** (1 / yrs) - 1
    return 100 * cagr, 100 * dd, cagr / dd if dd else 0
def yr(cv, a):
    by = {}
    for k in sorted(cv):
        if k >= a: by.setdefault(time.gmtime(k * 86400).tm_year, []).append(cv[k])
    o = []; pe = None
    for y in sorted(by): xs = by[y]; b0 = pe or xs[0]; o.append(f"{y%100}:{100*(xs[-1]/b0-1):+.0f}"); pe = xs[-1]
    return " ".join(o)
cd, _, _, _, CD = O.crypto_leg(); O.crypto_8h_leg("st", CD["BTCUSDT"]); O.crypto_8h_leg("bb", CD["BTCUSDT"])
coin = {s: O.combine([O.CURVES["日线EMA8/30"][s], O.CURVES["8h超级趋势"][s], O.CURVES["8h布林突破"][s]], [1/3] * 3) for s in O.M5}
cB = O.weighted_basket(coin, O.coin_weights(coin, range(2017, 2027)))
sc = O.stock_leg()[0]
_tr = O.tradable_set
def ex(*names):
    O.tradable_set = lambda: {s for s in _tr() if s not in names}
    r = O.stock_leg()[0]; O.tradable_set = _tr; return r
sc_x = ex("SNDK"); sc_xm = ex("MU")
SN, MU = O.stock_daily("SNDK"), O.stock_daily("MU")
sn_a, n1 = sim(SN); sn_f, _ = sim(SN, True); mu_a, n2 = sim(MU); mu_f, _ = sim(MU, True)
def pad(cv, ref):   # 上市前空仓 = 净值1
    f = min(cv); return {**{d: 1.0 for d in ref if d < f}, **cv}
sn_a, sn_f = pad(sn_a, cB), pad(sn_f, cB)
sn0 = SN[0]["t"] // DAY + 40; A0 = 17410; H = 18993
print(f"SNDK 交易 {n1} 笔, MU {n2} 笔；SNDK 起点 {time.strftime('%Y-%m-%d', time.gmtime(sn0*86400))}")
for nm, cv, a in (("美股腿现行", sc, sn0), ("美股腿剔SNDK", sc_x, sn0), ("SNDK腿 波动仓位", sn_a, sn0), ("SNDK腿 满1倍", sn_f, sn0)):
    x = st(cv, a); print(f"  腿 {nm:16s} {x[0]:+7.1f}%/{x[1]:4.1f}%/{x[2]:5.2f} | {yr(cv, a)}")
def rows(title, tri, a, halves=False):
    print(f"\n=== {title} ===")
    for nm, curves, ws, g in tri:
        F = O.vol_control(curves, ws, g)[0]; x = st(F, a)
        extra = f" | 前半{st(F, a, H)[2]:.2f} 后半{st(F, H)[2]:.2f}" if halves else ""
        print(f"  {nm:34s} {x[0]:+7.1f}%/{x[1]:4.1f}%/{x[2]:5.2f}{extra} | {yr(F, a)}", flush=True)
def plans(stk_x, s_a, s_f):
    return [("现行 币1+美股1", [cB, sc], [1, 1], 2.0),
            ("币1+美股(剔)1", [cB, stk_x], [1, 1], 2.0),
            ("融合 币1+美股(剔)1+单股1(波动仓位)", [cB, stk_x, s_a], [1, 1, 1], 3.0),
            ("融合 币1+美股(剔)1+单股1(满仓)", [cB, stk_x, s_f], [1, 1, 1], 3.0),
            ("守2倍 三份各2/3(满仓)", [cB, stk_x, s_f], [2/3] * 3, 2.0),
            ("守2倍 币1+美股(剔).5+单股.5(满仓)", [cB, stk_x, s_f], [1, .5, .5], 2.0)]
rows("A. SNDK 上市以来(约1.6年)", plans(sc_x, sn_a, sn_f), sn0)
rows("B. 2017 起全程(SNDK 上市前该腿空仓)", plans(sc_x, sn_a, sn_f), A0, True)
rows("C. 压力测试：把'单独做1倍的强势股'换成 MU，2017 起", plans(sc_xm, mu_a, mu_f), A0, True)
print("ALL DONE")
