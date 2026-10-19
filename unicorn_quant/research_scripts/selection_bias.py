"""2026-10-19 选择偏差量化(不是前视，但会让回测比实盘乐观)：
A 币圈"主流5个"是站在今天选的(活下来的赢家)。对照：每年1月1日按当时市值前5(不含稳定币)换币池，规则完全一样：
   2018 BTC XRP ETH BCH ADA | 2019 BTC XRP ETH EOS BCH | 2020 BTC ETH XRP BCH LTC | 2021 BTC ETH XRP LTC BCH
   2022 BTC ETH BNB SOL ADA | 2023 BTC ETH BNB XRP DOGE | 2024 BTC ETH BNB SOL XRP | 2025 BTC ETH XRP SOL BNB | 2026 BTC ETH XRP BNB SOL
   (2017 只有 BTC/ETH/BNB 有数据，两边相同；BCH 用币安 BCC→BCHABC→BCH 三段拼接；币池内仍按过去3年比值定权重、单币≤40%)
B 美股股票池(13个行业78只)也是今天选的。对照：用美国行业 ETF 当"股票"(SPDR + Vanguard 同行业各一只，2017 年就存在)，同样的行业前5轮动+EMA8/30+3ATR。"""
import sys, os, time, json, calendar, urllib.request
os.nice(19)
sys.path.insert(0, "/home/stratroster/unicorn_quant"); sys.path.insert(0, "/home/stratroster/eth-webhook-server")
from engine import optimal as O
DAY = 86400000; C = "/tmp/mtf_cache"
def kl(sym, iv):
    p = f"{C}/{sym}_{iv}_ohlc.json"
    if os.path.exists(p): return json.load(open(p))
    out = []; s = 1500000000000; H = {"1d": 24, "8h": 8}[iv]
    while True:
        r = json.load(urllib.request.urlopen(f"https://api.binance.com/api/v3/klines?symbol={sym}&interval={iv}&startTime={s}&limit=1000", timeout=20))
        if not r: break
        out += [{"t": int(x[0]), "o": float(x[1]), "h": float(x[2]), "l": float(x[3]), "c": float(x[4]), "v": float(x[5])} for x in r]
        if len(r) < 1000: break
        s = int(r[-1][0]) + 1; time.sleep(0.3)
    now = time.time() * 1000; out = [x for x in out if x["t"] + H * 3600000 <= now]
    json.dump(out, open(p, "w")); return out
def bch(iv):
    a = kl("BCCUSDT", iv); b = kl("BCHABCUSDT", iv); c = kl("BCHUSDT", iv)
    out = [x for x in a if x["t"] < b[0]["t"]] + [x for x in b if x["t"] < c[0]["t"]] + c
    return out
EXTRA = {"BCHUSDT": bch, "ADAUSDT": lambda iv: kl("ADAUSDT", iv), "EOSUSDT": lambda iv: kl("EOSUSDT", iv),
         "LTCUSDT": lambda iv: kl("LTCUSDT", iv), "DOGEUSDT": lambda iv: kl("DOGEUSDT", iv)}
_sd, _s8 = O.spot_daily, O.spot_8h
O.spot_daily = lambda s: EXTRA[s]("1d") if s in EXTRA else _sd(s)
O.spot_8h = lambda s: EXTRA[s]("8h") if s in EXTRA else _s8(s)
MEM = {2018: "BTC XRP ETH BCH ADA", 2019: "BTC XRP ETH EOS BCH", 2020: "BTC ETH XRP BCH LTC", 2021: "BTC ETH XRP LTC BCH",
       2022: "BTC ETH BNB SOL ADA", 2023: "BTC ETH BNB XRP DOGE", 2024: "BTC ETH BNB SOL XRP", 2025: "BTC ETH XRP SOL BNB", 2026: "BTC ETH XRP BNB SOL"}
MEM = {y: [x + "USDT" for x in v.split()] for y, v in MEM.items()}; MEM[2017] = ["BTCUSDT", "ETHUSDT", "BNBUSDT"]
def st(cv, a=17410, z=99999):
    ks = [k for k in sorted(cv) if a <= k <= z]; v = [cv[k] for k in ks]; pk = 0; dd = 0
    for x in v: pk = max(pk, x); dd = max(dd, 1 - x / pk)
    yrs = (ks[-1] - ks[0]) / 365.25; cagr = (v[-1] / v[0]) ** (1 / yrs) - 1
    return 100 * cagr, 100 * dd, cagr / dd if dd else 0
