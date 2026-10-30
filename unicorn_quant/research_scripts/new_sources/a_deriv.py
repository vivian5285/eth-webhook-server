"""A. 币安 UM 永续衍生品数据作方案F币圈腿的仓位调节(逐币、按仓位方向)。数据 fetch_metrics.py(data.binance.vision)。
每个指标先做成 180 天滚动 z 分数(只用过去)，用当天收盘值决定次日系数：
 拥挤多头(z 高) → 多头仓位×m；拥挤空头(z 低) → 空头仓位×m；另测反向用法(顺拥挤方向加仓 ×1.5)。
 A1 持仓量OI 7日变化率     A2 大户持仓多空比(top trader position)  A3 全市场账户多空比
 A4 主动买卖量比 7日均值    A5 基差(永续溢价指数) 7日均值             A6 资金费 7日累计
最后单独测一个'资金费极端反向'独立策略(闲置资金叠加)。数据从 2021 起，z 需 180 天预热 → 评估窗口 2022-01..2026-09。"""
from common import *
import itertools
cc = coin_combo(); res = []
DV = {s: pd.read_pickle(os.path.join(DATA, f"deriv_{s}.pkl")) for s in M5}
for s, d in DV.items(): d.index = pd.to_datetime(d.index).normalize()


# 基差：永续日收盘(1m数据合成，2022起) / 现货日收盘 - 1 (premiumIndexKlines 月文件 2022 后大面积缺失，改用自算)
PERP = os.path.join(HERE, "..", "orderflow_absorption", "data")
BASIS = {}
for _s in M5:
    _p = pd.read_pickle(os.path.join(PERP, f"{_s}_1d.pkl")); _p = pd.Series(_p.c.values, index=pd.to_datetime(_p.t, unit="ms").dt.normalize())
    BASIS[_s] = (_p / spot_close(_s).reindex(_p.index) - 1)


def z(x, n=180):
    return (x - x.rolling(n, min_periods=120).mean()) / x.rolling(n, min_periods=120).std()


def feat(name):
    out = {}
    for s, d in DV.items():
        if name == "oi": x = d.sum_open_interest_value.pct_change(7)
        elif name == "top": x = d.sum_toptrader_long_short_ratio
        elif name == "acct": x = d.count_long_short_ratio
        elif name == "taker": x = d.taker_mean.rolling(7).mean()
        elif name == "basis": x = BASIS[s].rolling(7).mean()
        elif name == "fund": x = d.funding.rolling(7).sum()
        out[s] = z(x.replace([np.inf, -np.inf], np.nan))
    Z = pd.DataFrame(out)
    return Z.shift(1).reindex(cc.index)           # 当天收盘值 → 次日生效


base_r = report(base(), "2022-01-01", "方案F"); base_r["ratio_0322"] = report(base(), "2022-03-01")["ratio"]; res.append(base_r)
print("方案F", fmt(base_r), base_r["ratio_0322"])
print("数据覆盖:", {s: (str(d.sum_open_interest.first_valid_index())[:10], str(d.funding.first_valid_index())[:10], str(d.premium.first_valid_index())[:10]) for s, d in DV.items()})
NAMES = {"oi": "A1 OI7日变化", "top": "A2 大户多空比", "acct": "A3 账户多空比", "taker": "A4 主动买卖比", "basis": "A5 基差", "fund": "A6 资金费7日"}
grid = {}
for k, nm in NAMES.items():
    Z = feat(k); print(nm, "z非空比例(2022+):", round(Z["2022":].notna().mean().mean(), 2), flush=True)
    for th, m in itertools.product((1.0, 1.5, 2.0, 2.5), (0.0, 0.5)):
        r = run_overlay(f"{nm} z>{th} 多头×{m}", (1 - (1 - m) * (Z > th)).astype(float), side=+1, res=res); grid[(k, "L", th, m)] = r["ratio_0322"]
        r = run_overlay(f"{nm} z<-{th} 空头×{m}", (1 - (1 - m) * (Z < -th)).astype(float), side=-1, res=res); grid[(k, "S", th, m)] = r["ratio_0322"]
    for th in (1.0, 1.5, 2.0):     # 反向：顺拥挤方向加仓 / 逆拥挤减仓
        run_overlay(f"{nm} z>{th} 多头×1.5(顺势加仓)", (1 + 0.5 * (Z > th)).astype(float), side=+1, res=res)
        run_overlay(f"{nm} z<-{th} 多头×0.5(冷清减多)", (1 - 0.5 * (Z < -th)).astype(float), side=+1, res=res)

# 独立策略：资金费 7日累计 z<-2 做多 7 天 / z>2 做空 7 天，5币等权，逐日盯市，扣费
Z = feat("fund"); px = pd.DataFrame({s: spot_close(s) for s in M5}).reindex(cc.index); pr = px.pct_change()
for th, H in itertools.product((1.5, 2.0, 2.5), (3, 7, 14)):
    pos = pd.DataFrame(0.0, index=cc.index, columns=M5)
    for s in M5:
        sig = np.where(Z[s] < -th, 1, np.where(Z[s] > th, -1, 0)); p = np.zeros(len(sig)); left = 0; cur = 0
        for i, v in enumerate(sig):
            if v != 0: cur, left = v, H
            p[i] = cur if left > 0 else 0; left -= 1
        pos[s] = p
    r = (pos * pr).sum(axis=1) / 5 - (pos.diff().abs().sum(axis=1) / 5) * COST
    a = stats(r["2022":END]); corr = r["2022":END].corr(base()["2022":END])
    x = report(addon(r, 1.0), "2022-01-01"); x2 = report(addon(r, 1.0), "2022-03-01")
    print(f"A7 资金费反向独立 z{th} 持{H}天: 单独{a[0]:.1%}/{a[1]:.1%}/{a[2]:.2f} 与F相关{corr:.2f}  叠加100%→{fmt(x)} 同窗口{x2['ratio']}", flush=True)
    res.append({"label": f"A7 资金费反向独立 z{th} 持{H}天 叠加", **{k: x[k] for k in ("cagr", "mdd", "ratio", "is", "oos")}, "ratio_0322": x2["ratio"]})
# 对照：每天都买/每天都空 独立策略(检验框架)
for sg in (1, -1):
    r = sg * pr.mean(axis=1) - 0 * COST; a = stats(r["2022":END]); print(f"对照 5币每天都{'买' if sg > 0 else '空'}(持有不换手): {a[0]:.1%}/{a[1]:.1%}")
pd.DataFrame(res).to_csv(os.path.join(HERE, "a_deriv.csv"), index=False)
g = pd.Series(grid); print("网格中同窗口比值>方案F(5.26)的比例:", round((g > base_r["ratio_0322"]).mean(), 2), " 最高:", g.idxmax(), g.max())
