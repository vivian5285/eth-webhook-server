import json
import sys

with open('/home/trading/binance-engine/binance_vps_state_XAUUSDT.json') as f:
    d = json.load(f)

print('tv_sl:', d.get('tv_sl'))
print('tv_sl_ref:', d.get('tv_sl_ref'))
print('current_sl:', d.get('current_sl'))
print('initial_stop:', d.get('initial_stop'))
sig = d.get('last_tv_signal', {})
print('signal_tv_sl:', sig.get('tv_sl'))
print('signal__tv_sl_ref:', sig.get('_tv_sl_ref'))
print('pipeline_phase:', d.get('pipeline', {}).get('phase'))
print('monitoring:', d.get('monitoring'))
