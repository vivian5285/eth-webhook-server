"""2026-10-19 弱点④选币偏差：完全机械的选币规则，事先定好：
每年 1 月 1 日，在候选池里按"过去 365 天币安现货成交额(成交量×收盘价)"排名，取前 5(排除稳定币)；
版本 a：直接取前 5；版本 b：只在上市满 2 年的币里取(避免刚上线就爆量的新币)。
候选池 18 个(当年在币安有现货的才参与)：BTC ETH BNB SOL XRP BCH LTC EOS ADA DOGE LINK DOT TRX AVAX ETC XLM MATIC SHIB。
币池内仍按"过去3年比值、单币≤40%"定权重，其余规则完全不变。对比：现行固定主流5、每年市值前5。"""
import sys, os, time, io, contextlib, calendar
os.nice(19)
sys.path.insert(0, "/home/stratroster/unicorn_quant")
src = open("/tmp/selection_bias.py", encoding="utf-8").read().split("# ---- 基准(现行) ----")[0]
exec(src)
for s in ("LINKUSDT", "DOTUSDT", "TRXUSDT", "AVAXUSDT", "ETCUSDT", "XLMUSDT", "MATICUSDT", "SHIBUSDT"):
    EXTRA[s] = (lambda s: (lambda iv: kl(s, iv)))(s)
CAND = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT", "BCHUSDT", "LTCUSDT", "EOSUSDT", "ADAUSDT", "DOGEUSDT",
        "LINKUSDT", "DOTUSDT", "TRXUSDT", "AVAXUSDT", "ETCUSDT", "XLMUSDT", "MATICUSDT", "SHIBUSDT"]
DD = {s: O.spot_daily(s) for s in CAND}
print({s[:-4]: len(v) for s, v in DD.items()}, flush=True)
def rank(y, min_age_days=0):
    y0 = calendar.timegm((y, 1, 1, 0, 0, 0)) * 1000
    preview = y0 > time.time() * 1000
    if preview: y0 = int(time.time() * 1000) - 86400000                    # 预览明年：用截至今天的数据
    sc = {}
    for s, b in DD.items():
        if not b or b[0]["t"] > y0 - min_age_days * 86400000: continue
        if preview:
            if b[-1]["t"] < y0 - 3 * 86400000: continue                     # 已下架
        elif not any(x["t"] >= y0 for x in b): continue                     # 当年还在交易
        sc[s] = sum(x.get("v", 0) * x["c"] for x in b if y0 - 365 * 86400000 <= x["t"] < y0)
    return [s for s, _ in sorted(sc.items(), key=lambda z: -z[1])[:5]]
def build_mem(min_age):
    M = {2017: ["BTCUSDT", "ETHUSDT", "BNBUSDT"]}
    for y in range(2018, 2027): M[y] = rank(y, min_age)
    return M
sc_stock = O.stock_leg()[0]
M5 = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT"]
def crypto(universe, wfun):
    O.M5[:] = universe
    cd, _, _, _, CD = O.crypto_leg(); O.crypto_8h_leg("st", CD["BTCUSDT"]); O.crypto_8h_leg("bb", CD["BTCUSDT"])
    coin = {s: O.combine([O.CURVES["日线EMA8/30"][s], O.CURVES["8h超级趋势"][s], O.CURVES["8h布林突破"][s]], [1/3] * 3) for s in universe}
    return O.weighted_basket(coin, wfun(coin))
cB0 = crypto(list(M5), lambda c: O.coin_weights(c, range(2017, 2027)))
show("现行 固定主流5", O.vol_control([cB0, sc_stock], [1, 1], 2.0)[0])
MEM_MCAP = dict(MEM)
for lab, mem in (("a 成交额前5", build_mem(0)), ("b 成交额前5(上市满2年)", build_mem(730))):
    MEM.clear(); MEM.update(mem)
    U = sorted({s for v in mem.values() for s in v})
    cB = crypto(U, lambda c: member_weights(c, range(2017, 2027)))
    show(lab, O.vol_control([cB, sc_stock], [1, 1], 2.0)[0])
    print("   每年币池：" + " | ".join(f"{y}:{' '.join(s[:-4] for s in mem[y])}" for y in range(2018, 2027)), flush=True)
MEM.clear(); MEM.update(MEM_MCAP)
U = sorted({s for v in MEM.values() for s in v})
show("对照 市值前5", O.vol_control([crypto(U, lambda c: member_weights(c, range(2017, 2027))), sc_stock], [1, 1], 2.0)[0])
O.M5[:] = M5
print("2027年按规则会选：a=" + ",".join(s[:-4] for s in rank(2027, 0)) + "  b=" + ",".join(s[:-4] for s in rank(2027, 730)))
print("ALL DONE")
