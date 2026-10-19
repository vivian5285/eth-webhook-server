"""2026-10-19 前视检验（截断法）：把所有行情数据截到某一天 T，重新跑整个正式引擎；
如果引擎在 T 之前的任何决定用到了 T 之后的数据，截断前后 T 之前的成交就会不一样。
比较三条币圈子腿 + 美股腿在 T 之前的每一笔开仓(时间/价格/方向/仓位)和平仓(时间/价格)，以及 T 之前的组合净值曲线、币种年度权重。
T 取 5 个时点：2019-06-30、2021-03-31、2022-11-15、2024-06-30、2026-01-31。"""
import sys, os, time, calendar, json
os.nice(19)
sys.path.insert(0, "/home/stratroster/unicorn_quant"); sys.path.insert(0, "/home/stratroster/eth-webhook-server")
from engine import optimal as O
DAY = 86400000
_sd, _s8, _st = O.spot_daily, O.spot_8h, O.stock_daily
CUT = {"t": None}
def cut(b, hours):
    if CUT["t"] is None: return b
    return [x for x in b if x["t"] + hours * 3600000 <= CUT["t"]]      # 只保留 T 之前已经收盘的K线
O.spot_daily = lambda s: cut(_sd(s), 24); O.spot_8h = lambda s: cut(_s8(s), 8); O.stock_daily = lambda s: cut(_st(s), 7)
ALL5 = list(O.M5)
def run():
    O.M5[:] = [s for s in ALL5 if len(O.spot_daily(s)) > 60]     # T 之前还没上市的币(SOL 2020-08)先不放
    cd, ctr, _, _, CD = O.crypto_leg()
    _, tst, _ = O.crypto_8h_leg("st", CD["BTCUSDT"]); _, tbb, _ = O.crypto_8h_leg("bb", CD["BTCUSDT"])
    coin = {s: O.combine([O.CURVES["日线EMA8/30"][s], O.CURVES["8h超级趋势"][s], O.CURVES["8h布林突破"][s]], [1/3] * 3) for s in O.M5}
    last_y = time.gmtime((CUT["t"] or time.time() * 1000) / 1000).tm_year
    WY = O.coin_weights(coin, range(2017, last_y + 1)); cB = O.weighted_basket(coin, WY)
    sc, str_, _, _ = O.stock_leg()
    F = O.vol_control([cB, sc], [1, 1], 2.0)[0]
    return {"日线": ctr, "8h超趋": tst, "8h布林": tbb, "美股": str_}, F, WY
FULL, FF, WYF = run()
def key_entry(t): return (t["symbol"], t["side"], t["entry_t"], round(t["entry"], 6), round(t.get("alloc") or 0, 4))
for T in ("2019-06-30", "2021-03-31", "2022-11-15", "2024-06-30", "2026-01-31"):
    y, m, d = map(int, T.split("-")); CUT["t"] = calendar.timegm((y, m, d, 0, 0, 0)) * 1000
    TR, FT, WYT = run(); Tms = CUT["t"]
    print(f"\n=== 截断到 {T} ===", flush=True)
    bad = 0
    for leg in FULL:
        a = {key_entry(t) for t in FULL[leg] if t["entry_t"] < Tms - 2 * DAY}
        b = {key_entry(t) for t in TR[leg] if t["entry_t"] < Tms - 2 * DAY}
        ax = {(t["symbol"], t["entry_t"], t["exit_t"], round(t["exit"], 6)) for t in FULL[leg] if t.get("exit_t") and t["exit_t"] < Tms - 2 * DAY}
        bx = {(t["symbol"], t["entry_t"], t["exit_t"], round(t["exit"], 6)) for t in TR[leg] if t.get("exit_t") and t["exit_t"] < Tms - 2 * DAY}
        de, dx = a ^ b, ax ^ bx; bad += len(de) + len(dx)
        print(f"  {leg:5s} 开仓 {len(a)} 笔 vs 截断后 {len(b)} 笔，不一致 {len(de)} | 平仓 {len(ax)} vs {len(bx)}，不一致 {len(dx)}", flush=True)
        for x in list(de)[:4] + list(dx)[:4]: print("     差异:", x, flush=True)
    ks = [k for k in FT if k < Tms // DAY - 2 and k in FF and k >= O.START_DAY]
    if ks:
        k0 = ks[0]; mx = max(abs(FT[k] / FT[k0] - FF[k] / FF[k0]) for k in ks)
        print(f"  组合净值曲线 {len(ks)} 天，最大差 {mx:.2e}", flush=True); bad += mx > 1e-9
    yrs = [y_ for y_ in WYT if y_ <= y]
    wd = max((abs(WYT[y_].get(s, 0) - WYF[y_].get(s, 0)) for y_ in yrs for s in ALL5), default=0)
    print(f"  币种年度权重({yrs[0]}~{yrs[-1]}) 最大差 {wd:.2e}", flush=True); bad += wd > 1e-9
    print(f"  结论：{'✅ 没有用到未来数据' if not bad else '❌ 发现差异，需要排查'}", flush=True)
print("ALL DONE")
