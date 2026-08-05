#!/usr/bin/env python3
import sys
sys.path.insert(0, '/home/trading/binance-engine')
from binance_client import binance_client
import json

orders = binance_client.get_open_orders('BNBUSDT')
stops = [o for o in (orders or []) if o and str(o.get('type') or o.get('orderType') or '').upper() in ('STOP', 'STOP_MARKET')]
print('Open STOP orders for BNBUSDT:')
for o in stops:
    print(json.dumps({
        'type': o.get('type'),
        'side': o.get('side'),
        'stopPrice': o.get('stopPrice'),
        'reduceOnly': o.get('reduceOnly'),
        'closePosition': o.get('closePosition'),
        'qty': o.get('qty'),
        'orderId': o.get('orderId') or o.get('algoId')
    }, indent=2))
