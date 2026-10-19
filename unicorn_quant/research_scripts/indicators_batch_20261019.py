"""2026-10-19 宝贝：DMI/ADX、HMA、GMMA 等还没在主方案里测过的指标，各种组合都试试。规则事先定死、全部列出。
① 子腿：复用 crypto_8h_leg 的执行框架(严格减半周期+BTC同向+波动率仓位+次根开盘)，只换方向信号；8h 和日线各测；看 单独/替换布林/替换超趋/第4条腿。
② 过滤器：只加在开仓条件上(币圈日线腿的趋势单+突破单；美股腿)，过滤器不同意就不开。
基准用同一次运行里的现行(各口径一致)。"""
import sys, os, time, inspect, math, json
os.nice(19)
sys.path.insert(0, "/home/stratroster/unicorn_quant"); sys.path.insert(0, "/home/stratroster/eth-webhook-server")
from engine import optimal as O
DAY = 86400000
# ---------- 指标 ----------
def ema(xs, n): return O.ema(xs, n)
def wma(xs, n):
    out = []; den = n * (n + 1) / 2
    for i in range(len(xs)):
        if i < n - 1: out.append(xs[i]); continue
        out.append(sum(xs[i - n + 1 + k] * (k + 1) for k in range(n)) / den)
    return out
