import json, os
p = "/home/stratroster/unicorn_quant/optimal_data/research.json"; d = json.load(open(p, encoding="utf-8"))
assert not any(x["title"].startswith("MA(简单均线) vs EMA") for x in d), "already added"
R = {(r["kind"], r["f"], r["s"]): r for r in json.load(open("/tmp/ma_vs_ema.json"))}
def row(k, f, s):
    r = R[(k, f, s)]; a = r["F_all"]
    return [f"{k} {f}/{s}", f"{a[0]}%/{a[1]}%/{a[2]}", str(r["F_0322"][2]), f"{r['F_is'][2]}/{r['F_oos'][2]}", f"{r['crypto_0322'][2]}/{r['stock_0322'][2]}"]
rows = [["均线", "全程 年化/回撤/比值", "同窗口比值", "内/外", "币圈腿/美股腿(同窗口)"]]
for f, s in ((8, 30), (5, 20), (10, 30), (8, 40), (12, 40), (10, 50), (15, 60)):
    rows.append(row("EMA", f, s)); rows.append(row("SMA", f, s))
rows.append(row("WMA", 8, 30))
d.append({"date": "2026-10-30", "title": "MA(简单均线) vs EMA(指数均线)：方案F里的8/30换成普通均线会怎样", "verdict": "维持EMA8/30", "tag": "组合",
  "q": "方案F用的是EMA8/30。换成普通均线MA(SMA)或线性加权WMA，或者换周期，会不会更好？",
  "a": "做法：把方案F里所有8/30均线(币圈日线腿、8h两条腿用的BTC日线方向过滤、美股腿进出)统一换掉，其余参数不动，在VPS上只读重跑完整回测。"
       "①同周期下EMA全面优于SMA：7组周期 7/7 都是EMA更高；8/30 时 SMA 只有 2.95(EMA 5.26)，币圈腿从2.38掉到1.42，主要因为SMA对最近价格反应慢、转向晚，趋势末端吐回更多。WMA 8/30 2.82，也不如EMA。"
       "②要提醒的一点：EMA8/30 是个比较尖的峰——相邻周期 10/30 4.73、8/40 4.34、5/20 2.93、12/40 3.85。拆开看，币圈腿对周期不敏感(2.2~2.38)，掉的主要是美股腿(2.26→1.4~1.75)和两腿的配合。"
       "这说明8/30的部分优势来自和其余参数(币权重、布林、突破单等，都是在8/30下调出来的)的共同优化，这正是之前'选择偏差'研究里把实盘预期打折到最保守2.81的原因之一；不是新问题，但不要把5.26当成能复制的精确数字。"
       "结论：MA不如EMA，保持EMA8/30，不改。口径：回测本身的成本口径(0.08%)；同窗口=2022-03..2026-09，内=2022-03..2024，外=2025-2026。脚本 new_sources/ma_vs_ema.py。",
  "rows": rows})
json.dump(d, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
U = "/home/stratroster/unicorn_quant/optimal_data/universe.json"
u = json.load(open(U, encoding="utf-8")); u["research"] = d
json.dump(u, open(U + ".tmp", "w", encoding="utf-8"), ensure_ascii=False); os.replace(U + ".tmp", U)
print(len(d))
