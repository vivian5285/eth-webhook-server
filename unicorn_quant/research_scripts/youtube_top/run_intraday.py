"""第一轮：日内/短周期(币安永续 1m 合成 5m~4h，主流5币 2022-01..2026-09)，每笔 R：零成本/全挂单(2bp)/含费(6.5bp)。
样本内 2022-2024 / 样本外 2025-2026。结果存 intraday_results.pkl"""
import pickle, time, ytlib as Y, strategies as S
RUNS = [
    ("ICT 银弹10-11点FVG", S.ict_silver_bullet, ["5m"], {}),
    ("ICT 亚盘区间Judas", S.ict_judas, ["15m", "5m"], {}),
    ("ICT 海龟汤20·2R", S.ict_turtle_soup, ["15m", "1h", "4h"], {}),
    ("ICT OTE 0.705回撤", S.ict_ote, ["15m", "1h", "4h"], {}),
    ("Cameron 放量后首次回调", S.cameron_micro_pullback, ["5m"], {}),
    ("Cameron 放量后首次回调 rvol3", S.cameron_micro_pullback, ["5m"], dict(rvol=3.0)),
    ("Wieland ORB30·对侧止损2R", S.orb, ["5m"], {}),
    ("Wieland ORB15·对侧止损2R", S.orb, ["5m"], dict(mins=15)),
    ("Wieland ORB30·中点止损·收盘平", S.orb, ["5m"], dict(rr=None, stop_mode="mid")),
    ("Wieland ORB30·不看趋势", S.orb, ["5m"], dict(trend=False)),
    ("SMB VWAP顺势回调·2R", S.vwap_pullback, ["5m", "15m"], {}),
    ("Rayner 200MA回调50MA·跌破50MA走", S.rayner_ma_pullback, ["1h", "4h"], {}),
    ("Rayner 200MA回调50MA·2R", S.rayner_ma_pullback, ["1h", "4h"], dict(exit="rr")),
    ("Rayner 布林收缩突破", S.rayner_squeeze, ["1h", "4h"], {}),
    ("Rayner 趋势内布林均值回归", S.rayner_bb_meanrev, ["15m", "1h", "4h"], {}),
    ("TradingCh 突破回踩·2R", S.tc_break_retest, ["15m", "1h", "4h"], {}),
    ("TradingCh 关键位pin bar·2R", S.tc_pinbar_sr, ["15m", "1h", "4h"], {}),
    ("TradingCh EMA20/50回调", S.tc_ema_pullback, ["15m", "1h", "4h"], {}),
    ("TradingCh RSI背离·2R", S.tc_rsi_divergence, ["15m", "1h", "4h"], {}),
    ("DaviddTech 吊灯+ZLSMA", S.dt_chandelier_zlsma, ["5m", "15m", "1h", "4h"], {}),
    ("DaviddTech UTBot+STC", S.dt_utbot_stc, ["5m", "15m", "1h", "4h"], {}),
    ("MACD+200EMA·1.5R", S.macd_200ema, ["15m", "1h", "4h"], {}),
]
t0 = time.time(); out = []
for nm, fn, tfs, kw in RUNS:
    for tf in tfs:
        try:
            r = Y.eval_intraday(nm, fn, tf, **kw); r.pop("T", None); out.append(r)
        except Exception as e:
            print(nm, tf, "ERR", repr(e), flush=True)
    pickle.dump(out, open("intraday_results.pkl", "wb"))
print(f"ALL DONE {time.time()-t0:.0f}s")
