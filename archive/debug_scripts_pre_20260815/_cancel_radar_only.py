#!/usr/bin/env python3
import sys
sys.path.insert(0, '/home/trading/binance-engine')
from binance_client import binance_client

# Cancel the old radar stop
try:
    result = binance_client.cancel_algo_order('BNBUSDT', 3000002121546201)
    print('Cancelled radar stop 3000002121546201:', result)
except Exception as e:
    print('Error:', e)

# Verify
orders = binance_client.get_open_orders('BNBUSDT')
stops = [o for o in (orders or []) if o and str(o.get('type') or '').upper() in ('STOP', 'STOP_MARKET')]
print('\nRemaining STOP orders:')
for o in stops:
    oid = o.get('orderId') or o.get('algoId')
    print(f"  {oid}: {o.get('type')} @ {o.get('stopPrice')} closePosition={o.get('closePosition')}")
if not stops:
    print('  No STOP orders')
