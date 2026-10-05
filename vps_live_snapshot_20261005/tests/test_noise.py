import ast,sys,time,types,logging
logging.basicConfig(level=logging.CRITICAL)
src=open("/tmp/psb_tp1.py",encoding="utf-8").read(); tree=ast.parse(src)
R=[]
def ck(n,ok,e=""): R.append(bool(ok)); print("PASS" if ok else "FAIL",n,e)
def fn(name): return [f for c in tree.body if isinstance(c,ast.ClassDef) for f in c.body if isinstance(f,ast.FunctionDef) and f.name==name][0]
# _tp_audit_ok
ns={"NO_REAL_TP_SYMBOLS":{"SNDKUSDT","ZECUSDT"}}
m=ast.Module([ast.ClassDef("S",[],[],[fn("_tp_audit_ok")],[])],[]); ast.fix_missing_locations(m); exec(compile(m,"m","exec"),ns); S=ns["S"]
def mk(sym):
    s=S(); s.symbol=sym; s.current_side="LONG"; s.watched_entry=1330.0; s.tp_levels_consumed=[]; s._effective_place_tp_levels=lambda:2; return s
ck("1 ZEC: 没有TP(expected=0)视为已齐",mk("ZECUSDT")._tp_audit_ok({"expected":0,"matched_full":0}))
ck("2 SNDK: 同上",mk("SNDKUSDT")._tp_audit_ok({"expected":0,"matched_full":0}))
ck("3 其他品种(BNB): expected=0且TP未吃完仍判不齐(原行为不变)",mk("BNBUSDT")._tp_audit_ok({"expected":0,"matched_full":0}) is False)
# _dyn_trail_candidate 预夹包线
sys.path.insert(0,"/tmp/live_B"); sys.path.insert(0,"/tmp/dyn_stage")
from strategy_engine import klines as KL
def mkbars(n=90,drift=1.0,start=100.0,rng=2.0):
    out=[];p=start
    for i in range(n):
        o=p;c=p+drift;out.append({"t":i*60000,"o":o,"h":max(o,c)+rng/2,"l":min(o,c)-rng/2,"c":c,"v":10.0});p=c
    return out
KL.get_bars=lambda sym,tf,limit=60: mkbars()
class BC: 
    def ip_rate_limit_remaining(s): return 0
lg=types.SimpleNamespace(info=lambda *a,**k:None,warning=lambda *a,**k:None)
ns2={"binance_client":BC(),"logger":lg,"time":time}
m=ast.Module([ast.ClassDef("S",[],[],[fn("_dyn_trail_candidate")],[])],[]); ast.fix_missing_locations(m); exec(compile(m,"m","exec"),ns2); S2=ns2["S"]
s=S2(); s.symbol="ZECUSDT"; s._clamp_to_radar_envelope=lambda p,prior=0.0: 170.0 if p>170.0 else p  # 包线=170(多单:止损不得高于170)
raw=s._dyn_trail_candidate("LONG",200.0)
ck("4 候选(>包线170)被预夹到包线170,不再留给后面刷WARNING",raw==170.0,str(raw))
s2=S2(); s2.symbol="ZECUSDT"; s2._clamp_to_radar_envelope=lambda p,prior=0.0: p
ck("5 候选没越过包线时原样返回",s2._dyn_trail_candidate("LONG",200.0)<200.0)
print("TOTAL",sum(R),"/",len(R))
