import sys,os,time,types,tempfile,threading
from unittest.mock import MagicMock
sys.path.insert(0,"/home/coinw/coinw-hft-server"); os.chdir(tempfile.mkdtemp())
import importlib.util
spec=importlib.util.spec_from_file_location("psc","/tmp/psc_new.py"); m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
m.CLIMAX_VETO_ENABLED=False
C=m.PositionSupervisorCoinW; R=[]
def mk(balance, avail=216.0, px=1335.0):
    s=C.__new__(C); s.symbol="ZEC"; s._lock=threading.RLock(); s.alerts=[]; s.cap=[]
    s.pipeline=MagicMock(); s.pipeline.data={}; s.radar=MagicMock(); s.client=MagicMock()
    s.client.get_symbol_leverage.return_value=5.0; s.client.get_available_balance.return_value=avail
    s._get_current_price=lambda: px; s._get_balance=lambda patient=False: balance
    s._clear_position=lambda *a,**k: True; s._safe_alert=lambda t: s.alerts.append(t)
    def ex(a,p,q,sig): s.cap.append(q); return {"ok":False,"error":"stop_here"}
    s._cooldown_until=0.0; s._reentry_count=0; s._intentional_close=False; s._execute_open=ex; return s
sig=lambda qty=None: types.SimpleNamespace(valid=True,action="LONG",symbol="ZEC",price=1335.0,stop_loss=1300.0,atr=16.0,tp1=0,tp2=0,tp3=0,qty=qty,tier="",leverage=5,error="",side="LONG",raw={})
def ck(n,ok,e=""): R.append(bool(ok)); print("PASS" if ok else "FAIL",n,e)
s=mk(374.5); s._handle_open(sig(0.05)); ck("1 TV数量上限0.05被忽略,下单0.28张",s.cap==[0.28],str(s.cap))
s=mk(374.5); s._handle_open(sig(None)); ck("2 正常下单0.28",s.cap==[0.28],str(s.cap)); ck("2b 锚点已记录",abs(s._open_target_qty-0.28)<1e-9)
s=mk(0.0); r=s._handle_open(sig(None)); ck("3 本金读不到→事前拦截不下单",s.cap==[] and "pretrade_reject" in str(r),str(r))
s=mk(374.5,avail=20.0); r=s._handle_open(sig(None)); ck("4 保证金不足→缩到可开量(<0.28)且>0",len(s.cap)==1 and 0<s.cap[0]<0.28,str(s.cap))
s=mk(374.5,avail=0.5); r=s._handle_open(sig(None)); ck("5 保证金极小→可开量≥1张? 否则拒绝",(s.cap==[] and "margin" in str(r)) or (len(s.cap)==1 and s.cap[0]>=0.01),str(s.cap)+str(r))
print("TOTAL",sum(R),"/",len(R))
