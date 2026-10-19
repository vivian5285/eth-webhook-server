"""2026-10-19 宝贝：美股一定要等开盘才成交吗？永续24小时可交易，信号在正股收盘时就有了。
对比美股腿进出场的成交时点(信号同为正股日线收盘)：
  O/O 现行：进场、离场都在第二天开盘(= 回测)
  C/C 收盘后立刻在永续上成交(用信号当天收盘价近似)
  C/O 收盘后立刻进场，离场等开盘
  O/C 开盘进场，收盘后立刻离场
保守：收盘后在永续上成交，额外加 10bp 成本(盘后永续价差更大、财报常在收盘后公布)。3×ATR 止损逻辑不变。"""
import sys, os, time, inspect
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
SRC = inspect.getsource(O.stock_leg)
A = '''            op = P["b"][i]["o"]; del pend[s]'''
assert A in SRC
B = '''            del pend[s]
            _close_px = P["b"][i - 1]["c"] if i >= 1 else P["b"][i]["o"]
            op = (_close_px * (1 + EXTRA_COST * (1 if act[0] == "enter" else -1)) if ((act[0] == "enter" and ENTRY_AT_CLOSE) or (act[0] == "exit" and EXIT_AT_CLOSE)) else P["b"][i]["o"])'''
exec(SRC.replace(A, B).replace("def stock_leg():", "def stock_leg_t():"), O.__dict__)
_, _, _, _, CD = O.crypto_leg(); O.crypto_8h_leg("st", CD["BTCUSDT"]); O.crypto_8h_leg("bb", CD["BTCUSDT"])
coin = {s: O.combine([O.CURVES["日线EMA8/30"][s], O.CURVES["8h超级趋势"][s], O.CURVES["8h布林突破"][s]], [1/3] * 3) for s in O.M5}
cB = O.weighted_basket(coin, O.coin_weights(coin, range(2017, 2027)))
for lab, ec, xc, cost in (("O/O 现行(开盘进、开盘出)", False, False, 0), ("C/C 收盘后进、收盘后出", True, True, 0.001),
                          ("C/O 收盘后进、开盘出", True, False, 0.001), ("O/C 开盘进、收盘后出", False, True, 0.001),
                          ("C/C 不加额外成本(乐观)", True, True, 0.0)):
    O.ENTRY_AT_CLOSE, O.EXIT_AT_CLOSE, O.EXTRA_COST = ec, xc, cost
    sc = O.stock_leg_t()[0]
    F = O.vol_control([cB, sc], [1, 1], 2.0)[0]
    print(f"{lab:26s} 美股腿 {st(sc)[0]:+.1f}%/{st(sc)[1]:.1f}%/{st(sc)[2]:.2f} 前{st(sc,17410,H)[2]:.2f}/后{st(sc,H)[2]:.2f} | 主方案 {f(F)}", flush=True)
print("ALL DONE")
