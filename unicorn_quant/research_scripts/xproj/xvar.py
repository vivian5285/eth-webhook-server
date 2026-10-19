"""variant builders: exec modified copies of production functions in a private namespace (no file edits)."""
import inspect, time, calendar, math
from xlib import *

B = load_base()
SUB = B["SUB"]; KD, KS, KB = "日线EMA8/30", "8h超级趋势", "8h布林突破"


def variant(fname, reps=(), extra=None):
    src = inspect.getsource(getattr(O, fname))
    for a, b in reps:
        assert a in src, (fname, a)
        src = src.replace(a, b)
    G = dict(O.__dict__); G.update(extra or {}); exec(src, G)
    return G[fname], G


def daily_leg(reps=(), extra=None):
    f, G = variant("crypto_leg", reps, extra); f()
    return dict(O.CURVES[KD])


def h8_leg(kind, reps=(), extra=None):
    f, G = variant("crypto_8h_leg", reps, extra); f(kind, B["CD"]["BTCUSDT"])
    return dict(O.CURVES[KS if kind == "st" else KB])


def stock(reps=(), extra=None):
    f, G = variant("stock_leg", reps, extra)
    return f()[0]


def avg_coin(members):
    return {s: O.combine([m[s] for m in members], [1 / len(members)] * len(members)) for s in O.M5}


def assemble(Dc=None, STc=None, BBc=None, scv=None, subw=(.5, .25, .25)):
    Dc = Dc or SUB[KD]; STc = STc or SUB[KS]; BBc = BBc or SUB[KB]; scv = scv or B["sc"]
    coin = {s: O.combine([Dc[s], STc[s], BBc[s]], list(subw)) for s in O.M5}
    WY = O.coin_weights(coin, range(2017, 2027)); cB = O.weighted_basket(coin, WY)
    F = O.vol_control([cB, scv], [1, 1], 2.0)[0]
    return F, cB, coin


def show(tid, F, legs=None, note=""):
    s = S(F); line = fmt(tid, s)
    if legs:
        line += "  | " + " ".join(f"{k}:{S(v)['r']:.2f}" for k, v in legs.items())
    print(line, note, flush=True)
    return s
