"""B. 时间季节性。
B1 币圈按 UTC 小时 / 星期的漂移：主流5币永续 1h K线(2022-01..2026-09)，样本内选出最好的时段，看样本外是否延续；
   再看'只在美盘/亚盘持有多头'这类可执行版本扣费后的收益，以及方案F币圈腿收益按星期的分布(能否按星期加减仓)。
B2 美股腿'只持隔夜'：用美股腿真实交易记录还原每天每只股票的持仓，把收益拆成 隔夜(昨收→今开) 和 日内(今开→今收)；
   币安美股永续可 24h 交易，所以'只持隔夜'= 每天收盘买、开盘卖，每天两边成本 2×6.5bp。"""
import sys
from common import *
sys.path.insert(0, os.path.join(HERE, "..", "speculation_channel"))
from lib import bars, SYMS
out = {}

# ---------- B1 小时 ----------
H = []
for s in SYMS:
    b = bars(s, "1h"); t = pd.to_datetime(b.t, unit="ms"); r = b.c / b.o - 1
    H.append(pd.DataFrame({"t": t, "r": r.values, "sym": s}))
H = pd.concat(H); H["hour"] = H.t.dt.hour; H["dow"] = H.t.dt.dayofweek; H["oos"] = H.t >= OS0
H = H[(H.t >= IS0) & (H.t <= END + " 23:59")]
g = H.groupby(["oos", "hour"]).r.agg(["mean", "std", "count"]); g["t"] = g["mean"] / g["std"] * np.sqrt(g["count"])
prof = (g["mean"] * 1e4).unstack(0); prof.columns = ["样本内bp", "样本外bp"]; tt = g["t"].unstack(0); prof["t内"] = tt[False].round(1); prof["t外"] = tt[True].round(1)
print(prof.round(2).to_string()); print("小时漂移 样本内/外 相关:", round(prof["样本内bp"].corr(prof["样本外bp"]), 2))
out["hour_corr"] = prof["样本内bp"].corr(prof["样本外bp"])
gd = H.groupby(["oos", "dow"]).r.mean().unstack(0) * 24 * 1e4; gd.columns = ["样本内bp/天", "样本外bp/天"]
print(gd.round(1).to_string()); print("星期 样本内/外 相关:", round(gd.iloc[:, 0].corr(gd.iloc[:, 1]), 2))


def window_strategy(hours, label):
    """每天在 hours(连续 UTC 小时段) 持有5币等权多头，段首开段尾平，扣 2×6.5bp。逐日盯市。"""
    x = H[H.hour.isin(hours)].copy(); x["d"] = x.t.dt.normalize()
    day = x.groupby(["d", "sym"]).r.apply(lambda v: np.prod(1 + v) - 1).unstack().mean(axis=1) - 2 * COST
    gross = x.groupby(["d", "sym"]).r.apply(lambda v: np.prod(1 + v) - 1).unstack().mean(axis=1)
    a, i, o = stats(day), stats(day[:IS1]), stats(day[OS0:])
    print(f"{label}: 毛 {gross.mean()*1e4:.1f}bp/天 净 {day.mean()*1e4:.1f}bp/天 年化{a[0]:.1%} 回撤{a[1]:.1%} 内年化{i[0]:.1%} 外年化{o[0]:.1%}", flush=True)
    return day


best = list(prof["样本内bp"].sort_values().index[-4:])
print("样本内最好4个小时:", best)
D = {}
D["样本内最好4小时"] = window_strategy(best, f"样本内最好4小时{best}")
D["美盘 13-20UTC"] = window_strategy(range(13, 21), "美盘 13-20UTC")
D["亚盘 0-7UTC"] = window_strategy(range(0, 8), "亚盘 0-7UTC")
D["欧盘 7-13UTC"] = window_strategy(range(7, 13), "欧盘 7-13UTC")
D["对照 全天(每天都买)"] = window_strategy(range(24), "对照 全天持有每天进出")
_, _, fF = legs(); fF = base()
for k, v in D.items():
    v = v.reindex(fF.index).fillna(0)
    for m in (0.25, 0.5):
        r = report(addon(v, m), "2022-01-01"); print(f"  叠加 {k} ×{m}: {fmt(r)} 同窗口{report(addon(v, m), '2022-03-01')['ratio']} 与F相关{v['2022':].corr(fF['2022':]):.2f}")
