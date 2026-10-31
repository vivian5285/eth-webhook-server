"""2026-11-01 宝贝：① 美股腿拓宽/分散(持有更多不同热度的行业) ② 日线做黄金，能不能让方案F更好？
同 planf_weekend_earnings.py 口径：直接复用正式引擎 engine/optimal.py，全程 2017-09 起 + 前半(..2021)/后半(2022..)，三段比值都超过基准才算过关。

① 美股腿变体(行业轮动：近6个月行业动量前 TOP_K 个行业，行业内股票日线 EMA8>30 做多，3×ATR 止损)：
   K7/K9/K13  = 持有前7/9/全部13个行业(更多不同热度)
   SEQ        = 行业等权(现在是按股票等权，AI/半导体名字多 → 天然超配)
   EXP        = 扩池：币安美股永续 235 只里能对上行业的再加 ~60 只 + 新行业「电力核能」「汽车」(注意：新加的多是近两年热门上市/新上永续，选择偏差更重)
   组合：EXP+K7、EXP+SEQ、EXP+K9+SEQ
② 黄金腿：Yahoo GC=F 日线(2008起)，规则照币圈日线腿的核心：EMA8/30 方向、仓位=min(1, 3%÷ATR%)、3×ATR 硬止损(止损后等方向翻转)、
   收盘信号次日开盘成交、单边 0.08%；只多 / 多空 两版；扣币安 XAUUSDT 实际资金费(多付空收，用近期平均年化)。
   接法：vol_control 第三条腿，权重 0.25/0.5/1.0(额外总敞口，和币圈/美股一样受 40% 组合波动率控制)。"""
import sys, os, time, json, inspect, math, urllib.request
os.nice(19)
sys.path.insert(0, "/home/stratroster/unicorn_quant"); sys.path.insert(0, "/home/stratroster/eth-webhook-server")
from engine import optimal as O

# ---------------- 公共 ----------------
Hh = 18993  # 2022-01-01
def st(cv, a=17410, z=99999):
    ks = [k for k in sorted(cv) if a <= k <= z]; v = [cv[k] for k in ks]; pk = 0; dd = 0
    for x in v: pk = max(pk, x); dd = max(dd, 1 - x / pk)
    yrs = (ks[-1] - ks[0]) / 365.25; cagr = (v[-1] / v[0]) ** (1 / yrs) - 1
    return 100 * cagr, 100 * dd, cagr / dd if dd else 0
def yr(cv):
    by = {}
    for k in sorted(cv):
        if k >= 17410: by.setdefault(time.gmtime(k * 86400).tm_year, []).append(cv[k])
    o = []; pe = None
    for y in sorted(by): xs = by[y]; b0 = pe or xs[0]; o.append(f"{y % 100}:{100 * (xs[-1] / b0 - 1):+.0f}"); pe = xs[-1]
    return " ".join(o)

def patch(fn, pairs):
    src = inspect.getsource(fn)
    for a, b in pairs:
        assert src.count(a) == 1, (fn.__name__, a); src = src.replace(a, b)
    exec(compile(src, f"<patched {fn.__name__}>", "exec"), O.__dict__)

# 行业等权：每个入选行业拿同样预算，行业内再按股票平分
O._SEQ = False
patch(O.stock_leg, [("            notional = E * alloc / max(k, MIN_SLOTS)\n",
                     "            notional = E * alloc / max(k, MIN_SLOTS)\n"
                     "            if _SEQ:\n"
                     "                _secs_on = {SEC_OF[x] for x in act_set}; _nsec = sum(1 for x in act_set if SEC_OF[x] == SEC_OF[s])\n"
                     "                notional = E * alloc / max(len(_secs_on), MIN_SLOTS) / max(_nsec, 1)\n")])

