"""双腿模拟的纯状态转换/会计，账户级统一限额，不复用单腿runner。

Claude入口: research/HEDGE_CYCLE_HANDOFF.md。state.cash是已兑现现金，不是总权益；
equity=cash+两腿浮盈亏。开仓收费立刻扣现金，平仓仅再扣退出费，资金费率逐事件扣/收。
所有循环先在深拷贝中规划，结果和成交事件由store同一SQLite事务提交。
门槛使用成交后权益和gross(多+空)，绝不用net抵消保证金。风控优先于永远双持。
"""
from __future__ import annotations
import copy
import math
from strategy_engine import indicators
from .config import PARAMS, SYMBOLS, HEDGE_NAME, CONTROL_NAME

EPS = 1e-10


def new_state(name, now_ms, p=None):
    return {'schema':1,'name':name,'config':copy.deepcopy(p or PARAMS),'created_ms':now_ms,
        'cash':1000.0,'peak_equity':1000.0,'max_dd':0.0,'fees':0.0,'funding':0.0,
        'realized_gross':0.0,'turnover':0.0,'paused':False,'reason':'等待行情',
        'seq':0,'slots':{s:{'direction':0,'cycle':0,'last_bar':0,'anchor':0.0,
            'unit_notional':0.0,'range_reduced':False,'blocked_bar':0,'legs':{}}
            for s in SYMBOLS}, 'funding_cursor':{s:now_ms for s in SYMBOLS},
        'symbol_funding':{s:0.0 for s in SYMBOLS},'last_tick_ms':0,'last_snapshot':{}}


def legs(state):
    for symbol,slot in state['slots'].items():
        for side,leg in list(slot['legs'].items()):
            if leg['qty']>EPS:yield symbol,side,leg


def metrics(state, prices):
    p=state['config'];unreal=gross=risk=long=short=maintenance=0.0
    by_symbol={}
    for symbol,side,leg in legs(state):
        mark=float(prices[symbol]);qty=leg['qty'];d=1 if side=='LONG' else -1
        unreal+=d*(mark-leg['entry'])*qty;notional=mark*qty;gross+=notional
        # 从当前权益到止损成交的损失，包含退出滑点/手续费；跳空仍可越过预算。
        stop_fill=_fill_price(state,leg['stop'],side,False)
        risk+=max(0.0,(d*(mark-stop_fill)+stop_fill*p['fee_rate'])*qty)
        if side=='LONG':long+=notional
        else:short+=notional
        maintenance+=notional*float(leg.get('maintenance_rate',0.01))
        by_symbol[symbol]=by_symbol.get(symbol,0.0)+d*notional
    equity=state['cash']+unreal
    denom=max(equity,EPS)
    return {'equity':equity,'unrealized':unreal,'gross':gross,'long':long,'short':short,
        'net':long-short,'net_by_symbol':by_symbol,'stop_risk':risk,
        'gross_mult':gross/denom,'margin_frac':gross/p['margin_leverage']/denom,
        'long_margin_frac':long/p['margin_leverage']/denom,
        'short_margin_frac':short/p['margin_leverage']/denom,
        'stop_risk_frac':risk/denom,'maintenance':maintenance,
        'estimated_exit_fees':gross*p['fee_rate']}


def within_caps(state,prices):
    m=metrics(state,prices);p=state['config']
    if m['equity']<=0:return False
    return (m['gross_mult']<=p['max_gross_mult']+1e-8
        and m['margin_frac']<=p['max_margin_frac']+1e-8
        and max(m['long_margin_frac'],m['short_margin_frac'])<=p['max_direction_margin_frac']+1e-8
        and m['stop_risk_frac']<=p['max_stop_risk_frac']+1e-8)


def _event(state,events,now_ms,symbol,side,action,**fields):
    state['seq']+=1
    item={'event_id':state['seq'],'strategy':state['name'],'time_ms':now_ms,
          'symbol':symbol,'side':side,'action':action,**fields}
    events.append(item)
    return item


