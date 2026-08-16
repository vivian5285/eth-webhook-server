#!/usr/bin/env python3
import sys
sys.path.insert(0, '/home/trading/binance-engine')
from binance_client import binance_client
for sym in ['XAUUSDT', 'BNBUSDT']:
    pos = binance_client.get_position(sym)
    size = float((pos or {}).get('size') or 0) if pos else 0
    print(f"{sym}: position={size}")