BASE_SECTORS = {k: list(v) for k, v in O.SECTORS.items()}
EXTRA = {  # 币安 TRADIFI 永续 2026-11-01 在列、且是美股正股的；按主营归行业
    "半导体": ["ALAB", "CRDO", "STM"], "AI算力/网络": ["LITE", "AAOI", "CRWV", "NBIS", "FLEX", "APLD", "IREN"],
    "软件": ["PLTR", "CRWD", "PANW", "SNOW", "DDOG", "NET", "MDB", "ZS", "TEAM", "IBM", "ACN", "SNPS", "APP", "SHOP"],
    "互联网": ["AAPL", "RDDT", "EBAY", "DIS", "MELI"], "金融": ["BAC", "V", "BX", "IBKR", "SOFI"],
    "能源": ["OXY", "EQT"], "医药": ["ABBV", "AMGN", "PFE", "MRNA", "MDT", "NVO", "VKTX"],
    "消费": ["CL", "CVNA", "DKNG"], "工业军工": ["RKLB", "ASTS"], "材料矿业": ["MP"], "加密相关": ["HUT", "CRCL"],
    "电力核能": ["CEG", "VST", "GEV", "OKLO", "SMR", "BE"], "汽车": ["TSLA", "RIVN"],
}

_info = json.load(urllib.request.urlopen("https://fapi.binance.com/fapi/v1/exchangeInfo", timeout=30))
LISTED = {s["symbol"][:-4] for s in _info["symbols"] if s.get("contractType") == "TRADIFI_PERPETUAL" and s.get("status") == "TRADING"}

def set_universe(expand):
    S = {k: list(v) for k, v in BASE_SECTORS.items()}
    if expand:
        for k, v in EXTRA.items(): S.setdefault(k, []); S[k] += [t for t in v if t not in S[k]]
    O.SECTORS.clear(); O.SECTORS.update(S); O.SEC_OF.clear(); O.SEC_OF.update({t: k for k, v in S.items() for t in v})
    tr = {t for t in O.SEC_OF if t in LISTED}  # 同正式引擎 tradable_set()：当前在列的才交易
    O.tradable_set = lambda: tr

# ---------------- 币圈腿(只算一次) ----------------
_, _, _, _, CD = O.crypto_leg(); d1 = dict(O.CURVES["日线EMA8/30"])
O.crypto_8h_leg("st", CD["BTCUSDT"]); stc = dict(O.CURVES["8h超级趋势"]); O.crypto_8h_leg("bb", CD["BTCUSDT"]); bbc = dict(O.CURVES["8h布林突破"])
coin = {s: O.combine([d1[s], stc[s], bbc[s]], [1 / 3] * 3) for s in O.M5}
cB = O.weighted_basket(coin, O.coin_weights(coin, range(2017, 2027)))

def planF(sc, extra=None, w=0.0):
    if extra is None: return O.vol_control([cB, sc], [1, 1], 2.0)[0]
    return O.vol_control([cB, sc, extra], [1, 1, w], 2.0 + w)[0]

set_universe(False); O.TOP_K = 5; O._SEQ = False
SC0 = O.stock_leg()[0]; BASE = planF(SC0)
b0 = (st(BASE)[2], st(BASE, 17410, Hh)[2], st(BASE, Hh)[2])
def show(lab, F, sc=None):
    a = st(F); s1, s2 = st(F, 17410, Hh)[2], st(F, Hh)[2]; ok = a[2] > b0[0] and s1 > b0[1] and s2 > b0[2]
    leg = f" [美股腿{st(sc)[0]:+.1f}%/{st(sc)[1]:.1f}%/{st(sc)[2]:.2f}]" if sc else ""
    print(f"{lab:34s} {a[0]:+6.1f}%/{a[1]:4.1f}%/{a[2]:5.2f} 前{s1:.2f}/后{s2:.2f}{' ★过关' if ok else ''}{leg} | {yr(F)}", flush=True)

print("=== ① 美股腿拓宽/分散 ===", flush=True)
show("基准 现行方案F", BASE, SC0)
for lab, exp, k, seq in (("K7 持有前7个行业", False, 7, False), ("K9 前9个行业", False, 9, False), ("K13 全部行业", False, 13, False),
                         ("SEQ 行业等权", False, 5, True), ("K9+SEQ", False, 9, True),
                         ("EXP 扩池(+~60只,+2行业)", True, 5, False), ("EXP+K7", True, 7, False), ("EXP+SEQ", True, 5, True),
                         ("EXP+K9+SEQ", True, 9, True), ("EXP+K15 全部行业", True, 15, False)):
    set_universe(exp); O.TOP_K = k; O._SEQ = seq
    try:
        sc = O.stock_leg()[0]; show(lab, planF(sc), sc)
    except Exception as e:
        print(lab, "ERR", repr(e), flush=True)