def _fill_price(state,mark,side,opening):
    # 多买高卖低，空卖低买高。滑点进入成交价，不再额外扣一次。
    d=1 if side=='LONG' else -1
    return mark*(1+d*(1 if opening else -1)*state['config']['slippage_bps']/10000)


def close_leg(state,symbol,side,qty,mark,now_ms,reason,events):
    slot=state['slots'][symbol];leg=slot['legs'].get(side)
    if not leg:return
    q=min(qty,leg['qty']);frac=q/leg['qty'];px=_fill_price(state,mark,side,False)
    d=1 if side=='LONG' else -1;gross=d*(px-leg['entry'])*q
    fee=px*q*state['config']['fee_rate'];entry_fee=leg['entry_fees_remaining']*frac
    funding=leg['funding_remaining']*frac
    state['cash']+=gross-fee;state['realized_gross']+=gross
    state['fees']+=fee;state['turnover']+=px*q
    _event(state,events,now_ms,symbol,side,'CLOSE',qty=q,price=px,entry=leg['entry'],
        opened_ms=leg['opened_ms'],role=leg['role'],reason=reason,leg_id=leg['id'],
        gross_pnl_usd=gross,fees_usd=entry_fee+fee,exit_fee=fee,
        allocated_entry_fee=entry_fee,funding_pnl_usd=funding,
        net_pnl_usd=gross-entry_fee-fee+funding,atr0=leg['atr0'])
    leg['qty']-=q;leg['entry_fees_remaining']-=entry_fee;leg['funding_remaining']-=funding
    if leg['qty']<=EPS:del slot['legs'][side]


def _open_raw(state,symbol,side,qty,mark,stop,atr,now_ms,role,maintenance_rate,events):
    px=_fill_price(state,mark,side,True);fee=px*qty*state['config']['fee_rate']
    slot=state['slots'][symbol];old=slot['legs'].get(side)
    if old:
        total=old['qty']+qty;old['entry']=(old['entry']*old['qty']+px*qty)/total
        old['qty']=total;old['entry_fees_remaining']+=fee
        old['stop']=max(old['stop'],stop) if side=='LONG' else min(old['stop'],stop)
        old['role']=role
    else:
        slot['legs'][side]={'id':state['seq']+1,'qty':qty,'entry':px,'stop':stop,
            'opened_ms':now_ms,'atr0':atr,'role':role,'entry_fees_remaining':fee,
            'funding_remaining':0.0,'maintenance_rate':maintenance_rate}
    state['cash']-=fee;state['fees']+=fee;state['turnover']+=px*qty
    _event(state,events,now_ms,symbol,side,'OPEN',qty=qty,price=px,fee=fee,
           role=role,stop=stop,leg_id=slot['legs'][side]['id'])


def safe_open(state,symbol,side,qty,prices,stop,atr,now_ms,role,maintenance_rate,events):
    """按成交后权益二分缩量；逆势仓有硬止损，无补亏/摊平入口。"""
    if state['paused'] or qty<=EPS or stop<=0:return 0.0
    mark=prices[symbol];d=1 if side=='LONG' else -1
    if d*(mark-stop)<=0:return 0.0
    original_qty=qty
    if role=='hedge':
        main_side='LONG' if state['slots'][symbol]['direction']==1 else 'SHORT'
        main=state['slots'][symbol]['legs'].get(main_side)
        if not main or side==main_side or state['slots'][symbol]['legs'].get(side):return 0.0
        qty=min(qty,main['qty']*state['config']['hedge_ratio_limit'])
    budget=state['config']['add_risk_frac']*metrics(state,prices)['equity']
    stop_fill=_fill_price(state,stop,side,False)
    risk_per_unit=abs(_fill_price(state,mark,side,True)-stop_fill)+stop_fill*state['config']['fee_rate']
    qty=min(qty,budget/max(risk_per_unit,EPS))
    lo,hi=0.0,max(0.0,qty)
    for _ in range(36):
        mid=(lo+hi)/2;candidate=copy.deepcopy(state)
        _open_raw(candidate,symbol,side,mid,mark,stop,atr,now_ms,role,maintenance_rate,[])
        if within_caps(candidate,prices):lo=mid
        else:hi=mid
    # 跳过碎单，避免风险边界上重复极小数量刷手续费。
    if lo*mark<1.0:
        state['reason']='risk_cap_or_dust';return 0.0
    _open_raw(state,symbol,side,lo,mark,stop,atr,now_ms,role,maintenance_rate,events)
    if lo<original_qty*(1-1e-6):state['reason']='risk_clamped'
    return lo


