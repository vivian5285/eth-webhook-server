"""Gate历史闭合4h事件回放；诊断而非精确逐分钟回测，不搜索最佳参数。

信号用交易K线，估值/模拟成交用mark_4h收盘，资金费率用真实历史事件；
结算价用结算前闭合mark_4h收盘近似。只在4h收盘检查止损，无法重现盘中触发，
因此收益和回撤不能当作实际可实现结果。输出完整输入，方便离线重复。
"""
import bisect,json,time,sys,collections,datetime
from pathlib import Path
from strategy_engine import klines
from strategy_engine.hedge_cycle import market,engine as e
from strategy_engine.hedge_cycle.config import SYMBOLS,HEDGE_NAME,CONTROL_NAME,PARAMS

def main(out='/tmp/hedge-replay'):
    base=Path(out);base.mkdir(parents=True,exist_ok=True)
    cache=base/'inputs.json'
    if cache.exists():data=json.loads(cache.read_text())
    else:
        data={}
        now=int(time.time()*1000)
        for sym in SYMBOLS:
            four=klines.get_bars(sym,'4h',limit=600);daily=klines.get_bars(sym,'1d',limit=200)
            mark=market.get_json('/candlesticks',{'contract':'mark_'+market.contract(sym),'interval':'4h','limit':650})
            mark={int(r['t'])*1000:float(r['c']) for r in mark if int(r['t'])*1000+14400000<=now}
            assert len(four)>=500 and len(daily)>=100,(sym,len(four),len(daily))
            start=four[0]['t'];fund=market.get_json('/funding_rate',{
                'contract':market.contract(sym),'from':start//1000,'to':now//1000,'limit':1000})
            assert len(fund)<1000,'Funding pagination required'
            normalized=[]
            for r in fund:
                t=int(r['t'])*1000
                if start<t<=four[-1]['t']+14400000:
                    mark_open=t//14400000*14400000-14400000
                    assert mark_open in mark,(sym,'Missing pre-settlement mark',t)
                    normalized.append({'time_ms':t,'rate':float(r['r']),'mark':mark[mark_open]})
            data[sym]={'four':four,'daily':daily,'marks':mark,'funding':sorted(normalized,key=lambda r:r['time_ms']),
                       'maintenance':float(market.metadata(sym)['maintenance_rate'])}
            print('REPLAY_DATA',sym,len(four),len(daily),len(normalized),flush=True)
        cache.write_text(json.dumps(data),encoding='utf-8')
    for item in data.values():item['marks']={int(t):v for t,v in item['marks'].items()}
    common=set.intersection(*(set(b['t'] for b in data[sym]['four']) & set(data[sym]['marks']) for sym in SYMBOLS))
    times=sorted(common)[150:]
    assert len(times)>300
    h=e.new_state(HEDGE_NAME,times[0]+14400000);c=e.new_state(CONTROL_NAME,times[0]+14400000)
    all_events=[];curve=[];tracking_errors=0;maxgross=maxrisk=maxmargin=0.0
    metadata={s:data[s]['maintenance'] for s in SYMBOLS}
    for t in times:
        now=t+14400000;prices={s:data[s]['marks'][t] for s in SYMBOLS};contexts={};fund={}
        for sym in SYMBOLS:
            item=data[sym]
            four=[b for b in item['four'] if b['t']<=t][-150:]
            daily=[b for b in item['daily'] if b['t']+86400000<=now][-150:]
            contexts[sym]=e.signal_context(four,daily,now)
            cursor=min(h['funding_cursor'][sym],c['funding_cursor'][sym])
            fund[sym]=[r for r in item['funding'] if cursor<r['time_ms']<=now]
        h,ev1=e.tick_hedge(h,prices,contexts,now,fund,metadata)
        c,ev2=e.tick_control(c,h,prices,now,fund,metadata)
        assert e.within_caps(h,prices) and e.within_caps(c,prices)
        for state in (h,c):
            assert abs(state['cash']-(1000+state['realized_gross']-state['fees']+state['funding']))<1e-6
        for sym in SYMBOLS:
            n=lambda state:sum((1 if side=='LONG' else -1)*leg['qty'] for side,leg in state['slots'][sym]['legs'].items())
            if abs(n(h)-n(c))>1e-6:tracking_errors+=1
        m=h['last_snapshot'];maxgross=max(maxgross,m['gross_mult']);maxrisk=max(maxrisk,m['stop_risk_frac']);maxmargin=max(maxmargin,m['margin_frac'])
        curve.append({'time_ms':now,'hedge_equity':m['equity'],'control_equity':c['last_snapshot']['equity']})
        all_events+=ev1+ev2
    def report(state):
        m=state['last_snapshot'];cost=m['gross']*(PARAMS['fee_rate']+PARAMS['slippage_bps']/10000)
        ev=[x for x in all_events if x['strategy']==state['name']]
        return {'equity_mtm':m['equity'],'equity_after_exit_estimate':m['equity']-cost,
            'return_after_exit_pct':(m['equity']-cost-1000)/10,'cash':state['cash'],
            'max_drawdown_pct':state['max_dd']*100,'fees':state['fees'],'funding':state['funding'],
            'turnover':state['turnover'],'cycles':sum(x['action']=='CYCLE' for x in ev),
            'close_fills':sum(x['action']=='CLOSE' for x in ev),'paused':state['paused']}
    result={'kind':'4h_close_only_diagnostic_replay_not_intrabar_backtest',
        'start_utc':datetime.datetime.fromtimestamp((times[0]+14400000)/1000,datetime.timezone.utc).isoformat(),
        'end_utc':datetime.datetime.fromtimestamp((times[-1]+14400000)/1000,datetime.timezone.utc).isoformat(),
        'steps':len(times),'config':PARAMS,'hedge':report(h),'net_control':report(c),
        'tracking_mismatch_symbol_ticks':tracking_errors,'max_gross_mult':maxgross,
        'max_stop_risk_pct':maxrisk*100,'max_margin_pct':maxmargin*100,
        'limitations':['4h收盘才检查止损，遗漏盘中路径','历史结算mark用结算前4h闭合mark收盘近似',
            '固定当前费率/维持保证金率','数量用标的单位，不量化合约整数手数','不得据此声称收益已获验证']}
    for filename,obj in (('report.json',result),('events.json',all_events),('curve.json',curve)):
        (base/filename).write_text(json.dumps(obj,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False,indent=2),flush=True)

if __name__=='__main__':main(sys.argv[1] if len(sys.argv)>1 else '/tmp/hedge-replay')
