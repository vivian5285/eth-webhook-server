"""2026-10-19 宝贝：研究幻方那种"对冲掉市场涨跌"的策略，作为方案F的补充。
事先定好(不调参)的5个经典市场中性因子，美股，Yahoo日线：
  MOM   12-1 月动量：每月初，过去12个月(去掉最近1个月)涨幅前20%做多、后20%做空
  RMOM  残差动量：每只股票对 QQQ 回归(过去252天)，去掉大盘部分后的12-1月残差累计/波动，前20%多后20%空
  REV   一周反转：每周，过去5天跌最多的20%做多、涨最多的20%做空
  LVOL  低波动：每月，过去60天波动最低20%做多、最高20%做空
  SECT  行业多空：每月，6个月行业涨幅前2行业做多、后2行业做空(现行轮动的多空版)
每条腿：多空各0.5、等权；再用 QQQ 把剩余beta对冲到0(过去252天beta)。收盘出信号 -> 次日开盘成交(开盘到开盘收益)，换手x0.08%。
每条腿再按过去60天波动缩放到年化15%(上限2倍名义)以便比较。股票池两种：A=币安可交易53只(实盘能做空的)，B=全部78只。
注意：股票池是今天还在的名单(幸存者偏差)；永续资金费没算。"""
import sys, os, math, time, collections, pickle
os.nice(19)
sys.path.insert(0, "/home/stratroster/unicorn_quant"); sys.path.insert(0, "/home/stratroster/eth-webhook-server")
from engine import optimal as O
DAY = 86400000; FEE = 0.0008; H = 18993


