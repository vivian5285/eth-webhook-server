import sys,os,ast,time,types,json,tempfile,importlib.util,logging
logging.basicConfig(level=logging.CRITICAL)
R=[]
def ck(n,ok,e=""): R.append(bool(ok)); print("PASS" if ok else "FAIL",n,e)
def mkbars(n=90,drift=-1.0,start=300.0,rng=2.0):
    out=[];p=start
    for i in range(n):
        o=p;c=p+drift;out.append({"t":i*60000,"o":o,"h":max(o,c)+rng/2,"l":min(o,c)-rng/2,"c":c,"v":10.0});p=c
    return out
dn=mkbars(drift=-1.0); up=mkbars(drift=1.0,start=100.0)
last_dn=dn[-1]["c"]; last_up=up[-1]["c"]
# ---------- B (AST) ----------
sys.path.insert(0,"/tmp/live_B")
from strategy_engine import klines as KL
src=open("/tmp/psb_dyn.py",encoding="utf-8").read(); tree=ast.parse(src)
want={"_check_pyramid_add","_pyramid_first_entry"}; fns=[f for c in tree.body if isinstance(c,ast.ClassDef) for f in c.body if isinstance(f,ast.FunctionDef) and f.name in want]
consts={"PYRAMID_ADD_SYMBOLS","PYRAMID_ADD_FAST_LEN","PYRAMID_ADD_SLOW_LEN","PYRAMID_ADD_ATR_LEN","PYRAMID_ADD_INTERVAL_MIN","PYRAMID_ADD_KLINE_LIMIT","PYRAMID_ADD_DIVERGENCE_THRESHOLDS","PYRAMID_ADD_CHECK_INTERVAL_SEC","PYRAMID_ADD_VOLUME_VETO_WINDOW","PYRAMID_ADD_VOLUME_VETO_FRAC","PYRAMID_ADD_MODE","PYRAMID_ADD_MILESTONE_ATR"}
lg=types.SimpleNamespace(info=lambda *a,**k:None,warning=lambda *a,**k:None,debug=lambda *a,**k:None)
ns={"time":time,"json":json,"logger":lg}
for n in tree.body:
    if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id in consts for t in n.targets): exec(compile(ast.Module([n],[]),"c","exec"),ns)
m=ast.Module([ast.ClassDef("S",[],[],fns,[])],[]); ast.fix_missing_locations(m); exec(compile(m,"m","exec"),ns); S=ns["S"]
def mkB(side,bars,e0_prof,stage=0,veto=False,a0=2.0,e0_override=None):
    os.chdir(tempfile.mkdtemp()); KL.get_bars=lambda sym,tf,limit=60: bars
    s=S(); s.symbol="ZECUSDT"; s.current_side=side; s.watched_qty=0.3; s.pyramid_stage=stage; s._pyramid_last_check_ts=0
    s._radar_sync_open_ts=time.time()-3600; s._get_locked_initial_atr=lambda:a0; s.calls=[]
    s._has_4h_reversal_signal=lambda side:(veto,"x",0,0); s._execute_pyramid_add=lambda st,a: s.calls.append((st,a))
    close=bars[-1]["c"]; d=1.0 if side=="LONG" else -1.0
    s.watched_entry=e0_override if e0_override else close-d*e0_prof*a0   # 首仓价=收盘-顺势*浮盈*ATR
    return s
