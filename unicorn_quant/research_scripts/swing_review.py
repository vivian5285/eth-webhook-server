"""2026-10-19 波段复盘：用 optimal.json 里的真实回测成交，逐个大波段看抓到多少。
波段 = 日线收盘 zigzag(币圈 ≥30%，美股 ≥25%)，且 ≥15 天。
每个波段：顺方向的第一笔进场 晚了几天/错过了波段的百分之几；离场 回吐了百分之几；
抓取率 = 窗口内所有顺方向成交的对数收益之和(按子腿权重) ÷ 波段对数幅度(包含波段里被来回打的亏损)。
没抓到的波段，标原因：减半周期方向限制 / BTC方向过滤 / 当年权重0 / 美股板块没入选 / 均线没给信号。"""
import sys, os, json, math, time, collections
os.nice(19)
sys.path.insert(0, "/home/stratroster/unicorn_quant")
from engine import optimal as O
OD = "/home/stratroster/unicorn_quant/optimal_data"
d = json.load(open(f"{OD}/optimal.json", encoding="utf-8")); T = d["trades"]; WY = d["crypto_weights"]
DAY = 86400000; D = lambda t: time.strftime("%Y-%m-%d", time.gmtime(t / 1000))
def bars(sym):
    b = json.load(open(f"{OD}/bars_{sym}.json")); return [(x[0], x[4]) for x in b]
def zigzag(b, th, mind=15):
    hi = lo = 0; dirn = 0; piv = []; ext = 0
    for i, (t, c) in enumerate(b):
        if dirn == 0:
            if c > b[hi][1]: hi = i
            if c < b[lo][1]: lo = i
            if c >= b[lo][1] * (1 + th): piv.append(lo); dirn = 1; ext = i
            elif c <= b[hi][1] * (1 - th): piv.append(hi); dirn = -1; ext = i
            continue
        if dirn == 1:
            if c > b[ext][1]: ext = i
            elif c <= b[ext][1] * (1 - th): piv.append(ext); dirn = -1; ext = i
        else:
            if c < b[ext][1]: ext = i
            elif c >= b[ext][1] * (1 + th): piv.append(ext); dirn = 1; ext = i
    piv.append(ext); piv = [(i, b[i][1]) for i in piv]
    out = []
    for (i0, p0), (i1, p1) in zip(piv[:-1], piv[1:]):
        if (b[i1][0] - b[i0][0]) / DAY >= mind: out.append((i0, i1, "LONG" if p1 > p0 else "SHORT"))
    return out
def review(sym, legs, th, t_from):
    b = bars(sym); tix = {t: i for i, (t, _) in enumerate(b)}; rows = []
    tr = [t for t in T if t["symbol"] == sym and (t.get("sub") or "美股") in legs]
    for i0, i1, side in zigzag(b, th):
        t0, t1, p0, p1 = b[i0][0], b[i1][0], b[i0][1], b[i1][1]
        if t0 < t_from: continue
        amp = abs(math.log(p1 / p0)); sg = 1 if side == "LONG" else -1
        r = {"sym": sym, "side": side, "t0": t0, "t1": t1, "move": 100 * (p1 / p0 - 1), "days": (t1 - t0) / DAY, "cap": 0.0, "legs": {}}
        for lg, w in legs.items():
            ins = [t for t in tr if (t.get("sub") or "美股") == lg and t["side"] == side and t0 - 10 * DAY <= t["entry_t"] <= t1]
            if not ins: r["legs"][lg] = None; continue
            ex = lambda t: t["exit"]
            cap = sum(sg * math.log(ex(t) / t["entry"]) for t in ins) / amp
            f = min(ins, key=lambda t: t["entry_t"]); l = max(ins, key=lambda t: t["entry_t"])
            miss = sg * math.log(f["entry"] / p0) / amp
            give = sg * math.log(p1 / ex(l)) / amp if (l["exit_t"] or 9e15) >= t1 - 5 * DAY else None
            r["legs"][lg] = {"cap": cap, "lag": (f["entry_t"] - t0) / DAY, "miss": miss, "give": give, "n": len(ins), "first": f}
            r["cap"] += w * cap
        rows.append(r)
    return rows
def why_crypto(sym, r):
    ph, mo = O.phase_of(r["t0"] + 5 * DAY)
    y = str(time.gmtime(r["t0"] / 1000).tm_year)
    if not WY.get(y, {}).get(sym): return "当年权重0(币种没入选)"
    if ph == "牛" and r["side"] == "SHORT": return "牛市阶段只做多"
    if ph == "熊" and r["side"] == "LONG": return "熊市阶段只做空"
    return "均线/过滤器没给信号"
