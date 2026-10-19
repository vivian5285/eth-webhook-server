import cands, extra_cands
from cands import *
coin = {s: O.combine([SUB[KD][s], SUB[KS][s], SUB[KB][s]], [.5, .25, .25]) for s in O.M5}
WY = O.coin_weights(coin, range(2017, 2027))
for y in range(2017, 2027): print("Y", y, {k[:3]: round(v, 2) for k, v in WY[y].items()})
W = extra_cands.month_weights(coin)
for y in (2018, 2021, 2022):
    for m in range(1, 13): print("M", y, m, {k[:3]: round(v, 2) for k, v in W[(y, m)].items()})
