"""2026-10-19 实盘执行研究：回测按"现货开盘价 + 每边0.08%"成交；实盘在币安永续上执行。用永续 1 分钟K线逐笔复盘 2024 年以来回测的每次成交：
A 普通信号(收盘出信号、下一根开盘成交)：
   市价：永续该分钟开盘价 + 1bp 半个价差，吃单 0.05%
   限价优先 N 分钟(N=1/5/15)：挂在该分钟开盘价(买单挂买一、卖单挂卖一)，N 分钟内有成交价穿过挂单价才算成交(保守，要求价格"穿过"而不是"碰到")，挂单 0.02%；
       没成交就第 N 分钟按市价补(吃单 0.05%)
   成本(bp) = 相对回测假设价(现货开盘价)的价差 + 手续费；回测假设是 8bp。拆成"基差(永续开盘-现货开盘)"和"执行"两部分
B 突破单/止损单(盘中触价)：永续 1 分钟K线里第一次触价那一分钟，按 max(该分钟开盘, 触发价)+1bp 成交(止损市价单)，吃单 0.05%
C 美股(币安美股永续 2026-04-07 上线以后的成交)：回测按正股开盘价；实盘在永续上，比较 正股开盘时刻的永续价格 vs 正股开盘价
全部只读公开行情，不碰任何账户。"""
import sys, os, json, time, urllib.request, collections
os.nice(19)
CACHE = "/tmp/exec_cache"; os.makedirs(CACHE, exist_ok=True)
d = json.load(open("/home/stratroster/unicorn_quant/optimal_data/optimal.json", encoding="utf-8")); T = d["trades"]
T0 = 1704067200000          # 2024-01-01
def k1m(sym, start, n):
    p = f"{CACHE}/{sym}_{start}_{n}.json"
    if os.path.exists(p): return json.load(open(p))
    for a in range(4):
        try:
            r = json.load(urllib.request.urlopen(f"https://fapi.binance.com/fapi/v1/klines?symbol={sym}&interval=1m&startTime={start}&limit={n}", timeout=20)); break
        except Exception as e:
            time.sleep(2 + 3 * a); r = None
    if r is None: return []
    out = [(int(x[0]), float(x[1]), float(x[2]), float(x[3]), float(x[4])) for x in r]
    json.dump(out, open(p, "w")); time.sleep(0.25); return out
def cost_bp(side_buy, fill, ref, fee):
    return ((fill / ref - 1) if side_buy else (1 - fill / ref)) * 1e4 + fee * 1e4
FILLS = []      # (kind, sym, t, buy, ref_px, extra)
for t in T:
    if t["leg"] != "币圈" or t["entry_t"] < T0: continue
    buy_in = t["side"] == "LONG"
    if t.get("brk") or ("止损" in (t.get("exit_reason") or "") and False):
        pass
    FILLS.append(("brk" if "盘中触价" in t["entry_reason"] else "open", t["symbol"], t["entry_t"], buy_in, t["entry"], t))
    if t.get("exit_t"):
        FILLS.append(("stop" if "止损" in (t.get("exit_reason") or "") else "open", t["symbol"], t["exit_t"], not buy_in, t["exit"], t))