# ZEC 参数: fast8 slow30，ZEC bars 用60根需>=32
s=mkB("SHORT",dn,2.2); s._check_pyramid_add(); ck("B1 空单浮盈2.2×ATR<3 → 不加",s.calls==[])
s=mkB("SHORT",dn,3.2); s._check_pyramid_add(); ck("B2 空单浮盈3.2×ATR≥3、趋势一致 → 加仓stage0",len(s.calls)==1 and s.calls[0][0]==0,str(s.calls))
s=mkB("LONG",dn,3.2); s._check_pyramid_add(); ck("B3 多单但行情是下跌(趋势不一致) → 不加",s.calls==[])
s=mkB("LONG",up,3.5); s._check_pyramid_add(); ck("B4 多单上涨浮盈3.5×ATR → 加",len(s.calls)==1,str(s.calls))
s=mkB("SHORT",dn,3.2,veto=True); s._check_pyramid_add(); ck("B5 4H反转信号一票否决 → 不加",s.calls==[])
s=mkB("SHORT",dn,5.0,stage=1); open("pyramid_e0_ZECUSDT.json","w").write(json.dumps({"key":"SHORT|%d"%int(s._radar_sync_open_ts),"e0":last_dn+5.0*2.0})); s._check_pyramid_add(); ck("B6 stage1需6×ATR: 浮盈5.0 → 不加",s.calls==[])
s=mkB("SHORT",dn,6.5,stage=1); open("pyramid_e0_ZECUSDT.json","w").write(json.dumps({"key":"SHORT|%d"%int(s._radar_sync_open_ts),"e0":last_dn+6.5*2.0})); s._check_pyramid_add(); ck("B7 stage1浮盈6.5×ATR → 加(用首仓价,而不是加权均价)",len(s.calls)==1 and s.calls[0][0]==1,str(s.calls))
s=mkB("SHORT",dn,7.0,stage=1); s._check_pyramid_add(); ck("B8 stage1却没有首仓价记录 → 宁可不加",s.calls==[])
s=mkB("SHORT",dn,3.2); s._check_pyramid_add(); s.watched_entry=last_dn+10*2.0; s._pyramid_last_check_ts=0; s.calls=[]; s._check_pyramid_add(); ck("B9 首仓价已落盘,账本入场价后来变成加权均价也不影响判断",len(s.calls)==1)
# ---------- CoinW ----------
os.chdir(tempfile.mkdtemp()); sys.path.insert(0,"/home/coinw/coinw-hft-server")
spec=importlib.util.spec_from_file_location("pscp","/tmp/psc_fix.py"); pm=importlib.util.module_from_spec(spec); spec.loader.exec_module(pm)
C=pm.PositionSupervisorCoinW
def mkC(side,bars,prof,stage=0,veto=False,a0=2.0,pid="P1"):
    os.makedirs("data",exist_ok=True)
    try: os.remove("data/pyramid_e0_ZEC.json")
    except Exception: pass
    s=C.__new__(C); s.symbol="ZEC"; close=bars[-1]["c"]; d=1.0 if side=="LONG" else -1.0
    s.pipeline=types.SimpleNamespace(data={"side":side,"position_id":pid,"entry":close-d*prof*a0})
    s.radar=types.SimpleNamespace(get_state=lambda: types.SimpleNamespace(initial_atr=a0))
    s._pyramid_stage_cache=stage; s._pyramid_last_check_ts=0; s.calls=[]
    s._get_risk_klines=lambda m,l:[[b["t"],b["o"],b["h"],b["l"],b["c"],b["v"]] for b in bars]
    s._has_4h_reversal_signal=lambda side:(veto,"x",0,0); s._execute_pyramid_add=lambda st,a,sd,px: s.calls.append((st,sd))
    s._load_pyramid_stage=lambda: stage
    return s
s=mkC("SHORT",dn,2.2); s._check_pyramid_add(last_dn); ck("C1 CoinW 空单2.2×ATR → 不加",s.calls==[])
s=mkC("SHORT",dn,3.2); s._check_pyramid_add(last_dn); ck("C2 CoinW 空单3.2×ATR → 加stage0",len(s.calls)==1 and s.calls[0]==(0,"SHORT"),str(s.calls))
s=mkC("LONG",dn,3.2); s._check_pyramid_add(last_dn); ck("C3 CoinW 趋势不一致 → 不加",s.calls==[])
s=mkC("SHORT",dn,3.2,veto=True); s._check_pyramid_add(last_dn); ck("C4 CoinW 4H否决 → 不加",s.calls==[])
s=mkC("SHORT",dn,7.0,stage=1); s._check_pyramid_add(last_dn); ck("C5 CoinW stage1无首仓记录 → 不加",s.calls==[])
s=mkC("SHORT",dn,3.2); s._check_pyramid_add(last_dn); s.pipeline.data["entry"]=last_dn+9*2.0; s._pyramid_last_check_ts=0; s.calls=[]; s._check_pyramid_add(last_dn); ck("C6 CoinW 首仓价已落盘,entry变均价后仍按首仓价判断",len(s.calls)==1)
print("TOTAL",sum(R),"/",len(R))
