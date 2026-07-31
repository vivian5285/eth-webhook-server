#!/usr/bin/env python3
"""
Manual order placement script for ETHUSDT
"""
import json
import sys
import time

sys.path.insert(0, '/home/trading/binance-engine')
from binance_client import BinanceClient

bc = BinanceClient()
symbol = "ETHUSDT"

print("=== Checking current state ===")
pos = bc.client.get_position_risk(symbol=symbol)
print(f"Position: {json.dumps(pos, indent=2)}")

orders = bc.client.get_open_orders(symbol=symbol)
print(f"Open orders: {json.dumps(orders, indent=2)}")

# Current price
ticker = bc.client.get_symbol_ticker(symbol=symbol)
current_px = float(ticker['price'])
print(f"Current price: {current_px}")

# ETH SHORT position - need to place TP sells above current price
# TP1: 1879.0 (better than TV 1877.11)
# TP2: 1865.0 (better than TV 1862.90)
# TP3: 1851.0 (better than TV 1848.70)
# Hard stop: 1915.0 (lower than TV 1917.32 for better protection)

qty = 0.85  # current position size
tp1_px = 1879.0
tp2_px = 1865.0
tp3_px = 1851.0
sl_px = 1915.0

print(f"\n=== Placing TP orders for SHORT position ===")
print(f"Qty: {qty}")
print(f"TP1: {tp1_px}")
print(f"TP2: {tp2_px}")
print(f"TP3: {tp3_px}")
print(f"HARD STOP: {sl_px}")

# TP1 - 10% of qty
tp1_qty = round(qty * 0.1, 3)
# TP2 - 20% of qty
tp2_qty = round(qty * 0.2, 3)
# TP3 - 70% of qty
tp3_qty = round(qty * 0.7, 3)

print(f"\nTP1: sell {tp1_qty} @ {tp1_px}")
try:
    r1 = bc.client.place_order(
        symbol=symbol,
        side='SELL',
        type='LIMIT',
        quantity=tp1_qty,
        price=tp1_px,
        timeInForce='GTC'
    )
    print(f"TP1 result: {json.dumps(r1, indent=2)}")
except Exception as e:
    print(f"TP1 error: {e}")

time.sleep(1)

print(f"\nTP2: sell {tp2_qty} @ {tp2_px}")
try:
    r2 = bc.client.place_order(
        symbol=symbol,
        side='SELL',
        type='LIMIT',
        quantity=tp2_qty,
        price=tp2_px,
        timeInForce='GTC'
    )
    print(f"TP2 result: {json.dumps(r2, indent=2)}")
except Exception as e:
    print(f"TP2 error: {e}")

time.sleep(1)

print(f"\nTP3: sell {tp3_qty} @ {tp3_px}")
try:
    r3 = bc.client.place_order(
        symbol=symbol,
        side='SELL',
        type='LIMIT',
        quantity=tp3_qty,
        price=tp3_px,
        timeInForce='GTC'
    )
    print(f"TP3 result: {json.dumps(r3, indent=2)}")
except Exception as e:
    print(f"TP3 error: {e}")

time.sleep(1)

print(f"\nHARD STOP: sell {qty} @ {sl_px} (STOP_MARKET)")
try:
    r4 = bc.client.place_order(
        symbol=symbol,
        side='SELL',
        type='STOP_MARKET',
        quantity=qty,
        stopPrice=sl_px
    )
    print(f"HARD STOP result: {json.dumps(r4, indent=2)}")
except Exception as e:
    print(f"HARD STOP error: {e}")

print("\n=== Done ===")
