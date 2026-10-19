"""2026-10-19 宝贝采纳"稳健版币种权重"：每年1月1日按过去3年每个币(三条子腿组合)的收益回撤比定权重，单币≤40%；
回测改成和擂台/实盘一样"按权重×总资金下单、每天平衡"。"""
import shutil, time, os, re, ast
U = "/home/stratroster/unicorn_quant"; A = "/home/stratroster/eth-webhook-server/strategy_engine/strategies"
bk = f"{U}/backups/pre_weights_{time.strftime('%Y%m%d_%H%M%S')}"; os.makedirs(bk, exist_ok=True)
for f in (f"{U}/engine/optimal.py", f"{U}/engine/universe.py", f"{U}/static/best.js", f"{U}/static/best.html", f"{A}/daily_ema_brk.py", f"{A}/intraday_trend_cycle.py"):
    shutil.copy(f, bk)
def sub(path, old, new, n=1):
    s = open(path, encoding="utf-8").read(); assert s.count(old) == n, (path, old[:70], s.count(old))
    s = s.replace(old, new)
    if path.endswith(".py"): ast.parse(s)
    open(path, "w", encoding="utf-8").write(s)
OPT = f"{U}/engine/optimal.py"
# ---- optimal.py ----
sub(OPT, "    return port, trades, state, meta, D", "    CURVES[\"日线EMA8/30\"] = curves\n    return port, trades, state, meta, D")
sub(OPT, "    return port, trades, opens", "    CURVES[\"8h超级趋势\" if kind == \"st\" else \"8h布林突破\"] = curves\n    return port, trades, opens")
sub(OPT, 'SUB_W = {"日线EMA8/30": 0.5, "8h超级趋势": 0.25, "8h布林突破": 0.25}', '''SUB_W = {"日线EMA8/30": 0.5, "8h超级趋势": 0.25, "8h布林突破": 0.25}
# 2026-10-19 宝贝采纳"稳健版币种权重"(/root/research weight_coins2 / yearly_compare_20261019.py)：
#   每年1月1日，按过去3年每个币(三条子腿组合)在本策略上的收益回撤比给权重，单币≤40%，历史不够就等权；
#   并且币圈腿按"权重×总资金、每天平衡"汇总——和擂台/实盘按总资金下单的方式一致(原来是各币独立滚动、不平衡)。
#   2017起主方案：各币独立滚动 4.43 → 等权每天平衡 4.86 → 稳健版 5.02(前后两半都更好，逐年 5/10 年收益更高、6/10 年回撤更小)。
CURVES: dict = {}
WEIGHT_LOOKBACK, WEIGHT_CAP = 1095, 0.4


def _win_ratio(cv, a, z):
    ks = [k for k in sorted(cv) if a <= k <= z]
    if len(ks) < 2: return None
    v = [cv[k] for k in ks]; pk = 0.0; dd = 0.0
    for x in v:
        pk = max(pk, x); dd = max(dd, 1 - x / pk if pk else 0)
    yrs = (ks[-1] - ks[0]) / 365.25
    if yrs <= 0 or v[0] <= 0 or v[-1] <= 0: return None
    cagr = (v[-1] / v[0]) ** (1 / yrs) - 1
    return cagr / dd if dd else None


def _capw(raw, cap):
    w = {s: x for s, x in raw.items() if x > 0}
    if not w: return {}
    for _ in range(20):
        t_ = sum(w.values()); w = {s: x / t_ for s, x in w.items()}
        over = [s for s, x in w.items() if x > cap + 1e-9]
        if not over: break
        rest = {s: x for s, x in w.items() if s not in over}; rt = sum(rest.values()); left = 1 - cap * len(over)
        w = {**{s: cap for s in over}, **({s: x / rt * left for s, x in rest.items()} if rt > 0 else {})}
    return w


def coin_weights(coin_curves, years):
    """{年份: {币: 权重}}：只用该年1月1日之前 WEIGHT_LOOKBACK 天的数据"""
    out = {}
    for y in years:
        y0 = calendar.timegm((y, 1, 1, 0, 0, 0)) // 86400; y1 = calendar.timegm((y + 1, 1, 1, 0, 0, 0)) // 86400
        avail = [s for s, cv in coin_curves.items() if any(y0 <= k < y1 for k in cv)]
        sc = {}
        for s in avail:
            ks = [k for k in coin_curves[s] if y0 - WEIGHT_LOOKBACK <= k < y0]
            if len(ks) >= 300:
                r = _win_ratio(coin_curves[s], y0 - WEIGHT_LOOKBACK, y0 - 1)
                if r is not None: sc[s] = max(0.0, r)
        w = _capw(sc, WEIGHT_CAP)
        out[y] = {s: round(x, 4) for s, x in (w or {s: 1 / len(avail) for s in avail}).items()} if avail else {}
    return out


def weighted_basket(coin_curves, wy):
    days = sorted(set().union(*[set(v) for v in coin_curves.values()])); prev = {}; v = 1.0; out = {}
    for d in days:
        w = wy.get(time.gmtime(d * 86400).tm_year, {}); r = 0.0
        for s, cv in coin_curves.items():
            if d in cv:
                if s in prev: r += w.get(s, 0.0) * (cv[d] / prev[s] - 1)
                prev[s] = cv[d]
        v *= 1 + r; out[d] = v
    return out''')
