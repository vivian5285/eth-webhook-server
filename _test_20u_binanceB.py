#!/usr/bin/env python3
import requests
import json
import time

WEBHOOK_URL = "http://localhost:5007/webhook"
SECRET = "528586"

# ETH当前价格约1867美元
# 20美金测试，用小qty
payload = {
    "secret": SECRET,
    "action": "LONG",
    "symbol": "ETHUSDT",
    "price": 1867.69,
    "atr": 15.0,
    "stop_loss": 1852.69,
    "tp1": 1882.69,
    "tp2": 1892.69,
    "tp3": 1910.00,
    "tier": 1,
    "tier_label": "中",
    "bot_id": "test_20u",
    "ticker": "ETHUSDT",
    "side": "LONG",
    "adx_tier": 1,
    "entry_type": "OPEN",
    "leverage": 5.0,
    "qty_ratio": 1.0,
    "_schema": "v6.5.6"
}

print("=== 发送20U模拟LONG信号 ===")
print(f"URL: {WEBHOOK_URL}")
print(f"Payload: {json.dumps(payload, indent=2)}")
print()

try:
    resp = requests.post(WEBHOOK_URL, json=payload, timeout=30)
    print(f"Status: {resp.status_code}")
    print(f"Response: {resp.text}")
except Exception as e:
    print(f"Error: {e}")
