"""Gate closed-candle replay: counts only, no production DB, no profit claims.

Run in staging first. Preserves entry engine and original ATR like the paper runner.
Hard stops use subsequent OHLC; strategy exits only see each historical prefix.
No same-bar exit/reentry. This is not a portfolio sizing/funding performance backtest.
"""
import collections
import json
import time
from pathlib import Path
from strategy_engine import klines
from strategy_engine.strategies import adx_dual_engine as strategy


def replay(bars, mode):
    position = None
    entries, exits = collections.Counter(), collections.Counter()
    for i in range(60, len(bars)):
        window, bar = bars[max(0,i-549):i+1], bars[i]
        if position:
            d = 1 if position['side']=='LONG' else -1
            hit_stop = bar['l'] <= position['stop'] if d>0 else bar['h'] >= position['stop']
            if hit_stop:
                exits['hard_stop'] += 1
                position = None
                continue
            sig = strategy.generate_signal({'base':window},{'engine_mode':mode},position)
            if sig and sig['action'].startswith('CLOSE'):
                exits[sig['reason'].split(';')[0]] += 1
                assert sig['bar_time'] > position['entry_bar_time']
                position = None
            continue
        sig = strategy.generate_signal({'base':window},{'engine_mode':mode})
        if sig and sig['action'] in ('LONG','SHORT'):
            entries[sig['entry_stage']] += 1
            position = dict(side=sig['action'],entry_stage=sig['entry_stage'],
                            entry_bar_time=sig['bar_time'],atr0=sig['atr'],stop=sig['stop_loss'])
    return {'entries':dict(entries),'exits':dict(exits),'open_at_end':bool(position)}


if __name__ == '__main__':
    report={'type':'signal_replay_not_portfolio_backtest','created_at':time.time(),'rows':[]}
    for tf in ('4h','1h'):
        for symbol in ('BTCUSDT','ETHUSDT','SOLUSDT','ZECUSDT','BNBUSDT','XRPUSDT'):
            assert symbol not in klines.BINANCE_SOURCE_SYMBOLS
            bars = klines.get_bars(symbol,tf,limit=550)
            minutes=klines.timeframe_to_minutes(tf)
            assert len(bars)>=100, (symbol,tf,len(bars))
            assert bars[-1]['t'] + minutes*60000 <= int(time.time()*1000)
            assert all(bars[i]['t']>bars[i-1]['t'] for i in range(1,len(bars)))
            for mode in (('both','trend','range') if tf=='4h' else ('both',)):
                result={'symbol':symbol,'tf':tf,'mode':mode,'bars':len(bars),
                        'from':bars[60]['t'],'to':bars[-1]['t'],**replay(bars,mode)}
                report['rows'].append(result)
                print(json.dumps(result,ensure_ascii=False),flush=True)
    path=Path(__file__).with_name('adx_dual_replay_validation.json')
    path.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print('REPORT',str(path),flush=True)
