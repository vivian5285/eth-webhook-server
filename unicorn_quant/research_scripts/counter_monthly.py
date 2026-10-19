"""2026-10-19 宝贝追加：月线长期趋势单 + 周线 RSI 逆势短单(例如月线多头时周线超买就做空)。规则同 counter_swing.py：
周线 RSI14 超买(>70/75/80)做空、超卖做多，RSI 回到 50 平，2×ATR(周)止损，最多持有 24 根；逆大势/顺大势/不分。
大周期 = 月线 EMA8/30(只用已收盘月线)。样本很少(月线EMA30要30个月才有，周线RSI超买超卖一年一两次)，只能当参考。
另外看"月线趋势 + 日线RSI"。"""
exec(open("/tmp/counter_swing.py", encoding="utf-8").read().split('print("下载1h K线…"')[0])
import calendar
def monthly(d):
    out = []
    for x in d:
        g = time.gmtime(x["t"] / 1000); k = calendar.timegm((g.tm_year, g.tm_mon, 1, 0, 0, 0)) * 1000
        if out and out[-1]["t"] == k:
            o = out[-1]; o["h"] = max(o["h"], x["h"]); o["l"] = min(o["l"], x["l"]); o["c"] = x["c"]
        else: out.append({"t": k, "o": x["o"], "h": x["h"], "l": x["l"], "c": x["c"]})
    for i, o in enumerate(out):    # 收盘时间 = 下个月1号
        g = time.gmtime(o["t"] / 1000); y, m = (g.tm_year + (g.tm_mon == 12), g.tm_mon % 12 + 1); o["close_t"] = calendar.timegm((y, m, 1, 0, 0, 0)) * 1000
    return out
def trend_month(mb):
    c = [x["c"] for x in mb]; e8, e30 = O.ema(c, 8), O.ema(c, 30); ts = [x["close_t"] for x in mb]
    def f(t):
        j = bisect.bisect_right(ts, t) - 1
        return 0 if j < 31 else (1 if e8[j] > e30[j] else -1)
    return f
cd, _, _, _, CD = O.crypto_leg(); O.crypto_8h_leg("st", CD["BTCUSDT"]); O.crypto_8h_leg("bb", CD["BTCUSDT"])
coin = {s: O.combine([O.CURVES["日线EMA8/30"][s], O.CURVES["8h超级趋势"][s], O.CURVES["8h布林突破"][s]], [1/3] * 3) for s in O.M5}
WY = O.coin_weights(coin, range(2017, 2027)); cB = O.weighted_basket(coin, WY); sc = O.stock_leg()[0]
BASE = O.vol_control([cB, sc], [1, 1], 2.0)[0]; b0 = (st(BASE)[2], st(BASE, 17410, H)[2], st(BASE, H)[2])
_src = open("/tmp/counter_swing.py", encoding="utf-8").read()
exec("def evaluate" + _src.split("def evaluate", 1)[1].split("\nfor hrs in", 1)[0])
# 月线 EMA30 需要更长历史：用 Yahoo BTC/ETH 月线(2014/2017起)补不了山寨，这里只用币安日线合成，能算的币才算
def wk_bars(d):
    w = resample([{**x, "t": x["t"] + 3 * DAY} for x in d], 24 * 7); return [{**x, "t": x["t"] - 3 * DAY} for x in w]
for lab, mk_b, hrs in (("周线RSI", wk_bars, 24 * 7), ("日线RSI", lambda d: d, 24)):
    for mode in ("逆大势", "顺大势", "不分"):
        for ob in (70, 75, 80):
            cvs = {}; nt = 0
            for s in O.M5:
                d = CD[s]; tf = trend_month(monthly(d))
                if mode == "不分": tf0 = tf; tf = lambda t, tf0=tf0: tf0(t) or 1
                cvs[s], n = sim(mk_b(d), hrs, tf, ob, mode); nt += n
            evaluate(f"月线趋势+{lab}{ob} {mode}", cvs, nt)
print("ALL DONE")
# 不算手续费再看一遍(判断是规则本身没优势，还是被手续费吃掉)
FEE = 0.0
H1 = {s: k1h(s) for s in O.M5}
for lab, getb, hrs, tfm in (("1h RSI80 逆大势(无手续费)", lambda s: H1[s], 1, lambda s: trend_fn(CD[s], 24)),
                             ("2h RSI80 逆大势(无手续费)", lambda s: resample(H1[s], 2), 2, lambda s: trend_fn(CD[s], 24)),
                             ("周线趋势+日线RSI80 逆大势(无手续费)", lambda s: CD[s], 24, lambda s: trend_fn(wk_bars(CD[s]), 24 * 7))):
    cvs = {}; nt = 0
    for s in O.M5:
        cvs[s], n = sim(getb(s), hrs, tfm(s), 80, "逆大势"); nt += n
    evaluate(lab, cvs, nt)
print("ALL DONE 2")
