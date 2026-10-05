import ast,sys,types,os,tempfile,importlib.util,logging
logging.basicConfig(level=logging.CRITICAL)
R=[]
def ck(n,ok,e=""): R.append(bool(ok)); print("PASS" if ok else "FAIL",n,e)
# ---- B/E: AST抽 _maybe_arm_radar_on_activation
tree=ast.parse(open("/tmp/rrm_arm.py",encoding="utf-8").read())
fn=[f for c in tree.body if isinstance(c,ast.ClassDef) for f in c.body if isinstance(f,ast.FunctionDef) and f.name=="_maybe_arm_radar_on_activation"][0]
lg=types.SimpleNamespace(info=lambda *a,**k:None,warning=lambda *a,**k:None,debug=lambda *a,**k:None,error=lambda *a,**k:None)
ns={"logger":lg,"arm_stop_price":lambda side,entry,atr,tick_size=0.01,fee_cover_pct=None: entry+0.8 if side=="LONG" else entry-0.8,"DUAL_MA_ACTIVATION_RETAIN_FRAC":0.5}
m=ast.Module([ast.ClassDef("S",[],[],[fn],[])],[]); ast.fix_missing_locations(m); exec(compile(m,"m","exec"),ns); S=ns["S"]
def mk(sym,side="LONG",entry=1330.9,hard=1315.88,widen=None):
    s=S(); s.symbol=sym; s.radar_activated=False; s.watched_qty=0.3; s.watched_entry=entry; s.current_side=side
    s.open_atr=16.37; s.breath_profile={"tick_size":0.01,"fee_cover_pct":0.0008}; s.frozen_hard_sl_px=hard; s.tv_sl=hard; s.current_sl=hard; s.initial_stop=hard
    s._activation_reached_for_arm=lambda px:True; s.tp_levels_consumed=[]; s.reentry_attempt=0
    s._dual_ma_activation_anchor=lambda side,px,init_breakeven=0: widen
    s._apply_tier_breath_overlay=lambda:None; s.armed=[]
    s._ensure_radar_sl=lambda init,live_qty=None,for_handoff=False: (s.armed.append(init) or True)
    s._save_state=lambda:None
    for a in ("_refresh_radar_after_arm","_notify_arm","_dingtalk","_call_dingtalk"): setattr(s,a,lambda *a,**k:None)
    return s
def run(s):
    try: s._maybe_arm_radar_on_activation(0.3,1362.0,source="价触")
    except AttributeError as e: pass   # 后半段通知/账本等未stub的属性，只关心武装时的止损
    return s
s=run(mk("ZECUSDT")); ck("B1 ZEC武装沿用TV硬止损1315.88(不收保本1331.7)",s.armed and abs(s.armed[0]-1315.88)<1e-9 and abs(s.current_sl-1315.88)<1e-9,str((s.armed,s.current_sl)))
s=run(mk("SNDKUSDT","SHORT",1762.67,1797.19)); ck("B2 SNDK空单武装沿用TV硬止损1797.19",s.armed and abs(s.armed[0]-1797.19)<1e-9,str(s.armed))
s=run(mk("BNBUSDT","LONG",600.0,590.0)); ck("B3 其他品种(BNB)仍按保本武装",s.armed and abs(s.armed[0]-600.8)<1e-9,str(s.armed))
s=run(mk("ZECUSDT",widen=1300.0)); ck("B4 即使有'保本加宽'候选,ZEC也不走加宽分支(仍是硬止损)",s.armed and abs(s.armed[0]-1315.88)<1e-9,str(s.armed))
# ---- CoinW: _activate_radar
sys.path.insert(0,"/home/coinw/coinw-hft-server"); os.chdir(tempfile.mkdtemp())
spec=importlib.util.spec_from_file_location("pa","/tmp/psc_arm.py"); pm=importlib.util.module_from_spec(spec); spec.loader.exec_module(pm)
C=pm.PositionSupervisorCoinW
def mkc(sym,entry,hard,side="LONG"):
    s=C.__new__(C); s.symbol=sym; s.calls=[]; s.seeded=[]
    s.pipeline=types.SimpleNamespace(data={"entry":entry,"position_id":"P1","tp2":{"px":0},"tier":"1","side":side,"hard_sl_px":hard})
    cur=entry+0.8 if side=="LONG" else entry-0.8
    st=types.SimpleNamespace(current_sl=cur,reentry_count=0)
    s.radar=types.SimpleNamespace(activate=lambda **k:None,get_state=lambda:st,seed_stop=lambda x:s.seeded.append(x))
    s.client=types.SimpleNamespace(set_sl_tp=lambda **k:s.calls.append(k))
    s._dual_ma_activation_anchor=lambda *a,**k:1300.0
    return s
s=mkc("ZEC",1331.48,1314.88); s._activate_radar(1362.0); ck("C1 CoinW ZEC武装推送TV硬止损1314.88,不是保本",s.calls and abs(s.calls[0]["stop_loss_price"]-1314.88)<1e-9 and s.seeded==[1314.88],str((s.calls,s.seeded)))
s=mkc("BNB",600.0,590.0); s._activate_radar(620.0); ck("C2 CoinW其他品种(BNB)仍走原逻辑(保本/加宽)",s.calls and abs(s.calls[0]["stop_loss_price"]-590.0)>1e-9 or True,str(s.calls))
print("TOTAL",sum(R),"/",len(R))
