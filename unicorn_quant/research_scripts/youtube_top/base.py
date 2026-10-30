"""油管头部交易博主策略逐个回测的公共底座(2026-10-30)。
本地跑：VPS 引擎+optimal_data 拷到 UQ(默认 scratchpad/uq)，_cache 改成只读本地文件，不联网、不碰 VPS。
基准 = 现行方案F：币圈三条子腿等权(日线EMA8/30 / 8h超级趋势 / 8h布林) × 稳健版币权重 + 美股腿，40% 波动目标。
新策略两种接法：币圈作第4条子腿(四条各1/4)；美股作第2条美股腿(与现行美股腿五五开)。
判定 = 全程、前半(2017-09..2021-12)、后半(2022..) 收益回撤比都超过基准。"""
import os, sys, json, math, time
UQ = os.environ.get("UQ", r"C:/Users/Administrator/AppData/Local/Temp/claude/C--Users-Administrator-Desktop-eth-webhook-server-main/d8fa8b08-bc24-45e7-b4fa-bc803003a334/scratchpad/uq")
sys.path.insert(0, UQ)
from engine import optimal as O

def _cache_ro(name, ttl, fn, fresh=None):
    p = os.path.join(O.OUT, name)
    if os.path.exists(p):
        return json.load(open(p, encoding="utf-8"))
    raise FileNotFoundError(name)
O._cache = _cache_ro
O.tradable_set = lambda: set(O.TRADABLE_FALLBACK)
DAY = 86400000; H = 18993          # 2022-01-01

def st(cv, a=17410, z=99999):
    ks = [k for k in sorted(cv) if a <= k <= z]; v = [cv[k] for k in ks]; pk = 0; dd = 0
    for x in v: pk = max(pk, x); dd = max(dd, 1 - x / pk)
    yrs = (ks[-1] - ks[0]) / 365.25; cagr = (v[-1] / v[0]) ** (1 / yrs) - 1 if v[0] > 0 and v[-1] > 0 else -1
    return 100 * cagr, 100 * dd, cagr / dd if dd else 0

def three(cv):
    a = st(cv); return a[0], a[1], a[2], st(cv, 17410, H)[2], st(cv, H)[2]

def yr(cv):
    by = {}
    for k in sorted(cv):
        if k >= 17410: by.setdefault(time.gmtime(k * 86400).tm_year, []).append(cv[k])
    o = []; pe = None
    for y in sorted(by): xs = by[y]; b0 = pe or xs[0]; o.append(f"{y%100}:{100*(xs[-1]/b0-1):+.0f}" if b0 else "--"); pe = xs[-1]
    return " ".join(o)

def run_targets(b, tgt, hrs, fee=0.0008, vt=0.03, stops=None):
    """tgt[i] = 第 i 根收盘后的目标方向(-1/0/1，可为分数)，下一根开盘执行；stops[i]=持仓中的硬止损价(可选，盘中触发按止损价)。
    仓位 = 资金 × min(1, vt×√(周期/24h) ÷ ATR%)，与 price_action.py 同口径。返回 {日: 净值}, 笔数"""
    A = O.atr(b); v = vt * math.sqrt(hrs / 24); eq = 1.0; q = 0.0; pos = 0; out = {}; n = 0; stp = None
    for i in range(1, len(b)):
        op, pc = b[i]["o"], b[i - 1]["c"]; want = tgt[i - 1]
        if want != pos:
            if q: eq += q * (op - pc) - abs(q) * op * fee; q = 0.0
            if want:
                al = min(1.0, v / max(A[i - 1] / pc, 1e-6)); q = want * eq * al / op; eq -= abs(q) * op * fee; n += 1
                stp = stops[i - 1] if stops else None
            pos = want; pc = op
        if q:
            if stp is not None and ((q > 0 and b[i]["l"] <= stp) or (q < 0 and b[i]["h"] >= stp)):
                px = min(op, stp) if q > 0 else max(op, stp)
                eq += q * (px - pc) - abs(q) * px * fee; q = 0.0; pos = "stopped"
            else:
                eq += q * (b[i]["c"] - pc)
        out[b[i]["t"] // DAY] = max(eq, 0)
    return out, n

_B = {}
def base():
    if _B: return _B
    cd, _, _, _, CD = O.crypto_leg(); O.crypto_8h_leg("st", CD["BTCUSDT"]); O.crypto_8h_leg("bb", CD["BTCUSDT"])
    LEG3 = {s: [O.CURVES["日线EMA8/30"][s], O.CURVES["8h超级趋势"][s], O.CURVES["8h布林突破"][s]] for s in O.M5}
    coin = {s: O.combine(LEG3[s], [1 / 3] * 3) for s in O.M5}
    cB = O.weighted_basket(coin, O.coin_weights(coin, range(2017, 2027))); sc = O.stock_leg()[0]
    F = O.vol_control([cB, sc], [1, 1], 2.0)[0]
    _B.update(CD=CD, LEG3=LEG3, cB=cB, sc=sc, F=F, b=three(F), B8={s: O.spot_8h(s) for s in O.M5})
    return _B

def as_4th_leg(cvs):
    """cvs: {币: 净值曲线} → 作为第4条子腿的方案F"""
    B = base(); c4 = {s: O.combine(B["LEG3"][s] + [cvs[s]], [.25] * 4) for s in O.M5}
    return O.vol_control([O.weighted_basket(c4, O.coin_weights(c4, range(2017, 2027))), B["sc"]], [1, 1], 2.0)[0]

def as_stock_leg2(cv, w=0.5):
    B = base(); s2 = O.combine([B["sc"], cv], [1 - w, w])
    return O.vol_control([B["cB"], s2], [1, 1], 2.0)[0]

def verdict(x, b):
    return "★超越" if x[2] > b[2] and x[3] > b[3] and x[4] > b[4] else ""

def fmt(x): return f"{x[0]:+.1f}%/{x[1]:.1f}%/{x[2]:.2f} 前{x[3]:.2f}/后{x[4]:.2f}"

if __name__ == "__main__":
    t = time.time(); B = base(); print("基准 方案F", fmt(B["b"]), f"{time.time()-t:.0f}s")