# 方案F币圈腿按星期
c, s, f = legs(); cw = c["2022":END].groupby(c["2022":END].index.dayofweek).agg(["mean", "count"])
cwi = c[IS0:IS1].groupby(c[IS0:IS1].index.dayofweek).mean() * 1e4; cwo = c[OS0:END].groupby(c[OS0:END].index.dayofweek).mean() * 1e4
print("币圈腿按星期 bp/天 内/外:\n", pd.DataFrame({"内": cwi, "外": cwo}).round(1).to_string())
worst = list(cwi.sort_values().index[:2]); print("样本内最差两个星期几:", worst)
for m in (0.5, 0.0):
    M = pd.Series([m if d.dayofweek in worst else 1.0 for d in coin_combo().index], index=coin_combo().index)
    run_overlay(f"B1c 币圈腿在星期{worst}×{m}", M, side=0)

# ---------- B2 美股腿 隔夜/日内 ----------
T = [t for t in dump()["trades"] if t.get("sub") is None]
px = {}
for tk in {t["symbol"] for t in T}:
    b = pd.DataFrame(json.load(open(os.path.join(DATA, f"bars_{tk}.json")))).iloc[:, :5]; b.columns = list("tohlc")
    b.index = pd.to_datetime(b.t, unit="ms").dt.normalize(); px[tk] = b[~b.index.duplicated()]
on = pd.Series(0.0, index=c.index); idr = pd.Series(0.0, index=c.index); expo = pd.Series(0.0, index=c.index)
for t in T:
    b = px[t["symbol"]]; d0 = pd.to_datetime(t["entry_t"], unit="ms").normalize(); d1 = pd.to_datetime(t["exit_t"] or 4e12, unit="ms").normalize()
    sg = 1 if t["side"] == "LONG" else -1; a = t["alloc"] or 0
    seg = b[(b.index >= d0) & (b.index <= min(d1, b.index[-1]))]
    if len(seg) == 0: continue
    o, cl = seg.o.values, seg.c.values; pc = np.r_[np.nan, cl[:-1]]
    ovn = np.where(seg.index > d0, o / pc - 1, 0.0)                              # 入场日没有隔夜
    intra = np.where(seg.index < d1, cl / o - 1, 0.0)                            # 出场日在开盘就走了
    on = on.add(pd.Series(sg * a * np.nan_to_num(ovn), index=seg.index), fill_value=0)
    idr = idr.add(pd.Series(sg * a * intra, index=seg.index), fill_value=0)
    expo = expo.add(pd.Series(a, index=seg.index[seg.index < d1]), fill_value=0)
on, idr, expo = (x.reindex(c.index).fillna(0) for x in (on, idr, expo))
print("还原检查：隔夜+日内 与 美股腿日收益 相关", round((on + idr)["2018":].corr(s["2018":]), 3), " 平均暴露", round(expo["2022":].mean(), 2))
for nm, x in (("隔夜", on), ("日内", idr), ("合计", on + idr)):
    print(f"美股腿{nm}部分 年化毛收益(简单加总): 内{x[IS0:IS1].mean()*252:.1%}  外{x[OS0:END].mean()*252:.1%}  全{x['2018':END].mean()*252:.1%}")
cost_day = 2 * COST * expo
for nm, sx in (("只持隔夜", on - cost_day), ("只持日内", idr - cost_day)):
    F2 = vol_control(c, sx); r = report(F2, "2022-01-01", nm); print(f"B2 美股腿{nm}(扣每天2×6.5bp) 方案F→ {fmt(r)} 同窗口{report(F2, '2022-03-01')['ratio']}")
    F3 = vol_control(c, sx + 0 * s); r0 = report(vol_control(c * 0, sx), "2022-01-01"); print(f"    美股腿单独 {fmt(r0)}")
r0 = report(vol_control(c * 0, s), "2022-01-01"); print("    现行美股腿单独", fmt(r0))
