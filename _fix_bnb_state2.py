#!/usr/bin/env python3
import json

# Fix 5003 BNB state
with open('/home/trading/binance-engine/binance_vps_state_BNBUSDT.json', 'r') as f:
    state = json.load(f)

print('Before fix:')
print(f"  initial_stop: {state.get('initial_stop')}")
print(f"  current_sl: {state.get('current_sl')}")
print(f"  frozen_hard_sl_px: {state.get('frozen_hard_sl_px')}")

# Fix: set current_sl to frozen_hard_sl_px (the correct hard stop)
frozen = state.get('frozen_hard_sl_px', 0)
if frozen > 0:
    state['initial_stop'] = frozen
    state['current_sl'] = frozen
    print(f"\nFixed: initial_stop and current_sl set to frozen_hard_sl_px={frozen}")
else:
    print('\nNo frozen_hard_sl_px found, using existing values')

# Clear radar state since radar is not activated
state['radar_activated'] = False
state['radar_pending_arm'] = True
state['defense_order_ids']['radar_stop'] = ''
state['defense_order_ids']['stop'] = ''

with open('/home/trading/binance-engine/binance_vps_state_BNBUSDT.json', 'w') as f:
    json.dump(state, f, indent=2)

print('\nAfter fix:')
print(f"  initial_stop: {state.get('initial_stop')}")
print(f"  current_sl: {state.get('current_sl')}")
print(f"  frozen_hard_sl_px: {state.get('frozen_hard_sl_px')}")
print(f"  radar_activated: {state.get('radar_activated')}")