def settle_funding(state,funding_events,now_ms,events):
    """Gate已结算事件逐条去重。正费率多付空收；各合约周期由实际事件决定。"""
    for symbol in SYMBOLS:
        cursor=state['funding_cursor'][symbol]
        for item in sorted(funding_events.get(symbol,[]),key=lambda x:x['time_ms']):
            t=int(item['time_ms'])
            if not cursor<t<=now_ms:continue
            rate=float(item['rate']);mark=float(item['mark'])
            if not math.isfinite(rate) or not math.isfinite(mark) or mark<=0:
                raise ValueError('Invalid funding event')
            for side,leg in state['slots'][symbol]['legs'].items():
                if leg['opened_ms']>=t:continue
                cash=(-1 if side=='LONG' else 1)*leg['qty']*mark*rate
                state['cash']+=cash;state['funding']+=cash
                state['symbol_funding'][symbol]+=cash;leg['funding_remaining']+=cash
                _event(state,events,t,symbol,side,'FUNDING',cashflow=cash,rate=rate,
                    settlement_mark=mark,leg_id=leg['id'])
            cursor=t
        state['funding_cursor'][symbol]=cursor


def signal_context(bars4,daily,now_ms,p=None):
    """闭合4h和日线严格AND。日线排序与斜率都同向；禁止OR放行矛盾方向。"""
    p=p or PARAMS
    bars4=[b for b in bars4 if int(b['t'])+14400000<=now_ms]
    daily=[b for b in daily if int(b['t'])+86400000<=now_ms]
    need=p['atr_percentile_lookback']+p['atr_period']+4
    if len(bars4)<need or len(daily)<p['ema_slow']+p['slope_lookback']+2:
        return None
    # 新仓不能使用一根已过期超过4h的信号/日线超过2日的缓存。
    if now_ms-(bars4[-1]['t']+14400000)>14400000 or now_ms-(daily[-1]['t']+86400000)>172800000:
        return None
    for seq in (bars4,daily):
        if any(not all(math.isfinite(float(b[k])) and float(b[k])>0 for k in ('o','h','l','c')) for b in seq):return None
        if any(seq[i]['t']<=seq[i-1]['t'] for i in range(1,len(seq))):return None
    c=indicators.closes(bars4);f=indicators.ema(c,p['ema_fast']);s=indicators.ema(c,p['ema_slow'])
    dc=indicators.closes(daily);df=indicators.ema(dc,p['ema_fast']);ds=indicators.ema(dc,p['ema_slow'])
    atrs=indicators.atr_series(bars4,p['atr_period']);atr=atrs[-1]
    if not math.isfinite(atr) or atr<=0:return None
    slope=p['slope_lookback'];direction=0
    for d in (1,-1):
        same4=all(d*(f[-1-k]-s[-1-k])>0 and d*(f[-1-k]-f[-1-k-slope])>0
                  for k in range(p['trend_confirm_bars']))
        sameD=d*(df[-1]-ds[-1])>0 and d*(df[-1]-df[-1-slope])>0
        if same4 and sameD:direction=d
    history=sorted(atrs[-p['atr_percentile_lookback']-1:-1])
    quantile=history[int((len(history)-1)*p['low_atr_quantile'])]
    dead_reasons=[]
    if abs(f[-1]-s[-1])/atr<p['ema_gap_atr']:dead_reasons.append('ema_glue')
    if atr<quantile:dead_reasons.append('low_atr')
    if not direction:dead_reasons.append('timeframe_or_slope_conflict')
    dead=bool(dead_reasons)
    prior=bars4[-p['breakout_period']-1:-1];last=bars4[-1]
    breakout_up=last['c']>max(b['h'] for b in prior)
    breakout_down=last['c']<min(b['l'] for b in prior)
    meanbody=sum(abs(b['c']-b['o']) for b in bars4[-21:-1])/20
    expansion=atr>sum(atrs[-21:-1])/20 and abs(last['c']-last['o'])>1.25*meanbody
    return {'direction':direction,'dead':dead,'atr':atr,'bar_time':int(last['t']),
        'fast4':f[-1],'slow4':s[-1],'fastD':df[-1],'slowD':ds[-1],
        'momentum': (breakout_up if direction==1 else breakout_down) or expansion,
        'dead_reasons':dead_reasons,'atr_low_threshold':quantile}


