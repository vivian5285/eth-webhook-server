"""2026-10-19 宝贝：纯价格行为放到美股腿回测。美股腿其余规则不变(每月行业前5轮动、只做多、3×ATR止损+止损后等信号重置、仓位/1倍上限)，
只把"正股日线 EMA8>EMA30 持有、<平仓"换成价格行为信号(多头状态持有，否则离场)：
PA1 结构BOS w2/w5、PA2 箱体突破、PA3 内包线突破、PA4 极端吞没反转、PA5 唐奇安20/10(定义同币圈那次)。
用法：A 替换 EMA；B 和现行 EMA 版各占美股腿一半。"""
import sys, os, time, inspect
os.nice(19)
sys.path.insert(0, "/home/stratroster/unicorn_quant"); sys.path.insert(0, "/home/stratroster/eth-webhook-server")
src = open("/tmp/price_action.py", encoding="utf-8").read()
exec(src.split("cd, _, _, _, CD = O.crypto_leg()")[0].split('"""', 2)[2])     # 复用 PA 函数、st、yr
S0 = inspect.getsource(O.stock_leg)
OLD = '"dir": [1 if f[i] > sl[i] else -1 for i in range(len(b))]'
assert OLD in S0
exec(S0.replace("def stock_leg():", "def stock_leg_pa(DIRFN):").replace(OLD, '"dir": DIRFN(b, f, sl)'), O.__dict__)
ema_dir = lambda b, f, sl: [1 if f[i] > sl[i] else -1 for i in range(len(b))]
def pa_dir(fn): return lambda b, f, sl: [1 if v > 0 else -1 for v in fn(b)]
cd, _, _, _, CD = O.crypto_leg(); O.crypto_8h_leg("st", CD["BTCUSDT"]); O.crypto_8h_leg("bb", CD["BTCUSDT"])
coin = {s: O.combine([O.CURVES["日线EMA8/30"][s], O.CURVES["8h超级趋势"][s], O.CURVES["8h布林突破"][s]], [1/3] * 3) for s in O.M5}
cB = O.weighted_basket(coin, O.coin_weights(coin, range(2017, 2027)))
sc0 = O.stock_leg_pa(ema_dir)[0]; sc_chk = O.stock_leg()[0]
assert abs(st(sc0)[2] - st(sc_chk)[2]) < 1e-9, "复刻不一致"
BASE = O.vol_control([cB, sc0], [1, 1], 2.0)[0]; b0 = (st(BASE)[2], st(BASE, 17410, H)[2], st(BASE, H)[2])
print(f"基准：美股腿自己 {st(sc0)[0]:+.1f}%/{st(sc0)[1]:.1f}%/{st(sc0)[2]:.2f} 前{st(sc0,17410,H)[2]:.2f}/后{st(sc0,H)[2]:.2f} | 主方案 {st(BASE)[0]:+.1f}%/{st(BASE)[1]:.1f}%/{b0[0]:.2f} 前{b0[1]:.2f}/后{b0[2]:.2f} | {yr(sc0)}", flush=True)
PAS = {"PA1 结构BOS w2": lambda b: pa_bos(b, 2), "PA1 结构BOS w5": lambda b: pa_bos(b, 5), "PA2 箱体突破": pa_box,
       "PA3 内包线突破": pa_inside, "PA4 极端吞没反转": pa_engulf, "PA5 唐奇安20/10": pa_donch}
for nm, fn in PAS.items():
    s2, tr, _, _ = O.stock_leg_pa(pa_dir(fn))
    a = st(s2); line = f"{nm:14s} 美股腿自己 {a[0]:+6.1f}%/{a[1]:4.1f}%/{a[2]:5.2f} 前{st(s2,17410,H)[2]:.2f}/后{st(s2,H)[2]:.2f} {len(tr)}笔"
    for lab, sc in (("A替换", s2), ("B各半", O.combine([sc0, s2], [.5, .5]))):
        F = O.vol_control([cB, sc], [1, 1], 2.0)[0]; x = st(F); q1, q2 = st(F, 17410, H)[2], st(F, H)[2]
        tag = "★" if x[2] > b0[0] and q1 > b0[1] and q2 > b0[2] else ""
        line += f" | {lab} 主方案 {x[0]:+.1f}%/{x[1]:.1f}%/{x[2]:.2f} 前{q1:.2f}/后{q2:.2f}{tag}"
    print(line + f" | {yr(s2)}", flush=True)
print("ALL DONE")
