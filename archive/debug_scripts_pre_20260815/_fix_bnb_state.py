#!/usr/bin/env python3
import json
with open('binance_vps_state_BNBUSDT.json', 'r') as f:
    state = json.load(f)
# Clear radar stop order ID since radar is not activated
state['defense_order_ids']['radar_stop'] = ''
state['defense_order_ids']['stop'] = ''
with open('binance_vps_state_BNBUSDT.json', 'w') as f:
    json.dump(state, f, indent=2)
print('State updated - radar_stop cleared')
print('Current defense_order_ids:', state.get('defense_order_ids'))
