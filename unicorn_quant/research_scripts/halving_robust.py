"""2026-10-19 弱点②减半周期规则只有 3 个样本：
A 边界敏感度：牛市结束月 15/18/21 × 熊市结束月 27/30/33(现行 18/30)，看是不是只有 18/30 这一个点好
B 每一轮周期分开看(2017-09~2020-05 / 2020-05~2024-04 / 2024-04~至今)：有周期规则 vs 没有，是不是三轮都更好
C 软化版：逆阶段方向不禁止、只开一半(用"有规则"和"没规则"两条币圈曲线各一半近似)——如果下一轮周期不按套路走，损失更小"""
import sys, os, time
os.nice(19)
sys.path.insert(0, "/home/stratroster/unicorn_quant")
from engine import optimal as O
H = 18993
def st(cv, a=17410, z=99999):
    ks = [k for k in sorted(cv) if a <= k <= z]; v = [cv[k] for k in ks]; pk = 0; dd = 0
    for x in v: pk = max(pk, x); dd = max(dd, 1 - x / pk)
    yrs = (ks[-1] - ks[0]) / 365.25; cagr = (v[-1] / v[0]) ** (1 / yrs) - 1
    return 100 * cagr, 100 * dd, cagr / dd if dd else 0
f = lambda F: f"{st(F)[0]:+6.1f}%/{st(F)[1]:4.1f}%/{st(F)[2]:5.2f} 前{st(F,17410,H)[2]:.2f}/后{st(F,H)[2]:.2f}"
orig = O.phase_of
def mk(bull_end, bear_end):
    def ph(t_ms):
        _, m = orig(t_ms)
        return ("牛" if m < bull_end else ("熊" if m < bear_end else "筑底")), m
    return ph
sc = O.stock_leg()[0]
def crypto(ph):
    O.phase_of = ph
    _, _, _, _, CD = O.crypto_leg(); D1 = dict(O.CURVES["日线EMA8/30"])
    O.crypto_8h_leg("st", CD["BTCUSDT"]); S8 = dict(O.CURVES["8h超级趋势"]); O.crypto_8h_leg("bb", CD["BTCUSDT"]); B8 = dict(O.CURVES["8h布林突破"])
    O.phase_of = orig
    coin = {s: O.combine([D1[s], S8[s], B8[s]], [1/3] * 3) for s in O.M5}
    return O.weighted_basket(coin, O.coin_weights(coin, range(2017, 2027)))
plan = lambda cB: O.vol_control([cB, sc], [1, 1], 2.0)[0]
print("=== A 边界敏感度(主方案) ===", flush=True)
CB = {}
for be in (15, 18, 21):
    for ee in (27, 30, 33):
        CB[(be, ee)] = crypto(mk(be, ee))
        print(f"牛市到第{be}月 熊市到第{ee}月{' ←现行' if (be, ee) == (18, 30) else '      '}  {f(plan(CB[(be, ee)]))}", flush=True)
NOPH = crypto(lambda t: ("筑底", orig(t)[1]))
print(f"不用周期规则                    {f(plan(NOPH))}", flush=True)
print("\n=== B 每一轮周期分开看(主方案比值 / 年化) ===", flush=True)
CYC = [("第2轮 2017-09~2020-05", 17410, 18393), ("第3轮 2020-05~2024-04", 18393, 19832), ("第4轮 2024-04~至今", 19832, 99999)]
Fp, Fn = plan(CB[(18, 30)]), plan(NOPH)
for lab, a, z in CYC:
    x, y = st(Fp, a, z), st(Fn, a, z)
    print(f"{lab}: 有周期规则 {x[0]:+.1f}%/{x[1]:.1f}%/{x[2]:.2f} | 没有 {y[0]:+.1f}%/{y[1]:.1f}%/{y[2]:.2f} | {'更好' if x[2] > y[2] else '更差'}", flush=True)
print("\n=== C 软化版(逆阶段方向半仓) ===", flush=True)
soft = O.combine([CB[(18, 30)], NOPH], [.5, .5])
print(f"现行(严格)  {f(Fp)}\n软化(半仓)  {f(plan(soft))}\n不用规则    {f(Fn)}", flush=True)
for lab, a, z in CYC:
    print(f"   {lab}: 严格 {st(Fp,a,z)[2]:.2f} 软化 {st(plan(soft),a,z)[2]:.2f} 不用 {st(Fn,a,z)[2]:.2f}", flush=True)
print("ALL DONE")
