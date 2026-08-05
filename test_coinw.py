#!/usr/bin/env python3
import requests
import json

# 测试coinw webhook
url = "http://127.0.0.1:5002/webhook"
data = {"secret": "528586", "action": "PING"}

print(f"Testing: {url}")
print(f"Data: {json.dumps(data)}")

try:
    r = requests.post(url, json=data, timeout=5)
    print(f"Status: {r.status_code}")
    print(f"Response: {r.text}")
except Exception as e:
    print(f"Error: {e}")
