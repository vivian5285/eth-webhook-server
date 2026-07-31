#!/usr/bin/env python3
from binance_client import BinanceClient
c = BinanceClient()
print("equity:", c.get_total_equity())
print("wallet:", c.get_principal_wallet_balance())
print("available:", c.get_available_balance())
