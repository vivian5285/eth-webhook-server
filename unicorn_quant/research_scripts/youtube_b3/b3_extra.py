"""第三批补测：番人说 纽约首根4H区间假突破回收(u-A6BTmVXpk)；威廉 5m RSI 系统+4招(z2YNY3QBuXE)。"""
import os, sys, itertools, pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import b3
from b3 import run, rec, ROWS

if __name__ == "__main__":
    if "fr" in sys.argv[1:]:
        for tf, rr, ms in itertools.product(("5m", "15m"), (1.0, 2.0), (1.0, 10.0)):
            rec("fr_4h", tf, run(b3.fr_4h, tf, rr=rr, max_stop_rng=ms), rr=rr, ms=ms)
        for tf, rr in itertools.product(("1h",), (1.0, 2.0)):
            rec("fr_4h", tf, run(b3.fr_4h, tf, rr=rr), rr=rr)
    syms3 = ["ETHUSDT", "SOLUSDT", "BTCUSDT"]  # 他自己挑的 ETH/SOL(+DOGE，我们没数据)，XRP 他说不适合
    for vol, wick, cool, sweep in [(True, False, 0, False), (False, True, 0, False), (False, False, 36, False), (False, False, 0, True),
                                   (True, True, 36, False), (True, True, 36, True), (False, False, 36, True)]:
        kw = dict(vol=vol, wick=wick, cool=cool, sweep=sweep)
        rec("rsi_sc2", "5m", run(b3.rsi_sc2, "5m", **kw), **kw)
        rec("rsi_sc2_3", "5m", run(b3.rsi_sc2, "5m", syms=syms3, **kw), **kw)
    pd.DataFrame(ROWS).to_csv(os.path.join(HERE, "b3_extra2.csv"), index=False)
