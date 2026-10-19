import time, pickle
t0 = time.time()
from xlib import *
instrument()
cd, ctr, _, _, CD = O.crypto_leg(); print("crypto_leg", round(time.time() - t0));
O.crypto_8h_leg("st", CD["BTCUSDT"]); O.crypto_8h_leg("bb", CD["BTCUSDT"]); print("8h", round(time.time() - t0))
SUB = {k: dict(O.CURVES[k]) for k in ["日线EMA8/30", "8h超级趋势", "8h布林突破"]}
coin = {s: O.combine([SUB["日线EMA8/30"][s], SUB["8h超级趋势"][s], SUB["8h布林突破"][s]], [.5, .25, .25]) for s in O.M5}
WY = O.coin_weights(coin, range(2017, 2027))
cB = O.weighted_basket(coin, WY)
sc, strades, sstate, SD = O.stock_leg(); print("stock", round(time.time() - t0))
F = O.vol_control([cB, sc], [1, 1], 2.0)[0]
# crypto leg gross exposure per unit weight
days = sorted(cB)
cx = {}
lastx = {(k, s): 0.0 for k in ["d", "st", "bb"] for s in O.M5}
for d in days:
    w = WY.get(time.gmtime(d * 86400).tm_year, {}); g = 0.0
    for s in O.M5:
        for k, ww in [("d", .5), ("st", .25), ("bb", .25)]:
            if d in EXPO.get(k, {}).get(s, {}): lastx[(k, s)] = abs(EXPO[k][s][d])
            g += w.get(s, 0) * ww * lastx[(k, s)]
    cx[d] = g
pickle.dump({"SUB": SUB, "coin": coin, "WY": WY, "cB": cB, "sc": sc, "F": F, "EXPO": EXPO, "cx": cx, "CD": CD, "SD": SD,
             "strades": strades, "ctr": ctr}, open(XP + "/base.pkl", "wb"))
print(fmt("baseline F", S(F)))
print(fmt("crypto basket", S(cB)))
print(fmt("stock leg", S(sc)))
print("WY 2026", WY.get(2026))
_, scl, G = vc([cB, sc], [1, 1], 2.0, expo=[cx, EXPO["stk"]])
gs = [G[d] for d in G if d >= START]
print("baseline gross: mean %.2f max %.2f p95 %.2f" % (sum(gs) / len(gs), max(gs), sorted(gs)[int(.95 * len(gs))]))
cxs = [cx[d] for d in cx if d >= START]; sxs = [EXPO["stk"][d] for d in EXPO["stk"] if d >= START]
print("crypto leg expo mean %.2f max %.2f; stock leg expo mean %.2f max %.2f" % (sum(cxs) / len(cxs), max(cxs), sum(sxs) / len(sxs), max(sxs)))
print("yearly", yearly(F))
print("done", round(time.time() - t0))
