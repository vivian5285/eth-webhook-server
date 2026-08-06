#!/usr/bin/env python3
import sys
sys.path.insert(0, '/home/binanceB/binance-engine')

from binance_client import binance_client

# XAUUSDT my trades
try:
    trades = binance_client.client.get_account_trades(symbol='XAUUSDT', limit=20)
    print('XAUUSDT trades:', len(trades))
    for t in trades:
        ts = t['time']
        side = t['side']
        qty = t['qty']
        price = t['price']
        tid = t['id']
        print('  %s | %s %s @ %s | id=%s' % (ts, side, qty, price, tid))
except Exception as e:
    print('trades ERROR:', e)

# XAUUSDT all orders
try:
    orders = binance_client.client.get_all_orders(symbol='XAUUSDT', limit=30)
    print('XAUUSDT orders:', len(orders))
    for o in orders[-10:]:
        ts = o['time']
        side = o['side']
        qty = o['origQty']
        price = o['price']
        otype = o['type']
        status = o['status']
        oid = o['orderId']
        print('  %s | %s %s @ %s | %s | status=%s | id=%s' % (ts, side, qty, price, otype, status, oid))
except Exception as e:
    print('orders ERROR:', e)
