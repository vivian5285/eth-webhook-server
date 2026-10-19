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
# ---------- ① 12h ----------
CP = "/tmp/spot_12h_cache.json"
def spot_12h(sym):
    cache = json.load(open(CP)) if os.path.exists(CP) else {}
    if sym in cache: return cache[sym]
    out = []; s = 1500000000000
    while True:
        r = json.load(urllib.request.urlopen(f"https://api.binance.com/api/v3/klines?symbol={sym}&interval=12h&startTime={s}&limit=1000", timeout=30))
        if not r: break
        out += [{"t": int(x[0]), "o": float(x[1]), "h": float(x[2]), "l": float(x[3]), "c": float(x[4]), "v": float(x[5])} for x in r]
        if len(r) < 1000: break
        s = int(r[-1][0]) + 1; time.sleep(0.6)
    now = int(time.time() * 1000); out = [b for b in out if b["t"] + 12 * 3600000 <= now]
    cache[sym] = out; json.dump(cache, open(CP, "w")); return out
def ema_dirs(f_, s_):
    def fn(b):
        c = [x["c"] for x in b]; a, z = O.ema(c, f_), O.ema(c, s_)
        return [0 if i < s_ else (1 if a[i] > z[i] else -1) for i in range(len(b))]
    return fn
daily = lambda s: CD[s]
print("==== ① 日线腿换 12h(同一执行框架：严格减半周期+BTC同向+波动率仓位；不含突破单和底部例外) ====", flush=True)
base = O.combine([dP, sP, bP], [.5, .25, .25])
print(f"{'现行 日线腿(含突破单+底部例外)':34s} 腿 {f(st(dP))} || 主方案 {plan(base)}", flush=True)
for nm, bf, hrs, ff, ss in (("同框架 日线EMA8/30(对照)", daily, 24, 8, 30), ("12h EMA8/30", spot_12h, 12, 8, 30), ("12h EMA16/60(≈日线8/30)", spot_12h, 12, 16, 60), ("12h EMA12/45", spot_12h, 12, 12, 45)):
    _, P_, _ = O.crypto_8h_leg_c("bb", CD["BTCUSDT"], BARSF=bf, HRS=hrs, DIRS=ema_dirs(ff, ss))
    print(f"{nm:34s} 腿 {f(st(P_))} || 替换日线腿后主方案 {plan(O.combine([P_, sP, bP], [.5, .25, .25]))}", flush=True)
# ---------- ② 币种权重 ----------
M5 = O.M5
COIN = {s: O.combine([dC[s], sC[s], bC[s]], [.5, .25, .25]) for s in M5}
print("\n==== ② 币种权重 ====", flush=True)
for s in M5: print(f"  {s[:-4]:4s} 单币(三条子腿) {f(st(COIN[s]))} 前{st(COIN[s],17410,H)[2]:.2f}/后{st(COIN[s],H)[2]:.2f}", flush=True)
def basket(ws):
    syms = [s for s in M5 if ws.get(s, 0) > 0]; return O.combine([COIN[s] for s in syms], [ws[s] for s in syms])
print(f"{'现行 五币各1/5':40s} 主方案 {plan(basket({s: .2 for s in M5}))}", flush=True)
print(f"{'事后版：剔除XRP(四币各1/4)':40s} 主方案 {plan(basket({s: .25 for s in M5 if s != 'XRPUSDT'}))}", flush=True)
r_all = {s: max(0.0, st(COIN[s])[2]) for s in M5}; tot = sum(r_all.values())
print(f"{'事后版：按全程收益回撤比加权':40s} 主方案 {plan(basket({s: r_all[s] / tot for s in M5}))}  权重 " + " ".join(f"{s[:-4]}{100*r_all[s]/tot:.0f}%" for s in M5), flush=True)
print(f"{'事后版：只做最好的两个(BNB+SOL)':40s} 主方案 {plan(basket({'BNBUSDT': .5, 'SOLUSDT': .5}))}", flush=True)
def rolling(mode, look=730, cap=0.4):
    days = sorted(set().union(*[set(COIN[s]) for s in M5])); out = {}; v = 1.0; prev = {}; picks = {}
    for y in range(2018, 2027):
        y0 = int(time.mktime((y, 1, 1, 0, 0, 0, 0, 0, 0)) // 86400); y1 = int(time.mktime((y + 1, 1, 1, 0, 0, 0, 0, 0, 0)) // 86400)
        sc_ = {}
        for s in M5:
            ks = [k for k in COIN[s] if y0 - look <= k < y0]
            if len(ks) >= 300: sc_[s] = st(COIN[s], y0 - look, y0 - 1)[2]
        if mode == "drop_worst":
            ok = sorted(sc_, key=sc_.get)[1:] if len(sc_) >= 3 else list(sc_)
            w = {s: 1 / len(ok) for s in ok}
        elif mode == "ratio_w":
            pos = {s: max(0.0, r) for s, r in sc_.items()}; t_ = sum(pos.values()) or 1
            w = {s: min(cap, p / t_) for s, p in pos.items()}; t2 = sum(w.values()) or 1; w = {s: x / t2 for s, x in w.items()}
        else:
            w = {s: 1 / len(sc_) for s in sc_}
        for s in M5:
            if s not in sc_ and s in COIN and any(y0 <= k < y1 for k in COIN[s]) and mode == "equal_all": w[s] = 0
        picks[y] = " ".join(f"{s[:-4]}{100*x:.0f}" for s, x in sorted(w.items(), key=lambda kv: -kv[1]) if x > 0)
        for d in [d for d in days if y0 <= d < y1]:
            r = 0.0
            for s, x in w.items():
                cv = COIN[s]
                if d in cv:
                    if s in prev: r += x * (cv[d] / prev[s] - 1)
                    prev[s] = cv[d]
            v *= 1 + r; out[d] = v
    return out, picks
for mode, lab in (("equal", "滚动版对照：等权(同样的可用币规则)"), ("drop_worst", "滚动版：每年剔除过去2年最差的1个"), ("ratio_w", "滚动版：按过去2年收益回撤比加权(单币≤40%)")):
    cv, picks = rolling(mode); print(f"{lab:40s} 主方案 {plan(cv)}", flush=True); print("      每年权重(%)：" + "；".join(f"{y}:{p}" for y, p in picks.items()), flush=True)
print("ALL DONE", flush=True)
