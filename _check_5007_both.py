#!/usr/bin/env python3
"""检查5007端口binanceB账户"""
import sys
import os
sys.path.insert(0, '/home/binanceB/binance-engine')

from binance_client import binance_client

def check_orders(symbol):
    print(f"\n{'='*60}")
    print(f"=== {symbol} (5007 binanceB) ===")
    all_orders = []
    try:
        orders = binance_client.get_open_orders(symbol)
        if orders:
            all_orders.extend(orders)
    except Exception as e:
        print(f"get_open_orders error: {e}")

    try:
        algos = binance_client.get_open_orders(symbol, include_algo=True)
        if algos:
            for o in algos:
                t = str(o.get('type', o.get('orderType', ''))).upper()
                if 'STOP' in t:
                    all_orders.append(o)
    except Exception as e:
        print(f"get_open_orders(algo) error: {e}")

    if not all_orders:
        print("No open orders")
    else:
        for o in all_orders:
            t = o.get('type', o.get('orderType', '?'))
            stop = o.get('stopPrice', o.get('stop_px', 'N/A'))
            close = o.get('closePosition', 'N/A')
            reduce = o.get('reduceOnly', 'N/A')
            oid = o.get('orderId', o.get('algoId', '?'))
            print(f"  {oid} | {t} | {o.get('side','?')} | price={o.get('price','N/A')} | stop={stop} | closePos={close} | reduce={reduce} | qty={o.get('origQty','N/A')}")

    print(f"\n--- Position ---")
    try:
        pos = binance_client.get_position(symbol)
        if pos:
            print(f"  Size: {pos.get('size','?')} | Entry: {pos.get('entryPrice','?')} | UnPnl: {pos.get('unrealizedPnl','?')}")
        else:
            print("  No position")
    except Exception as e:
        print(f"  Pos Error: {e}")

for sym in ['XAUUSDT', 'BNBUSDT']:
    check_orders(sym)
