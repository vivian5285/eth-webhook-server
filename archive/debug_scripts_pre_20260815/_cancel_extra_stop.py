#!/usr/bin/env python3
import sys
sys.path.insert(0, '/home/trading/binance-engine')
from binance_client import binance_client
import json

orders = binance_client.get_open_orders('BNBUSDT')
stops = [o for o in (orders or []) if o and str(o.get('type') or '').upper() in ('STOP', 'STOP_MARKET')]

print('Current BNB stops:')
for o in stops:
    oid = o.get('orderId') or o.get('algoId')
    print(f"  {oid}: {o.get('type')} {o.get('side')} @ {o.get('stopPrice')} reduceOnly={o.get('reduceOnly')} closePosition={o.get('closePosition')}")

# Only cancel the radar stop (not the hard stop)
for o in stops:
    oid = o.get('orderId') or o.get('algoId')
    px = float(o.get('stopPrice') or 0)
    # Keep the hard stop (closePosition=true), cancel the radar stop (higher price, closePosition=false)
    if o.get('closePosition') in (None, False, 'false', 'False'):
        print(f'Cancelling radar stop: {oid} @ {px}')
        try:
            if o.get('algoId'):
                result = binance_client.cancel_algo_order('BNBUSDT', oid)
            else:
                result = binance_client.cancel_order('BNBUSDT', oid)
            print(f'Cancelled: {result}')
        except Exception as e:
            print(f'Error: {e}')
    else:
        print(f'Keeping hard stop: {oid} @ {px} (closePosition=true)')

# Verify
print('\nAfter cleanup:')
orders2 = binance_client.get_open_orders('BNBUSDT')
stops2 = [o for o in (orders2 or []) if o and str(o.get('type') or '').upper() in ('STOP', 'STOP_MARKET')]
for o in stops2:
    oid = o.get('orderId') or o.get('algoId')
    print(f"  {oid}: {o.get('type')} {o.get('side')} @ {o.get('stopPrice')} closePosition={o.get('closePosition')}")
if not stops2:
    print('  No STOP orders remaining')