def _trim_ratio(state,symbol,mark,now,events):
    slot=state['slots'][symbol];d=slot['direction']
    if not d:return
    main_side='LONG' if d==1 else 'SHORT';hedge_side='SHORT' if d==1 else 'LONG'
    main=slot['legs'].get(main_side);hedge=slot['legs'].get(hedge_side)
    if hedge:
        limit=(main['qty'] if main else 0)*state['config']['hedge_ratio_limit']
        if hedge['qty']>limit+EPS:close_leg(state,symbol,hedge_side,hedge['qty']-limit,mark,now,'主仓减少，对冲限额同步缩减',events)


def enforce_caps(state,prices,now,events):
    """价格变化也会使gross/权益超限；所有腿同比例减仓，不靠净仓抵消。"""
    for _ in range(12):
        if within_caps(state,prices):return
        m=metrics(state,prices);p=state['config']
        ratios=[p['max_gross_mult']/max(m['gross_mult'],EPS),
            p['max_margin_frac']/max(m['margin_frac'],EPS),
            p['max_direction_margin_frac']/max(m['long_margin_frac'],m['short_margin_frac'],EPS),
            p['max_stop_risk_frac']/max(m['stop_risk_frac'],EPS)]
        keep=max(0.0,min(0.95,min(ratios)*0.995))
        for symbol,side,leg in list(legs(state)):
            close_leg(state,symbol,side,leg['qty']*(1-keep),prices[symbol],now,'总敞口/保证金/止损风险上限减仓',events)
        state['reason']='risk_reduced'
    # 极端负权益下不能靠比例缩量收敛。
    for symbol,side,leg in list(legs(state)):
        close_leg(state,symbol,side,leg['qty'],prices[symbol],now,'风险上限无法满足，清仓',events)
    state['paused']=True;state['reason']='risk_caps_failed'


def _drawdown_guard(state,prices,now,events):
    m=metrics(state,prices);eq=m['equity'];state['peak_equity']=max(state['peak_equity'],eq)
    dd=max(0,1-eq/max(state['peak_equity'],EPS));state['max_dd']=max(state['max_dd'],dd)
    if dd>=state['config']['max_drawdown'] or eq<=m['maintenance']*1.2:
        for symbol,side,leg in list(legs(state)):
            close_leg(state,symbol,side,leg['qty'],prices[symbol],now,'账户15%回撤/维持保证金熔断',events)
        state['paused']=True;state['reason']='drawdown_halt'


