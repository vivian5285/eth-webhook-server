"""2026-10-19 宝贝：存储降温 SNDK 也能做空——90m/2h 多空 1倍 有没有正收益？
数据：① 币安 SNDK/MU 永续 30m(2026-04 起，24小时) 合成 90m/2h；② Yahoo 1h 正股(近2年，常规时段) 合成 2h；SNDK 2025-02 上市，MU/WDC/STX 拿来看'存储降温'那段(2024 下半年~2025-04)空单行不行。
策略：A=EMA8/30 金叉多死叉空、始终在场；B=宝贝的 TV SNDK 源码(EMA7/30+5根突破+实体过滤+动量豁免，收盘跌破双线离场，反向信号反手，2.5×ATR 固定止损)。
成交：信号K线收盘 → 下一根开盘(实盘口径)；另给 TV 口径(信号K线收盘价成交)对照。手续费 0.05%/边(币安吃单)。
记账：每笔固定 1 倍名义(不复利)，多单/空单分开统计；同时给复利净值的回撤。"""
import json, time, urllib.request, os
os.nice(19)
def get(u):
    return json.load(urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0"}), timeout=30))
def perp30(sym):
    out = []; s = 1775520000000
    while True:
        r = get(f"https://fapi.binance.com/fapi/v1/klines?symbol={sym}&interval=30m&startTime={s}&limit=1500")
        if not r: break
        out += [{"t": x[0], "o": float(x[1]), "h": float(x[2]), "l": float(x[3]), "c": float(x[4]), "v": float(x[5])} for x in r]
        if len(r) < 1500: break
        s = r[-1][0] + 1; time.sleep(0.3)
    return [x for x in out if x["v"] > 0]
def yh1h(tk):
    d = get(f"https://query1.finance.yahoo.com/v8/finance/chart/{tk}?interval=60m&range=730d")["chart"]["result"][0]
    q = d["indicators"]["quote"][0]; out = []
    for i, t in enumerate(d["timestamp"]):
        if None in (q["open"][i], q["high"][i], q["low"][i], q["close"][i]): continue
        out.append({"t": t * 1000, "o": q["open"][i], "h": q["high"][i], "l": q["low"][i], "c": q["close"][i], "v": q["volume"][i] or 0})
    return out
def agg(b, n, by_day=False):
    out = []; grp = []
    for x in b:
        if grp and ((by_day and time.gmtime(x["t"]/1000)[:3] != time.gmtime(grp[0]["t"]/1000)[:3]) or len(grp) == n):
            out.append(grp); grp = []
        if not by_day and not grp and (x["t"] // 1800000) % n:   # 对齐整点周期
            continue
        grp.append(x)
    if grp: out.append(grp)
    return [{"t": g[0]["t"], "o": g[0]["o"], "h": max(y["h"] for y in g), "l": min(y["l"] for y in g), "c": g[-1]["c"]} for g in out]
def ema(c, n):
    k = 2 / (n + 1); o = [c[0]]
    for x in c[1:]: o.append(o[-1] + k * (x - o[-1]))
    return o
def atr(b, n=14):
    tr = [b[0]["h"] - b[0]["l"]] + [max(b[i]["h"] - b[i]["l"], abs(b[i]["h"] - b[i-1]["c"]), abs(b[i]["l"] - b[i-1]["c"])) for i in range(1, len(b))]
    o = [tr[0]]
    for x in tr[1:]: o.append(o[-1] + (x - o[-1]) / n)
    return o
FEE = 0.0005
def run(b, strat, tvfill=False):
    c = [x["c"] for x in b]; A = atr(b)
    if strat == "A": f, s = ema(c, 8), ema(c, 30)
    else: f, s = ema(c, 7), ema(c, 30)
    pos = 0; ent = 0; stop = None; trades = []; pend = None; eq = 1.0; pk = 1.0; dd = 0
    def close(px, why):
        nonlocal pos
        r = pos * (px / ent - 1) - 2 * FEE; trades.append((pos, r, why)); pos = 0
    for i in range(31, len(b)):
        x = b[i]
        if pend is not None and not tvfill:
            tgt, stp = pend; pend = None
            if pos and tgt != pos: close(x["o"], "sig")
            if tgt and not pos: pos = tgt; ent = x["o"]; stop = (x["o"] - pos * stp) if stp else None
        if pos and stop is not None and ((pos > 0 and x["l"] <= stop) or (pos < 0 and x["h"] >= stop)):
            px = min(x["o"], stop) if pos > 0 else max(x["o"], stop); close(px, "stop")
        # 收盘判断信号
        if strat == "A":
            tgt = 1 if f[i] > s[i] else -1; stp = None
            if tgt == pos: tgt = None
        else:
            o_, cl = x["o"], x["c"]; body = abs(cl - o_); a = A[i]
            hh = max(y["h"] for y in b[i-5:i]); ll = min(y["l"] for y in b[i-5:i])
            vb = body >= 0.2 * a; mom = body >= 1.5 * a
            stdL = f[i] > s[i] and f[i] > f[i-1] and cl > o_ and min(o_, cl) > max(f[i], s[i]) and vb and cl > hh
            stdS = f[i] < s[i] and f[i] < f[i-1] and cl < o_ and max(o_, cl) < min(f[i], s[i]) and vb and cl < ll
            momL = cl > o_ and mom and cl > max(f[i], s[i]) and cl > hh
            momS = cl < o_ and mom and cl < min(f[i], s[i]) and cl < ll
            tgt = None; stp = None
            if (stdL or momL) and pos <= 0: tgt = 1; stp = 2.5 * a
            elif (stdS or momS) and pos >= 0: tgt = -1; stp = 2.5 * a
            elif pos > 0 and cl < min(f[i], s[i]): tgt = 0
            elif pos < 0 and cl > max(f[i], s[i]): tgt = 0
        if tgt is not None:
            if tvfill:
                if pos and tgt != pos: close(x["c"], "sig")
                if tgt and not pos: pos = tgt; ent = x["c"]; stop = (x["c"] - pos * stp) if stp else None
            else: pend = (tgt, stp)
    if pos: close(b[-1]["c"], "end")
    for _, r, _ in trades:
        eq *= 1 + r; pk = max(pk, eq); dd = max(dd, 1 - eq / pk)
    L = [r for p, r, _ in trades if p > 0]; S = [r for p, r, _ in trades if p < 0]
    return {"n": len(trades), "sum": 100 * sum(r for _, r, _ in trades), "comp": 100 * (eq - 1), "dd": 100 * dd,
            "L": (len(L), 100 * sum(L), 100 * sum(1 for r in L if r > 0) / max(1, len(L))),
            "S": (len(S), 100 * sum(S), 100 * sum(1 for r in S if r > 0) / max(1, len(S)))}
def show(tag, b):
    d0, d1 = time.strftime("%Y-%m-%d", time.gmtime(b[0]["t"]/1000)), time.strftime("%Y-%m-%d", time.gmtime(b[-1]["t"]/1000))
    bh = 100 * (b[-1]["c"] / b[31]["o"] - 1)
    print(f"\n## {tag}  {d0}~{d1}  {len(b)}根  买入持有 {bh:+.0f}%")
    for st in ("A", "B"):
        for tv in (False, True):
            r = run(b, st, tv)
            nm = ("A EMA8/30多空" if st == "A" else "B TV源码") + (" TV口径" if tv else " 下根开盘")
            print(f"  {nm:20s} {r['n']:3d}笔 单利合计{r['sum']:+7.0f}% 复利{r['comp']:+8.0f}% 回撤{r['dd']:4.0f}% | 多{r['L'][0]:3d}笔 {r['L'][1]:+6.0f}% 胜{r['L'][2]:3.0f}% | 空{r['S'][0]:3d}笔 {r['S'][1]:+6.0f}% 胜{r['S'][2]:3.0f}%", flush=True)
for sym in ("SNDKUSDT", "MUUSDT"):
    b = perp30(sym)
    show(f"币安永续 {sym} 90m", agg(b, 3)); show(f"币安永续 {sym} 2h", agg(b, 4))
for tk in ("SNDK", "MU", "WDC", "STX"):
    try:
        b = yh1h(tk); show(f"Yahoo 正股 {tk} 2h(常规时段)", agg(b, 2, by_day=True))
        if tk != "SNDK":
            # 存储降温段：2024-07 ~ 2025-04
            seg = [x for x in b if 1719792000000 <= x["t"] <= 1745971200000]
            if seg: show(f"Yahoo 正股 {tk} 2h 降温段 2024-07~2025-04", agg(seg, 2, by_day=True))
    except Exception as e:
        print(tk, "失败", e)
print("ALL DONE")
