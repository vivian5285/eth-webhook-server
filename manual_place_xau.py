#!/usr/bin/env python3
"""
Manual order placement script for XAUUSDT - Open LONG position
"""
import json
import sys
import time

sys.path.insert(0, '/home/trading/binance-engine')
from binance_client import BinanceClient

bc = BinanceClient()
symbol = "XAUUSDT"

print("=== Checking current state ===")
pos = bc.client.get_position_risk(symbol=symbol)
print(f"Position: {json.dumps(pos, indent=2)}")

orders = bc.client.get_open_orders(symbol=symbol)
print(f"Open orders: {json.dumps(orders, indent=2)}")

# Current price
ticker = bc.client.get_symbol_ticker(symbol=symbol)
current_px = float(ticker['price'])
print(f"Current price: {current_px}")

# Check account balance
acc = bc.client.get_account()
usdt_balance = 0
for bal in acc.get('assets', []):
    if bal.get('asset') == 'USDT':
        usdt_balance = float(bal.get('availableBalance', 0))
        break
print(f"USDT balance: {usdt_balance}")

# XAU LONG position
# Entry: current price or slightly above (better than TV 4048.82)
# TP1: 4068.0
# TP2: 4085.0
# TP3: 4100.0
# Hard stop: 4030.0 (below TV 4032.78)

entry_px = current_px + 0.5  # slightly above current
qty = 0.01  # 0.01 XAU
tp1_px = 4068.0
tp2_px = 4085.0
tp3_px = 4100.0
sl_px = 4030.0

print(f"\n=== Opening LONG position ===")
print(f"Qty: {qty}")
print(f"Entry: {entry_px}")
print(f"TP1: {tp1_px}")
print(f"TP2: {tp2_px}")
print(f"TP3: {tp3_px}")
print(f"HARD STOP: {sl_px}")

# Market LONG
print(f"\n=== Opening LONG position ===")
print(f"Buy {qty} @ MARKET")
try:
    r = bc.client.place_order(
        symbol=symbol,
        side='BUY',
        type='MARKET',
        quantity=qty
    )
    print(f"Market order result: {json.dumps(r, indent=2)}")
except Exception as e:
    print(f"Market order error: {e}")

time.sleep(2)

# Get updated position
pos = bc.client.get_position_risk(symbol=symbol)
for p in pos:
    if float(p.get('positionAmt', 0)) != 0:
        qty = abs(float(p.get('positionAmt', 0)))
        print(f"Position updated, qty: {qty}")
        break

# TP1 - 10% of qty
tp1_qty = round(qty * 0.1, 3)
# TP2 - 20% of qty
tp2_qty = round(qty * 0.2, 3)
# TP3 - 70% of qty
tp3_qty = round(qty * 0.7, 3)

time.sleep(1)

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
