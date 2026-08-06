#!/usr/bin/env python3
import sys
import os
sys.path.insert(0, '/home/binanceB/binance-engine')

from binance.client import Client
from dotenv import load_dotenv

load_dotenv()

api_key = os.getenv('BINANCE_API_KEY', '')
api_secret = os.getenv('BINANCE_API_SECRET', '')

if not api_key or not api_secret:
    print('ERROR: BINANCE_API_KEY or BINANCE_API_SECRET not set')
    sys.exit(1)

client = Client(api_key, api_secret)

# XAUUSDT my trades (futures)
try:
    trades = client.futures_account_trades(symbol='XAUUSDT', limit=20)
    print('XAUUSDT trades:', len(trades))
    for t in trades[:5]:
        ts = t['time']
        side = t['side']
        qty = t['qty']
        price = t['price']
        tid = t['id']
        print('  %s | %s %s @ %s | id=%s' % (ts, side, qty, price, tid))
except Exception as e:
    print('trades ERROR:', e)

# XAUUSDT all orders (futures)
try:
    orders = client.futures_get_all_orders(symbol='XAUUSDT', limit=30)
    print('XAUUSDT orders:', len(orders))
    for o in orders[-5:]:
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
