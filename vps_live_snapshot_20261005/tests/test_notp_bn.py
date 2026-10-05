import ast, math, types
src=open("/tmp/psb_tp1.py",encoding="utf-8").read(); tree=ast.parse(src)
ns={"math":math,"NO_REAL_TP_SYMBOLS":{"SNDKUSDT","ZECUSDT"},"MIN_TP_LEG_QTY":0.001,"LEG_TP_RATIOS":[0.10,0.20,0.70],"logger":types.SimpleNamespace(debug=lambda *a,**k:None,info=lambda *a,**k:None,warning=lambda *a,**k:None)}
fn=[f for c in tree.body if isinstance(c,ast.ClassDef) for f in c.body if isinstance(f,ast.FunctionDef) and f.name in ("_split_tp_quantities","_tp_slices_for_initial")]
mod=ast.Module([ast.ClassDef("S",[],[],fn,[])],[]); ast.fix_missing_locations(mod); exec(compile(mod,"m","exec"),ns); S=ns["S"]
R=[]
def mk(sym,ratios):
    s=S(); s.symbol=sym; s.min_qty=0.001; s._leg_ratios=ratios; s.tv_tps=[1478.2,1494.6,1560.0]; s.regime_settings={1:{"ratios":ratios}}
    s._effective_place_tp_levels=lambda:2; s._tp_split_regime=lambda:1; return s
def ck(n,ok,e=""): R.append(bool(ok)); print("PASS" if ok else "FAIL",n,e)
s=mk("ZECUSDT",[0,0,1.0]); r=s._split_tp_quantities(0.327,[0.1,0.2,0.7]); ck("1 ZEC 拆分TP1/TP2恒0(即使传入旧比例)",r[0]==0 and r[1]==0,str(r))
ck("2 ZEC 可挂TP切片为空",s._tp_slices_for_initial(0.327)==[],str(s._tp_slices_for_initial(0.327)))
s=mk("SNDKUSDT",[0,0,1.0]); ck("3 SNDK 可挂TP切片为空",s._tp_slices_for_initial(0.23)==[])
s=mk("XRPUSDT",[0.1,0.2,0.7]); r=s._split_tp_quantities(100.0,[0.1,0.2,0.7]); ck("4 其他品种不受影响(10/20/70)",abs(r[0]-10)<1e-6 and abs(r[1]-20)<1e-6,str(r))
sl=s._tp_slices_for_initial(100.0); ck("5 其他品种仍挂TP1+TP2两档",len(sl)==2,str(sl))
print("TOTAL",sum(R),"/",len(R))
