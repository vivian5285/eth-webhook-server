import sys,os,ast,time,types,tempfile,threading,importlib.util,logging
logging.basicConfig(level=logging.CRITICAL)
sys.path.insert(0,"/tmp/dyn_stage")
import dyn_trail as D
R=[]
def ck(n,ok,e=""): R.append(bool(ok)); print("PASS" if ok else "FAIL",n,e)
# A 纯函数
ck("A1 强趋势k上限(发散1.6x/动能0.7x参考→<=7)",abs(D.trail_k(5,5,0.86,0.36)-6.6)<1e-9,str(D.trail_k(5,5,0.86,0.36)))
ck("A2 弱趋势k被下限夹到2.0",D.trail_k(0,0,0.86,0.36)==2.0,str(D.trail_k(0,0,0.86,0.36)))
ck("A3 中性(=参考值)k=4.0",abs(D.trail_k(0.86,0.36,0.86,0.36)-4.0)<1e-9)
def mkbars(n=80,drift=1.0,start=100.0,rng=2.0):
    out=[];p=start
    for i in range(n):
        o=p;c=p+drift;out.append({"t":i,"o":o,"h":max(o,c)+rng/2,"l":min(o,c)-rng/2,"c":c,"v":1.0});p=c
    return out
up=mkbars(drift=1.0)
st,m=D.dynamic_trail_stop("LONG",200.0,up,"ZECUSDT"); ck("A4 多单止损在峰值下方",st is not None and st<200.0,str((st,m)))
dn=mkbars(drift=-1.0,start=300.0)
st2,m2=D.dynamic_trail_stop("SHORT",150.0,dn,"SNDKUSDT"); ck("A5 空单止损在峰值上方",st2 is not None and st2>150.0,str((st2,m2)))
ck("A6 距离=k×ATR",abs((200.0-st)-m["k"]*m["atr"])<0.02,str((200.0-st,m["k"]*m["atr"])))
ck("A7 K线不足返回None",D.dynamic_trail_stop("LONG",200.0,up[:20],"ZECUSDT")[0] is None)
ck("A8 非ZEC/SNDK返回None",D.dynamic_trail_stop("LONG",200.0,up,"BNBUSDT")[0] is None)
# B 币安 _dyn_trail_candidate(AST抽出)
src=open("/tmp/psb_dyn.py",encoding="utf-8").read(); tree=ast.parse(src)
fn=[f for c in tree.body if isinstance(c,ast.ClassDef) for f in c.body if isinstance(f,ast.FunctionDef) and f.name=="_dyn_trail_candidate"][0]
class BC:
    def __init__(s,rl=0): s.rl=rl
    def ip_rate_limit_remaining(s): return s.rl
lg=types.SimpleNamespace(info=lambda *a,**k:None,warning=lambda *a,**k:None)
def build(rl=0,klines_fn=None):
    ns={"binance_client":BC(rl),"logger":lg,"time":time}
    m=ast.Module([ast.ClassDef("S",[],[],[fn],[])],[]); ast.fix_missing_locations(m); exec(compile(m,"m","exec"),ns)
    pk=types.ModuleType("strategy_engine"); kl=types.ModuleType("strategy_engine.klines"); kl.get_bars=klines_fn; pk.klines=kl
    sys.modules["strategy_engine"]=pk; sys.modules["strategy_engine.klines"]=kl
    s=ns["S"](); s.symbol="ZECUSDT"; return s
