"""双腿专用SQLite：两账户状态、成交、资金费率和快照一次事务提交。

原shadow_v2.db完全不写；网页只读，不初始化/修改本库。
事件主键(account,event_id)保证重启不重复落账，完整JSON状态恢复循环次数。
"""
import json,os,sqlite3,time
from contextlib import closing
from pathlib import Path
from .config import NAMES,SYMBOLS,DESCRIPTIONS,PARAMS
from .engine import metrics,legs

DB_PATH=Path(os.environ.get('HEDGE_CYCLE_DB',str(Path(__file__).resolve().parents[1]/'data/hedge_cycle.db')))


def initialize():
    DB_PATH.parent.mkdir(parents=True,exist_ok=True)
    with closing(sqlite3.connect(DB_PATH,timeout=10)) as db, db:
        db.execute('PRAGMA journal_mode=WAL')
        db.execute('CREATE TABLE IF NOT EXISTS accounts (name TEXT PRIMARY KEY,state_json TEXT NOT NULL,updated_ms INTEGER NOT NULL)')
        db.execute('CREATE TABLE IF NOT EXISTS events (account TEXT,event_id INTEGER,time_ms INTEGER,symbol TEXT,action TEXT,data_json TEXT NOT NULL,PRIMARY KEY(account,event_id))')
        db.execute('CREATE INDEX IF NOT EXISTS event_symbol_idx ON events(symbol,time_ms)')
        db.execute('CREATE TABLE IF NOT EXISTS snapshots(account TEXT,time_ms INTEGER,data_json TEXT,PRIMARY KEY(account,time_ms))')


def read_accounts():
    if not DB_PATH.exists():return {}
    with closing(sqlite3.connect(f'file:{DB_PATH}?mode=ro',uri=True,timeout=3)) as db:
        return {name:json.loads(raw) for name,raw in db.execute('SELECT name,state_json FROM accounts')}


def commit(accounts,events):
    with closing(sqlite3.connect(DB_PATH,timeout=10)) as db, db:
        db.execute('BEGIN IMMEDIATE')
        for state in accounts.values():
            raw=json.dumps(state,ensure_ascii=False,allow_nan=False)
            db.execute('INSERT INTO accounts VALUES(?,?,?) ON CONFLICT(name) DO UPDATE SET state_json=excluded.state_json,updated_ms=excluded.updated_ms',
                (state['name'],raw,state['last_tick_ms']))
            db.execute('INSERT OR REPLACE INTO snapshots VALUES(?,?,?)',
                (state['name'],state['last_tick_ms'],json.dumps(state['last_snapshot'],allow_nan=False)))
        for event in events:
            db.execute('INSERT INTO events VALUES(?,?,?,?,?,?)',
                (event['strategy'],event['event_id'],event['time_ms'],event['symbol'],event['action'],json.dumps(event,ensure_ascii=False,allow_nan=False)))


def read_events(name=None,symbol=None,action=None,limit=20000):
    if not DB_PATH.exists():return []
    where,args=[],[]
    for key,val in (('account',name),('symbol',symbol),('action',action)):
        if val is not None:where.append(key+'=?');args.append(val)
    query='SELECT data_json FROM events'+(' WHERE '+' AND '.join(where) if where else '')+' ORDER BY time_ms DESC,event_id DESC LIMIT ?'
    args.append(limit)
    with closing(sqlite3.connect(f'file:{DB_PATH}?mode=ro',uri=True,timeout=3)) as db:
        return [json.loads(r[0]) for r in db.execute(query,args)]


def _known_prices(state,prices):
    return {**state.get('last_prices',{}),**{k:v for k,v in (prices or {}).items() if v and v>0}}


