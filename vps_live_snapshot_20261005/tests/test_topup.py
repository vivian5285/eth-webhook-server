import sys,os,time,types,json,tempfile
sys.path.insert(0,"/home/coinw/coinw-hft-server"); os.chdir(tempfile.mkdtemp())
import importlib.util
spec=importlib.util.spec_from_file_location("psc","/tmp/psc_new.py"); m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
import threading
C=m.PositionSupervisorCoinW
def mk(live_qty, equity=374.0, age=600, stage=0):
    s=C.__new__(C); s.symbol="ZEC"; s._lock=threading.RLock()
    s.pipeline=types.SimpleNamespace(data={"side":"LONG","hard_sl_px":1300.0}, sync_position=lambda **k: calls.append(("sync",k)))
    s.radar=types.SimpleNamespace(get_state=lambda: types.SimpleNamespace(current_sl=0,activated=False,initial_atr=16.0))
    s._pyramid_stage_cache=stage
    pos={"baseSize":live_qty,"createdDate":(time.time()-age)*1000,"direction":"long","id":"1","openPrice":1330}
    state={"q":live_qty}
    def gp(*a,**k):
        p=dict(pos); p["baseSize"]=state["q"]; return p
    s.client=types.SimpleNamespace(ip_rate_limit_remaining=lambda:0,get_position=gp,get_symbol_leverage=lambda x:5.0,
        get_available_balance=lambda:216.0,get_tp_sl_info=lambda *a,**k:[{"stopLossPrice":1300,"triggerStatus":0}],
        set_sl_tp=lambda **k: calls.append(("sl",k)))
    s._live_side=lambda p:"LONG"; s._get_balance=lambda:equity; s._safe_alert=lambda t:calls.append(("alert",t))
    def lad(side,px,qty,tier=None): calls.append(("order",round(qty,4))); state["q"]+=qty; return {"ok":True}
    s._open_with_ladder=lad; s._topup_last_ts=0
    return s
for name,kw,expect in [("shortfall",dict(live_qty=0.205),"order"),("full",dict(live_qty=0.28),None),("too_young",dict(live_qty=0.205,age=30),None),("too_old",dict(live_qty=0.205,age=13*3600),None),("pyramided",dict(live_qty=0.205,stage=1),None)]:
    calls=[]; s=mk(**kw); s._check_principal_topup(1335.0)
    got=[c[0] for c in calls]
    print(name, got, [c[1] for c in calls if c[0]=="order"], "OK" if ((expect in got) if expect else not got) else "FAIL")
# attempts cap: shortfall persists (order doesn't fill)
calls=[]; s=mk(live_qty=0.205)
s._open_with_ladder=lambda *a,**k:(calls.append(("order",0)) or {"ok":False,"error":"x"})
for i in range(4): s._topup_last_ts=0; s._check_principal_topup(1335.0)
print("attempt-cap orders:",sum(1 for c in calls if c[0]=="order"),"(expect 2)")