set_universe(False); O.TOP_K = 5; O._SEQ = False

# ---------------- ② 黄金腿 ----------------
print("=== ② 黄金日线腿 ===", flush=True)
def funding_xau():
    try:
        r = json.load(urllib.request.urlopen("https://fapi.binance.com/fapi/v1/fundingRate?symbol=XAUUSDT&limit=1000", timeout=20))
        fr = [float(x["fundingRate"]) for x in r]
        if len(r) >= 2:
            per_day = 86400000 * (len(r) - 1) / (int(r[-1]["fundingTime"]) - int(r[0]["fundingTime"]))
            return sum(fr) / len(fr) * per_day * 365, len(r)
    except Exception as e:
        print("funding ERR", e)
    return 0.0, 0
FR_YR, nfr = funding_xau()
print(f"XAUUSDT 资金费 近{nfr}次 平均年化 {100 * FR_YR:+.2f}%(多单付)", flush=True)

def gold_leg(sym="GC=F", long_only=False, fr_yr=FR_YR):
    b = O.stock_daily(sym); c = [x["c"] for x in b]; f, s_ = O.ema(c, 8), O.ema(c, 30); A = O.atr(b)
    E = 1.0; q = 0.0; last = None; stp = None; side = 0; blocked = 0; pend = None; curve = {}
    for i, x in enumerate(b):
        d = x["t"] // O.DAY
        if pend is not None:  # 次日开盘执行
            tgt_side, notional = pend; pend = None; op = x["o"]
            if side and (tgt_side != side):
                E += q * (op - last) - abs(q) * op * O.FEE; q = 0.0; side = 0
            if tgt_side and not side:
                q = tgt_side * notional / op; E -= notional * O.FEE; side = tgt_side; last = op; stp = op - side * 3 * A[i - 1]
        if side:
            hit = (x["l"] <= stp) if side > 0 else (x["h"] >= stp)
            if hit:
                px = min(x["o"], stp) if side > 0 else max(x["o"], stp)
                E += q * (px - last) - abs(q) * px * O.FEE; blocked = side; q = 0.0; side = 0
            else:
                E += q * (x["c"] - last); last = x["c"]
                E -= abs(q) * x["c"] * fr_yr / 365 * side  # 多单付资金费、空单收
        curve[d] = E
        if i < 32: continue
        want = 1 if f[i] > s_[i] else -1
        if long_only and want < 0: want = 0
        if blocked and want != blocked: blocked = 0
        if blocked == want: want = 0
        if want != side:
            atrp = A[i] / x["c"]; pend = (want, E * min(1.0, O.VOL_TGT / atrp))
    return curve, b

for lo in (True, False):
    g, gb = gold_leg(long_only=lo); lab = "只多" if lo else "多空"
    gs = st(g, max(17410, min(g))); print(f"黄金腿 {lab} 单独(2017-09起) {gs[0]:+.1f}%/{gs[1]:.1f}%/{gs[2]:.2f} | GC=F 数据 {O.dstr(gb[0]['t'])}..{O.dstr(gb[-1]['t'])}", flush=True)
    gl = st(g, min(g)); print(f"黄金腿 {lab} 单独(全部历史)   {gl[0]:+.1f}%/{gl[1]:.1f}%/{gl[2]:.2f}", flush=True)
    for w in (0.25, 0.5, 1.0):
        show(f"方案F + 黄金{lab} {w}", planF(SC0, g, w))
bh = {x["t"] // O.DAY: x["c"] for x in O.stock_daily("GC=F")}; s = st(bh); print(f"参照 黄金一直拿着(2017-09起) {s[0]:+.1f}%/{s[1]:.1f}%/{s[2]:.2f}", flush=True)
print("ALL DONE", flush=True)
