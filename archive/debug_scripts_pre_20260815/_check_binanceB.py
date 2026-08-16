#!/usr/bin/env python3
import sys
sys.path.insert(0, '/home/binanceB/binance-engine')
from binance_client import BinanceClient

c = BinanceClient()
print("=== 持仓 ===")
has = False
for sym in ['ETHUSDT', 'XAUUSDT', 'BNBUSDT', 'ZECUSDT']:
    pos = c.get_position(sym)
    if pos:
        qty = float(pos.get("positionAmt", 0) or 0)
        if abs(qty) > 0.00001:
            has = True
            entry = pos.get('entryPrice', 'N/A')
            upnl = pos.get('unRealizedProfit', pos.get('unrealizedProfit', 'N/A'))
            print(f"{sym}: 数量={pos['positionAmt']}, 开仓价={entry}, 未实现盈亏={upnl}")
if not has:
    print("无持仓")
print()
print("=== 挂单 ===")
orders = c.get_open_orders()
if orders:
    for o in orders:
        print(f"{o['symbol']}: {o['side']} {o['origQty']} @ {o['price']} {o['status']}")
else:
    print("无挂单")
