import sys
sys.path.insert(0, '/home/trading/binance-engine')
from binance_api import BinanceAPIClient

c = BinanceAPIClient()
orders = c.get_open_orders('XAUUSDT')
if orders:
    for o in orders:
        print(f"{o['symbol']} {o['side']} {o['type']} qty={o['origQty']} price={o.get('price','N/A')} stop={o.get('stopPrice','N/A')} id={o['orderId']}")
else:
    print('No open orders for XAUUSDT')

# Also check position
pos = c.get_position('XAUUSDT')
print(f"Position: {pos}")
