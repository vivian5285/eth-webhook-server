"""实盘口径对比：方案F(含BTC) vs 不做BTC(BTC权重按比例分给其余4币，和执行器 targets_no_btc 同规则)。只读，不写 optimal.json。"""
import sys, time, math
sys.path.insert(0, "/home/stratroster/unicorn_quant")
from engine import optimal as O
cdaily, ctrades, cstate, cmeta, CD = O.crypto_leg()
cst, _, _ = O.crypto_8h_leg("st", CD["BTCUSDT"]); cbb, _, _ = O.crypto_8h_leg("bb", CD["BTCUSDT"])
coin_cv = {s: O.combine([O.CURVES["日线EMA8/30"][s], O.CURVES["8h超级趋势"][s], O.CURVES["8h布林突破"][s]],
                        [O.SUB_W["日线EMA8/30"], O.SUB_W["8h超级趋势"], O.SUB_W["8h布林突破"]]) for s in O.M5}
cy = time.gmtime().tm_year
WY = O.coin_weights(coin_cv, range(2017, cy + 1))
WN = {}
for y, w in WY.items():
    v = {s: x for s, x in w.items() if s != "BTCUSDT"}; t = sum(v.values()) or 1
    WN[y] = {s: x / t for s, x in v.items()}
cA = O.weighted_basket(coin_cv, WY); cN = O.weighted_basket(coin_cv, WN)
sc, _, _, _ = O.stock_leg()
FA, _ = O.vol_control([cA, sc], [O.W_CRYPTO, O.W_STOCK], O.W_CRYPTO + O.W_STOCK)
FN, _ = O.vol_control([cN, sc], [O.W_CRYPTO, O.W_STOCK], O.W_CRYPTO + O.W_STOCK)
def st(c, a=None, b=None):
    ks = [k for k in sorted(c) if (a is None or k >= a) and (b is None or k <= b)]
    v = [c[k] for k in ks]; yrs = (ks[-1] - ks[0]) / 365.25
    cg = (v[-1] / v[0]) ** (1 / yrs) - 1 if yrs > 0.05 else v[-1] / v[0] - 1
    pk = v[0]; dd = 0
    for x in v: pk = max(pk, x); dd = max(dd, 1 - x / pk)
    return cg, dd, (cg / dd if dd else float("nan")), v[-1] / v[0] - 1
D = lambda y, m, d: (time.mktime((y, m, d, 0, 0, 0, 0, 0, 0)) - time.timezone) // 86400
for lab, a, b in [("全程", None, None), ("2022-03~2026-09", D(2022, 3, 1), D(2026, 9, 30)), ("2025起", D(2025, 1, 1), None), ("2026起", D(2026, 1, 1), None), ("10-19起(实盘)", D(2026, 10, 19), None)]:
    x, y_ = st(FA, a, b), st(FN, a, b); xc, yc = st(cA, a, b), st(cN, a, b)
    print(f"{lab:16s} 含BTC {x[0]:7.1%}/{x[1]:5.1%}/{x[2]:5.2f} 累计{x[3]:+.2%} | 不做BTC {y_[0]:7.1%}/{y_[1]:5.1%}/{y_[2]:5.2f} 累计{y_[3]:+.2%} | 币圈腿 含{xc[3]:+.2%} 不含{yc[3]:+.2%}")
print("今年权重", WY.get(cy), "→", {s: round(v, 4) for s, v in WN.get(cy, {}).items()})