sub(OPT, '''    cport = combine([cdaily, cst, cbb], [SUB_W["日线EMA8/30"], SUB_W["8h超级趋势"], SUB_W["8h布林突破"]])''',
'''    coin_cv = {s: combine([CURVES["日线EMA8/30"][s], CURVES["8h超级趋势"][s], CURVES["8h布林突破"][s]],
                          [SUB_W["日线EMA8/30"], SUB_W["8h超级趋势"], SUB_W["8h布林突破"]]) for s in M5}
    cur_y = time.gmtime().tm_year
    WY = coin_weights(coin_cv, range(2017, cur_y + 1))
    cport = weighted_basket(coin_cv, WY)
    with open(os.path.join(OUT, "crypto_weights.json"), "w", encoding="utf-8") as f:      # 擂台三条币圈子腿按这个下单
        json.dump({"year": cur_y, "current": WY.get(cur_y, {}), "by_year": {str(k): v for k, v in WY.items()},
                   "rule": f"每年1月1日按过去{WEIGHT_LOOKBACK // 365}年每个币的收益回撤比定权重，单币≤{int(WEIGHT_CAP * 100)}%", "asof": int(time.time())}, f, ensure_ascii=False)''')
sub(OPT, '''        "crypto_mix": SUB_W,''', '''        "crypto_mix": SUB_W,
        "crypto_weights": {str(k): v for k, v in WY.items()}, "crypto_weights_current": WY.get(cur_y, {}),''')
s = open(OPT, encoding="utf-8").read()
for imp in ("import calendar", "import json", "import os", "import time"):
    assert re.search(rf"^{imp}\b", s, re.M), imp
# ---- universe.py 贡献按当年权重 ----
UNI = f"{U}/engine/universe.py"
sub(UNI, '''            t["contrib"] = None if t.get("pnl_pct") is None else round(t["pnl_pct"] * (w_c / 5 * W_SUB.get(sub, 1) if kind == "crypto" else w_s), 3)''',
'''            cw = (opt.get("crypto_weights") or {}).get(str(time.gmtime(t["entry_t"] / 1000).tm_year), {}).get(sym, 0.2)
            t["contrib"] = None if t.get("pnl_pct") is None else round(t["pnl_pct"] * (w_c * cw * W_SUB.get(sub, 1) if kind == "crypto" else w_s), 3)''')