s=build(0,lambda sym,tf,limit=60: mkbars(drift=1.0)); r=s._dyn_trail_candidate("LONG",200.0); ck("B1 正常返回候选止损(<峰值)",r is not None and r<200.0,str(r))
s=build(30,lambda sym,tf,limit=60: mkbars()); ck("B2 IP限流→None(不拉K线)",s._dyn_trail_candidate("LONG",200.0) is None)
def boom(*a,**k): raise RuntimeError("x")
s=build(0,boom); ck("B3 K线异常→None不抛",s._dyn_trail_candidate("LONG",200.0) is None)
s=build(0,lambda sym,tf,limit=60: mkbars()[:10]); ck("B4 K线不足→None",s._dyn_trail_candidate("LONG",200.0) is None)
# C CoinW
os.chdir(tempfile.mkdtemp()); sys.path.insert(0,"/home/coinw/coinw-hft-server")
spec=importlib.util.spec_from_file_location("pscd","/tmp/psc_fix.py"); pm=importlib.util.module_from_spec(spec); spec.loader.exec_module(pm)
C=pm.PositionSupervisorCoinW
def mkc(side,best,cand,rebase_log):
    s=C.__new__(C); s.symbol="ZEC"; s.pipeline=types.SimpleNamespace(data={"side":side})
    s.radar=types.SimpleNamespace(get_state=lambda: types.SimpleNamespace(best_price=best),rebase_to=lambda x: rebase_log.append(x))
    s._dyn_trail_candidate=lambda side,best: cand; return s
L=[]; s=mkc("LONG",1343.0,1320.0,L); r=s._dyn_trail_radar_step(1315.88); ck("C1 多单候选更紧→推送并内部rebase",r==1320.0 and L==[1320.0],str((r,L)))
L=[]; s=mkc("LONG",1343.0,1300.0,L); r=s._dyn_trail_radar_step(1315.88); ck("C2 候选更松→不放松(None),雷达内部回到原值",r is None and L==[1315.88],str((r,L)))
L=[]; s=mkc("LONG",1343.0,None,L); r=s._dyn_trail_radar_step(1315.88); ck("C3 取不到动态值→保持原止损",r is None and L==[1315.88])
L=[]; s=mkc("SHORT",1714.4,1750.0,L); r=s._dyn_trail_radar_step(1755.2); ck("C4 空单候选更紧(更低)→推送",r==1750.0 and L==[1750.0],str((r,L)))
L=[]; s=mkc("SHORT",1714.4,1796.0,L); r=s._dyn_trail_radar_step(1755.2); ck("C5 空单候选更松→不放松",r is None and L==[1755.2])
# CoinW _dyn_trail_candidate 用 _get_risk_klines(列表K线)
s=C.__new__(C); s.symbol="ZEC"; raw=[[b["t"],b["o"],b["h"],b["l"],b["c"],b["v"]] for b in mkbars(drift=1.0)]
s._get_risk_klines=lambda m,l: raw; r=s._dyn_trail_candidate("LONG",200.0); ck("C6 CoinW候选(列表K线转dict)",r is not None and r<200.0,str(r))
print("TOTAL",sum(R),"/",len(R))
# C7 包线夹取：候选比包线更紧时不拉低雷达内部止损
pm.clamp_to_envelope=pm.clamp_to_envelope
L=[]; s=mkc("SHORT",1713.53,1728.9,L); s._radar_envelope_stop=lambda: 1749.32; r=s._dyn_trail_radar_step(1749.32)
ck("C7 空单候选1728.9<包线1749.32→夹到包线,不比原止损更紧→不推送,内部保持1749.32",r is None and L==[1749.32],str((r,L)))
L=[]; s=mkc("SHORT",1713.53,1728.9,L); s._radar_envelope_stop=lambda: 1730.0; r=s._dyn_trail_radar_step(1755.2)
ck("C8 包线1730比原止损1755更紧→推进到1730",r==1730.0 and L==[1730.0],str((r,L)))
L=[]; s=mkc("LONG",1343.26,1330.0,L); s._radar_envelope_stop=lambda: 1307.0; r=s._dyn_trail_radar_step(1300.0)
ck("C9 多单候选1330比包线1307更紧→夹到1307(仍比原止损1300更紧)→推进1307",r==1307.0 and L==[1307.0],str((r,L)))
print("TOTAL2",sum(R),"/",len(R))
