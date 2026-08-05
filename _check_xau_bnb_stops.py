#!/usr/bin/env python3
"""检查XAU和BNB的当前状态"""
import sys
import os
sys.path.insert(0, '/home/trading/binance-engine')

from binance_client import binance_client
import json

def check_orders(symbol):
    print(f"\n{'='*60}")
    print(f"=== {symbol} Open Orders (5003) ===")
    try:
        orders = binance_client.get_open_orders(symbol)
        if orders == 'QUERY_FAILED':
            print("QUERY_FAILED")
        elif not orders:
            print("No open orders")
        else:
            for o in orders:
                print(f"  {o.get('orderId', o.get('algoId', '?'))} | {o.get('type','?')} | {o.get('side','?')} | price={o.get('price','N/A')} | stop={o.get('stopPrice','N/A')} | closePos={o.get('closePosition','N/A')} | qty={o.get('origQty','N/A')}")
    except Exception as e:
        print(f"Error: {e}")

    print(f"\n=== {symbol} Algo Orders (STOP/STOP_MARKET) ===")
    try:
        algos = binance_client.get_open_orders(symbol, include_algo=True)
        if algos == 'QUERY_FAILED':
            print("QUERY_FAILED")
        elif not algos:
            print("No algo orders")
        else:
            for o in algos:
                t = o.get('type', o.get('orderType', '?'))
                if 'STOP' in str(t).upper():
                    print(f"  {o.get('orderId', o.get('algoId', '?'))} | {t} | {o.get('side','?')} | stop={o.get('stopPrice','N/A')} | closePos={o.get('closePosition','N/A')} | qty={o.get('origQty','N/A')} | reduce={o.get('reduceOnly','N/A')}")
    except Exception as e:
        print(f"Error: {e}")

    print(f"\n=== {symbol} Position ===")
    try:
        pos = binance_client.get_position(symbol)
        if pos:
            print(f"  Size: {pos.get('size','?')} | Entry: {pos.get('entryPrice','?')} | PnL: {pos.get('unrealizedPnl','?')}")
        else:
            print("  No position")
    except Exception as e:
        print(f"Error: {e}")

for sym in ['XAUUSDT', 'BNBUSDT']:
    check_orders(sym)
    print()
