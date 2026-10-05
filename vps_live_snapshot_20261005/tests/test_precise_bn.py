import ast, sys, os, time, json, math, types, tempfile, logging
logging.basicConfig(level=logging.CRITICAL); logger=logging.getLogger("t")
tree=ast.parse(open("/tmp/psb_new.py",encoding="utf-8").read())
fns={"_check_principal_topup","_topup_state_path","_fit_qty_to_margin","_integrity_alert","_verify_add_fill"}
consts={"TOPUP_CHECK_INTERVAL_SEC","TOPUP_SETTLE_SEC","TOPUP_MAX_POS_AGE_SEC","TOPUP_MAX_ATTEMPTS","TOPUP_MIN_SHORTFALL_FRAC","TOPUP_MIN_SHORTFALL_USD","TOPUP_BURST_WINDOW_SEC","TOPUP_BURST_INTERVAL_SEC","TOPUP_BURST_SETTLE_SEC","INTEGRITY_ALERT_COOLDOWN_SEC","ADD_QTY_TOLERANCE_FRAC"}
ns={"time":time,"json":json,"math":math,"os":os,"logger":logger,"PYRAMID_ADD_SYMBOLS":{"SNDKUSDT","ZECUSDT"},"is_position_query_failed":lambda p:False}
cls=[]
for n in tree.body:
    if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id in consts for t in n.targets): exec(compile(ast.Module([n],[]),"c","exec"),ns)
    if isinstance(n,ast.ClassDef): cls+=[f for f in n.body if isinstance(f,ast.FunctionDef) and f.name in fns]
assert len(cls)==len(fns),[c.name for c in cls]
mod=ast.Module([ast.ClassDef("S",[],[],cls,[])],[]); ast.fix_missing_locations(mod); exec(compile(mod,"m","exec"),ns); S=ns["S"]
ap=types.ModuleType("account_profiles"); ap.get_symbol_settings=lambda s:{"mode":"equity_notional","enabled":True}; sys.modules["account_profiles"]=ap
dt=types.ModuleType("dingtalk"); dt.report_system_alert=lambda **k:None; sys.modules["dingtalk"]=dt
class BC:
    def __init__(s,q,avail=300.0,rl=0,lev=5.0): s.q=q;s.avail=avail;s.rl=rl;s.lev=lev;s.orders=[]
    def ip_rate_limit_remaining(s): return s.rl
    def get_position(s,sym,prefer_ws=True,force_rest=False): return {"positionAmt":s.q,"entryPrice":1331.0}
    def get_current_price(s,sym): return 1335.0
    def get_futures_account_summary(s,a="USDT"): return {"available_balance":s.avail}
    def get_symbol_leverage(s,sym,default=5.0): return s.lev
    def format_quantity(s,q,sym): return round(math.floor(q/0.001)*0.001,3)
    def place_market_order(s,side,qty,symbol=None): s.orders.append(qty); s.q+=qty; return {"id":1}
R=[]
def ck(n,ok,e=""): R.append(bool(ok)); print("PASS" if ok else "FAIL",n,e)
def mk(bc,anchor=None,equity=374.5,age=25,burst=True,target_rough=None):
    os.chdir(tempfile.mkdtemp()); ns["binance_client"]=bc
    s=S(); s.symbol="ZECUSDT"; s.current_side="LONG"; s.watched_qty=bc.q; s.pyramid_stage=0; s.qty_step=0.001; s.min_qty=0.001
    s.current_sl=1300.0; s.current_atr=16.0; s._radar_sync_open_ts=time.time()-age; s._topup_last_ts=0
    s._resolve_cap_sizing_base=lambda: equity; s._calc_vps_open_qty=lambda px:(equity/px,{"mode":"equity_notional"})
    s._try_better_than_tv_limit_entry=lambda *a,**k:(0.0,0.0); s._save_state=lambda:None; s._sync_exchange_stop=lambda **k:None
    s._dingtalk=lambda *a,**k:None; s._integrity_alert=lambda *a,**k:None; s._verify_add_fill=lambda *a,**k:True
    if anchor: s._open_target_qty=anchor; s._open_target_side="LONG"; s._open_target_ts=time.time()-age
    if burst: s._topup_burst_until=time.time()+200
    return s
bc=BC(0.280); s=mk(bc,anchor=0.2809); s._check_principal_topup(); ck("1 锚点0.2809持仓0.280差0.3%→不补",bc.orders==[])
bc=BC(0.250); s=mk(bc,anchor=0.2809); s._check_principal_topup(); ck("2 部分成交0.250(目标0.2809)→冲刺期补0.030",bc.orders==[0.030],str(bc.orders))
bc=BC(0.280); s=mk(bc,anchor=0.2809,equity=420.0); s._check_principal_topup(); ck("3 浮盈使权益涨到420→目标不漂移不补",bc.orders==[])
bc=BC(0.250); s=mk(bc,anchor=0.2809,age=10); s._check_principal_topup(); ck("4 冲刺期10秒<20秒→等",bc.orders==[])
bc=BC(0.250); s=mk(bc,anchor=0.2809,age=60,burst=False); s._check_principal_topup(); ck("5 非冲刺期60秒<90秒→等",bc.orders==[])
bc=BC(0.250); s=mk(bc,anchor=None,burst=False,age=200); s._check_principal_topup(); ck("6 无锚点(重启后)缺口10.7%≥10%→补",len(bc.orders)==1,str(bc.orders))
bc=BC(0.275); s=mk(bc,anchor=None,burst=False,age=200); s._check_principal_topup(); ck("7 无锚点缺口2%<10%→不补(防漂移)",bc.orders==[])
bc=BC(0.250,rl=60); s=mk(bc,anchor=0.2809); s._check_principal_topup(); ck("8 IP限流→跳过",bc.orders==[])
# fit to margin
bc=BC(0.0,avail=300.0); s=mk(bc); q=s._fit_qty_to_margin(0.28,1335.0); ck("9 保证金充足原样放行",q==0.28,str(q))
bc=BC(0.0,avail=40.0); s=mk(bc); q=s._fit_qty_to_margin(0.28,1335.0); ck("10 可用40U×5x×95%→缩到0.142",abs(q-0.142)<1e-9,str(q))
bc=BC(0.0,avail=40.0,rl=30); s=mk(bc); q=s._fit_qty_to_margin(0.28,1335.0); ck("11 限流时预检跳过原样放行(不阻断开仓)",q==0.28)
bc=BC(0.0,avail=0.0); s=mk(bc); q=s._fit_qty_to_margin(0.28,1335.0); ck("12 查不到可用余额原样放行",q==0.28)
print("TOTAL",sum(R),"/",len(R))
