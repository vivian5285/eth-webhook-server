"""2026-10-19 宝贝：存储接下来几年应该还有机会。① 拉 Yahoo 最长 K 线看存储股的历史周期；② SNDK 单独加权重，跟其余美股一起做会不会冲突？
① MU(1984起)/WDC/STX 日线 EMA8/30 只做多 + 3ATR 止损(同现行美股腿规则)，单只全仓，按年代看收益/回撤，以及买入持有对照。
② 加权重(SNDK 历史太短，用 MU 2017 起代替做长周期检验，SNDK 2025 起另看)：
   A 不加杠杆：美股腿里拿出 h 给单只，行业轮动剩 1-h(h=25%/50%)，总仍 2 倍
   B 额外加：行业轮动照旧 1 倍，再加单只 h(25%/50%/100%) → 总杠杆 2.25~3 倍(超出现行 2 倍上限，仅作对照)"""
import sys, os, time, requests
os.nice(19)
sys.path.insert(0, "/home/stratroster/unicorn_quant"); sys.path.insert(0, "/home/stratroster/eth-webhook-server")
exec(open("/tmp/single_stock.py", encoding="utf-8").read().split("cd, _, _, _, CD")[0].split('"""', 2)[2])
def yahoo_max(tk):
    r = requests.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{tk}", params={"period1": 0, "period2": int(time.time()), "interval": "1d"}, headers={"User-Agent": "Mozilla/5.0"}, timeout=30).json()["chart"]["result"][0]
    q = r["indicators"]["quote"][0]; out = []
    for k, t in enumerate(r["timestamp"]):
        if None in (q["open"][k], q["high"][k], q["low"][k], q["close"][k]): continue
        out.append({"t": t * 1000, "o": q["open"][k], "h": q["high"][k], "l": q["low"][k], "c": q["close"][k]})
    return out
print("=== ① 存储股长历史：日线EMA8/30只做多+3ATR止损(单只全仓) vs 买入持有 ===")
for tk in ("MU", "WDC", "STX"):
    b = yahoo_max(tk); cv, n = sim(b); t0 = b[0]["t"] // DAY
    a = st(cv, t0 + 40); bh = {x["t"] // DAY: x["c"] for x in b}; h = st(bh, t0 + 40)
    print(f"\n{tk} 从 {time.strftime('%Y-%m', time.gmtime(b[0]['t']/1000))} 起 {n}笔 | 策略 年化{a[0]:+.1f}% 最大回撤{a[1]:.0f}% 比{a[2]:.2f} | 买入持有 年化{h[0]:+.1f}% 最大回撤{h[1]:.0f}%", flush=True)
    by = {}
    for k in sorted(cv): by.setdefault(time.gmtime(k * 86400).tm_year // 5 * 5, []).append((k, cv[k]))
    row = []
    for y, xs in sorted(by.items()):
        if len(xs) < 200: continue
        d = dict(xs); s_ = st(d, xs[0][0]); row.append(f"{y}-{y+4}: {s_[0]:+.0f}%/年 回撤{s_[1]:.0f}%")
    print("   每5年：" + " | ".join(row), flush=True)
cd, _, _, _, CD = O.crypto_leg(); O.crypto_8h_leg("st", CD["BTCUSDT"]); O.crypto_8h_leg("bb", CD["BTCUSDT"])
coin = {s: O.combine([O.CURVES["日线EMA8/30"][s], O.CURVES["8h超级趋势"][s], O.CURVES["8h布林突破"][s]], [1/3] * 3) for s in O.M5}
cB = O.weighted_basket(coin, O.coin_weights(coin, range(2017, 2027))); sc = O.stock_leg()[0]
mu, _ = sim(O.stock_daily("MU")); sn, _ = sim(O.stock_daily("SNDK")); sn0 = min(sn) + 40
H = 18993
def P(lab, curves, ws, a, extra=""):
    F = O.vol_control(curves, ws, sum(ws))[0]; x = st(F, a)
    hv = f" 前{st(F,17410,H)[2]:.2f}/后{st(F,H)[2]:.2f}" if a == 17410 else ""
    print(f"   {lab:30s} 总{sum(ws):.2f}倍 {x[0]:+7.1f}%/{x[1]:4.1f}%/{x[2]:5.2f}{hv} | {yr(F, a)}", flush=True)
for nm, single, a in (("MU(2017起，完整周期)", mu, 17410), ("SNDK(2025-03起)", sn, sn0)):
    print(f"\n=== ② 给单只加权重：{nm} ===")
    P("现行", [cB, sc], [1, 1], a)
    for h in (0.25, 0.5): P(f"A 轮动{1-h:.2f}+单只{h:.2f}", [cB, sc, single], [1, 1 - h, h], a)
    for h in (0.25, 0.5, 1.0): P(f"B 轮动1+额外单只{h:.2f}", [cB, sc, single], [1, 1, h], a)
print("ALL DONE")
