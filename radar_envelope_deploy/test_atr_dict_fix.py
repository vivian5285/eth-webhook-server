import os, sys, tempfile, random
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from strategy_engine import indicators as ind
random.seed(1)
raw = []; px = 100.0
for i in range(60):
    o = px; px += random.uniform(-2, 2); h = max(o, px) + 1; l = min(o, px) - 1
    raw.append({"t": i, "o": o, "h": h, "l": l, "c": px, "v": 10})
bars = [[b["t"], b["o"], b["h"], b["l"], b["c"], b["v"]] for b in raw]
try:
    ind.atr_series(bars, 14); raise SystemExit("预期列表K线报错却没报错")
except TypeError:
    pass
want = ind.atr_series(raw, 14)[-1]
# 对supervisor里的两种写法做补丁并执行
src = '''
try:
    atr_ser = _sk_ind.atr_series(bars, REVERSAL_4H_ATR_LEN)
    atr_ser2 = _sk_ind.atr_series(bars, PYRAMID_ADD_ATR_LEN)
except Exception as e:
    atr_ser = None
'''
f = os.path.join(tempfile.mkdtemp(), "sup.py"); open(f, "w", encoding="utf-8").write(src)
import subprocess
here = os.path.dirname(os.path.abspath(__file__))
out = subprocess.check_output([sys.executable, os.path.join(here, "fix_atr_dict_bars.py"), f]).decode()
assert "已修复2处" in out, out
out2 = subprocess.check_output([sys.executable, os.path.join(here, "fix_atr_dict_bars.py"), f]).decode()
assert "无需修改" in out2, out2   # 幂等
ns = {"_sk_ind": ind, "bars": bars, "REVERSAL_4H_ATR_LEN": 14, "PYRAMID_ADD_ATR_LEN": 14}
exec(open(f, encoding="utf-8").read(), ns)
assert ns["atr_ser"] and abs(ns["atr_ser"][-1] - want) < 1e-9 and abs(ns["atr_ser2"][-1] - want) < 1e-9
print("ATR_DICT_FIX_OK", round(want, 4))

# ---- 包线混入模块的ATR取数(真indicators，假klines) ----
import radar_envelope as re_
from strategy_engine import klines as _k
_k.get_bars = lambda sym, tf, limit=60: raw
class B(re_.RadarEnvelopeMixin):
    symbol = "SNDKUSDT"
assert abs(B()._radar_env_atr4h() - want) < 1e-9
class C(re_.CoinWRadarEnvelopeMixin):
    symbol = "SNDK"
    def _get_risk_klines(self, m, n): return bars
assert abs(C()._radar_env_atr4h() - want) < 1e-9
print("MIXIN_ATR_OK")