CL = {"日线EMA8/30": .5, "8h超级趋势": .25, "8h布林突破": .25}
pct = lambda x: "  -- " if x is None else f"{100*x:4.0f}%"
allc = []
print("================ 币圈(日线EMA 50% + 8h超趋 25% + 8h布林 25%) 波段 ≥30% ================")
for s in O.M5:
    rs = review(s, CL, 0.30, 1504224000000); allc += rs
    print(f"\n--- {s} ---  共{len(rs)}个波段")
    for r in rs:
        dl = r["legs"]["日线EMA8/30"]
        tag = (f"日线: 晚{dl['lag']:3.0f}天 错过开头{pct(dl['miss'])} 回吐{pct(dl['give'])} 抓{pct(dl['cap'])}({dl['n']}笔)" if dl else f"日线: 没做 ← {why_crypto(s, r)}")
        sub = " ".join(f"{k[:4]}{pct(v['cap']) if v else ' 无 '}" for k, v in r["legs"].items() if k != "日线EMA8/30")
        print(f"{D(r['t0'])}→{D(r['t1'])} {'涨' if r['side']=='LONG' else '跌'}{r['move']:+5.0f}% {r['days']:4.0f}天 | 合计抓{pct(r['cap'])} | {tag} | {sub}")
print("\n================ 币圈汇总 ================")
for side in ("LONG", "SHORT"):
    rs = [r for r in allc if r["side"] == side]; hit = [r for r in rs if r["legs"]["日线EMA8/30"]]
    med = lambda xs: sorted(xs)[len(xs) // 2] if xs else float("nan")
    why = collections.Counter(why_crypto(r["sym"], r) for r in rs if not r["legs"]["日线EMA8/30"])
    print(f"{'上涨' if side=='LONG' else '下跌'}波段 {len(rs)} 个：日线腿做了 {len(hit)} 个；没做的原因 {dict(why)}")
    if hit:
        print(f"   做了的：中位 晚{med([r['legs']['日线EMA8/30']['lag'] for r in hit]):.0f}天，错过开头 {100*med([r['legs']['日线EMA8/30']['miss'] for r in hit]):.0f}%，"
              f"回吐 {100*med([r['legs']['日线EMA8/30']['give'] for r in hit if r['legs']['日线EMA8/30']['give'] is not None]):.0f}%，"
              f"日线抓 {100*med([r['legs']['日线EMA8/30']['cap'] for r in hit]):.0f}%，三腿合计抓 {100*med([r['cap'] for r in hit]):.0f}%")
print("\n================ 美股(板块轮动入选期间才做) 上涨波段 ≥25% (2020起) ================")
stk = sorted({t["symbol"] for t in T if t["leg"] == "美股"}); sr = []
for s in stk:
    try: rs = [r for r in review(s, {"美股": 1.0}, 0.25, 1577836800000) if r["side"] == "LONG"]
    except FileNotFoundError: continue
    sr += rs
held = [r for r in sr if r["legs"]["美股"]]
print(f"{len(stk)}只股票，2020起上涨波段 {len(sr)} 个；做了 {len(held)} 个(其余多半是当时板块没入选)")
med = lambda xs: sorted(xs)[len(xs) // 2] if xs else float("nan")
L = [r["legs"]["美股"] for r in held]
print(f"做了的：中位 晚{med([x['lag'] for x in L]):.0f}天，错过开头 {100*med([x['miss'] for x in L]):.0f}%，回吐 {100*med([x['give'] for x in L if x['give'] is not None]):.0f}%，抓 {100*med([x['cap'] for x in L]):.0f}%，平均每个波段 {med([x['n'] for x in L])} 笔")
big = sorted(sr, key=lambda r: -r["move"])[:25]
print("\n涨幅最大的 25 个波段：")
for r in big:
    x = r["legs"]["美股"]
    print(f"  {r['sym']:5s} {D(r['t0'])}→{D(r['t1'])} {r['move']:+5.0f}% {r['days']:4.0f}天 | " +
          (f"晚{x['lag']:3.0f}天 错过开头{pct(x['miss'])} 回吐{pct(x['give'])} 抓{pct(x['cap'])}({x['n']}笔) 首笔{x['first'].get('sector','')}" if x else "没做(板块没入选或没信号)"))
json.dump({"crypto": [{k: v for k, v in r.items() if k != "legs"} | {"daily": (r["legs"]["日线EMA8/30"] or {}).get("cap")} for r in allc]}, open("/tmp/swing_review.json", "w"), default=str)
print("ALL DONE")
