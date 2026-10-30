"""F. 美股腿 × 财报(PEAD)。财报日来自 SEC EDGAR 8-K Item 2.02(fetch_earnings.py)；TSM/ASML/ARM 等外国发行人无数据，按'无财报信息'处理。
美股腿按真实交易记录逐日还原(同 b_season.py B2，隔夜+日内)，然后对交易做过滤：
 F1 财报前 k 个交易日内不新开仓(规避事件风险)
 F2 PEAD 顺向：只在最近一次财报反应(E前收盘→E+1收盘) > x 的股票上开仓(反应在 60 个交易日内)
 F3 PEAD 逆向剔除：最近财报反应 < -x 的不开仓
 F4 持仓穿越财报：财报前一天收盘平仓、E+1 收盘后若仍满足条件按原仓位接回(用'跳过财报窗口'近似)
对照：随机剔除同样比例的交易(重复 20 次)。剔除的仓位不再分配(保守)，另报'剩余仓位按比例放大到原平均暴露'版本。
F5 独立 PEAD 策略：股票池里财报反应 > x 的，E+2 开盘买、持有 H 天，等权，闲置资金叠加。"""
from common import *
import itertools
c, s, f = legs()
EA = json.load(open(os.path.join(DATA, "earnings.json")))
T = [t for t in dump()["trades"] if t.get("sub") is None]
px = {}
for tk in {t["symbol"] for t in T} | {k for k, v in EA.items() if v}:
    p = os.path.join(DATA, f"bars_{tk}.json")
    if not os.path.exists(p): continue
    b = pd.DataFrame(json.load(open(p))).iloc[:, :5]; b.columns = list("tohlc")
    b.index = pd.DatetimeIndex(pd.to_datetime(b.t, unit="ms").dt.normalize()).astype("datetime64[ns]"); px[tk] = b[~b.index.duplicated()]   # 统一ns精度，否则 reindex 全是 NaN

# 每只股票每个财报：E = 8-K 提交日；反应 = C(E+1)/C(E-1)-1 (覆盖盘前/盘后发布两种情况)
EV = {}
for tk, ds in EA.items():
    if not ds or tk not in px: continue
    b = px[tk]; ix = b.index; rows = []
    for d in ds:
        d = pd.Timestamp(d); i = ix.searchsorted(d)
        if i < 1 or i + 1 >= len(ix): continue
        rows.append((ix[i], ix[i + 1], b.c.iloc[i + 1] / b.c.iloc[i - 1] - 1))
    EV[tk] = pd.DataFrame(rows, columns=["E", "E1", "ear"])
print("有财报日的股票", len(EV), "/", len(px), " 财报事件", sum(len(v) for v in EV.values()))


def leg_from(trs):
    on = np.zeros(len(c)); idx = c.index; pos = {d: i for i, d in enumerate(idx)}; ex = np.zeros(len(c))
    for t in trs:
        b = px.get(t["symbol"])
        if b is None: continue
        d0 = pd.to_datetime(t["entry_t"], unit="ms").normalize(); d1 = pd.to_datetime(t["exit_t"] or 4e12, unit="ms").normalize()
        seg = b[(b.index >= d0) & (b.index <= min(d1, b.index[-1]))]
        if len(seg) == 0: continue
        sg = 1 if t["side"] == "LONG" else -1; a = t["alloc"] or 0
        cl = seg.c.values.copy(); closed = t["exit_t"] is not None and seg.index[-1] >= d1
        if closed: cl[-1] = t["exit"]                                   # 出场日按真实出场价(含止损)
        prev = np.r_[t["entry"], cl[:-1]]; r = cl / prev - 1            # 入场日按真实入场价起算
        r = np.nan_to_num(r); r[0] -= COST; r[-1] -= COST if closed else 0
        for d, v in zip(seg.index, r):
            if d in pos: on[pos[d]] += sg * a * v; ex[pos[d]] += a
    return pd.Series(on, index=idx), pd.Series(ex, index=idx)


full, fex = leg_from(T)
print("还原 vs 美股腿 相关:", round(full["2018":].corr(s["2018":]), 3), "  年化(简单) 还原/原:", round(full["2022":].mean() * 252, 3), round(s["2022":].mean() * 252, 3))
B0 = vol_control(c, full); b0 = report(B0, "2022-01-01", "还原美股腿的方案F"); b0["ratio_0322"] = report(B0, "2022-03-01")["ratio"]
print("基准(还原美股腿)", fmt(b0), b0["ratio_0322"]); res = [b0]


def last_ear(tk, d, look=60):
    ev = EV.get(tk)
    if ev is None or not len(ev): return None
    e = ev[ev.E1 < d]
    if not len(e): return None
    e = e.iloc[-1]; return e.ear if (d - e.E1).days <= look * 1.45 else None


