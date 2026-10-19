"""2026-10-17：8890 加 8 小时周期 + 工作台加方案F币圈腿B的两条8h子腿(plan_f_st8h / plan_f_bb8h)。"""
import os
os.chdir("/home/stratroster/unicorn_quant")
p = "engine/data.py"; s = open(p, encoding="utf-8").read()
old = 'INTERVALS = ["1m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "12h", "1d", "1w"]'
if '"8h"' not in s.split("INTERVALS =")[1].split("\n")[0]:
    assert old in s
    s = s.replace(old, 'INTERVALS = ["1m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "8h", "12h", "1d", "1w"]')
    open(p, "w", encoding="utf-8").write(s)
p = "engine/backtest.py"; s = open(p, encoding="utf-8").read()
if '"8h": 28_800_000' not in s:
    old = '"6h": 21_600_000,'
    assert old in s
    s = s.replace(old, '"6h": 21_600_000, "8h": 28_800_000,', 1)
    open(p, "w", encoding="utf-8").write(s)
p = "engine/plan.py"; s = open(p, encoding="utf-8").read()
if "plan_f_st8h" not in s:
    i = s.index("BUILTINS = {")
    add = '''
# ---------------- 2026-10-17 B方案的两条 8h 子腿(各占币圈腿 25%；日线 EMA 那条占 50%) ----------------
import math as _math


def _st_dir_list(bars, n=10, m=3.0):
    A = ta.atr(bars, n); d = [1] * len(bars); ub = lb = None
    for i, x in enumerate(bars):
        a = A[i] or 0; hl = (x["h"] + x["l"]) / 2; u = hl + m * a; l = hl - m * a
        if i == 0:
            ub, lb = u, l; continue
        pc = bars[i - 1]["c"]
        ub = u if (u < ub or pc > ub) else ub; lb = l if (l > lb or pc < lb) else lb
        d[i] = 1 if x["c"] > ub else (-1 if x["c"] < lb else d[i - 1])
    return d


def _bb_state_list(bars, n=20, k=2.0):
    c = [x["c"] for x in bars]; out = [0] * len(c); st = 0
    for i in range(n - 1, len(c)):
        w = c[i - n + 1: i + 1]; mid = sum(w) / n; sd = (sum((v - mid) ** 2 for v in w) / n) ** .5
        if c[i] > mid + k * sd: st = 1
        elif c[i] < mid - k * sd: st = -1
        elif (st > 0 and c[i] < mid) or (st < 0 and c[i] > mid): st = 0
        out[i] = st
    return out


def _plan_8h(kind, bars_by_tf, params, position):
    p = params or {}
    bars = bars_by_tf["base"]
    if len(bars) < 80:
        return None
    d = (_st_dir_list(bars) if kind == "st" else _bb_state_list(bars))[-1]
    b = bars[-1]
    close_ms = b["t"] + 8 * 3600000
    ph, mo = phase_of(close_ms)
    ph_txt = f"减半后第{mo:.0f}个月·{ph}市({PHASE_RULE[ph]})"
    ts = time.strftime("%Y-%m-%d %H:%M", time.gmtime(close_ms / 1000))
    name = "8h超级趋势" if kind == "st" else "8h布林"
    sig_txt = (f"{ts} {name}{'转多' if d > 0 else '转空'}" if kind == "st" else
               f"{ts} 8h收盘" + {1: "突破布林上轨", -1: "跌破布林下轨", 0: "回到布林中轨"}[d])
    allowed = lambda x: not ((ph == "牛" and x < 0) or (ph == "熊" and x > 0))
    side = {"LONG": 1, "SHORT": -1}.get((position or {}).get("side"))
    if side:
        if d != side:
            return _sig(b, "CLOSE_QUICK_EXIT", sig_txt + "，离场")
        if not allowed(side):
            return _sig(b, "CLOSE_QUICK_EXIT", f"进入{ph_txt}，不再持{'多' if side > 0 else '空'}，平仓")
        return None
    if d == 0 or not allowed(d):
        return None
    sym = str(p.get("symbol") or "").upper()
    btc_txt = ""
    if sym and sym != "BTCUSDT":
        try:
            from strategy_engine import klines
            bb = [x for x in klines.get_bars("BTCUSDT", "1d", 80) if x["t"] + 86400000 <= close_ms]
            bc = [x["c"] for x in bb]; b8, b30 = ta.ema(bc, 8), ta.ema(bc, 30)
            bu = 1 if b8[-1] > b30[-1] else -1
        except Exception:
            return None
        if bu != d:
            return None
        btc_txt = "；BTC日线同向"
    a = ta.atr(bars, 14)[-1] or 0
    if not a:
        return None
    size = min(1.0, VOL_TGT * _math.sqrt(8 / 24) / (a / b["c"]))
    return _sig(b, "LONG" if d > 0 else "SHORT", f"{sig_txt} → 开{'多' if d > 0 else '空'}；{ph_txt}{btc_txt}；ATR%(8h)={100 * a / b['c']:.1f}，仓位×{size:.2f}",
                size_frac=size)


def plan_f_st8h(bars_by_tf, params, position):
    return _plan_8h("st", bars_by_tf, params, position)


def plan_f_bb8h(bars_by_tf, params, position):
    return _plan_8h("bb", bars_by_tf, params, position)


'''
    s = s[:i] + add + s[i:]
    old = 'OPTIMAL = list(BUILTINS)'
    assert old in s
    s = s.replace(old, '''BUILTINS["plan_f_st8h"] = {"fn": plan_f_st8h, "title": "方案F·币圈腿B 8h超级趋势", "params": {},
                           "desc": "币圈腿B的25%：8h超级趋势(10,3)+严格减半周期+山寨跟BTC日线同向+按波动率调仓(8时周期)"}
BUILTINS["plan_f_bb8h"] = {"fn": plan_f_bb8h, "title": "方案F·币圈腿C 8h布林突破", "params": {},
                           "desc": "币圈腿C的25%：8h收盘突破布林(20,2)上/下轨开仓、回中轨离场+严格减半周期+BTC同向+按波动率调仓(8时周期)"}
OPTIMAL = list(BUILTINS)''')
    open(p, "w", encoding="utf-8").write(s)
