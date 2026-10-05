import sys,os,types,tempfile,threading,importlib.util
sys.path.insert(0,"/home/coinw/coinw-hft-server"); os.chdir(tempfile.mkdtemp())
spec=importlib.util.spec_from_file_location("psa","/tmp/psc_s1n.py"); pm=importlib.util.module_from_spec(spec); spec.loader.exec_module(pm)
pm.time.sleep=lambda x:None
C=pm.PositionSupervisorCoinW; R=[]
def ck(n,ok,e=""): R.append(bool(ok)); print("PASS" if ok else "FAIL",n,e)
def mk(base,side="SHORT",fill=True,equity=374.0):
    s=C.__new__(C); s.symbol="SNDK"; s.orders=[]; s.state={"q":base}
    def gp(*a,**k):
        if s.state["q"]<=0: return None
        return {"baseSize":s.state["q"],"direction":side.lower(),"id":"P1"}
    def place(side,quantity,price,instrument,reduce_only=False):
        s.orders.append((side,quantity,price))
        if fill: s.state["q"]+=quantity
        return {"code":0,"data":{"value":"O1"}}
    s.client=types.SimpleNamespace(ip_rate_limit_remaining=lambda:0,get_position=gp,get_futures_usdt_balance=lambda:equity,
        place_limit_order=place,get_open_orders=lambda *a,**k:[],cancel_order=lambda *a,**k:None)
    s._live_side=lambda p:p["direction"].upper(); s._adverse_gap_pct=lambda a,p:0.0; s._safe_alert=lambda t:None
    return s
s=mk(0.208); r=s._open_sndk_single_limit("SHORT",1716.0,0.05,adding=True); ck("1 加仓模式:已有同向持仓→下单成功",r.get("ok") and len(s.orders)==1,str((r,s.orders)))
s=mk(0.208); r=s._open_sndk_single_limit("SHORT",1716.0,0.05,adding=False); ck("2 非加仓(开仓)有持仓仍报未确认净场(原保护不变)",not r["ok"] and "净场" in r["error"] and s.orders==[],str(r))
s=mk(0.0); r=s._open_sndk_single_limit("SHORT",1716.0,0.05,adding=True); ck("3 加仓模式但没有持仓→拒绝",not r["ok"] and "同向持仓" in r["error"])
s=mk(0.208,side="LONG"); r=s._open_sndk_single_limit("SHORT",1716.0,0.05,adding=True); ck("4 加仓方向与持仓相反→拒绝",not r["ok"] and s.orders==[])
s=mk(0.208,fill=False); r=s._open_sndk_single_limit("SHORT",1716.0,0.05,adding=True); ck("5 加仓限价没成交→返回失败(持仓未增加)",not r["ok"] and "未成交" in r["error"],str(r))
s=mk(0.0); r=s._open_sndk_single_limit("SHORT",1716.0,0.20,adding=False); ck("6 正常开仓路径不受影响",r.get("ok") and len(s.orders)==1,str(r))
s=mk(0.35,equity=374.0); r=s._open_sndk_single_limit("SHORT",1716.0,0.30,adding=True); ck("7 加仓受1.98倍封顶(374*1.98/1716=0.4315,已有0.35→最多加0.081)",(not s.orders) or s.orders[0][1]<=0.082,str(s.orders))
print("TOTAL",sum(R),"/",len(R))
