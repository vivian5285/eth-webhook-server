import sys,os,time,types,tempfile,threading
sys.path.insert(0,"/home/coinw/coinw-hft-server"); os.chdir(tempfile.mkdtemp())
import importlib.util
spec=importlib.util.spec_from_file_location("psc","/tmp/psc_new.py"); m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
C=m.PositionSupervisorCoinW
def mk(live=0.28, pieces=28, recs=None, side="LONG", px=1335.0, equity=374.5, activated=True, sl=1314.88, rl=0, stage=0):
    s=C.__new__(C); s.symbol="ZEC"; s._lock=threading.RLock(); s.alerts=[]; s.reads=[0]; s.sl_calls=[]
    s.pipeline=types.SimpleNamespace(data={"side":side,"hard_sl_px":1300.0})
    s.radar=types.SimpleNamespace(get_state=lambda: types.SimpleNamespace(current_sl=sl,activated=activated,initial_atr=16.0))
    s._pyramid_stage_cache=stage
    pos={"baseSize":live,"currentPiece":pieces,"createdDate":(time.time()-900)*1000,"direction":side.lower(),"id":"P1"}
    def gp(*a,**k): s.reads[0]+=1; return dict(pos)
    if recs is None: recs=[{"stopLossPrice":1314.88 if side=="LONG" else 1400.0,"closePiece":28,"triggerStatus":0}]
    s.client=types.SimpleNamespace(ip_rate_limit_remaining=lambda:rl,get_position=gp,get_tp_sl_info=lambda *a,**k:recs,
        set_sl_tp=lambda **k: s.sl_calls.append(k))
    s._live_side=lambda p:side; s._get_balance=lambda:equity; s._safe_alert=lambda t:s.alerts.append(t)
    s._integrity_last_ts=0; return s
def run(name,cond,s,px=1335.0):
    s._audit_integrity(px); ok=cond(s); print("PASS" if ok else "FAIL",name,"alerts=%d reads=%d resets=%d"%(len(s.alerts),s.reads[0],len(s.sl_calls))); return ok
R=[]
R.append(run("1 一切正常无告警",lambda s:not s.alerts and not s.sl_calls,mk()))
R.append(run("2 超仓告警(0.40 vs 目标0.28)",lambda s:any("超仓" in a for a in s.alerts),mk(live=0.40,pieces=40,recs=[{"stopLossPrice":1314.88,"closePiece":40,"triggerStatus":0}])))
R.append(run("3 已武装但无止损告警",lambda s:any("无止损" in a for a in s.alerts),mk(recs=[])))
R.append(run("4 多单止损在现价上方=方向错",lambda s:any("方向错误" in a for a in s.alerts),mk(recs=[{"stopLossPrice":1340.0,"closePiece":28,"triggerStatus":0}])))
R.append(run("5 空单止损在现价下方=方向错",lambda s:any("方向错误" in a for a in s.alerts),mk(side="SHORT",recs=[{"stopLossPrice":1300.0,"closePiece":28,"triggerStatus":0}])))
R.append(run("6 覆盖28/30张→自动重推止损",lambda s:len(s.sl_calls)==1 and any("覆盖不足" in a for a in s.alerts),mk(live=0.30,pieces=30,recs=[{"stopLossPrice":1314.88,"closePiece":28,"triggerStatus":0}])))
R.append(run("7 IP限流整轮跳过且零读取",lambda s:s.reads[0]==0 and not s.alerts,mk(live=0.40,rl=60)))
R.append(run("8 已金字塔化不报超仓",lambda s:not any("超仓" in a for a in s.alerts),mk(live=0.40,pieces=40,stage=1,recs=[{"stopLossPrice":1314.88,"closePiece":40,"triggerStatus":0}])))
R.append(run("9 两层止损(含更宽TV硬止损)正常",lambda s:not s.alerts and not s.sl_calls,mk(recs=[{"stopLossPrice":1314.88,"closePiece":28,"triggerStatus":0},{"stopLossPrice":1300.0,"closePiece":27,"triggerStatus":0}])))
# 10 ledger loosened
s=mk(); s._audit_integrity(1335.0); s._integrity_last_ts=0; s.radar=types.SimpleNamespace(get_state=lambda: types.SimpleNamespace(current_sl=1300.0,activated=True,initial_atr=16.0)); s._audit_integrity(1335.0)
ok=any("放松" in a for a in s.alerts); R.append(ok); print("PASS" if ok else "FAIL","10 账本止损被放松告警")
# 11 dedupe + throttle
s=mk(live=0.40,pieces=40,recs=[{"stopLossPrice":1314.88,"closePiece":40,"triggerStatus":0}]); s._audit_integrity(1335.0); s._audit_integrity(1335.0); s._integrity_last_ts=0; s._audit_integrity(1335.0)
ok=len([a for a in s.alerts if "超仓" in a])==1; R.append(ok); print("PASS" if ok else "FAIL","11 同类告警1小时去重",len(s.alerts))
# 12 add-fill verify
s=mk(); a=s._verify_add_fill("加仓",0.08,0.20,0.28); b=s._verify_add_fill("加仓",0.08,0.20,0.21)
ok=a and not b and len(s.alerts)==1; R.append(ok); print("PASS" if ok else "FAIL","12 加仓成交量核对(正常放行/严重偏差告警)",s.alerts)
print("TOTAL",sum(R),"/",len(R))
