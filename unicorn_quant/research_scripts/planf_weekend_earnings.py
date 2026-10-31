"""2026-10-31 宝贝：SNDK单票 v3 的两项优化(忽略周末信号、财报前处理)在方案F上有没有用？没有就维持现状并记录。
① 币圈腿忽略周末信号：日线EMA腿(UTC 周六日的日K线收盘不判断，盘中突破单那天也不挂)、8h两腿(收盘在 UTC 周六日的 8h K线不判断)。
   注意：币圈本来就 24×7，周末是真实交易时段，和 SNDK 永续"美股休市时的噪音"性质不同，这里只是验证。
② 美股腿财报：财报日(nasdaq 最近4次 + 往前每 91 天附近找最大跳空、逐季链式推断，近似)。美股腿是日线、收盘判断次日开盘执行，
   盘后公布的财报要在"公布当天开盘"前平掉，所以在公布前一个交易日收盘就判断：
   SE1 下一个交易日是财报日 → 平仓/不开(之后信号仍多再进)；SE4 未来 2 个交易日内有财报 → 不开新仓(已有仓位不动)。
判定同前：全程、前半(2017-09..2021)、后半(2022..)比值都要超过基准才算过关。"""
import sys, os, time, json, inspect, urllib.request, pickle
from datetime import datetime, timezone, timedelta
os.nice(19)
sys.path.insert(0, "/home/stratroster/unicorn_quant"); sys.path.insert(0, "/home/stratroster/eth-webhook-server")
from engine import optimal as O
MODE = {"wk_d1": False, "wk_8h": False, "earn": None}
def _wk_day(t_ms): return datetime.fromtimestamp(t_ms / 1000, timezone.utc).weekday() >= 5
O._MODE = MODE; O._wk_day = _wk_day
# ---- 财报日 ----
EC = "/tmp/planf_earn_cache.json"
E_RAW = json.load(open(EC)) if os.path.exists(EC) else {}
H = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
tks = sorted(O.SEC_OF)
for tk in tks:
    if tk in E_RAW: continue
    try:
        r = json.load(urllib.request.urlopen(urllib.request.Request(f"https://api.nasdaq.com/api/company/{tk}/earnings-surprise", headers=H), timeout=20))
        E_RAW[tk] = [x["dateReported"] for x in r["data"]["earningsSurpriseTable"]["rows"]]
    except Exception:
        E_RAW[tk] = []
    time.sleep(0.4)
json.dump(E_RAW, open(EC, "w"))
EDAYS = {}
n_known = n_inf = 0
for tk in tks:
    known = sorted(datetime.strptime(s, "%m/%d/%Y").date() for s in E_RAW.get(tk, []) if "/" in s)
    if not known: continue
    try: b = O.stock_daily(tk)
    except Exception: continue
    if not b: continue
    dd = [(datetime.fromtimestamp(x["t"] / 1000, timezone.utc).date(), x["o"], x["c"]) for x in b]
    out = set(known); n_known += len(known); anchor = known[0]
    while True:                              # 链式往前推：上一个财报日 ≈ anchor-91天，±12天内找"次日跳空最大"的那天
        guess = anchor - timedelta(days=91); best = None
        for i in range(len(dd) - 1):
            if abs((dd[i][0] - guess).days) <= 12:
                g = abs(dd[i + 1][1] / dd[i][2] - 1)
                if best is None or g > best[0]: best = (g, dd[i][0])
        if not best or best[1] < dd[0][0] + timedelta(days=30): break
        out.add(best[1]); anchor = best[1]; n_inf += 1
    EDAYS[tk] = out
O._EDAYS = EDAYS
print(f"财报日：{len(EDAYS)} 只股票，nasdaq 已知 {n_known} 次 + 推断 {n_inf} 次", flush=True)
def patch(fn, pairs):
    src = inspect.getsource(fn)
    for a, b in pairs:
        assert src.count(a) == 1, (fn.__name__, a); src = src.replace(a, b)
    exec(compile(src, f"<patched {fn.__name__}>", "exec"), O.__dict__)