# ---- best.js 贡献 + 方案卡 ----
BJ = f"{U}/static/best.js"
sub(BJ, '''const contrib = t => t.pnl_pct == null ? null : t.pnl_pct * (t.leg === "币圈" ? S.d.plan.w_crypto / 5 * (t.w || 1) : S.d.plan.w_stock);''',
'''const cw = t => ((S.d.crypto_weights || {})[new Date(t.entry_t).getUTCFullYear()] || {})[t.symbol] ?? 0.2;   // 2026-10-19 币种按年度权重
const contrib = t => t.pnl_pct == null ? null : t.pnl_pct * (t.leg === "币圈" ? S.d.plan.w_crypto * cw(t) * (t.w || 1) : S.d.plan.w_stock);''')
sub(BJ, '''          <li><b>币圈 1 倍</b>：BTC / ETH / BNB / SOL / XRP 各 1/5；''',
'''          <li><b>币圈 1 倍</b>：BTC / ETH / BNB / SOL / XRP，按过去 3 年表现每年 1 月 1 日定权重(单币≤40%)，今年 <b>${Object.entries(S.d.crypto_weights_current || {}).sort((a, b) => b[1] - a[1]).map(([s, w]) => s.replace('USDT', '') + ' ' + Math.round(w * 100) + '%').join(' · ') || '等权'}</b>；''')
h = open(f"{U}/static/best.html", encoding="utf-8").read(); h = re.sub(r"best\.js\?v=\w+", "best.js?v=18w", h); open(f"{U}/static/best.html", "w", encoding="utf-8").write(h)
# ---- 擂台三条币圈子腿：按今年权重下单 ----
HELP = '''

# 2026-10-19 币种权重：主方案按"过去3年表现每年定权重(单币≤40%)"，由 8890 optimal_run.py 每天写 crypto_weights.json；读不到就按 1/5
_CW = {"mt": 0, "w": {}}


def _coin_frac(symbol: str, default: float) -> float:
    import json, os
    p_ = "/home/stratroster/unicorn_quant/optimal_data/crypto_weights.json"
    try:
        mt = os.path.getmtime(p_)
        if mt != _CW["mt"]:
            with open(p_, "r", encoding="utf-8") as f:
                _CW.update(mt=mt, w=json.load(f).get("current") or {})
        return float(_CW["w"].get(symbol, 0.0)) if _CW["w"] else default
    except Exception:
        return default
'''
DB = f"{A}/daily_ema_brk.py"
sub(DB, "def generate_signal(bars_by_tf", HELP.lstrip("\n") + "\n\ndef generate_signal(bars_by_tf")
sub(DB, 'float(p["target_notional_frac"]) * alloc / default_notional', '_coin_frac(symbol, float(p["target_notional_frac"])) * alloc / default_notional', 2)
sub(DB, """    if not want:
        return None
    if symbol != "BTCUSDT":""", """    if not want or _coin_frac(symbol, float(p["target_notional_frac"])) <= 0:
        return None
    if symbol != "BTCUSDT":""")
IT = f"{A}/intraday_trend_cycle.py"
s = open(IT, encoding="utf-8").read()
m = re.search(r"\ndef generate_signal\(", s); assert m
s = s[:m.start()] + HELP + s[m.start():]
assert s.count('float(p["target_notional_frac"]) * alloc / default_notional') == 1
s = s.replace('float(p["target_notional_frac"]) * alloc / default_notional', '_coin_frac(symbol, float(p["target_notional_frac"])) * alloc / default_notional')
g_old = """    if not allowed(action):
        return None
    symbol = str(p.get("symbol") or "").upper()"""
assert s.count(g_old) == 1
s = s.replace(g_old, """    if not allowed(action):
        return None
    symbol = str(p.get("symbol") or "").upper()
    if _coin_frac(symbol, float(p["target_notional_frac"])) <= 0:
        return None                                        # 2026-10-19 今年权重为0的币不开新仓""")
ast.parse(s); open(IT, "w", encoding="utf-8").write(s)
print("ok", bk)
