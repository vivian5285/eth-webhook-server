"""Ross Cameron(Warrior Trading)动量选股 → 两个能拿到数据的对照：
A. 币安全部 USDT 现货(含已下架 BREAK)，"当日暴涨+放量"的币 = 他的 gap&go 低流通股：
   信号日 D：涨幅≥g、成交额≥5×过去20日均额、成交额≥500万U、收在当日上30%区间；D+1 开盘做多。
   出场三种：①D+1 收盘平 ②止损 D 日中点 + 2R ③3ATR 追踪(最多10天)；另测反向(暴涨后做空)看真实效应是延续还是回落。
B. 78 只美股日线：开盘跳空≥4%(开盘就知道) → 开盘买、收盘平(他是日内做)；以及跳空日收阳 → 次日延续。
每边 0.1%(山寨币滑点更大)；结果为每笔平均净收益%与 2022 前/后两段。"""
import os, json, glob, numpy as np, pandas as pd, ytlib as Y
HERE = os.path.dirname(os.path.abspath(__file__)); FEE = 0.001; SPLIT = pd.Timestamp("2023-01-01").value // 10**6


def load_alts():
    out = {}
    for f in glob.glob(os.path.join(HERE, "alts_1d", "*.json")):
        d = json.load(open(f))
        if len(d) < 60: continue
        b = pd.DataFrame(d, columns=["t", "o", "h", "l", "c", "qv"])
        out[os.path.basename(f)[:-5]] = b
    return out


def alt_events(A, g=0.20, rv=5.0):
    rows = []
    for s, b in A.items():
        if s in ("BTCUSDT", "ETHUSDT"): continue
        o, h, l, c, qv = (b[k].values for k in ("o", "h", "l", "c", "qv")); n = len(c); at = Y.atr(b.assign(v=qv))
        avg = pd.Series(qv).rolling(20).mean().shift(1).values
        for i in range(21, n - 1):
            if c[i - 1] <= 0 or not avg[i] or avg[i] <= 0: continue
            r = c[i] / c[i - 1] - 1; pos = (c[i] - l[i]) / max(h[i] - l[i], 1e-12)
            if r >= g and qv[i] >= rv * avg[i] and qv[i] >= 5e6 and pos >= 0.7:
                e = o[i + 1]; mid = (h[i] + l[i]) / 2
                r1 = c[i + 1] / e - 1                                       # ① 次日收盘平
                R = e - mid; tg = e + 2 * R; r2 = None                      # ② 中点止损 + 2R
                for j in range(i + 1, min(n, i + 11)):
                    if l[j] <= mid: r2 = min(o[j], mid) / e - 1 if j > i + 1 else mid / e - 1; break
                    if h[j] >= tg: r2 = tg / e - 1; break
                if r2 is None: r2 = c[min(n - 1, i + 10)] / e - 1
                st = e - 3 * at[i]; best = e; r3 = None                     # ③ 3ATR 追踪 最多10天
                for j in range(i + 1, min(n, i + 11)):
                    if l[j] <= st: r3 = min(o[j], st) / e - 1; break
                    best = max(best, h[j]); st = max(st, best - 3 * at[j])
                if r3 is None: r3 = c[min(n - 1, i + 10)] / e - 1
                rows.append(dict(sym=s, t=int(b.t.values[i]), gain=r, rv=qv[i] / avg[i], r1=r1 - 2 * FEE, r2=r2 - 2 * FEE, r3=r3 - 2 * FEE,
                                 short1=-r1 - 2 * FEE))
    return pd.DataFrame(rows)


def show(E, tag):
    if not len(E): print(tag, "无"); return
    pre, post = E[E.t < SPLIT], E[E.t >= SPLIT]
    def f(x, k): return f"{100*x[k].mean():+.2f}%(胜{(x[k]>0).mean():.0%},中位{100*x[k].median():+.2f}%)" if len(x) else "--"
    print(f"{tag} {len(E)}笔(前{len(pre)}/后{len(post)}) | ①次日收盘 {f(E,'r1')} 前{f(pre,'r1')} 后{f(post,'r1')} | ②中点止损2R {f(E,'r2')} 后{f(post,'r2')}"
          f" | ③3ATR追踪 {f(E,'r3')} 后{f(post,'r3')} | 反向做空次日 {f(E,'short1')} 后{f(post,'short1')}", flush=True)
    return E


def stock_gaps():
    rows = []
    for tk, b in Y.stocks().items():
        o, h, l, c = b.o.values, b.h.values, b.l.values, b.c.values; at = Y.atr(b)
        for i in range(20, len(c) - 1):
            gp = o[i] / c[i - 1] - 1
            if gp >= 0.04:
                rows.append(dict(sym=tk, t=int(b.t.values[i]), gap=gp, d0=c[i] / o[i] - 1 - 2 * 0.0008,
                                 d1=(c[i + 1] / o[i + 1] - 1 - 2 * 0.0008) if c[i] > o[i] else np.nan,
                                 hold5=(c[min(len(c) - 1, i + 5)] / o[i] - 1 - 2 * 0.0008)))
    E = pd.DataFrame(rows); pre, post = E[E.t < Y.BS.H * Y.DAY], E[E.t >= Y.BS.H * Y.DAY]
    for k, nm in (("d0", "跳空≥4% 开盘买收盘平"), ("d1", "跳空日收阳→次日开盘买收盘平"), ("hold5", "跳空≥4% 开盘买持5天")):
        x = E[k].dropna()
        print(f"美股 {nm}: {len(x)}笔 平均{100*x.mean():+.2f}% 胜{(x>0).mean():.0%} | 2022前 {100*pre[k].mean():+.2f}% 后 {100*post[k].mean():+.2f}%", flush=True)
    return E


if __name__ == "__main__":
    A = load_alts(); print(len(A), "个币", flush=True)
    for g in (0.15, 0.20, 0.30):
        for rv in (3.0, 5.0):
            show(alt_events(A, g, rv), f"山寨 涨≥{g:.0%} 量≥{rv:.0f}x")
    # 再按"当时已上市≥180天"去掉新币上线首波
    E = alt_events(A, 0.20, 5.0)
    first = {s: int(b.t.values[0]) for s, b in A.items()}
    show(E[E.apply(lambda r: r.t - first[r.sym] > 180 * 86400000, axis=1)], "山寨 涨≥20% 量≥5x 上市≥180天")
    stock_gaps()
