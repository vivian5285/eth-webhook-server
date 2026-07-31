#!/usr/bin/env python3
"""Manual ETH limit buy order placement"""
import sys
sys.path.insert(0, '/home/trading/binance-engine')
from binance_client import BinanceClient

bc = BinanceClient()
symbol = "ETHUSDT"

print("=== Placing ETH Limit BUY Order ===")

# Get current price
try:
    ticker = bc.client.futures_symbol_ticker(symbol=symbol)
    current_px = float(ticker['price'])
    print(f"Current ETH price: {current_px}")
except Exception as e:
    print(f"Failed to get price: {e}")
    current_px = None

# Calculate quantity
principal = 1595.6761178
leverage = 5.0
limit_price = 1910.0
qty = round(principal * leverage / limit_price, 3)
print(f"Quantity: {qty} ETH @ {limit_price}")

# Place limit long order (reduceOnly=False for opening new position)
try:
    order = bc.client.futures_create_order(
        symbol=symbol,
        side='BUY',
        type='LIMIT',
        quantity=qty,
        price=limit_price,
        timeInForce='GTC',
        reduceOnly=False
    )
    print(f"Order placed: {order}")
except Exception as e:
    print(f"Order error: {e}")
    # Try GTX (post-only) in case GTC fails
    try:
        order = bc.client.futures_create_order(
            symbol=symbol,
            side='BUY',
            type='LIMIT',
            quantity=qty,
            price=limit_price,
            timeInForce='GTX',
            reduceOnly=False
        )
        print(f"Order placed (GTX): {order}")
    except Exception as e2:
        print(f"GTX also failed: {e2}")

print("=== Done ===")
