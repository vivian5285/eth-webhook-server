"""Run only in codex_staging: verifies stage persistence and market-source isolation."""
import io,json,types,urllib.request
from pathlib import Path
from unittest.mock import patch
from strategy_engine import klines,shadow_store,multi_strategy_runner as runner
from strategy_engine.comparison_roster import ADX_DUAL_ENGINE_ROSTER,ADX_DUAL_ENGINE_SYMBOLS
from strategy_engine.pruned_strategies import PRUNED_STRATEGIES
from strategy_engine.strategies import get_strategy,STRATEGY_DESCRIPTIONS
from dashboard import roster_server as dashboard

assert 'codex_staging' in str(shadow_store.DB_PATH), 'Never run this against production DB'
names={e['strategy'] for e in ADX_DUAL_ENGINE_ROSTER}
assert len(names)==4 and not names & PRUNED_STRATEGIES
assert len(ADX_DUAL_ENGINE_ROSTER)==4*len(ADX_DUAL_ENGINE_SYMBOLS)
assert not set(ADX_DUAL_ENGINE_SYMBOLS)&klines.BINANCE_SOURCE_SYMBOLS
assert all(not e.get('early_entry') for e in ADX_DUAL_ENGINE_ROSTER)
for name in names:assert callable(get_strategy(name)) and STRATEGY_DESCRIPTIONS[name]

# Exercise runner -> SQLite -> restart hydration rather than mirroring dict assignment.
guard=types.SimpleNamespace(regime='normal',gross_cap_mult=2)
sig=dict(action='LONG',price=100,atr=1,stop_loss=98,tier=1,bar_time=123,
         entry_stage='adx_range',adx=17)
key=('TESTUSDT','adx_dual_engine_4h')
with patch.object(runner,'_guarded_qty',return_value=(1,guard)):
    pid=runner._open_from_signal(*key,'4h',sig,[])
assert pid
runner._open_positions.clear()
runner._hydrate_keys_from_db([key])
restored=runner._open_positions[key]
assert restored['entry_stage']=='adx_range' and restored['adx']==17

def response(rows):return io.BytesIO(json.dumps(rows).encode())
dashboard._price_cache={'ts':0,'data':{}}
with patch.object(dashboard.urllib.request,'urlopen',side_effect=[
    response([{'contract':'BTC_USDT','mark_price':'100'},
              {'contract':'PEPE_USDT','mark_price':'0.000004'},
              {'contract':'OPENAI_USDT','mark_price':'999'}]),
    response([{'symbol':'BTCUSDT','markPrice':'999'},
              {'symbol':'OPENAIUSDT','markPrice':'42'},
              {'symbol':'ANTHROPICUSDT','markPrice':'43'}])]) as mock:
    prices=dashboard._fetch_live_prices()
    assert prices['BTCUSDT']==100 and prices['1000PEPEUSDT']==0.004
    assert prices['OPENAIUSDT']==42 and prices['ANTHROPICUSDT']==43
    assert mock.call_count==2

dashboard._price_cache={'ts':0,'data':{}}
with patch.object(dashboard.urllib.request,'urlopen',side_effect=[
    OSError('Gate unavailable'),response([{'symbol':'BTCUSDT','markPrice':'999'}])]):
    assert 'BTCUSDT' not in dashboard._fetch_live_prices(), 'No Binance fallback for Gate symbols'

with urllib.request.urlopen(dashboard.GATE_PRICE_URL,timeout=15) as resp:
    tickers=json.load(resp)
contracts={r['contract'] for r in tickers}
missing=[s for s in ADX_DUAL_ENGINE_SYMBOLS if klines._symbol_to_gate_contract(s) not in contracts]
assert not missing,missing
dashboard._price_cache={'ts':0,'data':{}}
prices=dashboard._fetch_live_prices()
assert all(prices.get(s,0)>0 for s in ADX_DUAL_ENGINE_SYMBOLS)
meta=dashboard.app.test_client().get('/api/roster/meta').get_json()
assert meta['legacy_binance_symbols']==['ANTHROPICUSDT','OPENAIUSDT']
rows=dashboard.app.test_client().get('/api/roster/compare').get_json()['strategies']
assert names.issubset({r['strategy'] for r in rows})
print(json.dumps({'integration':'ok','new_strategy_names':sorted(names),
                  'gate_symbols':ADX_DUAL_ENGINE_SYMBOLS,'entries':len(ADX_DUAL_ENGINE_ROSTER),
                  'gate_live_prices':{s:prices[s] for s in ('BTCUSDT','ETHUSDT','1000PEPEUSDT')}},ensure_ascii=False),flush=True)
