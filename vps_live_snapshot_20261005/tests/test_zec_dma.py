import ast,types
def fn_of(path,name):
    t=ast.parse(open(path,encoding="utf-8").read())
    return [f for c in t.body if isinstance(c,ast.ClassDef) for f in c.body if isinstance(f,ast.FunctionDef) and f.name==name][0]
R=[]
def ck(n,ok): R.append(bool(ok)); print("PASS" if ok else "FAIL",n)
# B/E mixin
f=fn_of("/home/binanceB/binance-engine/radar_reentry_mixin.py","_maybe_fast_exit_on_dual_ma_break")
ns={"DUAL_MA_EXIT_ENABLED":True,"_smart_hard_stop_mode_enabled":lambda:True,"time":__import__("time")}
m=ast.Module([ast.ClassDef("S",[],[],[f],[])],[]); ast.fix_missing_locations(m); exec(compile(m,"x","exec"),ns)
S=ns["S"]
for sym in ("ZECUSDT","SNDKUSDT"):
    s=S(); s.symbol=sym; s.current_side="LONG"; s._dual_ma_exit_profit_gate_passed=True
    ck(f"B/E {sym} 直接放行不评估",s._maybe_fast_exit_on_dual_ma_break(1300.0,1250.0)==1250.0)
s=S(); s.symbol="BNBUSDT"; s.current_side="LONG"; s._dual_ma_exit_profit_gate_passed=True
try: s._maybe_fast_exit_on_dual_ma_break(1300.0,1250.0); passed=False
except Exception: passed=True   # 其他品种继续往下走(裸对象缺属性即证明没被短路)
ck("B/E 其他品种(BNB)不被短路,仍走原检查",passed)
# CoinW
f=fn_of("/home/coinw/coinw-hft-server/position_supervisor_coinw.py","_maybe_fast_exit_on_dual_ma_break")
ns2={"DUAL_MA_EXIT_ENABLED":True,"time":__import__("time")}
m=ast.Module([ast.ClassDef("S",[],[],[f],[])],[]); ast.fix_missing_locations(m); exec(compile(m,"x","exec"),ns2); S2=ns2["S"]
for sym in ("ZEC","SNDK"):
    s=S2(); s.symbol=sym; ck(f"CoinW {sym} 直接返回",s._maybe_fast_exit_on_dual_ma_break(1300.0) is None)
s=S2(); s.symbol="BNB"
try: s._maybe_fast_exit_on_dual_ma_break(1300.0); passed=False
except Exception: passed=True
ck("CoinW 其他品种(BNB)不被短路",passed)
print("TOTAL",sum(R),"/",len(R))
