#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from binance_client import binance_client

print("========================================")
print("🔍 正在直连币安金库 (动态总权益版)...")
print("========================================")

try:
    # 这里调用的就是咱们刚升级的 marginBalance 动态权益
    balance = binance_client.get_available_balance()
    price = binance_client.get_current_price()
    
    print(f"💰 币安合约账户动态总权益: {balance:.2f} USDT")
    print(f"📈 当前 ETH 实时盘口现价: {price:.2f} USDT")
except Exception as e:
    print(f"❌ 查询失败，请检查 API 或网络: {e}")
    
print("========================================")