print("币圈成交", collections.Counter(f[0] for f in FILLS), flush=True)
res = collections.defaultdict(list); basis = []
for kind, sym, ts, buy, ref, tr in FILLS:
    if kind == "open":
        m = k1m(sym, ts, 20)
        if len(m) < 16 or m[0][0] != ts: continue
        o0 = m[0][1]; basis.append(((o0 / ref - 1) if buy else (1 - o0 / ref)) * 1e4)
        res["市价"].append(cost_bp(buy, o0 * (1 + (1e-4 if buy else -1e-4)), ref, 0.0005))
        for N in (1, 5, 15):
            filled = any((x[3] < o0) if buy else (x[2] > o0) for x in m[:N])
            if filled: res[f"限价优先{N}分钟"].append(cost_bp(buy, o0, ref, 0.0002)); res[f"_成交率{N}"].append(1)
            else:
                px = m[N][1] * (1 + (1e-4 if buy else -1e-4)); res[f"限价优先{N}分钟"].append(cost_bp(buy, px, ref, 0.0005)); res[f"_成交率{N}"].append(0)
    else:   # 盘中触价：找当天第一次触价的那一分钟
        day0 = ts; lvl = ref
        mm = k1m(sym, day0, 1440 if (tr.get("sub") == "日线EMA8/30") else 480)
        hit = None
        for x in mm:
            if (buy and x[2] >= lvl) or ((not buy) and x[3] <= lvl): hit = x; break
        if not hit: res[f"_{kind}_没触价"].append(1); continue
        px = (max(hit[1], lvl) * 1.0001) if buy else (min(hit[1], lvl) * 0.9999)
        res[f"触价单({kind})"].append(cost_bp(buy, px, ref, 0.0005))
def mean(x): return sum(x) / len(x) if x else float("nan")
def pct(x, q): s = sorted(x); return s[int(q * (len(s) - 1))] if s else float("nan")
print(f"\n=== 币圈普通信号(下一根开盘成交) {len(basis)} 次，相对回测假设价的成本(bp，越小越好；回测假设 8bp) ===")
print(f"  其中 基差(永续开盘 vs 现货开盘，按买卖方向算)：平均 {mean(basis):+.2f}bp，中位 {pct(basis,.5):+.2f}bp，90%区间 {pct(basis,.05):+.1f}~{pct(basis,.95):+.1f}bp")
for k in ("市价", "限价优先1分钟", "限价优先5分钟", "限价优先15分钟"):
    fr = mean(res.get('_成交率' + k.replace('限价优先', '').replace('分钟', ''), [])) if '限价' in k else None
    print(f"  {k:12s} 平均 {mean(res[k]):+6.2f}bp  中位 {pct(res[k],.5):+6.2f}bp  差的5% {pct(res[k],.95):+6.1f}bp" + (f"  挂单成交率 {100*fr:.0f}%" if fr is not None else ""))
print("\n=== 币圈盘中触价单(突破进场 / 止损) ===")
for k in [k for k in res if k.startswith("触价单")]:
    print(f"  {k:14s} {len(res[k])}次 平均 {mean(res[k]):+6.2f}bp 中位 {pct(res[k],.5):+6.2f}bp 差的5% {pct(res[k],.95):+6.1f}bp")
for k in [k for k in res if k.startswith("_") and "没触价" in k]: print(f"  {k} {len(res[k])}次(永续当天没触到回测的价位)")
# ---- C 美股 ----
print("\n=== 美股：正股开盘价(回测) vs 同一时刻的币安美股永续 ===")
SB = []
for t in T:
    if t["leg"] != "美股" or t["entry_t"] < 1775606400000: continue
    for ts, px, buy in ((t["entry_t"], t["entry"], True), (t.get("exit_t"), t.get("exit"), False)):
        if not ts or "止损" in (t.get("exit_reason") or "") and not buy: continue
        yb = json.load(open(f"/home/stratroster/unicorn_quant/optimal_data/yf_{t['symbol']}_1d.json"))
        bar = next((x for x in yb if x["t"] // 86400000 == ts // 86400000), None)
        if not bar: continue
        m = k1m(t["symbol"] + "USDT", bar["t"], 6)
        if not m: continue
        SB.append(((m[0][1] / bar["o"] - 1) if buy else (1 - m[0][1] / bar["o"])) * 1e4)
print(f"  {len(SB)} 次：永续在正股开盘那一分钟的价格，相对正股开盘价(按买卖方向) 平均 {mean(SB):+.1f}bp 中位 {pct(SB,.5):+.1f}bp 90%区间 {pct(SB,.05):+.0f}~{pct(SB,.95):+.0f}bp")
print("ALL DONE")