def summary(prices=None):
    accounts=read_accounts();out=[]
    for name in NAMES:
        state=accounts.get(name)
        if not state:
            out.append({'strategy':name,'description':DESCRIPTIONS[name],
                'is_hedge_cycle':True,'trades':0,'wins':0,'open_count':0,
                'equity':1000,'net_equity_after_fees':1000,'net_return_after_fees_pct':0,
                'net_pnl_after_fees_usd':0,'mtm_return_after_fees_pct':0,
                'mtm_equity_after_fees':1000,'ranking_return_pct':0,
                'total_fees_usd':0,'open_unrealized_pnl_usd':0,
                'risk_regime':'waiting','started_ms':None})
            continue
        p=state['config'];known=_known_prices(state,prices);m=metrics(state,known)
        fills=read_events(name,action='CLOSE');wins=sum(e['net_pnl_usd']>0 for e in fills)
        # 假设全部平仓的手续费和滑点也预留，但不真的从cash扣第二次。
        close_cost=m['gross']*(p['fee_rate']+p['slippage_bps']/10000)
        mtm=m['equity']-close_cost;dd=max(state['max_dd'],1-m['equity']/max(state['peak_equity'],1e-10))
        counts=len(list(legs(state)));stale=time.time()*1000-state['last_tick_ms']>180000
        row={'strategy':name,'description':DESCRIPTIONS[name], 'is_hedge_cycle':True,
            'trades':len(fills),'wins':wins,'win_rate':round(wins/len(fills)*100,1) if fills else None,
            'total_pnl_usd':state['realized_gross'],'total_pnl_atr':0,'avg_pnl_atr':None,
            'worst_trade_atr':None,'open_count':counts,'equity':state['cash'],
            'equity_return_pct':(state['cash']/1000-1)*100,
            'net_equity_after_fees':state['cash'],'net_return_after_fees_pct':(state['cash']/1000-1)*100,
            'net_pnl_after_fees_usd':state['cash']-1000,
            'mtm_equity_after_fees':mtm,'mtm_return_after_fees_pct':(mtm/1000-1)*100,
            'mtm_net_pnl_after_fees_usd':mtm-1000,'ranking_return_pct':(mtm/1000-1)*100,
            'ranking_basis':'mtm_including_both_legs_and_exit_cost','total_fees_usd':state['fees'],
            'total_funding_pnl_usd':state['funding'],'open_unrealized_pnl_usd':m['unrealized'],
            'open_estimated_fees_usd':close_cost,'gross_exposure_usd':m['gross'],
            'gross_exposure_mult':m['gross_mult'],'net_exposure_usd':m['net'],
            'net_exposure_mult':m['net']/max(m['equity'],1e-10),
            'margin_usage_pct':m['margin_frac']*100,'long_margin_pct':m['long_margin_frac']*100,
            'short_margin_pct':m['short_margin_frac']*100,'stop_risk_usd':m['stop_risk'],
            'stop_risk_pct':m['stop_risk_frac']*100,'dynamic_stop_heat_cap_pct':p['max_stop_risk_frac']*100,
            'dynamic_gross_cap':p['max_gross_mult'],'risk_drawdown_pct':dd*100,
            'realized_max_drawdown_pct':state['max_dd']*100,'turnover_mult':state['turnover']/1000,
            'stop_count':sum('止损' in e['reason'] for e in fills),
            'worst_trade_net_usd':min((e['net_pnl_usd'] for e in fills),default=None),
            'risk_regime':'paused' if state['paused'] else ('stale_data' if stale else 'hedge_limits'),
            'new_entries_blocked':state['paused'] or stale,'guard_reason':state['reason'],
            'cycle_count':sum(s['cycle'] for s in state['slots'].values()),
            'cycle_total':sum(e['action']=='CYCLE' for e in read_events(name)),
            'priced_open_count':counts,'unpriced_open_count':0,
            'started_ms':state['created_ms'],'last_tick_ms':state['last_tick_ms']}
        out.append(row)
    return out


def positions(name=None,symbol=None,status='open',limit=100,prices=None):
    out=[]
    if status=='open':
        for account,state in read_accounts().items():
            if name and account!=name:continue
            known=_known_prices(state,prices)
            for sym,side,leg in legs(state):
                if symbol and sym!=symbol:continue
                mark=known[sym];d=1 if side=='LONG' else -1
                out.append({'id':leg['id'],'symbol':sym,'strategy':account,'side':side,
                    'entry':leg['entry'],'qty':leg['qty'],'entry_bar_time':leg['opened_ms'],
                    'current_price':mark,'unrealized_pnl_usd':d*(mark-leg['entry'])*leg['qty'],
                    'atr0':leg['atr0'],'stop':leg['stop'],'entry_stage':leg['role'],
                    'leg_role':leg['role'],'status':'open','timeframe':'4h+1d'})
    else:
        for e in read_events(name,symbol,'CLOSE',limit):
            out.append({'id':e['event_id'],'symbol':e['symbol'],'strategy':e['strategy'],'side':e['side'],
                'entry':e['entry'],'qty':e['qty'],'entry_bar_time':e['opened_ms'],'exit_bar_time':e['time_ms'],
                'exit_price':e['price'],'exit_reason':e['reason'],'gross_pnl_usd':e['gross_pnl_usd'],
                'fees_usd':e['fees_usd'],'funding_pnl_usd':e['funding_pnl_usd'],
                'net_pnl_after_fees_usd':e['net_pnl_usd'],'closed_at':e['time_ms']/1000,
                'status':'closed','leg_role':e['role'],'timeframe':'4h+1d'})
    return sorted(out,key=lambda r:r.get('exit_bar_time') or r['entry_bar_time'],reverse=True)[:limit]


def symbol_summary(name=None):
    out=[];accounts=read_accounts()
    for sym in SYMBOLS:
        ev=read_events(name,sym);closed=[e for e in ev if e['action']=='CLOSE']
        cash=sum((-e['fee'] if e['action']=='OPEN' else e['cashflow'] if e['action']=='FUNDING'
                  else e['gross_pnl_usd']-e['exit_fee'] if e['action']=='CLOSE' else 0) for e in ev)
        opened=positions(name,sym,'open',100)
        out.append({'symbol':sym,'trades':len(closed),'wins':sum(e['net_pnl_usd']>0 for e in closed),
            'total_pnl_usd':cash,'total_pnl_atr':0,'open_count':len(opened),
            'hedge_cycle_pnl_usd':cash,'hedge_cycle_open_count':len(opened),
            'open_unrealized_pnl_usd':sum(r['unrealized_pnl_usd'] for r in opened)})
    return out