def tick_hedge(original,prices,contexts,now_ms,funding_events=None,maintenance=None):
    state=copy.deepcopy(original);events=[];p=state['config'];maintenance=maintenance or {}
    if not all(s in prices and math.isfinite(prices[s]) and prices[s]>0 for s in SYMBOLS):
        raise ValueError('All three fresh marks required; never value missing legs as zero')
    settle_funding(state,funding_events or {},now_ms,events)
    _drawdown_guard(state,prices,now_ms,events)
    if not state['paused']:
        for symbol in SYMBOLS:
            slot=state['slots'][symbol];mark=prices[symbol];ctx=contexts.get(symbol)
            # 先按原止损检验当前mark，再更新追踪止损，不用历史整根K线追溯扫损。
            for side,leg in list(slot['legs'].items()):
                d=1 if side=='LONG' else -1
                if d*(mark-leg['stop'])<=0:
                    was_main=leg['role']=='main'
                    close_leg(state,symbol,side,leg['qty'],mark,now_ms,'顺势止损' if was_main else '对冲固定止损',events)
                    if was_main:
                        for other,other_leg in list(slot['legs'].items()):
                            close_leg(state,symbol,other,other_leg['qty'],mark,now_ms,'主仓止损，同时退出剩余对冲',events)
                        slot['direction']=0;slot['blocked_bar']=ctx['bar_time'] if ctx else slot['last_bar']
            _trim_ratio(state,symbol,mark,now_ms,events)
            if not ctx:continue  # 缺高周期数据仍管理旧仓止损，禁止新仓/加仓
            # 主仓2ATR追踪，只收紧；对冲固定止损不加宽。
            for side,leg in slot['legs'].items():
                if leg['role']=='main':
                    d=1 if side=='LONG' else -1;candidate=mark-d*p['main_stop_atr']*ctx['atr']
                    leg['stop']=max(leg['stop'],candidate) if d==1 else min(leg['stop'],candidate)
            if ctx['bar_time']<=slot['last_bar']:continue
            slot['last_bar']=ctx['bar_time']
            if ctx['dead'] or not ctx['direction']:
                # 仅减半一次，不能每分钟重复减到零；不加新对冲或主仓。
                if not slot['range_reduced']:
                    for side,leg in list(slot['legs'].items()):
                        close_leg(state,symbol,side,leg['qty']*0.5,mark,now_ms,'双周期矛盾/死水，底仓减半',events)
                    slot['range_reduced']=True
                continue
            slot['range_reduced']=False;d=ctx['direction'];atr=ctx['atr']
            main_side='LONG' if d==1 else 'SHORT';other_side='SHORT' if d==1 else 'LONG'
            if slot['direction'] and slot['direction']!=d:
                # 确认反转不要求两个周期在同一根发生交叉；看当前排序+斜率。
                old_main='LONG' if slot['direction']==1 else 'SHORT'
                old=slot['legs'].get(old_main)
                if old:close_leg(state,symbol,old_main,old['qty'],mark,now_ms,'双周期反转，退出旧主仓',events)
                retained=slot['legs'].get(main_side)
                if retained:retained['role']='main'
                slot['direction']=d;slot['cycle']=0;slot['anchor']=mark
                slot['unit_notional']=0.0
            if slot['blocked_bar']==ctx['bar_time']:continue
            main=slot['legs'].get(main_side)
            if not main or slot['unit_notional']<=0:
                slot['direction']=d;eq=metrics(state,prices)['equity']
                target=min(eq*p['max_symbol_main_mult'],eq*p['initial_leg_risk_frac']/max(2*atr/mark,EPS))
                current=main['qty'] if main else 0
                opened=safe_open(state,symbol,main_side,max(0,target/mark-current),prices,
                    mark-d*2*atr,atr,now_ms,'main',maintenance.get(symbol,0.01),events)
                main=slot['legs'].get(main_side)
                if main:
                    slot['unit_notional']=main['qty']*mark;slot['anchor']=mark;slot['cycle']=0
                    if not slot['legs'].get(other_side):safe_open(state,symbol,other_side,main['qty']*p['hedge_ratio'],prices,
                        mark+d*p['hedge_stop_atr']*atr,atr,now_ms,'hedge',maintenance.get(symbol,0.01),events)
                continue
            # 相距>=1ATR+新高/动能确认+两腿合计扣预计费用盈利，才允许循环。
            if slot['cycle']>=len(p['add_decay']) or d*(mark-slot['anchor'])<p['cycle_distance_atr']*atr or not ctx['momentum']:continue
            floating=sum((1 if side=='LONG' else -1)*(mark-leg['entry'])*leg['qty']
                -leg['entry_fees_remaining']+leg['funding_remaining'] for side,leg in slot['legs'].items())
            proposed_cost=2*main['qty']*mark*(p['fee_rate']+p['slippage_bps']/10000)
            if floating<p['min_cycle_cost_multiple']*proposed_cost or d*(mark-main['entry'])<=0:continue
            # 拷贝里的部分TP、递减重买、换对冲一起落库。没有释放资金不追加本金。
            close_qty=main['qty']*p['take_profit_ratio'];cash_before=state['cash']
            close_leg(state,symbol,main_side,close_qty,mark,now_ms,'循环部分止盈25%',events)
            realized=max(0,state['cash']-cash_before)
            k=slot['cycle'];decay=p['add_decay'][k]
            released_notional=close_qty*mark
            add_notional=min(slot['unit_notional']*decay,
                             released_notional+realized*p['margin_leverage']*0.5)
            main=slot['legs'].get(main_side)
            if main:
                # 盈利循环后移动硬止损到成本+往返费用缓冲，若成本在价外则不非法越价。
                breakeven=main['entry']*(1+d*2*(p['fee_rate']+p['slippage_bps']/10000))
                stop=max(main['stop'],breakeven) if d==1 else min(main['stop'],breakeven)
                if d*(mark-stop)>0:main['stop']=stop
                safe_open(state,symbol,main_side,add_notional/mark,prices,main['stop'],atr,
                    now_ms,'main',maintenance.get(symbol,0.01),events)
            hedge=slot['legs'].get(other_side)
            if hedge:close_leg(state,symbol,other_side,hedge['qty'],mark,now_ms,'循环换对冲，先平旧仓，不摊平亏损',events)
            main=slot['legs'].get(main_side)
            if main:safe_open(state,symbol,other_side,main['qty']*p['hedge_ratio']/(1+0.5*k),prices,
                mark+d*p['hedge_stop_atr']*atr,atr,now_ms,'hedge',maintenance.get(symbol,0.01),events)
            slot['cycle']+=1;slot['anchor']=mark
            _event(state,events,now_ms,symbol,main_side,'CYCLE',cycle=slot['cycle'],reason='部分止盈+递减再入场+换对冲')
        enforce_caps(state,prices,now_ms,events)
        _drawdown_guard(state,prices,now_ms,events)
    state['last_tick_ms']=now_ms;state['last_snapshot']=metrics(state,prices)
    state['max_dd']=max(state['max_dd'],1-state['last_snapshot']['equity']/max(state['peak_equity'],EPS))
    return state,events


