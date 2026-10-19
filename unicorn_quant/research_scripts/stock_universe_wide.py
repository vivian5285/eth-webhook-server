"""2026-10-19 宝贝：美股要不要放宽筛选、扩大范围？
币安美股永续现在有 221 个，我们池子只用了其中 53 个。把其余的美国个股(不含ETF/杠杆/港韩/未上市)按行业加进去，规则不变。
B = 老行业里加成员；C = B + 新行业(AI电力、汽车出行)。行业前5、EMA8/30、3ATR止损都不变。
注意：币安是挑了已经很火的股票才上永续，加进来的新票有"事后挑选"偏差，结果要打折看。"""
import sys, os, time
os.nice(19)
sys.path.insert(0, "/home/stratroster/unicorn_quant"); sys.path.insert(0, "/home/stratroster/eth-webhook-server")
from engine import optimal as O
ADD = {
    "半导体": ["ALAB", "CRDO", "SNPS"],
    "AI算力/网络": ["CRWV", "NBIS", "IREN", "APLD", "LITE", "AAOI", "FLEX"],
    "软件": ["PLTR", "CRWD", "PANW", "ZS", "NET", "SNOW", "DDOG", "MDB", "TEAM", "APP", "PATH", "GTLB", "SHOP", "IBM", "ACN"],
    "互联网": ["AAPL", "RDDT", "PDD", "BABA", "EBAY", "DKNG", "TTWO", "DIS"],
    "金融": ["V", "PYPL", "SOFI", "BX"],
    "消费": ["CL"],
    "医药": ["NVO", "MRNA", "HIMS", "VKTX", "TEM"],
    "工业军工": ["RKLB", "ASTS", "RDW"],
    "加密相关": ["CRCL", "BMNR", "HUT"],
}
NEW = {"AI电力": ["VST", "GEV", "OKLO", "BE"], "汽车出行": ["TSLA", "RIVN", "CVNA"]}
def st(cv, a=17410, z=99999):
    ks = [k for k in sorted(cv) if a <= k <= z]; v = [cv[k] for k in ks]; pk = 0; dd = 0
    for x in v: pk = max(pk, x); dd = max(dd, 1 - x / pk)
    yrs = (ks[-1] - ks[0]) / 365.25; cagr = (v[-1] / v[0]) ** (1 / yrs) - 1
    return 100 * cagr, 100 * dd, cagr / dd if dd else 0
H = 18993
def yearly(cv):
    by = {}
    for k in sorted(cv):
        if k < 17410: continue
        by.setdefault(time.gmtime(k * 86400).tm_year, []).append(cv[k])
    out = {}; pe = None
    for y in sorted(by): xs = by[y]; b0 = pe or xs[0]; out[y] = 100 * (xs[-1] / b0 - 1); pe = xs[-1]
    return out
f = lambda F: f"{st(F)[0]:+6.1f}%/{st(F)[1]:4.1f}%/{st(F)[2]:5.2f} 前{st(F,17410,H)[2]:.2f}/后{st(F,H)[2]:.2f} | " + " ".join(f"{y}:{v:+.0f}%" for y, v in yearly(F).items())
cd, _, _, _, CD = O.crypto_leg(); O.crypto_8h_leg("st", CD["BTCUSDT"]); O.crypto_8h_leg("bb", CD["BTCUSDT"])
coin = {s: O.combine([O.CURVES["日线EMA8/30"][s], O.CURVES["8h超级趋势"][s], O.CURVES["8h布林突破"][s]], [.5, .25, .25]) for s in O.M5}
cB = O.weighted_basket(coin, O.coin_weights(coin, range(2017, 2027)))
ORIG = {k: list(v) for k, v in O.SECTORS.items()}
def run(secs, lab):
    O.SECTORS.clear(); O.SECTORS.update(secs); O.SEC_OF.clear(); O.SEC_OF.update({t: k for k, v in secs.items() for t in v})
    r = O.stock_leg(); sc = r[0]; tr = [t for t in r[1] if isinstance(t, dict)] if len(r) > 1 and isinstance(r[1], list) else []
    F = O.vol_control([cB, sc], [1, 1], 2.0)[0]
    got = sum(1 for t in O.SEC_OF if os.path.exists(f"{O.OUT}/yf_{t}_1d.json"))
    print(f"{lab:22s} 主方案 {f(F)}\n{'':22s} 美股腿自己 {st(sc)[0]:+.1f}%/{st(sc)[1]:.1f}%/{st(sc)[2]:.2f} 前{st(sc,17410,H)[2]:.2f}/后{st(sc,H)[2]:.2f} | 股票{len(O.SEC_OF)}只(有数据{got})", flush=True)
    return sc
run(ORIG, "A 现行(53只)")
B = {k: v + ADD.get(k, []) for k, v in ORIG.items()}
scB = run(B, "B 老行业加成员")
C = dict(B); C.update(NEW)
run(C, "C B+新行业")
O.SECTORS.clear(); O.SECTORS.update(ORIG); O.SEC_OF.clear(); O.SEC_OF.update({t: k for k, v in ORIG.items() for t in v})
print("ALL DONE")
