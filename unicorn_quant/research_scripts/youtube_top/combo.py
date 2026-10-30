"""第二轮"组合拳"：第一轮里作第4腿最接近基准的几条(全部 8h 原始版，事先列定，不再调参)，
试 加多条 / 替换现有子腿 / 美股第2腿降到 25%。判定同前：全程、前半、后半比值都要超过基准。"""
import ytlib as Y, strategies as S, base as BS
O = Y.O; B = BS.base(); b0 = B["b"]; print("基准 方案F", BS.fmt(b0), flush=True)
CAND = {
    "UTBot+STC": (S.dt_utbot_stc, {}),
    "吊灯+ZLSMA": (S.dt_chandelier_zlsma, {}),
    "Rayner突破": (S.rayner_trend_breakout, {}),
    "Rayner收缩": (S.rayner_squeeze, {}),
    "EMA20/50回调": (S.tc_ema_pullback, {}),
}
D = Y.crypto("8h"); CV = {}
for k, (fn, kw) in CAND.items():
    CV[k] = {s: Y.curve(D[s], Y.trades(D[s], fn(D[s], **kw)), 8) for s in O.M5}
OLD = ["日线EMA", "8h超级趋势", "8h布林"]


def plan(legs):
    """legs: 子腿名列表(OLD 里的名字或 CAND 名)，等权"""
    c = {}
    for s in O.M5:
        cs = [B["LEG3"][s][OLD.index(x)] if x in OLD else CV[x][s] for x in legs]
        c[s] = O.combine(cs, [1 / len(cs)] * len(cs))
    return O.vol_control([O.weighted_basket(c, O.coin_weights(c, range(2017, 2027))), B["sc"]], [1, 1], 2.0)[0]


TRIES = [
    ("现行三腿(复核)", OLD),
    ("+UTBot+吊灯(5腿)", OLD + ["UTBot+STC", "吊灯+ZLSMA"]),
    ("+UTBot+Rayner收缩(5腿)", OLD + ["UTBot+STC", "Rayner收缩"]),
    ("+全部5条(8腿)", OLD + list(CAND)),
    ("UTBot 替换 日线EMA", ["UTBot+STC", "8h超级趋势", "8h布林"]),
    ("UTBot 替换 超级趋势", ["日线EMA", "UTBot+STC", "8h布林"]),
    ("UTBot 替换 布林", ["日线EMA", "8h超级趋势", "UTBot+STC"]),
    ("吊灯 替换 超级趋势", ["日线EMA", "吊灯+ZLSMA", "8h布林"]),
    ("Rayner收缩 替换 布林", ["日线EMA", "8h超级趋势", "Rayner收缩"]),
]
for nm, legs in TRIES:
    x = BS.three(plan(legs)); print(f"{nm:24s} {BS.fmt(x)} {BS.verdict(x, b0)}", flush=True)

# 美股：Rayner 布林收缩突破 作第2条美股腿，权重 25% / 50%
trs = {tk: (b, Y.trades(b, S.rayner_squeeze(b, long_only=True))) for tk, b in Y.stocks().items()}
cv = Y.stock_portfolio(trs)
for w in (0.25, 0.5):
    x = BS.three(BS.as_stock_leg2(cv, w)); print(f"美股第2腿 Rayner收缩 {w:.0%}      {BS.fmt(x)} {BS.verdict(x, b0)}", flush=True)
print("ALL DONE")
