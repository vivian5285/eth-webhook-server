"""2026-10-19 宝贝看图：多单平得太晚、回吐大。想法：阴线吞没阳线就平多(之后阳线再收复就重新开)；连续2~3根大阴线+价格回落就减仓；空单对称(三连阳且价格上涨就减空)。
做法：用正式回测每条子腿的真实成交(进出时间、价格、仓位)，在这条腿自己的K线(日线腿=日线，8h腿=8h)上逐根叠加规则：
  收盘出现形态 → 下一根开盘把仓位调到 s(0 或 50%)；之后出现"顺势K线收盘突破前一根高点(空单是低点)" → 下一根开盘恢复 100%。原来的进出场不变。
事先定好的版本：
  V1 吞没就全平   V2 吞没减半   V3 连续2根逆势K线(收盘逐根变差)减半   V4 连续3根减半   V5 连续3根全平   V6 连续2根"大"逆势K线(实体≥0.5×ATR)减半
作用范围：只日线腿 / 只8h两条腿 / 三条都加。先核对 s≡1 时与正式回测逐日一致。"""
import sys, os, time
os.nice(19)
sys.path.insert(0, "/home/stratroster/unicorn_quant"); sys.path.insert(0, "/home/stratroster/eth-webhook-server")
from engine import optimal as O
DAY = 86400000; FEE = O.FEE
cd, ctr, _, _, CD = O.crypto_leg(); _, tst, _ = O.crypto_8h_leg("st", CD["BTCUSDT"]); _, tbb, _ = O.crypto_8h_leg("bb", CD["BTCUSDT"])
OFF = {k: dict(O.CURVES[k]) for k in ("日线EMA8/30", "8h超级趋势", "8h布林突破")}
LEGS = {"日线EMA8/30": (ctr, "1d"), "8h超级趋势": (tst, "8h"), "8h布林突破": (tbb, "8h")}
BARS = {(s, "1d"): CD[s] for s in O.M5}; BARS.update({(s, "8h"): O.spot_8h(s) for s in O.M5})
ATR = {k: O.atr(v) for k, v in BARS.items()}
sc = O.stock_leg()[0]
def pattern(b, A, i, d, kind):
    """第 i 根收盘时，对方向 d 的持仓是否出现逆势形态"""
    adv = lambda j: (b[j]["c"] < b[j]["o"]) if d > 0 else (b[j]["c"] > b[j]["o"])
    fav = lambda j: (b[j]["c"] > b[j]["o"]) if d > 0 else (b[j]["c"] < b[j]["o"])
    worse = lambda j: (b[j]["c"] < b[j - 1]["c"]) if d > 0 else (b[j]["c"] > b[j - 1]["c"])
    body = lambda j: abs(b[j]["c"] - b[j]["o"])
    if kind == "engulf":
        return i >= 1 and adv(i) and fav(i - 1) and body(i) > body(i - 1) and ((b[i]["c"] < b[i - 1]["o"]) if d > 0 else (b[i]["c"] > b[i - 1]["o"]))
    n, big = {"seq2": (2, False), "seq3": (3, False), "big2": (2, True)}[kind]
    if i < n: return False
    return all(adv(j) and worse(j) and (not big or body(j) >= 0.5 * A[j - 1]) for j in range(i - n + 1, i + 1))
def recover(b, i, d):
    return (b[i]["c"] > b[i - 1]["h"]) if d > 0 else (b[i]["c"] < b[i - 1]["l"])
def leg_curve(sym, leg, rule):
    trades, tf = LEGS[leg]; b = BARS[(sym, tf)]; A = ATR[(sym, tf)]
    idx = {x["t"]: i for i, x in enumerate(b)}
    tr = sorted([t for t in trades if t["symbol"] == sym], key=lambda t: t["entry_t"])
    ent = {}; ext = {}
    for t in tr:
        ent.setdefault(t["entry_t"], []).append(t)
        if t.get("exit_t"): ext.setdefault(t["exit_t"], []).append(t)
    eq = 1.0; q = 0.0; s = 1.0; s_next = None; cur = None; out = {}; pc = None
    for i, x in enumerate(b):
        t = x["t"]
        if cur is not None and pc is not None and s_next is not None and s_next != s:   # 开盘调仓
            eq += s * q * (x["o"] - pc) - abs(s_next - s) * abs(q) * x["o"] * FEE; pc = x["o"]; s = s_next
        s_next = None
        for tt in ext.get(t, []):                                    # 平仓(开盘或盘中止损价)
            if cur is tt:
                eq += s * q * (tt["exit"] - pc) - abs(s * q) * tt["exit"] * FEE; cur = None; q = 0.0; pc = None
        for tt in ent.get(t, []):                                    # 开仓
            if cur is None:
                cur = tt; d = 1 if tt["side"] == "LONG" else -1; q = d * eq * (tt.get("alloc") or 1.0) / tt["entry"]
                eq -= abs(q) * tt["entry"] * FEE; s = 1.0; pc = tt["entry"]
                if tt.get("exit_t") == t:                            # 当根进当根出(止损)
                    eq += q * (tt["exit"] - pc) - abs(q) * tt["exit"] * FEE; cur = None; q = 0.0; pc = None
        if cur is not None:
            eq += s * q * (x["c"] - pc); pc = x["c"]
            if rule and cur["entry_t"] < t:
                kind, lvl = rule; d = 1 if q > 0 else -1
                if s == 1.0 and pattern(b, A, i, d, kind): s_next = lvl
                elif s < 1.0 and recover(b, i, d): s_next = 1.0
        out[t // DAY] = max(eq, 0)
    return out
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
# 复刻核对
worst = 0
for leg in LEGS:
    for s in O.M5:
        R = leg_curve(s, leg, None); Of = OFF[leg][s]
        ks = [k for k in Of if k in R]; worst = max(worst, max(abs(R[k] - Of[k]) / max(Of[k], 1e-9) for k in ks))
print(f"复刻核对：三条腿×5个币，与正式回测净值最大相对误差 {worst:.2e}", flush=True)
def plan(cfg):
    coin = {s: O.combine([leg_curve(s, leg, cfg.get(leg)) for leg in LEGS], [1/3] * 3) for s in O.M5}
    cB = O.weighted_basket(coin, O.coin_weights(coin, range(2017, 2027)))
    return O.vol_control([cB, sc], [1, 1], 2.0)[0]
B = plan({}); b0 = (st(B)[2], st(B, 17410, H)[2], st(B, H)[2])
def show(lab, F):
    a = st(F); s1, s2 = st(F, 17410, H)[2], st(F, H)[2]; tag = "★" if a[2] > b0[0] and s1 > b0[1] and s2 > b0[2] else ""
    print(f"{lab:30s} {a[0]:+6.1f}%/{a[1]:4.1f}%/{a[2]:5.2f} 前{s1:.2f}/后{s2:.2f}{tag} | {yr(F)}", flush=True)
show("基准(复刻正式)", B)
RULES = {"V1 吞没全平": ("engulf", 0.0), "V2 吞没减半": ("engulf", 0.5), "V3 连2根减半": ("seq2", 0.5),
         "V4 连3根减半": ("seq3", 0.5), "V5 连3根全平": ("seq3", 0.0), "V6 连2根大K减半": ("big2", 0.5)}
for nm, r in RULES.items():
    show(nm + " 日线腿", plan({"日线EMA8/30": r}))
    show(nm + " 8h两腿", plan({"8h超级趋势": r, "8h布林突破": r}))
    show(nm + " 三条腿", plan({k: r for k in LEGS}))
print("ALL DONE")
