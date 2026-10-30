"""第二批(DeepSeek 清单里之前没测过的)：python run2.py crypto|stock|intraday"""
import sys, pickle, time, ytlib as Y, strategies2 as S, base as BS
which = sys.argv[1]
CRY = [
    ("对照 买入持有", S.buy_hold, {}, (False,)),
    ("对照 收盘>200MA持多", S.ma200_hold, dict(mode="ma200"), (False,)),
    ("对照 50/200金叉持多", S.ma200_hold, dict(mode="golden"), (False,)),
    ("对照 Uptober只在10月持多", S.ma200_hold, dict(mode="uptober"), (False,)),
    ("Rayner MAE·1ATR止盈", S.rayner_mae, dict(tgt_mode="atr"), (False, True)),
    ("Rayner MAE·波段高点止盈", S.rayner_mae, dict(tgt_mode="swing"), (False, True)),
    ("Rayner MAE·跌破SMA50走", S.rayner_mae, dict(tgt_mode="trail"), (False, True)),
    ("蓄势突破(三角形)·3ATR追踪", S.buildup_breakout, {}, (False, True)),
    ("假突破反转·2R", S.false_breakout, {}, (False, True)),
    ("三重顶底+反转K·2R", S.triple_tb, {}, (False, True)),
    ("ICT Unicorn·2R", S.ict_unicorn, {}, (False, True)),
    ("EMA9/21+RSI", S.ema921_rsi, {}, (False, True)),
    ("EMA9/21+RSI+EMA200", S.ema921_rsi, dict(f200=True), (False, True)),
    ("SSL通道", S.ssl_channel, {}, (False, True)),
    ("超级趋势+EMA200", S.st_variant, dict(kind="ema200"), (False, True)),
    ("双超级趋势", S.st_variant, dict(kind="double"), (False, True)),
    ("超级趋势+RSI回调", S.st_variant, dict(kind="rsi"), (False, True)),
    ("Dynamic Breakout II", S.dynamic_breakout2, {}, (False, True)),
    ("AO零轴交叉", S.ao_zero, {}, (False, True)),
    ("AO零轴+布林突破", S.ao_zero, dict(bb=True), (False, True)),
    ("波动扩张均值回归", S.volx_meanrev, {}, (False, True)),
    ("布林+Aroon均值回归", S.bb_aroon, {}, (False, True)),
    ("RSI(2)+200MA", S.rsi2_200, {}, (False, True)),
]
STK = [
    ("对照 收盘>200MA持多", S.ma200_hold, dict(mode="ma200")),
    ("Rayner MAE·跌破SMA50走", S.rayner_mae, dict(tgt_mode="trail", long_only=True)),
    ("Rayner MAE·1ATR止盈", S.rayner_mae, dict(tgt_mode="atr", long_only=True)),
    ("蓄势突破(三角形)·3ATR追踪", S.buildup_breakout, dict(long_only=True)),
    ("三重底+反转K·2R", S.triple_tb, dict(long_only=True)),
    ("EMA9/21+RSI+EMA200", S.ema921_rsi, dict(f200=True, long_only=True)),
    ("SSL通道", S.ssl_channel, dict(long_only=True)),
    ("超级趋势+EMA200", S.st_variant, dict(kind="ema200", long_only=True)),
    ("Dynamic Breakout II", S.dynamic_breakout2, dict(long_only=True)),
    ("AO零轴交叉", S.ao_zero, dict(long_only=True)),
    ("波动扩张均值回归", S.volx_meanrev, dict(long_only=True)),
    ("布林+Aroon均值回归", S.bb_aroon, dict(long_only=True)),
    ("RSI(2)+200MA", S.rsi2_200, dict(long_only=True)),
]
ID = [
    ("ICT 2022模型", S.ict_2022, ["5m"], {}),
    ("ICT 2022模型·15m入场", S.ict_2022, ["15m"], dict(htf=16)),
    ("ICT Unicorn·2R", S.ict_unicorn, ["5m", "15m", "1h"], {}),
    ("Rayner MAE·1ATR止盈", S.rayner_mae, ["15m", "1h", "4h"], dict(tgt_mode="atr")),
    ("蓄势突破(三角形)·2R", S.buildup_breakout, ["15m", "1h", "4h"], dict(trail=False)),
    ("假突破反转·2R", S.false_breakout, ["15m", "1h", "4h"], {}),
    ("三重顶底+反转K·2R", S.triple_tb, ["15m", "1h", "4h"], {}),
    ("EMA9/21+RSI+EMA200", S.ema921_rsi, ["5m", "15m", "1h", "4h"], dict(f200=True)),
    ("ORB15 亚洲·对侧2R", S.orb_session, ["5m"], dict(sess="asia")),
    ("ORB15 伦敦·对侧2R", S.orb_session, ["5m"], dict(sess="london")),
    ("ORB15 纽约·对侧2R", S.orb_session, ["5m"], dict(sess="ny")),
    ("ORB15 纽约·中点止损·8h平", S.orb_session, ["5m"], dict(sess="ny", stop_mode="mid", rr=None)),
    ("ORB15 纽约·不看200EMA", S.orb_session, ["5m"], dict(sess="ny", f200=False)),
    ("Dual Thrust", S.dual_thrust, ["5m", "15m"], {}),
    ("VWAP+EMA9", S.vwap_9ema, ["15m", "1h"], {}),
    ("超级趋势+EMA200", S.st_variant, ["1h", "4h"], dict(kind="ema200")),
    ("SSL通道", S.ssl_channel, ["1h", "4h"], {}),
    ("AO零轴交叉", S.ao_zero, ["1h", "4h"], {}),
    ("波动扩张均值回归", S.volx_meanrev, ["1h", "4h"], {}),
    ("RSI(2)+200MA", S.rsi2_200, ["15m", "1h", "4h"], {}),
]
t0 = time.time(); B = BS.base(); print("基准 方案F", BS.fmt(B["b"]), flush=True); out = []
if which == "crypto":
    for nm, fn, kw, ph in CRY:
        for r in Y.eval_crypto(nm, fn, phase=ph, show_yr=nm.startswith("对照"), **kw): r.pop("cvs", None); out.append(r)
elif which == "stock":
    for nm, fn, kw in STK:
        r = Y.eval_stock(nm, fn, **kw); r.pop("cv", None); out.append(r)
else:
    for nm, fn, tfs, kw in ID:
        for tf in tfs:
            try: r = Y.eval_intraday(nm, fn, tf, **kw); r.pop("T", None); out.append(r)
            except Exception as e: print(nm, tf, "ERR", repr(e), flush=True)
pickle.dump(out, open(f"run2_{which}.pkl", "wb")); print(f"ALL DONE {time.time()-t0:.0f}s")
