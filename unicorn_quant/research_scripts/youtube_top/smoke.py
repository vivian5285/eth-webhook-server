import numpy as np, ytlib as Y


def ema_cross(b):
    c = b.c.values; f, s = Y.ema(c, 8), Y.ema(c, 30); a = Y.atr(b)
    sig = np.where(Y.cross_up(f, s), 1, np.where(Y.cross_up(s, f), -1, 0))
    return dict(sig=sig, stop=c - np.where(sig >= 0, 1, -1) * 3 * a, exit_sig=np.where(f < s, 1, np.where(f > s, -1, 0)))


Y.eval_crypto("对照 EMA8/30", ema_cross, tfs=("1d",), phase=(True,), show_yr=True)
Y.eval_stock("对照 EMA8/30", ema_cross)
Y.eval_intraday("对照 EMA8/30", ema_cross, "4h")
