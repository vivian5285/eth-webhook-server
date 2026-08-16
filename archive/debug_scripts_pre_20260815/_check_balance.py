#!/usr/bin/env python3
import sys
sys.path.insert(0, '/home/trading/binance-engine')
from binance_client import BinanceClient
c = BinanceClient()
acc = c.client.get_account()
for a in acc['balances']:
    free = float(a['free'])
    locked = float(a['locked'])
    if free > 0 or locked > 0:
        print(f"{a['asset']}: free={free}, locked={locked}")
