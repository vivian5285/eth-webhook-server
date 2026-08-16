#!/usr/bin/env python3
"""检查5007端口binanceB账户"""
import sys
import os
sys.path.insert(0, '/home/binanceB/binance-engine')

from binance_client import binance_client

def check_orders(symbol, label=""):
    print(f"\n{'='*60}")
    print(f"=== {symbol} {label} ===")
    try:
        orders = binance_client.get_open_orders(symbol)
        algos = binance_client.get_open_orders(symbol, include_algo=True) if hasattr(binance_client, 'get_open_orders') else []
    except Exception as e:
        print(f"Error: {e}")
        return

    if not orders:
        print("No open orders")
    else:
        for o in orders:
            t = o.get('type', o.get('orderType', '?'))
            stop = o.get('stopPrice', o.get('stop_px', 'N/A'))
            close = o.get('closePosition', 'N/A')
            reduce = o.get('reduceOnly', 'N/A')
            oid = o.get('orderId', o.get('algoId', '?'))
            print(f"  {oid} | {t} | {o.get('side','?')} | price={o.get('price','N/A')} | stop={stop} | closePos={close} | reduce={reduce} | qty={o.get('origQty','N/A')}")

    print(f"\n--- Algos (STOP) ---")
    try:
        algos = binance_client.get_open_orders(symbol, include_algo=True) or []
    except:
        algos = []
    if not algos:
        print("No algo orders")
    else:
        for o in algos:
            t = o.get('type', o.get('orderType', '?'))
            if 'STOP' in str(t).upper():
                oid = o.get('orderId', o.get('algoId', '?'))
                print(f"  {oid} | {t} | {o.get('side','?')} | stop={o.get('stopPrice','N/A')} | closePos={o.get('closePosition','N/A')} | reduce={o.get('reduceOnly','N/A')} | qty={o.get('origQty','N/A')}")

    print(f"\n--- Position ---")
    try:
        pos = binance_client.get_position(symbol)
        if pos:
            print(f"  Size: {pos.get('size','?')} | Entry: {pos.get('entryPrice','?')} | PnL: {pos.get('unrealizedPnl','?')} | UnPnl: {pos.get('unrealizedPnl','?')}")
        else:
            print("  No position")
    except Exception as e:
        print(f"  Pos Error: {e}")

for sym in ['XAUUSDT', 'BNBUSDT']:
    check_orders(sym)
