import sys, math, time
sys.path.insert(0, "/home/stratroster/unicorn_quant"); sys.path.insert(0, "/home/stratroster/eth-webhook-server")
from engine import optimal as O
cd, ct, cs, cm, CD = O.crypto_leg()
st8, _, _ = O.crypto_8h_leg("st", CD["BTCUSDT"]); bb8, _, _ = O.crypto_8h_leg("bb", CD["BTCUSDT"])
cB = O.combine([cd, st8, bb8], [.5, .25, .25])
sc, _, _, _ = O.stock_leg()
def show(nm, curve):
    s = O.stats(curve); c = s["cagr"] / 100
    dbl = math.log(2) / math.log(1 + c); y10 = math.log(10) / math.log(1 + c); y100 = math.log(100) / math.log(1 + c)
    print(f"{nm:34s} 年化{s['cagr']:+6.1f}% 回撤{s['max_dd']:5.1f}% 比{s['ratio']:5.2f} | 翻倍{dbl:4.1f}年 10倍{y10:4.1f}年 100倍{y100:4.1f}年 | 近12月{s['last12m']:+.0f}%(回撤{s['last12m_dd']:.0f}%)")
    print("     逐年: " + " ".join(f"{y}:{v:+.0f}%" for y, v in s["yearly"].items()))
show("币圈腿B(日线50+8h超级25+8h布林25) 1倍", cB)
show("币圈腿(只日线EMA) 1倍", cd)
show("美股腿(行业轮动) 1倍", sc)
show("方案F 币B×0.75+股×0.75(1.5倍)", O.combine([cB, sc], [.75, .75]))
show("币B×1 + 股×1 (2倍)", O.combine([cB, sc], [1, 1]))
show("币(只日线)×1 + 股×1 (2倍)", O.combine([cd, sc], [1, 1]))
show("币B×0.5+股×0.5 (1倍)", O.combine([cB, sc], [.5, .5]))
