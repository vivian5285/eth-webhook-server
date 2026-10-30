"""DeepSeek 清单 #31 配对交易(币圈主流5币 10对，日线 2017起) + #37 马丁格尔(只作风险反面教材)。
配对：滚动 90 日对数价 OLS 对冲比 β，价差 z(60日)；近 180 日价差 AR(1) 半衰期<30 天才交易(协整代理，statsmodels 无)；
|z|>2 反向开(两腿各 0.5 名义)，z 回 0 平，|z|>4 或 30 天止损；每腿每边 0.08%。10 对等权作独立一条腿，按 0.5/1 权重加进方案F。
马丁：BTC 1h 每根随机方向开仓，止盈/止损都 1%，亏了按 1→2→6→1(DeepSeek 写法)或翻倍 1→2→4→8 加码，赢了回到 1。"""
import math, numpy as np, pandas as pd, ytlib as Y, base as BS, lib as L
O = Y.O; B = BS.base(); b0 = B["b"]; print("基准 方案F", BS.fmt(b0), flush=True)
D = Y.crypto("1d"); CL = pd.DataFrame({s: pd.Series(D[s].c.values, index=(D[s].t.values // Y.DAY).astype(int)) for s in O.M5}).sort_index()


def pair_curve(x, y, fee=0.0008):
    df = pd.concat([np.log(CL[x]), np.log(CL[y])], axis=1, keys=["x", "y"]).dropna()
    lx, ly = df.x.values, df.y.values; n = len(df); eq = 1.0; out = {}; pos = 0; held = 0; qx = qy = 0.0
    px, py = np.exp(lx), np.exp(ly)
    for i in range(240, n):
        if eq <= 0.01:                            # 爆了就停(否则 1e-9 起步再复利会算出天文数字)
            out[int(df.index[i])] = 0.01; continue
        if pos:                                   # 盯市
            eq += qx * (px[i] - px[i - 1]) + qy * (py[i] - py[i - 1]); held += 1
        wx, wy = lx[i - 90:i], ly[i - 90:i]; beta = np.polyfit(wx, wy, 1)[0]
        sp = ly[i - 180:i + 1] - beta * lx[i - 180:i + 1]; z = (sp[-1] - sp[-60:].mean()) / (sp[-60:].std() + 1e-12)
        s0 = sp[:-1] - sp[:-1].mean(); phi = np.dot(s0[1:], s0[:-1]) / (np.dot(s0[:-1], s0[:-1]) + 1e-12)
        hl = -math.log(2) / math.log(phi) if 0 < phi < 1 else 999
        if pos and (pos * z >= 0 or abs(z) > 4 or held >= 30):
            eq -= (abs(qx) * px[i] + abs(qy) * py[i]) * fee; pos = 0; qx = qy = 0.0
        if not pos and abs(z) > 2 and hl < 30:
            pos = -1 if z > 0 else 1                 # z>0 价差偏高：空 y 多 x
            qy = pos * 0.5 * eq / py[i]; qx = -pos * 0.5 * eq / px[i] * min(max(beta, 0.2), 3); held = 0
            eq -= (abs(qx) * px[i] + abs(qy) * py[i]) * fee
        out[int(df.index[i])] = max(eq, 0.01)
    return out


pairs = [(a, b) for i, a in enumerate(O.M5) for b in O.M5[i + 1:]]
cvs = {f"{a}/{b}": pair_curve(a, b) for a, b in pairs}
for k, v in cvs.items():
    x = BS.three(v); print(f"  {k:18s} {BS.fmt(x)}", flush=True)
P = O.combine(list(cvs.values()), [1 / len(cvs)] * len(cvs)); xp = BS.three(P)
print(f"配对10对等权 腿 {BS.fmt(xp)} | {BS.yr(P)}", flush=True)
for w in (0.5, 1.0):
    F = BS.three(O.vol_control([B["cB"], B["sc"], P], [1, 1, w], 2.0 + w)[0])
    print(f"配对腿 权重{w} 加进方案F {BS.fmt(F)} {BS.verdict(F, b0)}", flush=True)

# ---- 马丁格尔 ----
b = L.bars("BTCUSDT", "1h"); o, h, l, c = b.o.values, b.h.values, b.l.values, b.c.values
for fee in (0.0, 0.0013):
    for name, seq in (("1-2-6-1", [1, 2, 6, 1]), ("翻倍1-2-4-8", [1, 2, 4, 8]), ("固定1倍(对照)", [1])):
        res = []
        for seed in range(200):
            rng = np.random.default_rng(seed); eq = 1.0; pk = 1.0; mdd = 0.0; k = 0; i = int(rng.integers(0, 2000)); n = 0
            cyc = cw = 0; ceq = eq
            while i < len(c) - 2 and eq > 0.05 and n < 1500:
                s_ = rng.choice([-1, 1]); e = o[i + 1]; tp = e * (1 + s_ * 0.01); sl = e * (1 - s_ * 0.01); j = i + 1; r = None
                while j < len(c):
                    if (s_ > 0 and l[j] <= sl) or (s_ < 0 and h[j] >= sl): r = -0.01; break
                    if (s_ > 0 and h[j] >= tp) or (s_ < 0 and l[j] <= tp): r = 0.01; break
                    j += 1
                if r is None: break
                eq *= 1 + 0.5 * seq[k] * (r - fee); n += 1          # 基础名义 0.5 倍权益
                if r > 0 or k == len(seq) - 1:                       # 一轮结束(赢了 或 加码打满)
                    cyc += 1; cw += eq > ceq; ceq = eq; k = 0
                else: k += 1
                pk = max(pk, eq); mdd = max(mdd, 1 - eq / pk); i = j
            res.append((eq, mdd, cw / max(cyc, 1)))
        a_ = np.array(res)
        print(f"马丁 {'零成本' if fee == 0 else '含费'} {name:12s} 200条随机路径×1500笔(BTC 1h ±1%)：终值中位 {np.median(a_[:,0]):.2f} "
              f"亏到剩5%以下 {np.mean(a_[:,0]<=0.05):.0%} 最大回撤中位 {np.median(a_[:,1]):.0%} 每轮胜率 {np.mean(a_[:,2]):.0%}", flush=True)
print("ALL DONE")
