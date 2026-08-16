#!/usr/bin/env python3
"""Cancel orphaned orders on 5003 (trading account)"""
import sys
sys.path.insert(0, '/home/trading/binance-engine')

from binance_client import binance_client

for sym in ['XAUUSDT', 'BNBUSDT']:
    pos = binance_client.get_position(sym)
    size = float((pos or {}).get('size') or 0) if pos else 0
    if size > 0:
        print(f"{sym}: still has position {size}, skip cancel")
        continue

    all_orders = []
    try:
        o1 = binance_client.get_open_orders(sym)
        if o1:
            all_orders.extend(o1)
    except Exception as e:
        print(f"{sym} get_open_orders error: {e}")
    try:
        o2 = binance_client.get_open_orders(sym, include_algo=True)
        if o2:
            all_orders.extend(o2)
    except Exception as e:
        print(f"{sym} get_open_algo error: {e}")

    print(f"\n{sym}: position={size} (EMPTY), canceling {len(all_orders)} orphaned orders")
    for o in all_orders:
        oid = o.get('orderId') or o.get('algoId', '?')
        t = o.get('type') or o.get('orderType', '')
        px = o.get('stopPrice') or o.get('price', 'N/A')
        try:
            binance_client.cancel_order(sym, order_id=str(oid))
            print(f"  Cancelled {oid} | {t} | {px}")
        except Exception as e:
            print(f"  Failed cancel {oid}: {e}")