H = 18993
def yr(cv):
    by = {}
    for k in sorted(cv):
        if k >= 17410: by.setdefault(time.gmtime(k * 86400).tm_year, []).append(cv[k])
    o = []; pe = None
    for y in sorted(by): xs = by[y]; b0 = pe or xs[0]; o.append(f"{y%100}:{100*(xs[-1]/b0-1):+.0f}"); pe = xs[-1]
    return " ".join(o)
def show(lab, F):
    a = st(F); print(f"{lab:30s} {a[0]:+6.1f}%/{a[1]:4.1f}%/{a[2]:5.2f} 前{st(F,17410,H)[2]:.2f}/后{st(F,H)[2]:.2f} | {yr(F)}", flush=True)
def member_weights(coin, years):
    out = {}
    for y in years:
        y0 = calendar.timegm((y, 1, 1, 0, 0, 0)) // 86400; y1 = calendar.timegm((y + 1, 1, 1, 0, 0, 0)) // 86400
        avail = [s for s in MEM[y] if s in coin and any(y0 <= k < y1 for k in coin[s])]; sc = {}
        for s in avail:
            ks = [k for k in coin[s] if y0 - O.WEIGHT_LOOKBACK <= k < y0]
            if len(ks) >= 300:
                r = O._win_ratio(coin[s], y0 - O.WEIGHT_LOOKBACK, y0 - 1)
                if r is not None: sc[s] = max(0.0, r)
        w = O._capw(sc, O.WEIGHT_CAP)
        out[y] = w or ({s: 1 / len(avail) for s in avail} if avail else {})
    return out
# ---- 基准(现行) ----
sc_stock = O.stock_leg()[0]
def crypto(universe, wfun):
    O.M5[:] = universe
    cd, _, _, _, CD = O.crypto_leg(); O.crypto_8h_leg("st", CD["BTCUSDT"]); O.crypto_8h_leg("bb", CD["BTCUSDT"])
    coin = {s: O.combine([O.CURVES["日线EMA8/30"][s], O.CURVES["8h超级趋势"][s], O.CURVES["8h布林突破"][s]], [1/3] * 3) for s in universe}
    WY = wfun(coin); return O.weighted_basket(coin, WY), WY
M5 = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT"]
cB0, _ = crypto(list(M5), lambda c: O.coin_weights(c, range(2017, 2027)))
print("=== A 币圈：今天选的主流5 vs 每年按当时市值前5 ===")
show("币圈腿 现行主流5", cB0)
U = sorted({s for v in MEM.values() for s in v})
cBH, WYH = crypto(U, lambda c: member_weights(c, range(2017, 2027)))
show("币圈腿 每年当时前5", cBH)
for y in range(2018, 2027): print(f"   {y} 权重 " + " ".join(f"{s[:-4]}{100*w:.0f}%" for s, w in sorted(WYH[y].items(), key=lambda x: -x[1])), flush=True)
show("主方案 现行", O.vol_control([cB0, sc_stock], [1, 1], 2.0)[0])
show("主方案 币圈换成当时前5", O.vol_control([cBH, sc_stock], [1, 1], 2.0)[0])
O.M5[:] = M5
# ---- B 美股：行业 ETF ----
print("\n=== B 美股：今天选的78只 vs 行业ETF(2017年就有的) ===")
ETF = {"科技": ["XLK", "VGT"], "金融": ["XLF", "VFH"], "能源": ["XLE", "VDE"], "医药": ["XLV", "VHT"], "可选消费": ["XLY", "VCR"],
       "必需消费": ["XLP", "VDC"], "工业": ["XLI", "VIS"], "材料": ["XLB", "VAW"], "公用事业": ["XLU", "VPU"], "房地产": ["XLRE", "VNQ"], "通信": ["XLC", "VOX"]}
ORIG = {k: list(v) for k, v in O.SECTORS.items()}; _tr = O.tradable_set
O.SECTORS.clear(); O.SECTORS.update(ETF); O.SEC_OF.clear(); O.SEC_OF.update({t: k for k, v in ETF.items() for t in v})
O.tradable_set = lambda: set(O.SEC_OF)
scE = O.stock_leg()[0]
O.SECTORS.clear(); O.SECTORS.update(ORIG); O.SEC_OF.clear(); O.SEC_OF.update({t: k for k, v in ORIG.items() for t in v}); O.tradable_set = _tr
show("美股腿 现行78只", sc_stock); show("美股腿 行业ETF", scE)
show("主方案 现行", O.vol_control([cB0, sc_stock], [1, 1], 2.0)[0])
show("主方案 美股换成行业ETF", O.vol_control([cB0, scE], [1, 1], 2.0)[0])
show("主方案 两个都换(最保守)", O.vol_control([cBH, scE], [1, 1], 2.0)[0])
print("ALL DONE")
