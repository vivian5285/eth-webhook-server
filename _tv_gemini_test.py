#!/usr/bin/env python3
import json, time, urllib.request

URL = "http://127.0.0.1:5003/webhook"

def send_signal(payload):
    req = urllib.request.Request(
        URL,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read())

tests = [
    {"secret":"528586","symbol":"ETHUSDT.P","action":"LONG","price":1902,"qty":0.0105,"stop_loss":1882,"tp1":1922,"tp2":1932,"tp3":1952,"atr":15,"leverage":5,"bar_index":1,"seq":1,"bot_id":"tv_gemini_test","_schema":"v6.5.6"},
    {"secret":"528586","symbol":"ETHUSDT.P","action":"SHORT","price":1900,"qty":0.0105,"stop_loss":1920,"tp1":1880,"tp2":1870,"tp3":1850,"atr":15,"leverage":5,"bar_index":2,"seq":2,"bot_id":"tv_gemini_test","_schema":"v6.5.6"},
    {"secret":"528586","symbol":"XAUUSDT.P","action":"LONG","price":4084,"qty":0.0049,"stop_loss":4064,"tp1":4104,"tp2":4114,"tp3":4134,"atr":20,"leverage":5,"bar_index":3,"seq":3,"bot_id":"tv_gemini_test","_schema":"v6.5.6"},
    {"secret":"528586","symbol":"XAUUSDT.P","action":"SHORT","price":4082,"qty":0.0049,"stop_loss":4102,"tp1":4062,"tp2":4052,"tp3":4032,"atr":20,"leverage":5,"bar_index":4,"seq":4,"bot_id":"tv_gemini_test","_schema":"v6.5.6"},
]

print("=" * 50)
print("TV Gemini 内测 20U 开单测试")
print("=" * 50)

for i, t in enumerate(tests, 1):
    sym = t["symbol"].replace(".P", "")
    direction = t["action"]
    print(f"\n=== 测试{i}: {sym} {direction} 20U ===")
    try:
        result = send_signal(t)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except Exception as e:
        print(f"错误: {e}")
    time.sleep(3)

print("\n" + "=" * 50)
print("最终状态检查")
print("=" * 50)
with urllib.request.urlopen("http://127.0.0.1:5003/health", timeout=10) as r:
    health = json.loads(r.read())
    print(json.dumps(health, indent=2, ensure_ascii=False))
