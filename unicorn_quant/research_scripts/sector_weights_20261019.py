"""2026-10-19 美股腿按行业板块分配资金(其余规则一字不改)：
cur 现行：每只股票 E×alloc÷max(当月可做只数,5)
secEq 行业等权：所选5个行业各20%，行业内股票平分
rank  按排名：30/25/20/15/10
mom   按过去6个月行业涨幅成比例(单行业≤40%)
track 按过去3年"策略在该行业股票上"的平均收益回撤比(同币圈稳健版思路，单行业≤40%，每年1月1日定)
单只股票 ≤ 总资金20%；美股总仓位 ≤ 1倍。币圈用已采纳的新口径。"""
import sys, os, time, inspect, math, calendar
os.nice(19)
sys.path.insert(0, "/home/stratroster/unicorn_quant"); sys.path.insert(0, "/home/stratroster/eth-webhook-server")
from engine import optimal as O
from engine.universe import solo_stock
DAY = 86400000
src = inspect.getsource(O.stock_leg).replace("def stock_leg():", "def stock_leg_x():")
old = """            notional = E * alloc / max(k, MIN_SLOTS)"""
assert src.count(old) == 1
src = src.replace(old, """            notional = E * alloc * SECW(m, secs, s, act_set, k, scores_by_m)""")
O.__dict__["MODE"] = "cur"
def _capw(raw, cap):
    w = {s: x for s, x in raw.items() if x > 0}
    if not w: return {}
    for _ in range(20):
        t_ = sum(w.values()); w = {s: x / t_ for s, x in w.items()}
        over = [s for s, x in w.items() if x > cap + 1e-9]
        if not over: break
        rest = {s: x for s, x in w.items() if s not in over}; rt = sum(rest.values()); left = 1 - cap * len(over)
        w = {**{s: cap for s in over}, **({s: x / rt * left for s, x in rest.items()} if rt > 0 else {})}
    return w
# track：每年1月1日，各行业"策略单股曲线"过去3年收益回撤比的平均
SOLO = {}
for tk in O.SEC_OF:
    try:
        b = O.stock_daily(tk)
        if len(b) < 300: continue
        c = [x["c"] for x in b]; e8, e30 = O.ema(c, 8), O.ema(c, 30)
        rows = [[x["t"], x["o"], x["h"], x["l"], x["c"], e8[i], e30[i]] for i, x in enumerate(b)]
        SOLO[tk] = dict(solo_stock(rows)[0])
    except Exception as e:
        print("skip", tk, e)
def wr(cv, a, z):
    ks = [k for k in sorted(cv) if a <= k <= z]
    if len(ks) < 250: return None
    v = [cv[k] for k in ks]; pk = 0; dd = 0
    for x in v: pk = max(pk, x); dd = max(dd, 1 - x / pk)
    yrs = (ks[-1] - ks[0]) / 365.25; cg = (v[-1] / v[0]) ** (1 / yrs) - 1
    return cg / dd if dd else None
TRACK = {}
for y in range(2017, 2027):
    y0 = calendar.timegm((y, 1, 1, 0, 0, 0)) // 86400
    for sec, lst in O.SECTORS.items():
        rs = [wr(SOLO[t], y0 - 1095, y0 - 1) for t in lst if t in SOLO]; rs = [r for r in rs if r is not None]
        TRACK[(y, sec)] = sum(rs) / len(rs) if rs else 0.0
