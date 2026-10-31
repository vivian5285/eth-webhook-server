"""2026-10-31 SNDK单票方案：①合约资金费成本 ②限价追价 vs 市价 成交成本(币安永续1m) ③币安/OKX/Gate 永续+现货 同一策略对比。
策略 = v1(1h EMA7/25 只多 4ATR) 和 v2(v1 + 波动调仓 仓位=min(1, 2%÷1hATR%))。下根开盘成交。"""
import json, time, urllib.request, os, pickle, statistics as stt
os.nice(19)


def get(u):
    err = None
    for _ in range(3):
        try:
            return json.load(urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0"}), timeout=30))
        except Exception as e:
            err = e; time.sleep(2)
    raise err


CACHE = "/root/research/sndk_venue_cache.pkl"
D = pickle.load(open(CACHE, "rb")) if os.path.exists(CACHE) and time.time() - os.path.getmtime(CACHE) < 6 * 3600 else {}
NOW = int(time.time() * 1000)


def bn(base, sym, iv, start, ms):
    out = []; s = start
    while True:
        r = get(f"{base}/klines?symbol={sym}&interval={iv}&startTime={s}&limit=1000")
        if not r: break
        out += [{"t": x[0], "o": float(x[1]), "h": float(x[2]), "l": float(x[3]), "c": float(x[4]), "v": float(x[5])} for x in r]
        if len(r) < 1000: break
        s = r[-1][0] + 1; time.sleep(0.15)
    return [x for x in out if x["t"] + ms <= NOW]


def okx(inst):
    out = []; after = NOW
    while True:
        r = get(f"https://www.okx.com/api/v5/market/history-candles?instId={inst}&bar=1H&after={after}&limit=100")["data"]
        if not r: break
        out += [{"t": int(x[0]), "o": float(x[1]), "h": float(x[2]), "l": float(x[3]), "c": float(x[4]), "v": float(x[5])} for x in r if x[8] == "1"]
        after = int(r[-1][0]); time.sleep(0.12)
    return sorted(out, key=lambda x: x["t"])


def gate(path, key, val):
    out = []; s = 1772600000
    while s < NOW // 1000:
        e = min(s + 3600 * 900, NOW // 1000)
        r = get(f"https://api.gateio.ws/api/v4/{path}?{key}={val}&interval=1h&from={s}&to={e}")
        for x in r:
            if isinstance(x, dict):
                out.append({"t": int(x["t"]) * 1000, "o": float(x["o"]), "h": float(x["h"]), "l": float(x["l"]), "c": float(x["c"]), "v": float(x.get("v") or 0)})
            else:
                out.append({"t": int(x[0]) * 1000, "o": float(x[5]), "h": float(x[3]), "l": float(x[4]), "c": float(x[2]), "v": float(x[1])})
        s = e + 1; time.sleep(0.2)
    seen = {}
    for x in out: seen.setdefault(x["t"], x)
    return [seen[k] for k in sorted(seen) if k + 3600000 <= NOW]


if not D:
    D["bn_perp"] = bn("https://fapi.binance.com/fapi/v1", "SNDKUSDT", "1h", 1775520000000, 3600000)
    D["bn_perp_1m"] = bn("https://fapi.binance.com/fapi/v1", "SNDKUSDT", "1m", 1775520000000, 60000)
    D["bn_spot"] = bn("https://api.binance.com/api/v3", "SNDKBUSDT", "1h", 1781136000000, 3600000)
    D["okx_swap"] = okx("SNDK-USDT-SWAP"); D["okx_spot"] = okx("XSNDK-USDT")
    D["gate_perp"] = gate("futures/usdt/candlesticks", "contract", "SNDK_USDT"); D["gate_spot"] = gate("spot/candlesticks", "currency_pair", "SNDKG_USDT")
    fr = []; s = 1775520000000
    while True:
        r = get(f"https://fapi.binance.com/fapi/v1/fundingRate?symbol=SNDKUSDT&startTime={s}&limit=1000")
        fr += [(x["fundingTime"], float(x["fundingRate"])) for x in r]
        if len(r) < 1000: break
        s = r[-1]["fundingTime"] + 1
    D["fr_bn"] = fr
    fo = []; after = NOW
    while True:
        r = get(f"https://www.okx.com/api/v5/public/funding-rate-history?instId=SNDK-USDT-SWAP&after={after}&limit=100")["data"]
        if not r: break
        fo += [(int(x["fundingTime"]), float(x.get("realizedRate") or x["fundingRate"])) for x in r]; after = int(r[-1]["fundingTime"]); time.sleep(0.12)
    D["fr_okx"] = sorted(fo)
    D["fr_gate"] = sorted((int(x["t"]) * 1000, float(x["r"])) for x in get("https://api.gateio.ws/api/v4/futures/usdt/funding_rate?contract=SNDK_USDT&limit=1000"))
    pickle.dump(D, open(CACHE, "wb"))
for k, v in D.items():
    print(k, len(v), time.strftime("%Y-%m-%d", time.gmtime((v[0][0] if isinstance(v[0], tuple) else v[0]["t"]) / 1000)))


def ema(c, n):
    k = 2 / (n + 1); o = [c[0]]
    for x in c[1:]: o.append(o[-1] + k * (x - o[-1]))
    return o


def atr(b, n=14):
    A = [b[0]["h"] - b[0]["l"]]
    for i in range(1, len(b)):
        A.append(A[-1] + (max(b[i]["h"] - b[i]["l"], abs(b[i]["h"] - b[i - 1]["c"]), abs(b[i]["l"] - b[i - 1]["c"])) - A[-1]) / n)
    return A


def run(b, fee_sig, fee_stop, vt=None, fr=None, t0=0):
    """fee_sig=信号单(开/平)单边成本，fee_stop=止损单单边成本；fr=[(时间,费率)] 持多时每期付 费率×仓位名义"""
    c = [x["c"] for x in b]; ef, es = ema(c, 7), ema(c, 25); A = atr(b)
    fi = 0; frs = fr or []; eq = 1.0; q = 0.0; d = 0; pend = None; stop = None; lock = 0; cv = []; legs = []; fund = 0.0; n = 0
    for i in range(1, len(b)):
        op, lo, cl, pc, t = b[i]["o"], b[i]["l"], b[i]["c"], b[i - 1]["c"], b[i]["t"]
        while fi < len(frs) and frs[fi][0] <= t:
            if d and t >= t0:
                x = q * pc * frs[fi][1]; eq -= x; fund += x / eq
            fi += 1
        if pend is not None:
            tgt = pend; pend = None
            if d and not tgt:
                eq += q * (op - pc) - q * op * fee_sig; legs.append(("sell", t)); q = 0; d = 0; pc = op
            if tgt and not d:
                m = min(1.0, vt / (A[i - 1] / c[i - 1])) if vt else 1.0
                d = 1; q = eq * m / op; eq -= q * op * fee_sig; pc = op; stop = op - 4 * A[i - 1]; legs.append(("buy", t))
                if t >= t0: n += 1
        if d and lo <= stop:
            px = min(op, stop); eq += q * (px - pc) - q * px * fee_stop; q = 0; d = 0; lock = 1
        elif d:
            eq += q * (cl - pc)
        if t >= t0: cv.append((t, eq))
        if i < 25: continue
        want = 1 if ef[i] > es[i] else 0
        if lock and want != lock: lock = 0
        if lock and want == lock: want = 0
        if want != d: pend = want
    return cv, legs, fund, n


def stats(cv):
    v = [x[1] for x in cv]; pk = 0; dd = 0
    for x in v: pk = max(pk, x); dd = max(dd, 1 - x / pk)
    return 100 * (v[-1] / v[0] - 1), 100 * dd


day = lambda s: int(time.mktime(time.strptime(s, "%Y-%m-%d"))) * 1000
# ---------- ① 资金费 ----------
print("\n### ① 永续资金费(只在持多时付；正费率=多头付钱)")
for nm, b, fr in (("币安永续", D["bn_perp"], D["fr_bn"]), ("OKX永续", D["okx_swap"], D["fr_okx"]), ("Gate永续", D["gate_perp"], D["fr_gate"])):
    t0 = max(b[0]["t"], fr[0][0]) + 14 * 86400000
    rs = [r for t, r in fr if t >= t0]
    for vt in (None, 0.02):
        cv0, _, _, _ = run(b, 0.0004, 0.0004, vt, None, t0); cv1, _, fund, _ = run(b, 0.0004, 0.0004, vt, fr, t0)
        print(f"  {nm} {'v2' if vt else 'v1'} {time.strftime('%m-%d', time.gmtime(t0 / 1000))}起：费率均值 {1e4 * stt.mean(rs):+.2f}bp/期(正的占{100 * sum(1 for r in rs if r > 0) / len(rs):.0f}%)，"
              f"不含资金费 {stats(cv0)[0]:+.1f}% → 含资金费 {stats(cv1)[0]:+.1f}%，持仓期间资金费合计约 {100 * fund:.2f}% 净值")
# ---------- ② 执行方式(币安永续 1m) ----------
print("\n### ② 信号单执行方式(币安永续1m，按 v2 实际信号时间；成本=相对理想开盘价的偏差+手续费，bp，正=吃亏)")
m1 = D["bn_perp_1m"]; idx = {x["t"]: i for i, x in enumerate(m1)}
_, legs, _, _ = run(D["bn_perp"], 0, 0, 0.02, None, 0)


def sim(side, t, how, taker=4.0):
    i = idx.get(t)
    if i is None: return None
    ref = m1[i]["o"]; sgn = 1 if side == "buy" else -1
    if how == "market": return taker
    if how.startswith("chase"):            # 每分钟按当时价格重挂 GTX，价格穿过才算成交(排队保守)，N 分钟没成交就市价
        N = int(how[5:])
        for j in range(i, min(i + N, len(m1))):
            px = m1[j]["o"]; x = m1[j]
            if (side == "buy" and x["l"] < px) or (side == "sell" and x["h"] > px): return sgn * (px / ref - 1) * 1e4
        j = min(i + N, len(m1) - 1); return sgn * (m1[j]["o"] / ref - 1) * 1e4 + taker
    off = float(how[3:]) / 1e4             # off10 = 比开盘价有利 10bp 挂单等 60 分钟，不成交市价
    px = ref * (1 - sgn * off)
    for j in range(i, min(i + 60, len(m1))):
        x = m1[j]
        if (side == "buy" and x["l"] < px) or (side == "sell" and x["h"] > px): return -off * 1e4
    j = min(i + 60, len(m1) - 1); return sgn * (m1[j]["o"] / ref - 1) * 1e4 + taker


for how in ("market", "chase5", "chase15", "chase30", "off5", "off10", "off20", "off50"):
    cs = [x for x in (sim(s, t, how) for s, t in legs) if x is not None]
    print(f"  {how:8s} {len(cs)}次 平均成本 {stt.mean(cs):+6.2f}bp  中位 {stt.median(cs):+6.2f}bp  最差 {max(cs):+7.1f}bp")
print("  (现货吃单手续费10bp：market=10bp，追价没成交转市价的部分也按10bp)")
for how in ("market", "chase15", "chase30"):
    cs = [x for x in (sim(s, t, how, 10.0) for s, t in legs) if x is not None]
    print(f"  现货 {how:8s} 平均成本 {stt.mean(cs):+6.2f}bp")
# ---------- ③ 交易所/现货对比 ----------
print("\n### ③ 同一策略在各交易所(信号单按挂单费，止损按吃单费；永续含资金费)")
V = [("币安永续", D["bn_perp"], 0.0, 0.0004, D["fr_bn"]), ("币安现货SNDKB", D["bn_spot"], 0.0, 0.001, None),
     ("OKX永续", D["okx_swap"], 0.0002, 0.0005, D["fr_okx"]), ("OKX现货XSNDK", D["okx_spot"], 0.0008, 0.001, None),
     ("Gate永续", D["gate_perp"], -0.0001, 0.00075, D["fr_gate"]), ("Gate现货SNDKG", D["gate_spot"], 0.002, 0.002, None)]
for lab, t0 in (("A. 03-18 起(OKX/Gate 永续全程)", day("2026-03-18")), ("B. 04-21 起(币安永续可比)", day("2026-04-21")),
                ("C. 06-25 起(币安现货可比)", day("2026-06-25")), ("D. 07-30 起(全部可比)", day("2026-07-30"))):
    print(f"  -- {lab}")
    for nm, b, fs, ft, fr in V:
        if b[0]["t"] > t0 - 10 * 86400000: continue
        row = []
        for vt in (None, 0.02):
            cv, _, fund, n = run(b, fs, ft, vt, fr, t0); tot, dd = stats(cv)
            row.append(f"{'v2' if vt else 'v1'} {tot:+6.1f}%/回撤{dd:4.1f}%")
        print(f"    {nm:14s} {n:3d}笔 | " + " | ".join(row))
