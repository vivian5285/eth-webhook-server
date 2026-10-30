"""D. 主流5币相对强弱轮动 vs 现行'每年1月1日按3年收益回撤比定权重(单币≤40%)'。
做法：币圈三条子腿不变，只改每个币的权重，按周/月重算，只用重算日收盘前的数据，次日生效。
 R1 价格动量倾斜：过去L天涨幅排名，前k名等权(其余0)；
 R2 动量倾斜(连续)：w ∝ max(0, 动量排名分)，与现行年度权重 50/50 混合；
 R3 策略自身近期表现：过去L天每币'三腿合成'净值的收益回撤比 ∝ 权重(≤40%)；
 R4 ETH/BTC 比价趋势：ETH/BTC 在 EMA(n) 之上 → 山寨(ETH/SOL/BNB/XRP)权重×(1+a)，否则 BTC×(1+a)，再归一。
对照：现行年度权重；等权；每天都随机权重(检验框架无系统偏差)。"""
from common import *
import itertools
cc = coin_combo(); W0 = year_w(); syms = list(cc.columns)
px = pd.DataFrame({s: spot_close(s) for s in syms}).reindex(cc.index)
avail = px.notna() & (W0.sum(axis=1) > 0).values[:, None]
avail = avail & (pd.DataFrame({s: W0[s] > 0 for s in syms}) | True)   # 有数据就可用(SOL 2020-08 起)


def rebal_days(freq):
    if not freq: return set(cc.index)
    p = cc.index.to_period(freq); return set(cc.index[np.r_[True, p[1:] != p[:-1]]])   # 修正：原写法 Period.diff().ne(0) 恒为真=每天都调


def build(fn, freq):
    """fn(day) -> dict 权重(用 day 收盘前数据)；在 freq 周期首日重算，次日生效。"""
    rd = rebal_days(freq); rows = {}; cur = None
    for d in cc.index:
        if cur is None or d in rd: cur = fn(d)
        rows[d] = cur
    W = pd.DataFrame(rows).T.reindex(columns=syms).fillna(0)
    return W.shift(1).fillna(0)


def norm_cap(w, cap=0.4):
    w = {k: max(0, v) for k, v in w.items() if v > 0}
    if not w: return {}
    for _ in range(10):
        t = sum(w.values()); w = {k: v / t for k, v in w.items()}
        over = {k for k, v in w.items() if v > cap}
        if not over: break
        rest = 1 - cap * len(over); free = {k: v for k, v in w.items() if k not in over}
        tf = sum(free.values()) or 1
        w = {**{k: cap for k in over}, **{k: v / tf * rest for k, v in free.items()}}
        if all(v <= cap + 1e-9 for v in w.values()): break
    return w


def ok(d, L=0):  # 当天有至少 max(200, L+2) 天历史的币
    return [s for s in syms if px[s][:d].notna().sum() >= max(200, L + 2)]


def R1(L, k):
    def f(d):
        a = ok(d, L); m = {s: px[s][:d].iloc[-1] / px[s][:d].iloc[-L - 1] - 1 for s in a}
        top = sorted(a, key=lambda s: -m[s])[:k]; return {s: 1 / len(top) for s in top}
    return f


def R2(L, mix=0.5):
    def f(d):
        a = ok(d, L); m = {s: px[s][:d].iloc[-1] / px[s][:d].iloc[-L - 1] - 1 for s in a}
        rk = {s: i + 1 for i, s in enumerate(sorted(a, key=lambda s: m[s]))}
        tilt = norm_cap({s: rk[s] for s in a}, 1.0); y = W0.loc[d].to_dict()
        return norm_cap({s: mix * tilt.get(s, 0) + (1 - mix) * y.get(s, 0) for s in syms}, 1.0)
    return f


eqcc = (1 + cc).cumprod()


def R3(L):
    def f(d):
        sc = {}
        for s in ok(d):
            e = eqcc[s][:d].iloc[-L:]
            if len(e) < L: continue
            dd = (1 - e / e.cummax()).max(); r = e.iloc[-1] / e.iloc[0] - 1
            sc[s] = max(0, r) / max(dd, 0.02)
        w = norm_cap(sc) if sum(sc.values()) > 0 else {s: 1 / len(ok(d)) for s in ok(d)}; return w
    return f


ethbtc = px.ETHUSDT / px.BTCUSDT


def R4(n, a):
    e = ethbtc.ewm(span=n, adjust=False).mean()
    def f(d):
        y = W0.loc[d].to_dict(); up = ethbtc[:d].iloc[-1] > e[:d].iloc[-1]
        w = {s: y.get(s, 0) * ((1 + a) if (s != "BTCUSDT") == up else 1.0) for s in syms}
        return norm_cap(w, 1.0)
    return f


if __name__ == "__main__":
    res = []
    def run(name, W):
        _, F = with_weights(W); r = report(F, "2022-01-01", name); r2 = report(F, "2022-03-01")
        r["ratio_0322"] = r2["ratio"]; res.append(r); print(name, fmt(r), "同窗口", r2["ratio"], flush=True)
    run("现行年度权重(对照)", W0.shift(0))
    print("调仓日数 M/W:", len(rebal_days("M")), len(rebal_days("W")))
    run("等权(有数据的币)", build(lambda d: {s: 1 / len(ok(d)) for s in ok(d)}, "M"))
    rng = np.random.default_rng(0)
    run("随机权重每天换(框架检验)", build(lambda d: norm_cap(dict(zip(ok(d), rng.random(len(ok(d))))), 1.0), None))
    for freq in ("M", "W"):
        for L, k in itertools.product((30, 60, 90, 180), (2, 3)):
            run(f"R1 {freq} 动量{L}天 前{k}等权", build(R1(L, k), freq))
        for L in (30, 60, 90, 120, 180, 240, 365):
            run(f"R2 {freq} 动量{L}天排名倾斜50%+年度50%", build(R2(L), freq))
        for L in (60, 90, 180, 365):
            run(f"R3 {freq} 近{L}天三腿收益回撤比", build(R3(L), freq))
    for n, a in itertools.product((20, 50, 100, 200), (0.5, 1.0)):
        run(f"R4 日 ETH/BTC EMA{n} 倾斜{a}", build(R4(n, a), None))
    pd.DataFrame(res).to_csv(os.path.join(HERE, "d_rotation.csv"), index=False)
