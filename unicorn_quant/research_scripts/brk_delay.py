"""突破单延迟成本：回测在盘中触价那一刻按触发价成交；实盘现在要等擂台在该小时K线收盘后检测到，执行器再跟随(约 +2~5 分钟)。
用 exec_study 缓存的永续1分钟K线，算 2024 年以来日线腿的突破进场：触发价 vs 触发所在小时收盘后 3 分钟的价格，差多少 bp(按方向，正=实盘更贵)。"""
import json, os, time
d = json.load(open("/home/stratroster/unicorn_quant/optimal_data/optimal.json", encoding="utf-8"))
C = "/tmp/exec_cache"; out = []
for t in d["trades"]:
    if t["leg"] != "币圈" or t["entry_t"] < 1704067200000 or "盘中触价" not in t["entry_reason"]:
        continue
    p = f"{C}/{t['symbol']}_{t['entry_t']}_1440.json"
    if not os.path.exists(p):
        continue
    m = json.load(open(p)); buy = t["side"] == "LONG"; lvl = t["entry"]
    hit = next((i for i, x in enumerate(m) if (buy and x[2] >= lvl) or ((not buy) and x[3] <= lvl)), None)
    if hit is None:
        continue
    t_hit = m[hit][0]; t_check = (t_hit // 3600000 + 1) * 3600000 + 3 * 60000
    after = next((x for x in m if x[0] >= t_check), None)
    if not after:
        continue
    px = after[1]; bp = ((px / lvl - 1) if buy else (1 - px / lvl)) * 1e4
    out.append((bp, t["symbol"], time.strftime("%Y-%m-%d %H:%M", time.gmtime(t_hit / 1000)), round((t_check - t_hit) / 60000)))
out.sort()
n = len(out); avg = sum(x[0] for x in out) / n
print(f"{n} 次突破进场：等擂台小时收盘再跟，平均多付 {avg:+.1f}bp(中位 {out[n//2][0]:+.1f}bp)，平均晚 {sum(x[3] for x in out)/n:.0f} 分钟")
print("最不利的5次:", [(round(b), s, t) for b, s, t, _ in out[-5:]])
print("最有利的5次:", [(round(b), s, t) for b, s, t, _ in out[:5]])
print(f"每年约 {n/2.8:.0f} 次 × 平均 {avg:.0f}bp × 单腿仓位(≈净值×币权重×1/3) → 对总资金每年约 {n/2.8*avg/1e4*0.2/3*100:.2f}%")