def tick_control(original,hedge_state,prices,now_ms,funding_events=None,maintenance=None):
    """镜像每个品种净数量；对冲内部抵消的换手不会变成对照组的虚构交易。"""
    state=copy.deepcopy(original);events=[];maintenance=maintenance or {}
    settle_funding(state,funding_events or {},now_ms,events)
    _drawdown_guard(state,prices,now_ms,events)
    if not state['paused']:
        for symbol in SYMBOLS:
            src=hedge_state['slots'][symbol];slot=state['slots'][symbol]
            net=sum((1 if side=='LONG' else -1)*leg['qty'] for side,leg in src['legs'].items())
            side='LONG' if net>0 else 'SHORT';target=abs(net);mark=prices[symbol]
            for held,leg in list(slot['legs'].items()):
                if held!=side or target<EPS:close_leg(state,symbol,held,leg['qty'],mark,now_ms,'净敞口镜像换向/归零',events)
                elif leg['qty']>target:close_leg(state,symbol,held,leg['qty']-target,mark,now_ms,'净敞口镜像减仓',events)
            source_leg=src['legs'].get(side)
            if not source_leg or target<EPS:continue
            slot['direction']=1 if side=='LONG' else -1
            held=slot['legs'].get(side);current=held['qty'] if held else 0
            if held:held['stop']=source_leg['stop']
            if target>current+EPS:
                q=safe_open(state,symbol,side,target-current,prices,source_leg['stop'],source_leg['atr0'],
                    now_ms,'net_control',maintenance.get(symbol,0.01),events)
                if q<target-current-1e-6:state['reason']='net_control_tracking_clamped'
        enforce_caps(state,prices,now_ms,events)
        _drawdown_guard(state,prices,now_ms,events)
    state['last_tick_ms']=now_ms;state['last_snapshot']=metrics(state,prices)
    state['max_dd']=max(state['max_dd'],1-state['last_snapshot']['equity']/max(state['peak_equity'],EPS))
    return state,events
