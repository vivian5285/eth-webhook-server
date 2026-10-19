import json, time, os
d = json.load(open("/home/stratroster/unicorn_quant/optimal_data/optimal.json", encoding="utf-8")); T = d["trades"]
C = "/tmp/exec_cache"; rows = []
for t in T:
    if t["leg"] != "美股" or t["entry_t"] < 1775606400000: continue
    for ts, buy in ((t["entry_t"], True), (t.get("exit_t"), False)):
        if not ts: continue
        yb = json.load(open(f"/home/stratroster/unicorn_quant/optimal_data/yf_{t['symbol']}_1d.json"))
        bar = next((x for x in yb if x["t"] // 86400000 == ts // 86400000), None)
        if not bar: continue
        p = f"{C}/{t['symbol']}USDT_{bar['t']}_6.json"
        if not os.path.exists(p): continue
        m = json.load(open(p))
        if not m or m[0][0] != bar["t"]: continue
        bp = ((m[0][1] / bar["o"] - 1) if buy else (1 - m[0][1] / bar["o"])) * 1e4
        rows.append((abs(bp), t["symbol"], time.strftime("%Y-%m-%d %H:%M", time.gmtime(bar["t"] / 1000)), time.strftime("%H:%M", time.gmtime(m[0][0] / 1000)), bar["o"], m[0][1], round(bp)))
rows.sort(reverse=True)
for r in rows[:12]: print(r[1:])
print("n", len(rows), "中位|bp|", sorted(r[0] for r in rows)[len(rows)//2])
