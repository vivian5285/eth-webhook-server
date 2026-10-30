"""新信息源研究的公共框架：用 VPS 导出的方案F中间结果(dump_vps.py → data/ns_dump.json)在本地精确重建方案F，
再在上面做两种口径的叠加检验，全部逐日盯市：
  ① overlay(m)：币圈腿每天乘以仓位系数 m_t(只能用 t-1 收盘前的信息)，|Δm| 按吃单5bp+滑点1.5bp 扣成本，再走同一个组合波动率控制；
  ② addon(r)：方案F不变，候选用闲置资金单独记账，日收益直接相加(同 combo_mtm.py 口径)；
  ③ replace(r, w)：币圈腿 (1-w)×原币圈 + w×候选，再走组合波动率控制。
样本内 2022-01-01..2024-12-31，样本外 2025-01-01..2026-09-30。"""
import os, json, math, numpy as np, pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__)); DATA = os.path.join(HERE, "data")
M5 = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT"]
COST = 0.0005 + 0.00015          # 每单位名义变动的成本
FEE_PROD = 0.0008                # 生产 optimal.py 里组合波动率控制用的成本
VC_TARGET, VC_LB = 0.40, 60
IS0, IS1, OS0, END = "2022-01-01", "2024-12-31", "2025-01-01", "2026-09-30"
_D = None


def dump():
    global _D
    if _D is None: _D = json.load(open(os.path.join(DATA, "ns_dump.json")))
    return _D


def ser(rows):
    a = np.array(rows, float); s = pd.Series(a[:, 1], index=pd.to_datetime(a[:, 0], unit="ms" if a[0, 0] > 1e9 else "D").normalize())
    return s[~s.index.duplicated(keep="last")].sort_index()


def rets(curve):
    return curve.pct_change().dropna()


def legs():
    """币圈腿/美股腿/方案F 的日收益(美股腿只在交易日有值，其余日=0)。"""
    d = dump()
    c, s, f = (rets(ser(d[k])) for k in ("crypto", "stock", "F"))
    idx = c.index.union(s.index)
    return c.reindex(idx).fillna(0), s.reindex(idx).fillna(0), f.reindex(idx).fillna(0)


def coin_rets():
    """{子腿: DataFrame(日×币)} 每个币每条子腿的日收益。"""
    d = dump(); out = {}
    for sub, m in d["curves"].items():
        out[sub] = pd.DataFrame({s: rets(ser(rows)) for s, rows in m.items()}).fillna(0)
    return out


def coin_combo():
    """每个币三条子腿按 SUB_W 合成后的日收益 DataFrame。"""
    cr = coin_rets(); w = dump()["sub_w"]
    idx = sorted(set().union(*[set(x.index) for x in cr.values()]))
    return sum(w[k] * cr[k].reindex(idx).fillna(0) for k in cr)


def vol_control(rc, rs, wc=1.0, ws=1.0):
    """与 engine/optimal.py vol_control 同逻辑(按日收益向量化)。"""
    r = (wc * rc + ws * rs).values; out = np.empty(len(r)); hist = []; sp = 1.0; eq = 1.0
    for i, x in enumerate(r):
        s = 1.0
        if len(hist) >= VC_LB:
            w = np.array(hist[-VC_LB:]); sd = w.std(ddof=1) * math.sqrt(365)
            s = min(1.0, VC_TARGET / sd) if sd > 0 else 1.0
        out[i] = s * x - abs(s - sp) * (wc + ws) * FEE_PROD; sp = s; hist.append(x)
    return pd.Series(out, index=rc.index)


def stats(r):
    r = r.dropna()
    if len(r) < 30: return (np.nan,) * 3
    eq = (1 + r).cumprod(); yrs = (r.index[-1] - r.index[0]).days / 365.25
    cagr = eq.iloc[-1] ** (1 / yrs) - 1; mdd = (1 - eq / eq.cummax()).max()
    return cagr, mdd, cagr / mdd if mdd > 0 else np.nan


def report(r, start=None, label=""):
    """全程(start..END)/样本内/样本外 的 年化/回撤/比值。"""
    start = start or "2022-01-01"; r = r[start:END]
    a, i, o = stats(r), stats(r[:IS1]), stats(r[OS0:])
    return {"label": label, "cagr": round(a[0], 3), "mdd": round(a[1], 3), "ratio": round(a[2], 2),
            "is": round(i[2], 2), "oos": round(o[2], 2)}


def overlay(m, rc=None, rs=None):
    """m: 按日的币圈腿仓位系数(当天生效)。返回新的方案F日收益。"""
    c, s, _ = legs(); rc = c if rc is None else rc; rs = s if rs is None else rs
    m = m.reindex(rc.index).ffill().fillna(1.0)
    rc2 = m * rc - m.diff().abs().fillna(0) * COST
    return vol_control(rc2, rs)


def addon(r, k=1.0):
    _, _, f = legs(); return f + k * r.reindex(f.index).fillna(0)


