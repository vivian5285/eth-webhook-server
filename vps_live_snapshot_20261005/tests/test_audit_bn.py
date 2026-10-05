import ast, sys, os, time, json, math, types, tempfile, logging
logging.basicConfig(level=logging.CRITICAL)
logger=logging.getLogger("t")
tree=ast.parse(open("/tmp/psb_new.py",encoding="utf-8").read())
fns={"_integrity_alert","_verify_add_fill","_audit_integrity"}
consts={"INTEGRITY_AUDIT_INTERVAL_SEC","INTEGRITY_ALERT_COOLDOWN_SEC","INTEGRITY_OVERSIZE_FRAC","ADD_QTY_TOLERANCE_FRAC","TOPUP_MIN_SHORTFALL_USD"}
ns={"time":time,"json":json,"math":math,"os":os,"logger":logger,"PYRAMID_ADD_SYMBOLS":{"SNDKUSDT","ZECUSDT"},
    "is_position_query_failed":lambda p: isinstance(p,dict) and p.get("_failed")}
cls=[]
for n in tree.body:
    if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id in consts for t in n.targets): exec(compile(ast.Module([n],[]),"c","exec"),ns)
    if isinstance(n,ast.ClassDef):
        cls+= [f for f in n.body if isinstance(f,ast.FunctionDef) and f.name in fns]
assert len(cls)==3
mod=ast.Module([ast.ClassDef("S",[],[],cls,[])],[]); ast.fix_missing_locations(mod); exec(compile(mod,"m","exec"),ns); S=ns["S"]
ap=types.ModuleType("account_profiles"); ap.get_symbol_settings=lambda s: {"mode":"equity_notional","enabled":True}; sys.modules["account_profiles"]=ap
dt=types.ModuleType("dingtalk"); dt.report_system_alert=lambda **k:None; sys.modules["dingtalk"]=dt
class BC:
    def __init__(s,q,rl=0,sign=1,price=1335.0): s.q=q;s.rl=rl;s.sign=sign;s.price=price;s.reads=0
    def ip_rate_limit_remaining(s): return s.rl
    def get_position(s,sym,prefer_ws=True,force_rest=False): assert force_rest and not prefer_ws; s.reads+=1; return {"positionAmt":s.sign*s.q}
    def get_current_price(s,sym): return s.price
def mk(bc,side="LONG",stops=(1314.88,),radar=True,sl=1314.88,stage=0,equity=374.5,mode_ok=True):
    ns["binance_client"]=bc; s=S(); s.symbol="ZECUSDT"; s.current_side=side; s.watched_qty=bc.q; s.pyramid_stage=stage; s.qty_step=0.001
    s.current_sl=sl; s._radar_sync_open_ts=time.time()-900; s.alerts=[]
    s._resolve_cap_sizing_base=lambda: equity; s._count_protective_stops=lambda: (None if stops is None else list(stops))
    s._is_radar_active=lambda: radar; s._dingtalk=lambda fn,**k: s.alerts.append(k.get("title")); s._integrity_last_ts=0
    return s
R=[]
def run(name,cond,s):
    s._audit_integrity(); ok=cond(s); R.append(ok); print("PASS" if ok else "FAIL",name,s.alerts)
run("1 正常无告警",lambda s:not s.alerts,mk(BC(0.28)))
run("2 超仓告警",lambda s:any("超仓" in a for a in s.alerts),mk(BC(0.40)))
run("3 已武装无止损告警",lambda s:any("无止损" in a for a in s.alerts),mk(BC(0.28),stops=()))
run("4 挂单查询失败(None)不误报",lambda s:not s.alerts,mk(BC(0.28),stops=None))
run("5 多单止损在现价上方=方向错",lambda s:any("方向错误" in a for a in s.alerts),mk(BC(0.28),stops=(1340.0,)))
run("6 空单止损在现价下方=方向错",lambda s:any("方向错误" in a for a in s.alerts),mk(BC(0.28,sign=-1),side="SHORT",stops=(1300.0,),sl=1350.0))
run("7 空单正常止损在上方无告警",lambda s:not s.alerts,mk(BC(0.28,sign=-1),side="SHORT",stops=(1350.0,),sl=1350.0))
bc=BC(0.40,rl=60); s=mk(bc); s._audit_integrity(); ok=(bc.reads==0 and not s.alerts); R.append(ok); print("PASS" if ok else "FAIL","8 IP限流整轮跳过零读取")
run("9 已金字塔化不报超仓",lambda s:not any("超仓" in a for a in s.alerts),mk(BC(0.40),stage=1))
run("10 未武装雷达无止损不告警",lambda s:not s.alerts,mk(BC(0.28),stops=(),radar=False))
s=mk(BC(0.28,sign=-1),side="SHORT",stops=(1350.0,),sl=1350.0); s.watched_qty=0.28; s.current_sl=1350.0; s._audit_integrity(); s._integrity_last_ts=0; s.current_sl=1380.0; s._audit_integrity()
ok=any("放松" in a for a in s.alerts); R.append(ok); print("PASS" if ok else "FAIL","11 空单账本止损被放松告警",s.alerts)
s=mk(BC(0.40)); s._audit_integrity(); s._audit_integrity(); s._integrity_last_ts=0; s._audit_integrity(); ok=len(s.alerts)==1; R.append(ok); print("PASS" if ok else "FAIL","12 同类告警去重",s.alerts)
s=mk(BC(0.28)); a=s._verify_add_fill("补仓",0.08,0.20,0.28); b=s._verify_add_fill("补仓",0.08,0.20,0.21); ok=a and not b and len(s.alerts)==1; R.append(ok); print("PASS" if ok else "FAIL","13 补/加仓成交量核对",s.alerts)
bc=BC(0.28); s=mk(bc); s.current_side="LONG"; bc.sign=-1; s._audit_integrity(); ok=not s.alerts; R.append(ok); print("PASS" if ok else "FAIL","14 本地/交易所方向不一致跳过不乱报")
print("TOTAL",sum(R),"/",len(R))
