"""大黑美股(UCvZznN0wCMZaP2VdtLyxPTw) QQQ/TQQQ 系列：EMA5/30 金叉死叉只做多、MACD、牛熊均线切换 TQQQ/QQQ、动态风险分配。
数据 Yahoo 日线(QQQ 1999起，TQQQ 2010起，^IRX 无风险利率)。他的主力是 QQQ 30 分钟线，我们只有日线长历史，所以只测日线版；
另用真实 TQQQ 校验'QQQ×3 每日重置'模拟(费率0.9%/年+融资(L-1)×IRX)。
成交：收盘出信号 → 次日开盘成交；隔夜跳空归旧仓位、当日开→收归新仓位；每次换仓 QQQ 2bp/边。
最后逐日盯市接进方案F(同窗口 2022-03..2026-09 比值5.26)。"""
import os, sys, json, urllib.request, numpy as np, pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__)); D = os.path.join(HERE, "data"); os.makedirs(D, exist_ok=True)
SC = os.path.join(HERE, "..", "speculation_channel"); sys.path.insert(0, SC)


def stats(r):
    """按实际日历跨度年化(美股一年约252个交易日，combo.stats 的 len/365.25 会把美股年化算高)。"""
    eq = (1 + r).cumprod(); yrs = max((r.index[-1] - r.index[0]).days / 365.25, 0.1)
    cagr = eq.iloc[-1] ** (1 / yrs) - 1; mdd = (1 - eq / eq.cummax()).max()
    return cagr, mdd, cagr / mdd if mdd > 0 else np.nan


