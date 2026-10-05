import sys,os,types,tempfile,threading,importlib.util
from unittest.mock import MagicMock
sys.path.insert(0,"/home/coinw/coinw-hft-server"); os.chdir(tempfile.mkdtemp())
spec=importlib.util.spec_from_file_location("pshs","/tmp/psc_hs_new.py"); pm=importlib.util.module_from_spec(spec); spec.loader.exec_module(pm)
C=pm.PositionSupervisorCoinW
def mk(sym, rec_ok=True, set_ok=True):
    s=C.__new__(C); s.symbol=sym; s._lock=threading.RLock(); s.sl_calls=[]
    s.pipeline=MagicMock(); s.pipeline.data={}
    s.radar=MagicMock(); s.client=MagicMock()
    def set_sl_tp(**k): s.sl_calls.append(k); return {"code":0} if set_ok else {"code":1}
    s.client.set_sl_tp=set_sl_tp
    s.client.get_tp_sl_info.return_value=[{"stopType":2,"triggerStatus":0,"stopLossPrice":1315.88}] if rec_ok else []
    s._get_current_price=lambda:1330.8; s._get_risk_klines=lambda m,l:[]; s._fetch_dual_ma_exit_klines=lambda:[]
    return s
sig=lambda sl=1315.88: types.SimpleNamespace(action="LONG",price=1332.41,stop_loss=sl,tp1=0,tp2=0,tp3=0,tier="",atr=16.37)
R=[]
def ck(n,ok,e=""): R.append(bool(ok)); print("PASS" if ok else "FAIL",n,e)
s=mk("ZEC"); r=s._place_defense_orders(sig(),{"position_id":"P1","entry_price":1330.81,"qty":0.27,"total_pieces":27}); ck("1 ZEC硬止损=TV值1315.88(不是综合硬止损1321.22)",s.sl_calls and abs(s.sl_calls[0]["stop_loss_price"]-1315.88)<1e-9,str(s.sl_calls[:1]))
s=mk("ZEC",rec_ok=False); r=s._place_defense_orders(sig(),{"position_id":"P1","entry_price":1330.81,"qty":0.27,"total_pieces":27}); ck("2 ZEC止损交易所未确认→返回False(上层会净场)",r is False,str(r))
s=mk("ZEC"); r=s._place_defense_orders(sig(0.0),{"position_id":"P1","entry_price":1330.81,"qty":0.27,"total_pieces":27}); ck("3 ZEC无有效TV止损→拒绝(返回False)",r is False,str(r))
print("TOTAL",sum(R),"/",len(R))
