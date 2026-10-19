"""2026-10-19 宝贝：①日线腿换成12h会不会更好？②剔除贡献小/表现差的币、加码表现好的，会不会更好？
②防事后诸葛亮：事后版(用全部历史挑，作弊上限) vs 滚动版(每年1月1日只用过去2年数据定今年权重)。
币圈逐币曲线：把 optimal.crypto_leg / crypto_8h_leg 的 return 换成逐币 curves(其余不动)，每币=三条子腿 50/25/25。"""
import sys, os, time, inspect, math, json, urllib.request
os.nice(19)
sys.path.insert(0, "/home/stratroster/unicorn_quant"); sys.path.insert(0, "/home/stratroster/eth-webhook-server")
from engine import optimal as O
DAY = 86400000
s1 = inspect.getsource(O.crypto_leg).replace("def crypto_leg():", "def crypto_leg_c():").replace("return port, trades, state, meta, D", "return curves, port, trades, D")
s2 = inspect.getsource(O.crypto_8h_leg).replace("def crypto_8h_leg(kind, btc_daily):", "def crypto_8h_leg_c(kind, btc_daily, BARSF=None, HRS=8, DIRS=None):")
s2 = s2.replace("vt = VOL_TGT * math.sqrt(8 / 24); P8 = 8 * 3600000", "vt = VOL_TGT * math.sqrt(HRS / 24); P8 = HRS * 3600000")
s2 = s2.replace('b = spot_8h(sym); A = atr(b); D = _st_dirs(b) if kind == "st" else _bb_states(b)',
                'b = (BARSF or spot_8h)(sym); A = atr(b); D = DIRS(b) if DIRS else (_st_dirs(b) if kind == "st" else _bb_states(b))')
s2 = s2.replace("return port, trades, opens", "return curves, port, trades")
exec(s1, O.__dict__); exec(s2, O.__dict__)
dC, dP, dT, CD = O.crypto_leg_c()
sC, sP, _ = O.crypto_8h_leg_c("st", CD["BTCUSDT"]); bC, bP, _ = O.crypto_8h_leg_c("bb", CD["BTCUSDT"])
sc = O.stock_leg()[0]
def st(cv, a=17410, z=99999):
    ks = [k for k in sorted(cv) if a <= k <= z]; v = [cv[k] for k in ks]; pk = 0; dd = 0
    for x in v: pk = max(pk, x); dd = max(dd, 1 - x / pk)
    yrs = (ks[-1] - ks[0]) / 365.25; cagr = (v[-1] / v[0]) ** (1 / yrs) - 1
    return 100 * cagr, 100 * dd, cagr / dd if dd else 0
f = lambda a: f"{a[0]:+6.1f}%/{a[1]:4.1f}%/{a[2]:5.2f}"
H = 18993
def plan(cB):
    F = O.vol_control([cB, sc], [1, 1], 2.0)[0]; return f"{f(st(F))} 前{st(F,17410,H)[2]:.2f}/后{st(F,H)[2]:.2f}"
# ---------- ② 币种权重 稳健性 ----------
M5 = O.M5
COIN = {s: O.combine([dC[s], sC[s], bC[s]], [.5, .25, .25]) for s in M5}
print("\n==== ② 币种权重 ====", flush=True)
for s in M5: print(f"  {s[:-4]:4s} 单币(三条子腿) {f(st(COIN[s]))} 前{st(COIN[s],17410,H)[2]:.2f}/后{st(COIN[s],H)[2]:.2f}", flush=True)
def basket(ws):
    syms = [s for s in M5 if ws.get(s, 0) > 0]; return O.combine([COIN[s] for s in syms], [ws[s] for s in syms])
#:40s} 主方案 {plan(basket({s: .2 for s in M5}))}", flush=True)
#：剔除XRP(四币各1/4)':40s} 主方案 {plan(basket({s: .25 for s in M5 if s != 'XRPUSDT'}))}", flush=True)
r_all = {s: max(0.0, st(COIN[s])[2]) for s in M5}; tot = sum(r_all.values())
#：按全程收益回撤比加权':40s} 主方案 {plan(basket({s: r_all[s] / tot for s in M5}))}  权重 " + " ".join(f"{s[:-4]}{100*r_all[s]/tot:.0f}%" for s in M5), flush=True)
#：只做最好的两个(BNB+SOL)':40s} 主方案 {plan(basket({'BNBUSDT': .5, 'SOLUSDT': .5}))}", flush=True)

def capw(raw, cap):
    w = dict(raw)
    for _ in range(20):
        t_ = sum(w.values()) or 1; w = {s: x / t_ for s, x in w.items()}
        over = {s for s, x in w.items() if x > cap + 1e-9}
        if not over: break
        fixed = {s: cap for s in over}; rest = {s: x for s, x in w.items() if s not in over}
        rt = sum(rest.values()); left = 1 - cap * len(over)
        w = {**fixed, **({s: x / rt * left for s, x in rest.items()} if rt > 0 else {})}
        if all(x <= cap + 1e-9 for x in w.values()): break
    return w
def rolling(mode, look=730, cap=0.4):
    days = sorted(set().union(*[set(COIN[s]) for s in M5])); out = {}; v = 1.0; prev = {}
    for y in range(2019, 2027):
        y0 = int(time.mktime((y, 1, 1, 0, 0, 0, 0, 0, 0)) // 86400); y1 = int(time.mktime((y + 1, 1, 1, 0, 0, 0, 0, 0, 0)) // 86400)
        sc_ = {}
        for s in M5:
            ks = [k for k in COIN[s] if y0 - look <= k < y0]
            if len(ks) >= min(300, look * 0.8): sc_[s] = st(COIN[s], y0 - look, y0 - 1)
        if mode == "equal": w = {s: 1 / len(sc_) for s in sc_}
        elif mode == "ratio": w = capw({s: max(0.0, a[2]) for s, a in sc_.items()}, cap)
        elif mode == "ret": w = capw({s: max(0.0, a[0]) for s, a in sc_.items()}, cap)
        elif mode == "drop": ok = sorted(sc_, key=lambda k: sc_[k][2])[1:] if len(sc_) >= 3 else list(sc_); w = {s: 1 / len(ok) for s in ok}
        for d in [d for d in days if y0 <= d < y1]:
            r = 0.0
            for s, x in w.items():
                cv = COIN[s]
                if d in cv:
                    if s in prev: r += x * (cv[d] / prev[s] - 1)
                    prev[s] = cv[d]
            v *= 1 + r; out[d] = v
    return out
A0 = 17897   # 2019-01-01，所有滚动版同一起点
def pl(cv):
    F = O.vol_control([cv, sc], [1, 1], 2.0)[0]; return st(F, A0)[2], st(F, A0, H)[2], st(F, H)[2]
print("==== 币种权重稳健性(2019 起，同一起点；比 全程/前半2019-2021/后半2022-) ====", flush=True)
for look in (365, 730, 1095):
    eq = pl(rolling("equal", look)); print(f"回看{look}天 等权对照                 {eq[0]:.2f} / {eq[1]:.2f} / {eq[2]:.2f}", flush=True)
    for mode in ("drop", "ratio", "ret"):
        for cap in ((0.3, 0.4, 0.5) if mode != "drop" else (None,)):
            r = pl(rolling(mode, look, cap or 0.4)); tag = "  ← 两半都好" if r[1] > eq[1] and r[2] > eq[2] else ""
            print(f"回看{look}天 {mode:5s} 上限{cap}            {r[0]:.2f} / {r[1]:.2f} / {r[2]:.2f}{tag}", flush=True)
print("ALL DONE", flush=True)