def replace(r, w):
    c, s, _ = legs(); r = r.reindex(c.index).fillna(0)
    return vol_control((1 - w) * c + w * r, s)


def base():
    c, s, f = legs(); return vol_control(c, s)


if __name__ == "__main__":
    c, s, f = legs(); f2 = base()
    print("dump F", report(f, "2017-10-01"), "\nrebuilt", report(f2, "2017-10-01"))
    print("同窗口 2022-03", report(f2, "2022-03-01"))
    print("max abs diff", (f - f2)["2018":].abs().max())


def year_w():
    W = dump()["weights"]; cc = coin_combo()
    return pd.DataFrame([[W.get(str(d.year), {}).get(s, 0.0) for s in cc.columns] for d in cc.index], index=cc.index, columns=cc.columns)


def crypto_from_coins(cc=None, w=None):
    cc = coin_combo() if cc is None else cc; w = year_w() if w is None else w
    return (cc * w.reindex(cc.index).fillna(0)).sum(axis=1)


_EXP = None


def exposure():
    """由交易记录还原每天每币的净方向暴露 Σ side×子腿权重×alloc (该日内持有的仓位)。"""
    global _EXP
    if _EXP is not None: return _EXP
    cc = coin_combo(); E = pd.DataFrame(0.0, index=cc.index, columns=cc.columns)
    days = cc.index.values.astype("datetime64[ms]").astype(np.int64)
    for t in dump()["trades"]:
        if t.get("sub") is None or t["symbol"] not in E.columns: continue
        a = t["entry_t"]; b = t["exit_t"] or 4e12; sgn = 1 if t["side"] == "LONG" else -1
        m = (days + 86400000 > a) & (days < b)
        E.loc[m, t["symbol"]] += sgn * t["w"] * (t.get("alloc") or 1.0)
    _EXP = E; return E


def overlay_coins(M, side=+1, cost=True):
    """M: DataFrame(日×币) 仓位系数(当天生效、只用前一天收盘前信息)。只作用于净暴露方向==side 的币(side=0 全部)。
    返回 (新币圈腿日收益, 新方案F日收益)。成本按 |Δm|×|暴露| 扣。"""
    c, s, _ = legs(); cc = coin_combo(); E = exposure(); w = year_w()
    M = M.reindex(cc.index).ffill().reindex(columns=cc.columns).fillna(1.0)
    act = (np.sign(E) == side) if side else pd.DataFrame(True, index=E.index, columns=E.columns)
    Meff = M.where(act, 1.0)
    cc2 = cc * Meff - (Meff.diff().abs().fillna(0) * E.abs() * COST if cost else 0)
    rc = crypto_from_coins(cc2, w).reindex(c.index).fillna(0)
    return rc, vol_control(rc, s)


def spot_close(sym):
    b = pd.DataFrame(json.load(open(os.path.join(DATA, f"bars_{sym}.json")))).iloc[:, :5]; b.columns = list("tohlc")
    return pd.Series(b.c.values, index=pd.to_datetime(b.t.values, unit="ms").normalize())


def with_weights(W, cost=True):
    """用自定义币权重 W(日×币，当天生效) 合成币圈腿，再走组合波动率控制。权重变动成本 |Δw|×|暴露|。"""
    c, s, _ = legs(); cc = coin_combo(); E = exposure()
    W = W.reindex(cc.index).ffill().reindex(columns=cc.columns).fillna(0)
    rc = (cc * W).sum(axis=1) - ((W.diff().abs().fillna(0) * E.abs()).sum(axis=1) * COST if cost else 0)
    rc = rc.reindex(c.index).fillna(0); return rc, vol_control(rc, s)


def fmt(rep):
    return f"{rep['cagr']:.1%}/{rep['mdd']:.1%}/{rep['ratio']:.2f} 内{rep['is']:.2f} 外{rep['oos']:.2f}"


def yh(name):
    return pd.read_pickle(os.path.join(DATA, f"yh_{name}.pkl"))


def to_crypto_days(sig, lag=1):
    """把按某日历(美股交易日等)的信号对齐到币圈日：用 ≤ d-lag 的最近值，周末/假日沿用。"""
    idx = coin_combo().index
    s = sig.copy(); s.index = s.index + pd.Timedelta(days=lag)
    return s.reindex(idx.union(s.index)).ffill().reindex(idx)


def run_overlay(name, m, side=+1, res=None, start="2022-01-01"):
    """m: Series(日) 或 DataFrame(日×币)。打印并收集 全程/样本内/样本外 + 同窗口(2022-03)比值。"""
    cc = coin_combo()
    M = pd.DataFrame({s: m for s in cc.columns}) if isinstance(m, pd.Series) else m
    _, F = overlay_coins(M, side); r = report(F, start, name); r["ratio_0322"] = report(F, "2022-03-01")["ratio"]
    r["avg_m"] = round(float(M.reindex(cc.index)[start:END].mean().mean()), 3)
    if res is not None: res.append(r)
    print(name, fmt(r), "同窗口", r["ratio_0322"], "平均系数", r["avg_m"], flush=True); return r
