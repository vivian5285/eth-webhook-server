"""会计、故障恢复和极端路径验收；不依赖网络/真实账户。"""
import copy,random,tempfile,unittest,sqlite3
from unittest.mock import patch
from pathlib import Path
from strategy_engine.hedge_cycle import engine as e,store
from strategy_engine.hedge_cycle.config import SYMBOLS,HEDGE_NAME,CONTROL_NAME

def prices(p=100):return {s:p for s in SYMBOLS}
def ctx(t,d=1,dead=False,atr=2):return {s:{'direction':d,'dead':dead,'atr':atr,'bar_time':t,'momentum':True} for s in SYMBOLS}

class HedgeTests(unittest.TestCase):
    def initial(self):return e.tick_hedge(e.new_state(HEDGE_NAME,1),prices(),ctx(10),20)
    def assert_safe(self,s,p):
        self.assertTrue(e.within_caps(s,p))
        for slot in s['slots'].values():
            if slot['direction']:
                side='LONG' if slot['direction']==1 else 'SHORT'
                main=slot['legs'].get(side);other=slot['legs'].get('SHORT' if side=='LONG' else 'LONG')
                if other:self.assertLessEqual(other['qty'],(main['qty'] if main else 0)*.5+1e-8)
    def test_shared_account_and_repeated_bar(self):
        s,ev=self.initial();self.assert_safe(s,prices());self.assertEqual(len(list(e.legs(s))),6)
        again,ev2=e.tick_hedge(s,prices(),ctx(10),21)
        self.assertEqual(ev2,[]);self.assertEqual(s['cash'],again['cash'])
        self.assertLess(s['last_snapshot']['stop_risk_frac'],.02501)
    def test_partial_fee_and_funding_reconciliation(self):
        s=e.new_state(HEDGE_NAME,1);ev=[];s['slots'][SYMBOLS[0]]['direction']=1
        e.safe_open(s,SYMBOLS[0],'LONG',1,prices(),98,2,2,'main',.01,ev)
        q=s['slots'][SYMBOLS[0]]['legs']['LONG']['qty']
        funds={SYMBOLS[0]:[{'time_ms':10,'rate':.001,'mark':100}]}
        e.settle_funding(s,funds,10,ev);paid=s['funding'];self.assertAlmostEqual(paid,-q*.1)
        cash=s['cash'];e.settle_funding(s,funds,20,ev);self.assertEqual(cash,s['cash'])
        e.close_leg(s,SYMBOLS[0],'LONG',q*.25,110,21,'partial',ev)
        e.close_leg(s,SYMBOLS[0],'LONG',q,110,22,'exit',ev)
        self.assertAlmostEqual(s['cash'],1000+s['realized_gross']-s['fees']+s['funding'])
        closed=[x for x in ev if x['action']=='CLOSE']
        self.assertAlmostEqual(sum(x['net_pnl_usd'] for x in closed),s['cash']-1000)
    def test_hedge_cannot_average_down(self):
        s,_=self.initial();ev=[];sym=SYMBOLS[0];leg=s['slots'][sym]['legs']['SHORT'];qty=leg['qty']
        opened=e.safe_open(s,sym,'SHORT',1,prices(),102,2,30,'hedge',.01,ev)
        self.assertEqual(opened,0);self.assertEqual(leg['qty'],qty)
    def test_cycle_replaces_hedge_and_max_three(self):
        s,_=self.initial();cycles=0
        for i,p in enumerate([101.9,108,116,125,134,143]):
            s,events=e.tick_hedge(s,prices(p),ctx(100+i),1000+i)
            self.assert_safe(s,prices(p));cycles+=sum(x['action']=='CYCLE' for x in events)
            for sym in SYMBOLS:self.assertLessEqual(s['slots'][sym]['cycle'],3)
        self.assertGreater(cycles,0)
    def test_reversal_closes_old_main_regardless_loss(self):
        s,_=self.initial();s,ev=e.tick_hedge(s,prices(99),ctx(11,-1),30)
        self.assert_safe(s,prices(99))
        for slot in s['slots'].values():
            self.assertEqual(slot['direction'],-1);self.assertEqual(slot['legs']['SHORT']['role'],'main')
        self.assertTrue(any('反转' in x.get('reason','') for x in ev))
    def test_deadwater_reduces_once_and_never_adds(self):
        s,_=self.initial();before=sum(x['qty'] for _,_,x in e.legs(s))
        s,ev=e.tick_hedge(s,prices(),ctx(11,dead=True),30)
        s,ev2=e.tick_hedge(s,prices(),ctx(12,dead=True),31)
        self.assertAlmostEqual(sum(x['qty'] for _,_,x in e.legs(s)),before*.5)
        self.assertFalse(any(x['action']=='OPEN' for x in ev+ev2))
    def test_stop_with_missing_signal_closes_pair(self):
        s,_=self.initial();s,ev=e.tick_hedge(s,prices(95),{},30)
        self.assertEqual(len(list(e.legs(s))),0)
        self.assertTrue(any('止损' in x.get('reason','') for x in ev))
    def test_drawdown_halt_persists(self):
        s,_=self.initial();s['peak_equity']=1200
        s,_=e.tick_hedge(s,prices(),ctx(11),30)
        self.assertTrue(s['paused']);self.assertEqual(len(list(e.legs(s))),0)
        s,ev=e.tick_hedge(s,prices(),ctx(12),31);self.assertEqual(ev,[])
    def test_net_control_tracks_without_gross_netting(self):
        s,_=self.initial();control,ev=e.tick_control(e.new_state(CONTROL_NAME,1),s,prices(),20)
        for sym in SYMBOLS:
            net=sum((1 if side=='LONG' else -1)*leg['qty'] for side,leg in s['slots'][sym]['legs'].items())
            self.assertAlmostEqual(control['slots'][sym]['legs']['LONG']['qty'],net,7)
        self.assertLess(e.metrics(control,prices())['gross'],e.metrics(s,prices())['gross'])
    def test_random_gaps_keep_caps_or_halt(self):
        rng=random.Random(19);s,_=self.initial();p=prices()
        for t in range(200):
            p={sym:max(20,val*(1+rng.uniform(-.07,.07))) for sym,val in p.items()}
            s,_=e.tick_hedge(s,p,ctx(100+t,1 if t%60<30 else -1),1000+t)
            self.assert_safe(s,p)
            self.assertAlmostEqual(s['cash'],1000+s['realized_gross']-s['fees']+s['funding'],7)
    def test_missing_mark_rejects_entire_state(self):
        s,_=self.initial();old=copy.deepcopy(s)
        with self.assertRaises(ValueError):e.tick_hedge(s,{'ETHUSDT':100},{},30)
        self.assertEqual(s,old)
    def test_closed_bars_and_daily_conflict(self):
        now=1800000000000;now-=now%86400000
        def bars(n,period):
            return [{'t':now-(n-i)*period,'o':100+i,'h':102+i,'l':99+i,'c':101+i,'v':1} for i in range(n)]
        four=bars(150,14400000);daily=bars(60,86400000)
        base=e.signal_context(four,daily,now);self.assertEqual(base['direction'],1)
        self.assertEqual(e.signal_context(four+[{'t':now,'o':1,'h':2,'l':.5,'c':1}],daily,now),base)
        down=[{**b,'o':300-i,'h':302-i,'l':299-i,'c':301-i} for i,b in enumerate(daily)]
        conflict=e.signal_context(four,down,now);self.assertEqual(conflict['direction'],0);self.assertTrue(conflict['dead'])
        self.assertIsNone(e.signal_context(four,daily,now+3*86400000))
    def test_delayed_funding_timestamp_uses_closed_minute(self):
        from strategy_engine.hedge_cycle import market
        stamp=1790035203;market._funding_mark_cache.clear()
        with patch.object(market,'get_json',return_value=[{'t':stamp//60*60-60,'c':'100'}]) as fetch:
            self.assertEqual(market.funding_mark('ETHUSDT',stamp),100)
            self.assertEqual(fetch.call_args.args[1]['from'],stamp//60*60-60)
    def test_transaction_rollback_and_restart(self):
        oldpath=store.DB_PATH
        with tempfile.TemporaryDirectory() as tmp:
            try:
                store.DB_PATH=Path(tmp)/'test.db';store.initialize();s,ev=self.initial()
                s['last_prices']=prices();store.commit({HEDGE_NAME:s},ev)
                restored=store.read_accounts()[HEDGE_NAME];self.assertEqual(restored,s)
                bad=copy.deepcopy(s);bad['cash']=999999
                with self.assertRaises(sqlite3.IntegrityError):store.commit({HEDGE_NAME:bad},ev)
                self.assertEqual(store.read_accounts()[HEDGE_NAME]['cash'],s['cash'])
                s2,ev2=e.tick_hedge(restored,prices(),ctx(10),21);self.assertEqual(ev2,[])
            finally:store.DB_PATH=oldpath

if __name__=='__main__':unittest.main()
