"""第二批组合拳(事先列定)：Dynamic Breakout II(币圈 8h 减半版 / 美股只做多) 与 双超级趋势 8h 原始版。
美股：DB2 腿自己比值 1.43 > 现行美股腿 1.12 → 试 替换现行美股腿 / 25% / 50% / 75%；币圈：加腿与替换。"""
import ytlib as Y, strategies2 as S, base as BS
O = Y.O; B = BS.base(); b0 = B["b"]; print("基准 方案F", BS.fmt(b0), flush=True)
D = Y.crypto("8h")
def cv(fn, ph=False, **kw):
    out = {}
    for s in O.M5:
        sp = fn(D[s], **kw)
        if ph: sp = dict(sp, sig=Y.phase_filt(D[s], sp["sig"], 8))
        out[s] = Y.curve(D[s], Y.trades(D[s], sp), 8)
    return out
CV = {"DB2": cv(S.dynamic_breakout2, ph=True), "双ST": cv(S.st_variant, kind="double")}
OLD = ["日线EMA", "8h超级趋势", "8h布林"]
def plan(legs, sc=None):
    c = {}
    for s in O.M5:
        cs = [B["LEG3"][s][OLD.index(x)] if x in OLD else CV[x][s] for x in legs]
        c[s] = O.combine(cs, [1 / len(cs)] * len(cs))
    return O.vol_control([O.weighted_basket(c, O.coin_weights(c, range(2017, 2027))), sc or B["sc"]], [1, 1], 2.0)[0]
for nm, legs in [("+DB2+双ST(5腿)", OLD + ["DB2", "双ST"]), ("双ST 替换 超级趋势", ["日线EMA", "双ST", "8h布林"]),
                 ("DB2 替换 日线EMA", ["DB2", "8h超级趋势", "8h布林"]), ("DB2 替换 布林", ["日线EMA", "8h超级趋势", "DB2"])]:
    x = BS.three(plan(legs)); print(f"{nm:22s} {BS.fmt(x)} {BS.verdict(x, b0)}", flush=True)
trs = {tk: (b, Y.trades(b, S.dynamic_breakout2(b, long_only=True))) for tk, b in Y.stocks().items()}
sdb = Y.stock_portfolio(trs); print("美股 DB2 腿", BS.fmt(BS.three(sdb)), "| 现行美股腿", BS.fmt(BS.three(B["sc"])), flush=True)
print("  逐年 DB2:", BS.yr(sdb)); print("  逐年 现行:", BS.yr(B["sc"]))
for w in (0.25, 0.5, 0.75, 1.0):
    x = BS.three(BS.as_stock_leg2(sdb, w)); print(f"美股 DB2 占美股腿 {w:.0%}        {BS.fmt(x)} {BS.verdict(x, b0)}", flush=True)
print("ALL DONE")
