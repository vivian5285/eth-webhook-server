#!/usr/bin/env python3
import json
import sys
sys.path.insert(0, '/home/trading/binance-engine')
from binance_client import BinanceClient

bc = BinanceClient()

print("=== XAU未成交订单 ===")
xau_orders = bc.client.get_open_orders(symbol='XAUUSDT')
print(json.dumps(xau_orders, indent=2))
print()

print("=== ETH未成交订单 ===")
eth_orders = bc.client.get_open_orders(symbol='ETHUSDT')
print(json.dumps(eth_orders, indent=2))
print()

print("=== ETH持仓 ===")
pos = bc.client.get_position_risk(symbol='ETHUSDT')
print(json.dumps(pos, indent=2))
print()

print("=== 账户余额 ===")
acc = bc.client.get_account()
print(f"可用: {acc['availableBalance']} USDT")
print(f"未实现盈亏: {acc['totalUnrealizedProfit']} USDT")
print()

print("=== XAU持仓 ===")
xau_pos = bc.client.get_position_risk(symbol='XAUUSDT')
print(json.dumps(xau_pos, indent=2))
