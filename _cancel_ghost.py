#!/usr/bin/env python3
"""检查并取消幽灵止损单"""
import sys
import os
import time
sys.path.insert(0, '/home/trading/binance-engine')

from binance_client import binance_client

def check_and_cancel(symbol):
    print(f"\n{'='*60}")
    print(f"=== {symbol} ===")

    # 1. Position
    try:
        pos = binance_client.get_position(symbol)
        if pos:
            print(f"  Position: size={pos.get('size','?')} entry={pos.get('entryPrice','?')}")
        else:
            print(f"  Position: 空仓")
    except Exception as e:
        print(f"  Position error: {e}")

    # 2. All open orders
    all_orders = []
    try:
        orders = binance_client.get_open_orders(symbol)
        if orders:
            all_orders.extend(orders)
    except Exception as e:
        print(f"  get_open_orders error: {e}")

    try:
        algos = binance_client.get_open_orders(symbol, include_algo=True) or []
        all_orders.extend(algos)
    except Exception as e:
        print(f"  get_open_algo error: {e}")

    stop_orders = []
    limit_orders = []
    for o in all_orders:
        t = str(o.get('type', o.get('orderType', ''))).upper()
        if 'STOP' in t:
            stop_orders.append(o)
        elif 'LIMIT' in t:
            limit_orders.append(o)

    print(f"\n  LIMIT orders ({len(limit_orders)}):")
    for o in limit_orders:
        oid = o.get('orderId', o.get('algoId', '?'))
        print(f"    {oid} | {o.get('side')} | price={o.get('price','N/A')} | qty={o.get('origQty','N/A')}")

    print(f"\n  STOP orders ({len(stop_orders)}):")
    for o in stop_orders:
        oid = o.get('orderId', o.get('algoId', '?'))
        close = o.get('closePosition', 'N/A')
        reduce = o.get('reduceOnly', 'N/A')
        stop_px = o.get('stopPrice', 'N/A')
        print(f"    {oid} | stop={stop_px} | closePos={close} | reduce={reduce} | qty={o.get('origQty','N/A')}")

    return stop_orders, limit_orders

for sym in ['XAUUSDT', 'BNBUSDT']:
    check_and_cancel(sym)
