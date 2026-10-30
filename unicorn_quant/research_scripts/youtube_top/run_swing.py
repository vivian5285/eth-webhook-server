"""第一轮：摆动周期(币圈日线/8h 2017 起，原始+减半过滤；美股日线只做多) → 单策略 + 接进方案F。结果存 swing_results.pkl"""
import sys, pickle, time, ytlib as Y, strategies as S, base as BS
which = sys.argv[1] if len(sys.argv) > 1 else "crypto"
CRY = [
    ("Rayner 200MA回调50MA·跌破50MA走", S.rayner_ma_pullback, {}),
    ("Rayner 200MA回调50MA·2R", S.rayner_ma_pullback, dict(exit="rr")),
    ("Rayner 趋势内布林均值回归", S.rayner_bb_meanrev, {}),
    ("Rayner 200MA+50根突破+3ATR追踪", S.rayner_trend_breakout, {}),
    ("Rayner 布林收缩突破", S.rayner_squeeze, {}),
    ("ICT 海龟汤20·2R", S.ict_turtle_soup, {}),
    ("ICT OTE 0.705回撤", S.ict_ote, {}),
    ("TradingCh 突破回踩·2R", S.tc_break_retest, {}),
    ("TradingCh 突破回踩·3ATR追踪", S.tc_break_retest, dict(trail=True)),
    ("TradingCh 关键位pin bar·2R", S.tc_pinbar_sr, {}),
    ("TradingCh EMA20/50回调", S.tc_ema_pullback, {}),
    ("TradingCh RSI背离·2R", S.tc_rsi_divergence, {}),
    ("DaviddTech 吊灯+ZLSMA", S.dt_chandelier_zlsma, {}),
    ("DaviddTech UTBot+STC", S.dt_utbot_stc, {}),
    ("SMB 强势日次日跟进", S.smb_day2, {}),
    ("MACD+200EMA·1.5R", S.macd_200ema, {}),
]
STK = [
    ("Rayner 200MA回调50MA·跌破50MA走", S.rayner_ma_pullback, dict(long_only=True)),
    ("Rayner 200MA+50根突破+3ATR追踪", S.rayner_trend_breakout, dict(long_only=True)),
    ("Rayner 52周新高+200MA", S.rayner_52w_high, {}),
    ("Rayner 布林收缩突破", S.rayner_squeeze, dict(long_only=True)),
    ("Rayner 趋势内布林均值回归", S.rayner_bb_meanrev, dict(long_only=True)),
    ("ICT 海龟汤20·2R", S.ict_turtle_soup, dict(long_only=True)),
    ("TradingCh 突破回踩·3ATR追踪", S.tc_break_retest, dict(trail=True, long_only=True)),
    ("TradingCh 关键位pin bar·2R", S.tc_pinbar_sr, dict(long_only=True)),
    ("TradingCh EMA20/50回调", S.tc_ema_pullback, dict(long_only=True)),
    ("DaviddTech 吊灯+ZLSMA", S.dt_chandelier_zlsma, dict(long_only=True)),
    ("DaviddTech UTBot+STC", S.dt_utbot_stc, dict(long_only=True)),
    ("SMB 强势日次日跟进", S.smb_day2, dict(long_only=True)),
    ("MACD+200EMA·1.5R", S.macd_200ema, dict(long_only=True)),
]
t0 = time.time(); B = BS.base(); print("基准 方案F", BS.fmt(B["b"]), flush=True); out = []
if which == "crypto":
    for nm, fn, kw in CRY:
        for r in Y.eval_crypto(nm, fn, **kw): r.pop("cvs", None); out.append(r)
else:
    for nm, fn, kw in STK:
        r = Y.eval_stock(nm, fn, **kw); r.pop("cv", None); out.append(r)
pickle.dump(out, open(f"swing_{which}.pkl", "wb")); print(f"ALL DONE {time.time()-t0:.0f}s")
