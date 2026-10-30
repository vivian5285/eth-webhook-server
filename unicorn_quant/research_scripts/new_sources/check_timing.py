"""时间对齐检查：币圈腿日收益 key=d 对应哪天的BTC涨跌；以及 overlay(m≡1) 与基准一致。"""
from common import *
c, s, f = legs()
b = json.load(open(os.path.join(DATA, "bars_BTCUSDT.json"))); b = pd.DataFrame(b).iloc[:, :5]; b.columns = list("tohlc")
bc = pd.Series(b.c.values, index=pd.to_datetime(b.t, unit="ms").dt.normalize()).pct_change()
for lag in (-1, 0, 1): print("lag", lag, round(c["2022":].corr(bc.shift(lag).reindex(c["2022":].index)), 3))
print("overlay(1)==base:", (overlay(pd.Series(1.0, index=c.index)) - base()).abs().max())