p = "static/app.js"; s = open(p, encoding="utf-8").read()
if "plan_f_st8h" not in s:
    s = s.replace("'6h': '6时', '12h': '12时'", "'6h': '6时', '8h': '8时', '12h': '12时'")
    s = s.replace("const FAV = ['plan_f_crypto', 'plan_f_stock'];", "const FAV = ['plan_f_crypto', 'plan_f_st8h', 'plan_f_bb8h', 'plan_f_stock'];")
    old = "  if (/^plan_f_/.test($('#stSel').value) && +$('#stBars').value < 3000) $('#stBars').value = '3000';   // 日线策略默认看约8年\n  if (/^plan_f_/.test($('#stSel').value) && S.interval !== '1d') { S.interval = '1d'; localStorage.uq_itv = '1d'; renderItv(); S.bt = null; await loadChart(); }"
    assert old in s
    s = s.replace(old, """  // 方案F：日线腿用1天周期(3000根≈8年)，两条8h腿用8时周期(6000根≈5.5年)
  if (isStock(S.symbol) && /^plan_f_(st8h|bb8h)$/.test($('#stSel').value)) { $('#stSel').value = 'plan_f_stock'; showStDesc(); syncPills(); }
  if (/^plan_f_/.test($('#stSel').value)) {
    const want = /8h$/.test($('#stSel').value) ? '8h' : '1d', bars = want === '8h' ? '6000' : '3000';
    if (+$('#stBars').value < +bars) $('#stBars').value = bars;
    if (S.interval !== want) { S.interval = want; localStorage.uq_itv = want; renderItv(); S.bt = null; await loadChart(); }
  }""")
    open(p, "w", encoding="utf-8").write(s)
s = open("static/index.html", encoding="utf-8").read()
import re
s = re.sub(r"(app\.js\?v=)([\w]+)", lambda m: m.group(1) + m.group(2) + "8", s)
open("static/index.html", "w", encoding="utf-8").write(s)
print("8h ok")
