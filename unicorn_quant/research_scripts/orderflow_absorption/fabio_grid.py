"""Fabio 三套策略 × 5 币 × 变体，输出每笔交易到 fabio_trades.pkl。"""
import pandas as pd
from multiprocessing import Pool
from fabio import prep, ivb, trend, meanrev

SYMS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT"]


def cfgs():
    for trig, k in [("touch", 1.0), ("body", 1.0), ("flow", 0.5), ("flow", 1.0), ("flow", 1.5)]:
        for rr in (2.0, 2.5):
            for be in (0.0, 1.0):
                for short in (True, False):
                    yield f"IVB|{trig}|k{k}|rr{rr}|be{be}|short{short}", ivb, dict(trig=trig, big_k=k, rr=rr, be_r=be, short=short)
    yield "TREND|lvn|rr2.5", trend, dict(pos="lvn")
    yield "TREND|lvn|trail", trend, dict(pos="lvn", rr=10, be_r=1.0, trail=True)
    yield "AAA|val->vah", trend, dict(pos="val", tgt_mode="va")
    yield "AAA|val|k1.5", trend, dict(pos="val", tgt_mode="va", big_k=1.5)
    for k in (0.5, 1.0, 1.5):
        yield f"MR|k{k}", meanrev, dict(big_k=k)


def job(s):
    d = prep(s); rows = []
    for name, f, kw in cfgs():
        for r in f(d, **kw):
            r.update(sym=s, cfg=name); rows.append(r)
    print("done", s, len(rows), flush=True)
    return rows


if __name__ == "__main__":
    with Pool(5) as p:
        out = sum(p.map(job, SYMS), [])
    pd.DataFrame(out).to_pickle("fabio_trades.pkl")
    print("saved", len(out))
