"""2026-10-19 自我审视：方案F 的数字有多少来自"调参/事后"？
① 逐笔统计：胜率、赔率(平均盈/平均亏)、中位数口径、去掉最大1%赢家后的赔率，分腿看
② 拆解(ablation)：从最朴素的版本开始，一项一项加回后来调过的东西，看每一步贡献多少(全程/前半/后半)
   S0 只有日线EMA8/30多空 + 5币等权 + 美股腿，没有减半周期/突破单/底部例外/8h腿/波动率控制，币圈美股直接1+1
   S1 + 减半周期阶段规则(只基于3轮周期，事后最危险的一条)
   S2 + 5日突破单 + 熊市底部反转例外
   S3 + 两条8h子腿(布林用教科书默认20/2，子腿50/25/25)
   S4 + 组合波动率控制40%
   S5 + 币种稳健版权重
   S6 + 子腿等权 + 布林(25,1.25) = 现行"""
import sys, os, time, json, statistics as stt
os.nice(19)
sys.path.insert(0, "/home/stratroster/unicorn_quant")
from engine import optimal as O, bottom
DAY = 86400000; H = 18993
def st(cv, a=17410, z=99999):
    ks = [k for k in sorted(cv) if a <= k <= z]; v = [cv[k] for k in ks]; pk = 0; dd = 0
    for x in v: pk = max(pk, x); dd = max(dd, 1 - x / pk)
    yrs = (ks[-1] - ks[0]) / 365.25; cagr = (v[-1] / v[0]) ** (1 / yrs) - 1
    return 100 * cagr, 100 * dd, cagr / dd if dd else 0
f = lambda F: f"{st(F)[0]:+6.1f}%/{st(F)[1]:4.1f}%/{st(F)[2]:5.2f} 前{st(F,17410,H)[2]:.2f}/后{st(F,H)[2]:.2f}"
# ① 逐笔
d = json.load(open("/home/stratroster/unicorn_quant/optimal_data/optimal.json", encoding="utf-8"))
T = [t for t in d["trades"] if not t.get("open")]
def tstats(lab, ts):
    r = [t["ret"] for t in ts]; w = [x for x in r if x > 0]; l = [x for x in r if x <= 0]
    if not w or not l: return
    w_sorted = sorted(w); cut = w_sorted[: max(1, int(len(w_sorted) * 0.99))]
    print(f"{lab:14s} {len(r):5d}笔 胜率{100*len(w)/len(r):4.1f}% | 平均盈{sum(w)/len(w):+6.1f}% 平均亏{sum(l)/len(l):+5.1f}% 赔率{(sum(w)/len(w))/abs(sum(l)/len(l)):4.2f}"
          f" | 中位盈{stt.median(w):+5.1f}% 中位亏{stt.median(l):+5.1f}% 中位赔率{stt.median(w)/abs(stt.median(l)):4.2f}"
          f" | 去掉最大1%赢家后赔率{(sum(cut)/len(cut))/abs(sum(l)/len(l)):4.2f} | 最大单笔{max(r):+.0f}%", flush=True)
print("=== ① 逐笔(回测全部已平仓交易，单笔收益=价格涨跌，不含仓位权重) ===")
tstats("全部", T)
for leg in ("日线EMA8/30", "8h超级趋势", "8h布林突破"):
    tstats(leg, [t for t in T if t.get("sub") == leg])
tstats("美股", [t for t in T if t["leg"] == "美股"])
# ② 拆解
print("\n=== ② 拆解：从朴素版开始逐项加回 ===")
orig_phase, orig_brk, orig_bchk, orig_bb, orig_vc = O.phase_of, O.BRK_N, bottom.check, O._bb_states, O.VC_TARGET
sc = O.stock_leg()[0]
def crypto(phase=True, brk=True, legs8=True, sub_w=(1/3, 1/3, 1/3), bb_default=False, weights=True):
    O.phase_of = orig_phase if phase else (lambda t: ("筑底", orig_phase(t)[1]))
    O.BRK_N = orig_brk if brk else 10 ** 9
    bottom.check = orig_bchk if brk else (lambda bars: (False, ""))
    O._bb_states = (lambda b: orig_bb(b, 20, 2.0)) if bb_default else orig_bb
    _, _, _, _, CD = O.crypto_leg(); D1 = dict(O.CURVES["日线EMA8/30"])
    if legs8:
        O.crypto_8h_leg("st", CD["BTCUSDT"]); S8 = dict(O.CURVES["8h超级趋势"]); O.crypto_8h_leg("bb", CD["BTCUSDT"]); B8 = dict(O.CURVES["8h布林突破"])
        coin = {s: O.combine([D1[s], S8[s], B8[s]], list(sub_w)) for s in O.M5}
    else:
        coin = D1
    O.phase_of, O.BRK_N, bottom.check, O._bb_states = orig_phase, orig_brk, orig_bchk, orig_bb
    if weights:
        return O.weighted_basket(coin, O.coin_weights(coin, range(2017, 2027)))
    eq = {}
    for y in range(2017, 2027):
        y0 = time.mktime((y, 1, 1, 0, 0, 0, 0, 0, 0)) // 86400; y1 = time.mktime((y + 1, 1, 1, 0, 0, 0, 0, 0, 0)) // 86400
        av = [s for s in coin if any(y0 <= k < y1 for k in coin[s])]; eq[y] = {s: 1 / len(av) for s in av}
    return O.weighted_basket(coin, eq)
def plan(cB, vc=True):
    return O.vol_control([cB, sc], [1, 1], 2.0)[0] if vc else O.combine([cB, sc], [1, 1])
steps = [
    ("S0 朴素：日线EMA+等权+美股，1+1", dict(phase=False, brk=False, legs8=False, weights=False), False),
    ("S1 +减半周期阶段", dict(phase=True, brk=False, legs8=False, weights=False), False),
    ("S2 +突破单+底部例外", dict(phase=True, brk=True, legs8=False, weights=False), False),
    ("S3 +两条8h腿(布林20/2,50/25/25)", dict(phase=True, brk=True, legs8=True, sub_w=(.5, .25, .25), bb_default=True, weights=False), False),
    ("S4 +波动率控制40%", dict(phase=True, brk=True, legs8=True, sub_w=(.5, .25, .25), bb_default=True, weights=False), True),
    ("S5 +币种稳健权重", dict(phase=True, brk=True, legs8=True, sub_w=(.5, .25, .25), bb_default=True, weights=True), True),
    ("S6 +子腿等权+布林25/1.25(现行)", dict(phase=True, brk=True, legs8=True, sub_w=(1/3, 1/3, 1/3), bb_default=False, weights=True), True),
    ("对照：S6去掉减半周期", dict(phase=False, brk=True, legs8=True, sub_w=(1/3, 1/3, 1/3), bb_default=False, weights=True), True),
]
for lab, kw, vc in steps:
    print(f"{lab:32s} {f(plan(crypto(**kw), vc))}", flush=True)
print("ALL DONE")
