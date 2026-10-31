"""2026-10-31 复核币圈截面残差动量多空(10-06 研究，擂台纸面因风控 risk_budget_full 拦单没真正跑起来)。
规则同擂台 residual_cs_ls：44 个币(BTC 只当因子不交易)，日线；每个币对 BTC 日收益做 120 天回归取残差，过去 20 天残差累加=残差动量；
排序做多前 1/3、做空后 1/3，等权，多空各 0.5 倍净值(总 1 倍)，每 10 天调仓(H5 的 BTC+ETH 版也算)。
成本：换手×0.08%/边 + 真实资金费(币安 fundingRate，多头付正费率、空头收)。
看：2023 起逐月、10-06 研究之后(样本外前进期)、多头腿/空头腿分开。"""
import json, time, urllib.request, os, pickle, statistics as stt, math
os.nice(19)
SYMS = None
src = open("/home/stratroster/eth-webhook-server/strategy_engine/comparison_roster.py", encoding="utf-8").read()
blk = src.split("_RESID_CS_SYMBOLS = [")[1].split("]")[0]
SYMS = [s.strip().strip('"').strip("'") for s in blk.replace("\n", ",").split(",") if s.strip().strip('"').strip("'")]
print("币数", len(SYMS), SYMS[:6], "...")
CACHE = "/root/research/resid_cs_recheck_cache.pkl"
D = pickle.load(open(CACHE, "rb")) if os.path.exists(CACHE) and time.time() - os.path.getmtime(CACHE) < 12 * 3600 else {}
def get(u):
    for k in range(3):
        try: return json.load(urllib.request.urlopen(u, timeout=30))
        except Exception: time.sleep(2 + 2 * k)
    return []
NOW = int(time.time() * 1000); DAY = 86400000; START = 1640995200000   # 2022-01-01
for s in SYMS:
    if ("k", s) in D: continue
    out = []; st = START
    while True:
        r = get(f"https://fapi.binance.com/fapi/v1/klines?symbol={s}&interval=1d&startTime={st}&limit=1000")
        if not r: break
        out += [(x[0] // DAY, float(x[4])) for x in r if x[0] + DAY <= NOW]
        if len(r) < 1000: break
        st = r[-1][0] + 1; time.sleep(0.3)
    D[("k", s)] = dict(out)
    fr = []; st = START
    while True:
        r = get(f"https://fapi.binance.com/fapi/v1/fundingRate?symbol={s}&startTime={st}&limit=1000")
        if not r: break
        fr += [(x["fundingTime"] // DAY, float(x["fundingRate"])) for x in r]
        if len(r) < 1000: break
        st = r[-1]["fundingTime"] + 1; time.sleep(0.3)
    f = {}
    for d, v in fr: f[d] = f.get(d, 0.0) + v
    D[("f", s)] = f
    time.sleep(0.2)
pickle.dump(D, open(CACHE, "wb"))
days = sorted(set().union(*[set(D[("k", s)]) for s in SYMS]))
def ret(s, d):
    k = D[("k", s)]; return (k[d] / k[d - 1] - 1) if d in k and d - 1 in k else None
FEE = 0.0008
def backtest(factors, mom, reg, H, q=0.33):
    tradable = [s for s in SYMS if s not in factors]
    eq = 1.0; w = {}; curve = {}; legL = 1.0; legS = 1.0; n_reb = 0
    for di, d in enumerate(days):
        if di == 0: continue
        # 当天收益(持仓 w 是昨天收盘定的)
        r = 0.0; rl = 0.0; rs = 0.0; fund = 0.0
        for s, wt in w.items():
            x = ret(s, d)
            if x is None: continue
            r += wt * x; fund += -wt * D[("f", s)].get(d, 0.0)       # 多头付正费率，空头收
            if wt > 0: rl += wt * x
            else: rs += wt * x
        eq *= 1 + r + fund; legL *= 1 + rl * 2; legS *= 1 + rs * 2
        curve[d] = eq
        if d % H: continue
        # 调仓：残差动量
        sc = {}
        for s in tradable:
            xs, fs = [], []
            for dd in range(d - reg + 1, d + 1):
                a = ret(s, dd); fb = [ret(f, dd) for f in factors]
                if a is None or None in fb: continue
                xs.append(a); fs.append(sum(fb) / len(fb))
            if len(xs) < reg * 0.8: continue
            mx, mf = stt.mean(xs), stt.mean(fs); vf = sum((f - mf) ** 2 for f in fs)
            beta = sum((x - mx) * (f - mf) for x, f in zip(xs, fs)) / vf if vf else 0
            res = [x - beta * f for x, f in zip(xs, fs)]
            sc[s] = sum(res[-mom:])
        if len(sc) < 10: continue
        rk = sorted(sc, key=sc.get); k = max(1, int(len(rk) * q))
        nw = {s: -0.5 / k for s in rk[:k]}; nw.update({s: 0.5 / k for s in rk[-k:]})
        turn = sum(abs(nw.get(s, 0) - w.get(s, 0)) for s in set(nw) | set(w))
        eq *= 1 - turn * FEE; w = nw; n_reb += 1
    return curve, n_reb
def st(cv, a, z=10**9):
    ks = [k for k in sorted(cv) if a <= k <= z]
    if len(ks) < 5: return None
    v = [cv[k] for k in ks]; pk = 0; dd = 0
    for x in v: pk = max(pk, x); dd = max(dd, 1 - x / pk)
    rs = [v[i] / v[i - 1] - 1 for i in range(1, len(v))]; m = stt.mean(rs); sd = stt.pstdev(rs)
    return 100 * (v[-1] / v[0] - 1), 100 * dd, (m / sd * math.sqrt(365)) if sd else 0
def monthly(cv, a):
    by = {}
    for k in sorted(cv):
        if k >= a: g = time.gmtime(k * 86400); by.setdefault((g.tm_year, g.tm_mon), []).append(cv[k])
    o = []; pe = None
    for m in sorted(by): xs = by[m]; b0 = pe or xs[0]; o.append(f"{m[0] % 100}-{m[1]:02d}:{100 * (xs[-1] / b0 - 1):+.1f}%"); pe = xs[-1]
    return o
dd = lambda s: int(time.mktime(time.strptime(s, "%Y-%m-%d")) // 86400)
for nm, fac, mom, reg, H in (("residual_cs_ls(BTC因子 mom20 reg120 H10)", ["BTCUSDT"], 20, 120, 10), ("residual_cs_ls_btceth(BTC+ETH mom30 reg120 H5)", ["BTCUSDT", "ETHUSDT"], 30, 120, 5)):
    cv, n = backtest(fac, mom, reg, H)
    print(f"\n### {nm}，调仓 {n} 次(含手续费+真实资金费)")
    for lab, a, z in (("2023 全年", dd("2023-01-01"), dd("2023-12-31")), ("2024 全年", dd("2024-01-01"), dd("2024-12-31")), ("2025 全年", dd("2025-01-01"), dd("2025-12-31")),
                      ("2026 至今", dd("2026-01-01"), 10**9), ("研究之前 2023-01~2026-10-05", dd("2023-01-01"), dd("2026-10-05")), ("研究之后 10-06~今天(样本外)", dd("2026-10-06"), 10**9),
                      ("最近 3 个月", dd("2026-08-01"), 10**9)):
        x = st(cv, a, z)
        if x: print(f"  {lab:28s} 收益 {x[0]:+6.1f}%  最大回撤 {x[1]:5.1f}%  夏普 {x[2]:+5.2f}")
    print("  逐月(2025 起)：" + " ".join(monthly(cv, dd("2025-01-01"))))