def SECW(m, secs, s, act_set, k, scores_by_m):
    MODE = O.MODE
    if MODE == "cur" or not secs: return 1 / max(k, O.MIN_SLOTS)
    sec = O.SEC_OF[s]
    if sec not in secs: return 1 / max(k, O.MIN_SLOTS)
    if MODE == "secEq": w = {x: 1 / len(secs) for x in secs}
    elif MODE == "rank":
        base = [.30, .25, .20, .15, .10][:len(secs)]; t_ = sum(base); w = {x: base[i] / t_ for i, x in enumerate(secs)}
    elif MODE == "mom":
        sc = dict(scores_by_m.get(m, [])); w = _capw({x: max(0.0, sc.get(x, 0)) for x in secs}, 0.4) or {x: 1 / len(secs) for x in secs}
    else:
        w = _capw({x: max(0.0, TRACK.get((m[0], x), 0)) for x in secs}, 0.4) or {x: 1 / len(secs) for x in secs}
    n_s = sum(1 for t in act_set if O.SEC_OF[t] == sec) or 1
    return min(0.2, w.get(sec, 0) / n_s)
O.__dict__["SECW"] = SECW; O.__dict__["MODE"] = "cur"
exec(src, O.__dict__)
# 币圈：已采纳的新口径
CD = O.crypto_leg()[4]; O.crypto_8h_leg("st", CD["BTCUSDT"]); O.crypto_8h_leg("bb", CD["BTCUSDT"])
coin_cv = {s: O.combine([O.CURVES["日线EMA8/30"][s], O.CURVES["8h超级趋势"][s], O.CURVES["8h布林突破"][s]], [.5, .25, .25]) for s in O.M5}
cB = O.weighted_basket(coin_cv, O.coin_weights(coin_cv, range(2017, 2027)))
def st(cv, a=17410, z=99999):
    ks = [k for k in sorted(cv) if a <= k <= z]; v = [cv[k] for k in ks]; pk = 0; dd = 0
    for x in v: pk = max(pk, x); dd = max(dd, 1 - x / pk)
    yrs = (ks[-1] - ks[0]) / 365.25; cagr = (v[-1] / v[0]) ** (1 / yrs) - 1
    return 100 * cagr, 100 * dd, cagr / dd if dd else 0
def yearly(cv, a=17410):
    by = {}
    for k in sorted(cv):
        if k < a: continue
        by.setdefault(time.gmtime(k * 86400).tm_year, []).append(cv[k])
    out = {}; pe = None
    for y in sorted(by):
        xs = by[y]; b0 = pe or xs[0]; out[y] = 100 * (xs[-1] / b0 - 1); pe = xs[-1]
    return out
f = lambda a: f"{a[0]:+6.1f}%/{a[1]:4.1f}%/{a[2]:5.2f}"
H = 18993; RES = {}
LAB = {"cur": "现行(每只股平分)", "secEq": "行业等权(各20%)", "rank": "按排名 30/25/20/15/10", "mom": "按行业6月涨幅(≤40%)", "track": "按过去3年策略表现(≤40%)"}
for mode in ("cur", "secEq", "rank", "mom", "track"):
    O.MODE = mode; sc, tr, _, _ = O.stock_leg_x(); F = O.vol_control([cB, sc], [1, 1], 2.0)[0]; RES[mode] = (sc, F)
    print(f"{LAB[mode]:26s} 美股腿 {f(st(sc, 17167))} 前{st(sc,17167,H)[2]:.2f}/后{st(sc,H)[2]:.2f} {len(tr)}笔 || 主方案 {f(st(F))} 前{st(F,17410,H)[2]:.2f}/后{st(F,H)[2]:.2f}", flush=True)
ys = sorted(yearly(RES["cur"][1]))
print("\n主方案逐年收益：" + " ".join(f"{y:>7d}" for y in ys), flush=True)
for mode, (sc, F) in RES.items():
    yy = yearly(F); print(f"{LAB[mode]:26s} " + " ".join(f"{yy[y]:+6.0f}%" for y in ys), flush=True)
c0 = yearly(RES["cur"][1])
for mode in ("secEq", "rank", "mom", "track"):
    yy = yearly(RES[mode][1]); print(f"  {LAB[mode]} 比现行收益高的年份 {sum(1 for y in ys if yy[y] > c0[y])}/{len(ys)}", flush=True)
print("ALL DONE", flush=True)