def load(tks):
    D = {}
    for tk in tks:
        try:
            b = O.stock_daily(tk)
            if b:
                D[tk] = {x["t"] // DAY: x for x in b}
        except Exception as e:
            print("skip", tk, e)
    return D


ALL = sorted(O.SEC_OF); TR = sorted(O.tradable_set())
D = load(ALL + ["QQQ"]); Q = D.pop("QQQ")
days = sorted(d for d in Q if d >= 16800)


def build(univ, fac):
    U = [s for s in univ if s in D]
    op = {s: [D[s].get(d, {}).get("o") for d in days] for s in U}
    cl = {s: [D[s].get(d, {}).get("c") for d in days] for s in U}
    qo = [Q[d]["o"] for d in days]; qc = [Q[d]["c"] for d in days]

    def ret_oo(arr, i):
        if i + 1 >= len(arr):
            return 0.0
        a, b = arr[i], arr[i + 1]
        return (b / a - 1) if a and b else 0.0

    qr = [0.0] + [qc[i] / qc[i - 1] - 1 for i in range(1, len(days))]
    sr = {s: [0.0] + [(cl[s][i] / cl[s][i - 1] - 1) if cl[s][i] and cl[s][i - 1] else 0.0 for i in range(1, len(days))] for s in U}

    def beta(s, i, n=252):
        xs = qr[i - n + 1:i + 1]; ys = sr[s][i - n + 1:i + 1]
        mx = sum(xs) / n; my = sum(ys) / n
        vx = sum((x - mx) ** 2 for x in xs)
        return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / vx if vx else 1.0

    w = {}; eq = 1.0; out = {}; hist = []; prev_w = {}; lastm = None; lastwk = None
    for i in range(260, len(days) - 1):
        d = days[i]; g = time.gmtime(d * 86400); m = (g.tm_year, g.tm_mon); wk = (d + 3) // 7
        rebal = (fac == "REV" and wk != lastwk) or (fac != "REV" and m != lastm)
        lastm, lastwk = m, wk
        if rebal:
            sc = {}
            live = [s for s in U if cl[s][i] and cl[s][i - 252] and all(cl[s][j] for j in (i - 21, i - 60, i - 126, i - 5))]
            for s in live:
                if fac == "MOM":
                    sc[s] = cl[s][i - 21] / cl[s][i - 252] - 1
                elif fac == "REV":
                    sc[s] = -(cl[s][i] / cl[s][i - 5] - 1)
                elif fac == "LVOL":
                    x = sr[s][i - 59:i + 1]; mu = sum(x) / 60; sc[s] = -math.sqrt(sum((y - mu) ** 2 for y in x) / 59)
                elif fac == "RMOM":
                    b_ = beta(s, i - 21); res = [sr[s][j] - b_ * qr[j] for j in range(i - 252, i - 21)]
                    mu = sum(res) / len(res); sd = math.sqrt(sum((y - mu) ** 2 for y in res) / (len(res) - 1)) or 1e-9
                    sc[s] = sum(res) / sd
            if fac == "SECT":
                secs = collections.defaultdict(list)
                for s in live:
                    secs[O.SEC_OF[s]].append(cl[s][i] / cl[s][i - 126] - 1)
                ss = {k: sum(v) / len(v) for k, v in secs.items() if len(v) >= 2}
                rk = sorted(ss, key=ss.get, reverse=True)
                if len(rk) >= 4:
                    top, bot = set(rk[:2]), set(rk[-2:])
                    L = [s for s in live if O.SEC_OF[s] in top]; S = [s for s in live if O.SEC_OF[s] in bot]
                else:
                    L = S = []
            else:
                rk = sorted(sc, key=sc.get, reverse=True); n = max(1, len(rk) // 5)
                L, S = (rk[:n], rk[-n:]) if len(rk) >= 10 else ([], [])
            nw = {}
            for s in L:
                nw[s] = nw.get(s, 0) + 0.5 / len(L)
            for s in S:
                nw[s] = nw.get(s, 0) - 0.5 / len(S)
            if nw:
                nw["QQQ"] = -sum(v * beta(s, i) for s, v in nw.items())
            w = nw
        k = 1.0
        if len(hist) >= 60:
            x = hist[-60:]; mu = sum(x) / 60; sd = math.sqrt(sum((y - mu) ** 2 for y in x) / 59) * math.sqrt(252)
            k = min(2.0, 0.15 / sd) if sd > 0 else 1.0
        ww = {s: v * k for s, v in w.items()}
        rr = {s: (ret_oo(qo, i + 1) if s == "QQQ" else ret_oo(op[s], i + 1)) for s in w}
        r = sum(v * rr[s] for s, v in ww.items())
        turn = sum(abs(ww.get(s, 0) - prev_w.get(s, 0)) for s in set(ww) | set(prev_w)); prev_w = ww
        hist.append(sum(v * rr[s] for s, v in w.items()))
        eq *= 1 + r - turn * FEE; out[days[i + 1]] = eq
    return out


def st(cv, a=0, z=99999):
    ks = [k for k in sorted(cv) if a <= k <= z]; v = [cv[k] for k in ks]; pk = 0; dd = 0
    for x in v:
        pk = max(pk, x); dd = max(dd, 1 - x / pk)
    yrs = (ks[-1] - ks[0]) / 365.25; cagr = (v[-1] / v[0]) ** (1 / yrs) - 1
    rs = [v[j] / v[j - 1] - 1 for j in range(1, len(v))]; mu = sum(rs) / len(rs)
    sd = math.sqrt(sum((x - mu) ** 2 for x in rs) / (len(rs) - 1))
    return 100 * cagr, 100 * dd, mu / sd * math.sqrt(252) if sd else 0


def yr(cv):
    by = {}
    for k in sorted(cv):
        by.setdefault(time.gmtime(k * 86400).tm_year, []).append(cv[k])
    o = []; pe = None
    for y in sorted(by):
        xs = by[y]; b0 = pe or xs[0]; o.append(f"{y%100}:{100*(xs[-1]/b0-1):+.0f}"); pe = xs[-1]
    return " ".join(o)


def corr(a, b):
    ks = sorted(set(a) & set(b))
    ra = [a[ks[j]] / a[ks[j - 1]] - 1 for j in range(1, len(ks))]; rb = [b[ks[j]] / b[ks[j - 1]] - 1 for j in range(1, len(ks))]
    ma, mb = sum(ra) / len(ra), sum(rb) / len(rb)
    return sum((x - ma) * (y - mb) for x, y in zip(ra, rb)) / math.sqrt(sum((x - ma) ** 2 for x in ra) * sum((y - mb) ** 2 for y in rb))


print("数据", len(D), "只股票，", time.strftime("%Y-%m-%d", time.gmtime(days[0] * 86400)), "起", flush=True)
qcv = {d: Q[d]["c"] for d in days}
res = {}
for pool, univ in (("A53", TR), ("B78", ALL)):
    for fac in ("MOM", "RMOM", "REV", "LVOL", "SECT"):
        cv = build(univ, fac); res[(pool, fac)] = cv
        x = st(cv); a1 = st(cv, 0, H); a2 = st(cv, H)
        print(f"{pool} {fac:5s} 年化{x[0]:+6.1f}% 回撤{x[1]:5.1f}% 夏普{x[2]:5.2f} | 前半夏普{a1[2]:5.2f} 后半{a2[2]:5.2f} | 与QQQ相关{corr(cv, qcv):+.2f} | {yr(cv)}", flush=True)
pickle.dump(res, open("/tmp/mkt_neutral_res.pkl", "wb"))
cd, _, _, _, CD = O.crypto_leg(); O.crypto_8h_leg("st", CD["BTCUSDT"]); O.crypto_8h_leg("bb", CD["BTCUSDT"])
coin = {s: O.combine([O.CURVES["日线EMA8/30"][s], O.CURVES["8h超级趋势"][s], O.CURVES["8h布林突破"][s]], [1 / 3] * 3) for s in O.M5}
cB = O.weighted_basket(coin, O.coin_weights(coin, range(2017, 2027))); sc = O.stock_leg()[0]
F0 = O.vol_control([cB, sc], [1, 1], 2.0)[0]; A0 = 17410


def ratio(F, a=A0, z=99999):
    x = st(F, a, z); return x[0] / x[1]


print(f"\n现行方案F 比值 全程{ratio(F0):.2f} 前半{ratio(F0, A0, H):.2f} 后半{ratio(F0, H):.2f}")
for key, cv in res.items():
    a1, a2 = st(cv, 0, H)[2], st(cv, H)[2]
    print(f"  {key[0]} {key[1]:5s} 与方案F相关{corr(cv, F0):+.2f}", end="")
    if a1 > 0.3 and a2 > 0.3:
        for wgt, g in ((0.5, 2.0), (1.0, 2.0), (0.5, 2.5), (1.0, 3.0)):
            F = O.vol_control([cB, sc, cv], [1, 1, wgt], g)[0]
            x = st(F, A0)
            print(f" | +{wgt}(总{g}) {x[0]:.0f}%/{x[1]:.1f}% 比{ratio(F):.2f} 前{ratio(F, A0, H):.2f} 后{ratio(F, H):.2f}", end="")
    print(flush=True)
print("ALL DONE")
