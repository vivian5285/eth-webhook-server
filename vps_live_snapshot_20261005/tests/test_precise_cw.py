import sys,os,time,types,tempfile,threading,json
sys.path.insert(0,"/home/coinw/coinw-hft-server"); os.chdir(tempfile.mkdtemp())
import importlib.util
spec=importlib.util.spec_from_file_location("psc","/tmp/psc_new.py"); m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
C=m.PositionSupervisorCoinW; R=[]
def ck(name,ok,extra=""): R.append(bool(ok)); print("PASS" if ok else "FAIL",name,extra)
def base(sym="ZEC"):
    s=C.__new__(C); s.symbol=sym; s._lock=threading.RLock(); s.alerts=[]; s._safe_alert=lambda t:s.alerts.append(t); return s
# 1 round to piece
s=base(); ck("1a 0.2809→28张=0.28",s._round_to_piece(0.2809,374.5,1335)==0.28)
ck("1b 0.2851不超本金102%→回落28张",s._round_to_piece(0.2851,374.5,1335)==0.28)
ck("1c 0.2870四舍五入29张但名义387>382→28张",s._round_to_piece(0.2870,374.5,1335)==0.28)
ck("1d 小账户至少1张",s._round_to_piece(0.001,5,1335)==0.01)
s2=base("SNDK"); ck("1e SNDK单位0.001",s2._round_to_piece(0.2086,374.5,1795)==0.209 or s2._round_to_piece(0.2086,374.5,1795)==0.208, str(s2._round_to_piece(0.2086,374.5,1795)))
# 2 balance
def mkb(seq, rl=0):
    s=base(); it=iter(seq); s.client=types.SimpleNamespace(get_principal_equity=lambda: next(it), ip_rate_limit_remaining=lambda: rl, get_available_balance=lambda: 99.0); return s
m.time.sleep=lambda x:None
s=mkb([0,0,374.0]); v=s._get_balance(patient=True); ck("2a 前两次读失败第三次成功(patient)",v==374.0,str(v))
s=mkb([0]); v=s._get_balance(patient=False); ck("2b 非patient只读一次,回退到落盘的最近真实值374",v==374.0,str(v))
os.remove("data/last_equity.json"); s=mkb([0,0,0]); v=s._get_balance(patient=True); ck("2c 全失败且无缓存→ZEC返回0(宁可不开)",v==0.0)
s=base("XAU"); s.client=types.SimpleNamespace(get_principal_equity=lambda:0.0,ip_rate_limit_remaining=lambda:0,get_available_balance=lambda:99.0); v=s._get_balance(); ck("2d 非本金等值品种可退回可用余额",v==99.0)
json.dump({"equity":300.0,"ts":time.time()-30*3600},open("data/last_equity.json","w")); s=mkb([0]); v=s._get_balance(); ck("2e 缓存超24小时不用",v==0.0,str(v))
# 3 anchored topup + burst
def mkt(live, equity, anchor=None, age=25, burst=True, px=1335.0):
    s=base(); s.pipeline=types.SimpleNamespace(data={"side":"LONG","hard_sl_px":1300.0}, sync_position=lambda **k:None)
    s.radar=types.SimpleNamespace(get_state=lambda: types.SimpleNamespace(current_sl=0,activated=False,initial_atr=16.0))
    s._pyramid_stage_cache=0; st={"q":live}; s.orders=[]
    def gp(*a,**k): return {"baseSize":st["q"],"currentPiece":int(round(st["q"]/0.01)),"createdDate":(time.time()-age)*1000,"direction":"long","id":"1","openPrice":1330}
    s.client=types.SimpleNamespace(ip_rate_limit_remaining=lambda:0,get_position=gp,get_symbol_leverage=lambda x:5.0,get_available_balance=lambda:216.0,
        get_tp_sl_info=lambda *a,**k:[{"stopLossPrice":1300,"triggerStatus":0,"closePiece":28}],set_sl_tp=lambda **k:None)
    s._live_side=lambda p:"LONG"; s._get_balance=lambda patient=False:equity
    def lad(side,px,qty,tier=None): s.orders.append(round(qty,4)); st["q"]+=qty; return {"ok":True}
    s._open_with_ladder=lad; s._topup_last_ts=0
    if anchor: s._open_target_qty=anchor; s._open_target_side="LONG"; s._open_target_ts=time.time()-age
    if burst: s._topup_burst_until=time.time()+200
    return s
s=mkt(0.27,374.5,anchor=0.28); s._check_principal_topup(px:=1335.0) if False else s._check_principal_topup(1335.0); ck("3a 锚定0.28 持仓0.27 → 冲刺期20s后补1张0.01",s.orders==[0.01],str(s.orders))
s=mkt(0.28,420.0,anchor=0.28); s._check_principal_topup(1335.0); ck("3b 浮盈使权益涨到420,目标不漂移→不补",s.orders==[],str(s.orders))
s=mkt(0.28,374.5,anchor=None); s._check_principal_topup(1335.0); ck("3c 无锚点满仓不补",s.orders==[])
s=mkt(0.27,374.5,anchor=None); s._check_principal_topup(1335.0); ck("3d 无锚点(重启后)缺口仅3.6%<10%→不补(防漂移)",s.orders==[],str(s.orders))
s=mkt(0.27,374.5,anchor=0.28,age=10); s._check_principal_topup(1335.0); ck("3e 冲刺期但仅10秒<20秒settle→等",s.orders==[])
s=mkt(0.27,374.5,anchor=0.28,burst=False,age=60); s._check_principal_topup(1335.0); ck("3f 非冲刺期60秒<90秒settle→等",s.orders==[])
# 4 oversize audit uses anchor
s=mkt(0.28,200.0,anchor=0.28); s.radar=types.SimpleNamespace(get_state=lambda: types.SimpleNamespace(current_sl=1314.0,activated=True,initial_atr=16.0)); s._integrity_last_ts=0
s._audit_integrity(1335.0); ck("4 亏损使权益缩到200,以开仓目标量为准→不报假超仓",not any("超仓" in a for a in s.alerts),str(s.alerts))
print("TOTAL",sum(R),"/",len(R))