def hma(xs, n):
    a, b = wma(xs, n // 2), wma(xs, n); d = [2 * x - y for x, y in zip(a, b)]; return wma(d, max(2, int(math.sqrt(n))))
def dmi(b, n=14):
    h = [x["h"] for x in b]; l = [x["l"] for x in b]; c = [x["c"] for x in b]; N = len(b)
    pdm = [0.0] * N; ndm = [0.0] * N; tr = [0.0] * N
    for i in range(1, N):
        up, dn = h[i] - h[i-1], l[i-1] - l[i]
        pdm[i] = up if up > dn and up > 0 else 0.0; ndm[i] = dn if dn > up and dn > 0 else 0.0
        tr[i] = max(h[i] - l[i], abs(h[i] - c[i-1]), abs(l[i] - c[i-1]))
    def rma(x):
        o = [0.0] * N; s = None
        for i in range(1, N):
            s = x[i] if s is None else s - s / n + x[i]
            o[i] = s
        return o
    TR, P, M = rma(tr), rma(pdm), rma(ndm)
    pdi = [100 * P[i] / TR[i] if TR[i] else 0 for i in range(N)]; ndi = [100 * M[i] / TR[i] if TR[i] else 0 for i in range(N)]
    dx = [100 * abs(pdi[i] - ndi[i]) / (pdi[i] + ndi[i]) if (pdi[i] + ndi[i]) else 0 for i in range(N)]
    adx = [0.0] * N; a = None
    for i in range(N):
        a = dx[i] if a is None else (a * (n - 1) + dx[i]) / n; adx[i] = a
    return pdi, ndi, adx
def sig_hma(n):
    def fn(b):
        c = [x["c"] for x in b]; hm = hma(c, n)
        return [0 if i < n + 5 else (1 if hm[i] > hm[i-1] and c[i] > hm[i] else (-1 if hm[i] < hm[i-1] and c[i] < hm[i] else 0)) for i in range(len(b))]
    return fn
def sig_gmma(b):
    c = [x["c"] for x in b]; S = [ema(c, n) for n in (3, 5, 8, 10, 12, 15)]; L = [ema(c, n) for n in (30, 35, 40, 45, 50, 60)]; d = [0] * len(b); st = 0
    for i in range(60, len(b)):
        smin, smax = min(s[i] for s in S), max(s[i] for s in S); lmin, lmax = min(x[i] for x in L), max(x[i] for x in L)
        if smin > lmax: st = 1
        elif smax < lmin: st = -1
        elif (st > 0 and smax < lmax) or (st < 0 and smin > lmin): st = 0     # 短期组掉回长期组里面就离场
        d[i] = st
    return d
def sig_dmi(th=20):
    def fn(b):
        p, n_, a = dmi(b); d = [0] * len(b); st = 0
        for i in range(30, len(b)):
            if p[i] > n_[i] and a[i] > th: st = 1
            elif n_[i] > p[i] and a[i] > th: st = -1
            elif (st > 0 and n_[i] > p[i]) or (st < 0 and p[i] > n_[i]): st = 0
            d[i] = st
        return d
    return fn
def sig_ichimoku(b):
    h = [x["h"] for x in b]; l = [x["l"] for x in b]; c = [x["c"] for x in b]; N = len(b); d = [0] * N
    mid = lambda i, n: (max(h[i-n+1:i+1]) + min(l[i-n+1:i+1])) / 2
    for i in range(80, N):
        tk, kj = mid(i, 9), mid(i, 26); sa = (mid(i-26, 9) + mid(i-26, 26)) / 2; sb = mid(i-26, 52); top, bot = max(sa, sb), min(sa, sb)
        d[i] = 1 if c[i] > top and tk > kj else (-1 if c[i] < bot and tk < kj else 0)
    return d
def sig_psar(b, af0=0.02, afm=0.2):
    h = [x["h"] for x in b]; l = [x["l"] for x in b]; N = len(b); d = [0] * N
    up = True; sar = l[0]; ep = h[0]; af = af0
    for i in range(1, N):
        sar = sar + af * (ep - sar)
        if up:
            sar = min(sar, l[i-1], l[i-2] if i > 1 else l[i-1])
            if l[i] < sar: up = False; sar = ep; ep = l[i]; af = af0
            elif h[i] > ep: ep = h[i]; af = min(afm, af + af0)
        else:
            sar = max(sar, h[i-1], h[i-2] if i > 1 else h[i-1])
            if h[i] > sar: up = True; sar = ep; ep = h[i]; af = af0
            elif l[i] < ep: ep = l[i]; af = min(afm, af + af0)
        d[i] = 1 if up else -1
    return d
def sig_kama(b, n=10, f=2, s=30):
    c = [x["c"] for x in b]; N = len(c); k = [c[0]] * N; d = [0] * N
    fs, ss = 2 / (f + 1), 2 / (s + 1)
    for i in range(1, N):
        if i < n: k[i] = c[i]; continue
        ch = abs(c[i] - c[i-n]); vol = sum(abs(c[j] - c[j-1]) for j in range(i-n+1, i+1)) or 1e-12
        sc = (ch / vol * (fs - ss) + ss) ** 2; k[i] = k[i-1] + sc * (c[i] - k[i-1])
        d[i] = 1 if c[i] > k[i] and k[i] > k[i-1] else (-1 if c[i] < k[i] and k[i] < k[i-1] else 0)
    return d
def sig_aroon(b, n=25):
    h = [x["h"] for x in b]; l = [x["l"] for x in b]; N = len(b); d = [0] * N; st = 0
    for i in range(n, N):
        wh = h[i-n:i+1]; wl = l[i-n:i+1]
        au = 100 * (n - (n - wh.index(max(wh)))) / n; ad = 100 * (n - (n - wl.index(min(wl)))) / n
        au = 100 * wh.index(max(wh)) / n; ad = 100 * wl.index(min(wl)) / n
        if au > 70 and ad < 30: st = 1
        elif ad > 70 and au < 30: st = -1
        elif (st > 0 and ad > au) or (st < 0 and au > ad): st = 0
        d[i] = st
    return d
def sig_ha(b):
    N = len(b); d = [0] * N; ho = (b[0]["o"] + b[0]["c"]) / 2; col = []
    for i, x in enumerate(b):
        hc = (x["o"] + x["h"] + x["l"] + x["c"]) / 4
        if i: ho = (ho + hcp) / 2
        hcp = hc; col.append(1 if hc > ho else -1)
        if i >= 2 and col[-1] == col[-2] == col[-3]: d[i] = col[-1]
        else: d[i] = d[i-1] if i else 0
    return d
def sig_vwap(n=20):
    def fn(b):
        N = len(b); d = [0] * N
        for i in range(n, N):
            w = b[i-n+1:i+1]; vv = sum(x.get("v", 0) or 0 for x in w) or 1e-12
            vw = sum(((x["h"] + x["l"] + x["c"]) / 3) * (x.get("v", 0) or 0) for x in w) / vv
            w0 = b[i-n:i]; vv0 = sum(x.get("v", 0) or 0 for x in w0) or 1e-12
            vw0 = sum(((x["h"] + x["l"] + x["c"]) / 3) * (x.get("v", 0) or 0) for x in w0) / vv0
            d[i] = 1 if b[i]["c"] > vw and vw > vw0 else (-1 if b[i]["c"] < vw and vw < vw0 else 0)
        return d
    return fn
def sig_keltner(b, n=20, k=2.0):
    c = [x["c"] for x in b]; m = ema(c, n); A = O.atr(b); d = [0] * len(b); st = 0
    for i in range(n, len(b)):
        if c[i] > m[i] + k * A[i]: st = 1
        elif c[i] < m[i] - k * A[i]: st = -1
        elif (st > 0 and c[i] < m[i]) or (st < 0 and c[i] > m[i]): st = 0
        d[i] = st
    return d
SIGS = {"HMA55": sig_hma(55), "HMA20": sig_hma(20), "GMMA": sig_gmma, "DMI(ADX>20)": sig_dmi(20), "DMI(ADX>25)": sig_dmi(25), "一目均衡": sig_ichimoku,
        "抛物线SAR": sig_psar, "KAMA": sig_kama, "Aroon25": sig_aroon, "平均K线HA": sig_ha, "滚动VWAP20": sig_vwap(20), "肯特纳突破": sig_keltner}
# ---------- 子腿框架 ----------
s2 = inspect.getsource(O.crypto_8h_leg).replace("def crypto_8h_leg(kind, btc_daily):", "def leg_x(kind, btc_daily, BARSF=None, HRS=8, DIRS=None):")
s2 = s2.replace("vt = VOL_TGT * math.sqrt(8 / 24); P8 = 8 * 3600000", "vt = VOL_TGT * math.sqrt(HRS / 24); P8 = HRS * 3600000")
s2 = s2.replace('b = spot_8h(sym); A = atr(b); D = _st_dirs(b) if kind == "st" else _bb_states(b)', 'b = (BARSF or spot_8h)(sym); A = atr(b); D = DIRS(b) if DIRS else (_st_dirs(b) if kind == "st" else _bb_states(b))')
exec(s2, O.__dict__)
# ---------- 日线腿过滤器 ----------
s1 = inspect.getsource(O.crypto_leg).replace("def crypto_leg():", "def crypto_leg_f():")
old_e = """            elif not pos and want and new_ok:
                pend = ("enter", want,"""
assert s1.count(old_e) == 1
s1 = s1.replace(old_e, """            elif not pos and want and new_ok and FILT(sym, b, i, want):
                pend = ("enter", want,""")
anchor = '''                    order = (-1, lvl, f"{dstr(t)}收盘后挂突破单：明天跌破前{BRK_N}日最低 {lvl:.4g} 就开空(不等EMA8下穿EMA30)；{ph_txt}")'''
if s1.count(anchor) != 1:
    anchor = anchor.replace(":.4g", ":.6g")
assert s1.count(anchor) == 1
s1 = s1.replace(anchor, anchor + """
                if order and not FILT(sym, b, i, order[0]):
                    order = None""")
FC = {}
def mk_filter(kind):
    def f(sym, b, i, side):
        key = (kind, sym)
        if key not in FC:
            if kind == "ADX>20": _, _, a = dmi(b); FC[key] = [x > 20 for x in a]
            elif kind == "放量": v = [x.get("v", 0) or 0 for x in b]; FC[key] = [i_ >= 20 and v[i_] > sum(v[i_-20:i_]) / 20 for i_ in range(len(b))]
            elif kind == "GMMA同向": FC[key] = sig_gmma(b)
            elif kind == "HMA同向": FC[key] = sig_hma(20)(b)
        arr = FC[key]
        if kind in ("GMMA同向", "HMA同向"): return arr[i] == side
        return bool(arr[i])
    return f
O.__dict__["FILT"] = lambda sym, b, i, side: True
exec(s1, O.__dict__)
# ---------- 基准 ----------
cd, _, _, _, CD = O.crypto_leg(); sP, _, _ = O.crypto_8h_leg("st", CD["BTCUSDT"]); bP, _, _ = O.crypto_8h_leg("bb", CD["BTCUSDT"]); sc = O.stock_leg()[0]
def st(cv, a=17410, z=99999):
    ks = [k for k in sorted(cv) if a <= k <= z]; v = [cv[k] for k in ks]; pk = 0; dd = 0
    for x in v: pk = max(pk, x); dd = max(dd, 1 - x / pk)
    yrs = (ks[-1] - ks[0]) / 365.25; cagr = (v[-1] / v[0]) ** (1 / yrs) - 1
    return 100 * cagr, 100 * dd, cagr / dd if dd else 0
H = 18993
f = lambda a: f"{a[0]:+6.1f}%/{a[1]:4.1f}%/{a[2]:5.2f}"
def plan(cB, s_=None):
    F = O.vol_control([cB, s_ or sc], [1, 1], 2.0)[0]; return st(F), st(F, 17410, H)[2], st(F, H)[2]
B0 = plan(O.combine([cd, sP, bP], [.5, .25, .25]))
print(f"基准 现行(日线50+超趋25+布林25)  主方案 {f(B0[0])} 前{B0[1]:.2f}/后{B0[2]:.2f}", flush=True)
for nm, cv in (("日线EMA8/30", cd), ("8h超级趋势", sP), ("8h布林", bP)): print(f"  现有子腿 {nm:10s} {f(st(cv))} 前{st(cv,17410,H)[2]:.2f}/后{st(cv,H)[2]:.2f}", flush=True)
def flag(r): return "  ← 全程+两半都好" if r[0][2] > B0[0][2] and r[1] > B0[1] and r[2] > B0[2] else ""
print("\n==== ① 新指标子腿 ====", flush=True)
daily = lambda s: CD[s]
best = []
for nm, fn in SIGS.items():
    for tf, bf, hrs in (("8h", O.spot_8h, 8), ("日线", daily, 24)):
        try:
            cv, _, _ = O.leg_x("bb", CD["BTCUSDT"], BARSF=bf, HRS=hrs, DIRS=fn)
        except Exception as e:
            print(f"{nm} {tf} 出错 {e}", flush=True); continue
        a = st(cv); line = f"{nm:12s} {tf:3s} 自己 {f(a)} 前{st(cv,17410,H)[2]:.2f}/后{st(cv,H)[2]:.2f}"
        for lab, legs, ws in (("替布林", [cd, sP, cv], [.5, .25, .25]), ("替超趋", [cd, cv, bP], [.5, .25, .25]), ("第4腿", [cd, sP, bP, cv], [.4, .2, .2, .2])):
            r = plan(O.combine(legs, ws)); line += f" | {lab} {r[0][2]:.2f}({r[1]:.2f}/{r[2]:.2f}){'★' if flag(r) else ''}"
            best.append((r[0][2], nm, tf, lab, r))
        print(line, flush=True)
print("\n==== ② 开仓过滤器 ====", flush=True)
for kind in ("ADX>20", "放量", "GMMA同向", "HMA同向"):
    FC.clear(); O.FILT = mk_filter(kind)
    cdf, trf, _, _, _ = O.crypto_leg_f()
    r = plan(O.combine([cdf, sP, bP], [.5, .25, .25]))
    print(f"币圈日线腿 + {kind:8s} 日线腿 {f(st(cdf))} {len(trf)}笔 || 主方案 {f(r[0])} 前{r[1]:.2f}/后{r[2]:.2f}{flag(r)}", flush=True)
O.FILT = lambda sym, b, i, side: True
# 美股过滤
src = inspect.getsource(O.stock_leg).replace("def stock_leg():", "def stock_leg_f():")
old = """            atrp = P["A"][i] / P["b"][i]["c"]; alloc = min(1.0, VOL_TGT / atrp)"""
assert src.count(old) == 1
src = src.replace(old, """            if not SFILT(s, P["b"], i):
                continue
            atrp = P["A"][i] / P["b"][i]["c"]; alloc = min(1.0, VOL_TGT / atrp)""")
SFC = {}
def mk_sfilt(kind):
    def f(s, b, i):
        key = (kind, s)
        if key not in SFC:
            if kind == "ADX>20": SFC[key] = [x > 20 for x in dmi(b)[2]]
            elif kind == "GMMA多头": SFC[key] = [x > 0 for x in sig_gmma(b)]
            elif kind == "HMA向上": SFC[key] = [x > 0 for x in sig_hma(20)(b)]
        return SFC[key][i]
    return f
exec(src, O.__dict__)
for kind in ("ADX>20", "GMMA多头", "HMA向上"):
    SFC.clear(); O.__dict__["SFILT"] = mk_sfilt(kind)
    scf, trs, _, _ = O.stock_leg_f(); r = plan(O.combine([cd, sP, bP], [.5, .25, .25]), scf)
    print(f"美股腿 + {kind:8s} 美股腿 {f(st(scf, 17167))} {len(trs)}笔 || 主方案 {f(r[0])} 前{r[1]:.2f}/后{r[2]:.2f}{flag(r)}", flush=True)
best.sort(key=lambda x: -x[0])
print("\n子腿组合里主方案比最高的5个：" + "；".join(f"{nm}{tf}{lab} {r[0][2]:.2f}({r[1]:.2f}/{r[2]:.2f})" for _, nm, tf, lab, r in best[:5]), flush=True)
print(f"全程+前后两半都好于基准的子腿组合：{sum(1 for x in best if flag(x[4]))}/{len(best)}", flush=True)
print("ALL DONE", flush=True)