def yahoo(t):
    f = os.path.join(D, f"{t.replace('^', '')}.pkl")
    if os.path.exists(f): return pd.read_pickle(f)
    u = f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.request.quote(t)}?period1=915148800&period2=1793500000&interval=1d&events=div,split"
    j = json.load(urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0"}), timeout=30))["chart"]["result"][0]
    q = j["indicators"]["quote"][0]; adj = j["indicators"].get("adjclose", [{}])[0].get("adjclose", q["close"])
    df = pd.DataFrame({"o": q["open"], "h": q["high"], "l": q["low"], "c": q["close"], "ac": adj}, index=pd.to_datetime(j["timestamp"], unit="s").normalize())
    df = df.dropna(subset=["c", "o"]); df = df[~df.index.duplicated(keep="last")]
    df.to_pickle(f); return df


def ema(x, n): return x.ewm(span=n, adjust=False).mean()


def legs(q):
    """把 QQQ 日收益拆成 隔夜(前收→今开) 与 日内(今开→今收)，用复权因子修正分红。"""
    f = q.ac / q.c
    on = (q.o * f) / (q.c.shift(1) * f.shift(1)) - 1
    intra = q.c / q.o - 1
    return on.fillna(0), intra.fillna(0)


def run(q, sig, lev, rf, cost=0.0002, er=0.009):
    """sig: 收盘时的目标暴露(0/1)，lev: 目标杠杆序列(可随牛熊变)。返回日收益。"""
    on, intra = legs(q)
    P = (sig * lev).shift(1).fillna(0)          # 收盘t-1决定、开盘t生效
    Pprev = P.shift(1).fillna(0)
    fin = (np.maximum(P - 1, 0) * rf + np.where(P > 1, er, 0)) / 252
    r = Pprev * on + P * intra - fin - cost * (P - Pprev).abs()
    return r


def regime_lev(q, n, hi=3.0, lo=1.0):
    s = q.c.rolling(n).mean()
    return pd.Series(np.where(q.o > s.shift(1), hi, lo), index=q.index)   # 他用'当天开盘价 vs 日线均线'


def macd_sig(c, f=12, s=26, g=9):
    m = ema(c, f) - ema(c, s); return (m > ema(m, g)).astype(float)


def dyn_alloc(q, n=150, bull=0.7, bear=0.05, rf=None):
    """动态风险分配(买入持有版)：牛市(收盘>n日线) TQQQ占bull、熊市占bear，其余QQQ；跨牛熊时再平衡，其余时间自由漂移。"""
    on, intra = legs(q); r1 = (1 + on) * (1 + intra) - 1
    r3 = 3 * r1 - (2 * rf + 0.009) / 252
    st = (q.c > q.c.rolling(n).mean()).shift(1).fillna(False)
    w = np.zeros(len(q)); cur = None; wt = 0.0; out = np.zeros(len(q))
    for i in range(len(q)):
        reg = st.iloc[i]
        if reg != cur: wt = bull if reg else bear; cur = reg
        out[i] = wt * r3.iloc[i] + (1 - wt) * r1.iloc[i]
        g3, g1 = wt * (1 + r3.iloc[i]), (1 - wt) * (1 + r1.iloc[i]); wt = g3 / max(g3 + g1, 1e-12)
    return pd.Series(out, index=q.index)


def table(name, r, rows):
    r = r.dropna()
    for lab, a, b in [("2000起", "2000-01-01", None), ("2010起", "2010-02-11", None), ("同窗口", "2022-03-01", "2026-09-30")]:
        x = r[a:b]; s = stats(x); rows.append((name, lab, s[0], s[1], s[2]))


if __name__ == "__main__":
    q = yahoo("QQQ"); tq = yahoo("TQQQ"); irx = yahoo("^IRX").c / 100
    rf = irx.reindex(q.index).ffill().fillna(0.02)
    rows = []
    hold = run(q, pd.Series(1.0, index=q.index), pd.Series(1.0, index=q.index), rf); table("长持QQQ", hold, rows)
    # 模拟 vs 真实 TQQQ
    sim3 = run(q, pd.Series(1.0, index=q.index), pd.Series(3.0, index=q.index), rf, cost=0)
    real = tq.ac.pct_change()
    j = pd.concat([sim3, real], axis=1, keys=["sim", "real"]).dropna()["2010-03-01":]
    print(f"TQQQ 模拟校验 2010-03起：模拟年化 {stats(j.sim)[0]:.1%} 真实 {stats(j.real)[0]:.1%}，日相关 {j.sim.corr(j.real):.4f}")
    table("长持TQQQ(模拟)", sim3, rows)
    one = pd.Series(1.0, index=q.index); three = pd.Series(3.0, index=q.index)
    for f_, s_ in [(5, 30), (3, 30), (4, 30), (5, 25), (10, 30), (8, 30)]:
        sig = (ema(q.c, f_) > ema(q.c, s_)).astype(float)
        table(f"EMA{f_}/{s_} 1x", run(q, sig, one, rf), rows)
        table(f"EMA{f_}/{s_} 3x", run(q, sig, three, rf), rows)
        for n in (100, 150):
            table(f"EMA{f_}/{s_} 牛{n}日线3x/熊1x", run(q, sig, regime_lev(q, n), rf), rows)
    ms = macd_sig(q.c)
    table("MACD日线 1x", run(q, ms, one, rf), rows); table("MACD日线 3x", run(q, ms, three, rf), rows)
    table("MACD日线 牛150 3x/熊1x", run(q, ms, regime_lev(q, 150), rf), rows)
    for n, bu, be in [(100, 0.7, 0.05), (150, 0.7, 0.05), (150, 0.3, 0.1), (200, 0.7, 0.05)]:
        table(f"动态分配 牛{n} T{bu:.0%}/熊T{be:.0%}", dyn_alloc(q, n, bu, be, rf), rows)
    out = pd.DataFrame(rows, columns=["策略", "区间", "年化", "回撤", "比值"])
    pd.set_option("display.width", 200); pd.set_option("display.max_rows", 300)
    print(out.pivot_table(index="策略", columns="区间", values=["年化", "回撤", "比值"], sort=False).round(3).to_string())
    out.to_csv(os.path.join(HERE, "qqq_tqqq.csv"), index=False)