patch(O.crypto_leg, [("            if i < 32:\n                continue", "            if i < 32 or (_MODE['wk_d1'] and _wk_day(t)):\n                continue")])
patch(O.crypto_8h_leg, [("            if i < 60:\n                continue", "            if i < 60 or (_MODE['wk_8h'] and _wk_day(t + P8)):\n                continue")])
patch(O.stock_leg, [('            raw = P["dir"][i]; want = raw if s in act_set else 0',
                     '            raw = P["dir"][i]; want = raw if s in act_set else 0\n'
                     '            if _MODE["earn"] and want > 0 and s in _EDAYS:\n'
                     '                _nx = [time.gmtime(P["b"][j]["t"] / 1000) for j in range(i + 1, min(i + 3, len(P["b"])))]\n'
                     '                _nd = [__import__("datetime").date(g.tm_year, g.tm_mon, g.tm_mday) for g in _nx]\n'
                     '                if _MODE["earn"] == "SE1" and _nd[:1] and _nd[0] in _EDAYS[s]: want = 0\n'
                     '                if _MODE["earn"] == "SE4" and s not in pos and any(x in _EDAYS[s] for x in _nd[:2]): want = 0')])
Hh = 18993
def st(cv, a=17410, z=99999):
    ks = [k for k in sorted(cv) if a <= k <= z]; v = [cv[k] for k in ks]; pk = 0; dd = 0
    for x in v: pk = max(pk, x); dd = max(dd, 1 - x / pk)
    yrs = (ks[-1] - ks[0]) / 365.25; cagr = (v[-1] / v[0]) ** (1 / yrs) - 1
    return 100 * cagr, 100 * dd, cagr / dd if dd else 0
def yr(cv):
    by = {}
    for k in sorted(cv):
        if k >= 17410: by.setdefault(time.gmtime(k * 86400).tm_year, []).append(cv[k])
    o = []; pe = None
    for y in sorted(by): xs = by[y]; b0 = pe or xs[0]; o.append(f"{y % 100}:{100 * (xs[-1] / b0 - 1):+.0f}"); pe = xs[-1]
    return " ".join(o)
def build():
    _, _, _, _, CD = O.crypto_leg(); d1 = dict(O.CURVES["日线EMA8/30"])
    O.crypto_8h_leg("st", CD["BTCUSDT"]); stc = dict(O.CURVES["8h超级趋势"]); O.crypto_8h_leg("bb", CD["BTCUSDT"]); bbc = dict(O.CURVES["8h布林突破"])
    sc = O.stock_leg()[0]
    coin = {s: O.combine([d1[s], stc[s], bbc[s]], [1 / 3] * 3) for s in O.M5}
    cB = O.weighted_basket(coin, O.coin_weights(coin, range(2017, 2027)))
    return O.vol_control([cB, sc], [1, 1], 2.0)[0], cB, sc
BASE, cB0, sc0 = build(); b0 = (st(BASE)[2], st(BASE, 17410, Hh)[2], st(BASE, Hh)[2])
def show(lab, F, cB, sc):
    a = st(F); s1, s2 = st(F, 17410, Hh)[2], st(F, Hh)[2]; ok = a[2] > b0[0] and s1 > b0[1] and s2 > b0[2]
    print(f"{lab:30s} {a[0]:+6.1f}%/{a[1]:4.1f}%/{a[2]:5.2f} 前{s1:.2f}/后{s2:.2f}{' ★过关' if ok else ''} [币圈腿{st(cB)[2]:.2f} 美股腿{st(sc)[2]:.2f}] | {yr(F)}", flush=True)
show("基准 现行方案F", BASE, cB0, sc0)
for lab, m in (("① 日线EMA腿 忽略周末", {"wk_d1": True}), ("① 8h两腿 忽略周末", {"wk_8h": True}), ("① 币圈三腿都忽略周末", {"wk_d1": True, "wk_8h": True}),
               ("② 美股腿 SE1 财报前平仓", {"earn": "SE1"}), ("② 美股腿 SE4 财报前2天不开仓", {"earn": "SE4"})):
    MODE.update(wk_d1=False, wk_8h=False, earn=None); MODE.update(m)
    F, cB, sc = build(); show(lab, F, cB, sc)
MODE.update(wk_d1=False, wk_8h=False, earn=None)
print("ALL DONE")