def next_e_days(tk, d):
    ev = EV.get(tk)
    if ev is None: return None
    e = ev[ev.E >= d]
    return None if not len(e) else np.busday_count(d.date(), e.E.iloc[0].date())


def test(name, keep):
    kept = [t for t in T if keep(t)]; frac = len(kept) / len(T)
    leg, ex = leg_from(kept); F2 = vol_control(c, leg); r = report(F2, "2022-01-01", name); r["ratio_0322"] = report(F2, "2022-03-01")["ratio"]
    sc = (fex["2022":].mean() / max(ex["2022":].mean(), 1e-9)); F3 = vol_control(c, leg * sc); r3 = report(F3, "2022-01-01")
    rng = np.random.default_rng(7); rr = []
    for _ in range(20):
        ks = [t for t in T if rng.random() < frac]; l2, _ = leg_from(ks); rr.append(report(vol_control(c, l2), "2022-03-01")["ratio"])
    r["keep"] = round(frac, 3); r["rand_med"] = round(float(np.median(rr)), 2); r["rand_p90"] = round(float(np.percentile(rr, 90)), 2); r["scaled"] = r3["ratio"]
    res.append(r); print(f"{name}: 保留{frac:.0%} {fmt(r)} 同窗口{r['ratio_0322']} | 随机剔除同比例 中位{r['rand_med']} 90分位{r['rand_p90']} | 放大到原暴露 {r3['ratio']}", flush=True)


ent = lambda t: pd.to_datetime(t["entry_t"], unit="ms").normalize()
for k in (2, 5, 10):
    test(f"F1 财报前{k}个交易日不开仓", lambda t, k=k: not ((n := next_e_days(t["symbol"], ent(t))) is not None and n <= k))
for x in (0.0, 0.03, 0.05):
    test(f"F2 只在最近财报反应>{x:.0%} 的开仓(无财报信息的照开)", lambda t, x=x: (e := last_ear(t["symbol"], ent(t))) is None or e > x)
for x in (0.03, 0.05, 0.10):
    test(f"F3 最近财报反应<-{x:.0%} 的不开仓", lambda t, x=x: (e := last_ear(t["symbol"], ent(t))) is None or e > -x)

# F5 独立 PEAD
allr = pd.DataFrame({tk: b.c.pct_change() for tk, b in px.items()}).reindex(c.index)
for x, H in itertools.product((0.03, 0.05, 0.08), (20, 40, 60)):
    pos = pd.DataFrame(0.0, index=c.index, columns=allr.columns)
    for tk, ev in EV.items():
        if tk not in pos: continue
        ix = px[tk].index
        for _, e in ev[ev.ear > x].iterrows():
            i = ix.searchsorted(e.E1) + 1                  # E+1 收盘后确认 → E+2 起持有(E+2 开盘买，近似用 E+2 收盘收益起算)
            if i + H < len(ix): pos.loc[ix[i + 1]:ix[i + H], tk] = 1.0
    n = pos.sum(axis=1).replace(0, np.nan); w = pos.div(n, axis=0).fillna(0).mul(np.minimum(n.fillna(0), 10) / 10, axis=0)   # 最多10只等权，名义≤1
    r = (w * allr.fillna(0)).sum(axis=1) - w.diff().abs().sum(axis=1) * COST
    a = stats(r["2022":END]); ai, ao = stats(r[IS0:IS1]), stats(r[OS0:END]); corr = r["2022":END].corr(B0["2022":END])
    for k in (0.5, 1.0):
        X = B0 + k * r; xr = report(X, "2022-01-01"); x2 = report(X, "2022-03-01")["ratio"]
        print(f"F5 PEAD独立 反应>{x:.0%} 持{H}天: 单独{a[0]:.1%}/{a[1]:.1%}/{a[2]:.2f}(内{ai[2]:.2f}/外{ao[2]:.2f}) 与F相关{corr:.2f} 叠加×{k}→{fmt(xr)} 同窗口{x2}", flush=True)
        res.append({"label": f"F5 PEAD 反应>{x:.0%} 持{H}天 叠加×{k}", **{q: xr[q] for q in ("cagr", "mdd", "ratio", "is", "oos")}, "ratio_0322": x2})
    if x == 0.05 and H == 40:
        Rp = vol_control(c, 0.8 * full + 0.2 * r * (fex.mean() or 1)); print("   替换20%美股腿→", fmt(report(Rp, "2022-01-01")), report(Rp, "2022-03-01")["ratio"])
pd.DataFrame(res).to_csv(os.path.join(HERE, "f_pead.csv"), index=False)
