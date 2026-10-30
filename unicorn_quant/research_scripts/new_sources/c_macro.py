"""C. 跨资产宏观 regime 作币圈腿的风险开关/加减仓。信号用美股收盘(≤前一天)，次个币圈日生效。
默认只作用于币圈多头仓位(空单不动)；另测'全部仓位'版本。
 C1 纳指 NDX < EMA(n) → 多头×m      C2 美元指数 DXY > EMA(n) → 多头×m
 C3 10年美债 TNX 近 L 天上行 > x 个基点 → 多头×m     C4 三个开关计数 → 多头×(1-0.25×计数)
 C5 VIX > 阈值 → 多头×m(附带)
对照：m≡1(=方案F)、随机开关(同频率)。"""
from common import *
import itertools
res = []
ndx, dxy, tnx, vix = yh("NDX").c, yh("DXY").c, yh("TNX").c, yh("VIX").c
base_r = report(base(), "2022-01-01", "方案F"); base_r["ratio_0322"] = report(base(), "2022-03-01")["ratio"]; res.append(base_r)
print("方案F", fmt(base_r), base_r["ratio_0322"])
run_overlay("对照 m≡1", pd.Series(1.0, index=coin_combo().index), res=res)


def flag_ema(x, n, above):
    e = x.ewm(span=n, adjust=False).mean(); return (x > e) if above else (x < e)


for n, m in itertools.product((20, 50, 100, 200), (0.0, 0.5)):
    f = to_crypto_days(flag_ema(ndx, n, False).astype(float))
    run_overlay(f"C1 NDX<EMA{n} 多头×{m}", 1 - (1 - m) * f, res=res)
for n in (50, 100, 200):
    f = to_crypto_days(flag_ema(ndx, n, False).astype(float))
    run_overlay(f"C1b NDX<EMA{n} 全部仓位×0.5", 1 - 0.5 * f, side=0, res=res)
for n, m in itertools.product((20, 50, 100, 200), (0.0, 0.5)):
    f = to_crypto_days(flag_ema(dxy, n, True).astype(float))
    run_overlay(f"C2 DXY>EMA{n} 多头×{m}", 1 - (1 - m) * f, res=res)
for L, x in itertools.product((20, 60, 120), (0.15, 0.30, 0.50)):   # TNX 单位=百分点
    f = to_crypto_days((tnx - tnx.shift(L) > x).astype(float))
    run_overlay(f"C3 TNX {L}日升>{int(x*100)}bp 多头×0.5", 1 - 0.5 * f, res=res)
for n in (50, 100, 200):
    k = to_crypto_days(flag_ema(ndx, n, False).astype(float) + flag_ema(dxy, n, True).astype(float)
                       + (tnx - tnx.shift(60) > 0.30).astype(float))
    run_overlay(f"C4 三开关计数 EMA{n} 多头×(1-0.25k)", 1 - 0.25 * k, res=res)
for th, m in itertools.product((20, 25, 30), (0.0, 0.5)):
    f = to_crypto_days((vix > th).astype(float))
    run_overlay(f"C5 VIX>{th} 多头×{m}", 1 - (1 - m) * f, res=res)
rng = np.random.default_rng(1)
f = to_crypto_days(flag_ema(ndx, 100, False).astype(float)); p = f["2022":].mean()
for i in range(5):
    rnd = pd.Series((rng.random(len(f)) < 1 / 20).astype(float), index=f.index)   # 随机切换点、状态持续
    st = (rnd.cumsum() % 2); st = st if st.mean() > 0 else 1 - st
    run_overlay(f"随机开关{i} 多头×0.5(频率同C1)", 1 - 0.5 * st, res=res)
pd.DataFrame(res).to_csv(os.path.join(HERE, "c_macro.csv"), index=False)
