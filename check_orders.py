import sys
sys.path.insert(0, '/home/trading/binance-engine')

from binance.client import Client
from account_profiles import get_account_config

cfg = get_account_config()
client = Client(cfg['api_key'], cfg['api_secret'])

# XAUUSDT my trades
try:
    trades = client.get_account_trades(symbol='XAUUSDT', limit=20)
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
    orders = client.get_all_orders(symbol='XAUUSDT', limit=30)
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
