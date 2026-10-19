"""2026-10-19 统一口径：8h两条子腿回测里没有止损，擂台有 4×ATR(8h) 灾难止损。测回测里加上 k×ATR 灾难止损(k=3/4/5/6)的影响，
决定回测和擂台怎么统一(实盘也需要一个交易所止损兜底，防宕机)。止损后等信号翻转才重新进场(同擂台)。"""
import sys, os, time, inspect
os.nice(19)
sys.path.insert(0, "/home/stratroster/unicorn_quant")
from engine import optimal as O
SRC = inspect.getsource(O.crypto_8h_leg)
A1 = '''            if pos:
                eq += qty * (cl - pc)'''
B1 = '''            if pos and STOPK and ((pos > 0 and b[i]["l"] <= stp) or (pos < 0 and b[i]["h"] >= stp)):
                px = min(op, stp) if pos > 0 else max(op, stp)
                eq += qty * (px - pc) - abs(qty) * px * FEE
                cur.update(exit_t=t, exit=px, exit_reason="灾难止损", ret=round(100 * (pos * (px / cur["entry"] - 1) - 2 * FEE), 2),
                           pnl_pct=round(100 * (eq / cur["_eq0"] - 1), 2), days=round((t - cur["entry_t"]) / DAY, 1))
                trades.append(cur); cur = None; locked = pos; qty = 0; pos = 0
            elif pos:
                eq += qty * (cl - pc)'''
A2 = '''                    pos = newpos; qty = eq * alloc / op * pos; eq -= abs(qty) * op * FEE; pc = op'''
B2 = '''                    pos = newpos; qty = eq * alloc / op * pos; eq -= abs(qty) * op * FEE; pc = op; stp = op - pos * STOPK * A[i - 1] if STOPK else None'''
A3 = '''            if (ph == "牛" and want < 0) or (ph == "熊" and want > 0):'''
B3 = '''            if locked and want != locked: locked = 0
            if locked and want == locked: want = 0
            if (ph == "牛" and want < 0) or (ph == "熊" and want > 0):'''
B3_OLD = '''
            if locked and want != locked: locked = 0
            if locked and want == locked: want = 0'''
A4 = '''        eq = 1.0; qty = 0.0; pos = 0; pend = None; cur = None; curve = {}'''
B4 = '''        eq = 1.0; qty = 0.0; pos = 0; pend = None; cur = None; curve = {}; locked = 0; stp = None'''
for a in (A1, A2, A3, A4): assert a in SRC, a[:40]
S2 = SRC.replace(A1, B1).replace(A2, B2).replace(A3, B3).replace(A4, B4).replace('"回到布林中轨"}[D[i]]', '"回到布林中轨"}[D[i]]')
exec(S2, O.__dict__)
def st(cv, a=17410, z=99999):
    ks = [k for k in sorted(cv) if a <= k <= z]; v = [cv[k] for k in ks]; pk = 0; dd = 0
    for x in v: pk = max(pk, x); dd = max(dd, 1 - x / pk)
    yrs = (ks[-1] - ks[0]) / 365.25; cagr = (v[-1] / v[0]) ** (1 / yrs) - 1
    return 100 * cagr, 100 * dd, cagr / dd if dd else 0
H = 18993
_, _, _, _, CD = O.crypto_leg(); D1 = dict(O.CURVES["日线EMA8/30"]); sc = O.stock_leg()[0]
for k in (0, 3, 4, 5, 6):
    O.STOPK = k
    _, ts_, _ = O.crypto_8h_leg("st", CD["BTCUSDT"]); S = dict(O.CURVES["8h超级趋势"]); _, tb_, _ = O.crypto_8h_leg("bb", CD["BTCUSDT"]); B = dict(O.CURVES["8h布林突破"])
    coin = {s: O.combine([D1[s], S[s], B[s]], [1/3] * 3) for s in O.M5}
    F = O.vol_control([O.weighted_basket(coin, O.coin_weights(coin, range(2017, 2027))), sc], [1, 1], 2.0)[0]
    n = sum(1 for t in ts_ + tb_ if t.get("exit_reason") == "灾难止损")
    a = st(F); print(f"{'无止损(现行回测)' if not k else f'{k}×ATR灾难止损':14s} 主方案 {a[0]:+.1f}%/{a[1]:.1f}%/{a[2]:.2f} 前{st(F,17410,H)[2]:.2f}/后{st(F,H)[2]:.2f} | 止损触发 {n} 次", flush=True)
print("ALL DONE")
