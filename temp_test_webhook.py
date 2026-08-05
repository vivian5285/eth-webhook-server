import urllib.request, json
payload = {"secret": "528586", "action": "buy", "ticker": "ETHUSDT", "qty": 0.011, "price": 1850, "stop_loss": 1800, "atr": 13.4, "adx": 25}
data = json.dumps(payload).encode()
req = urllib.request.Request("http://127.0.0.1:5007/webhook", data=data, headers={"Content-Type": "application/json"}, method="POST")
try:
    with urllib.request.urlopen(req, timeout=30) as resp:
        print(resp.read().decode())
except Exception as e:
    print("ERROR:", e)
