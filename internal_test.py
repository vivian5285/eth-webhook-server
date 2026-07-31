#!/usr/bin/env python3
"""内测模拟：ETH多空 + XAU多空，按VPS规格v1.0发送真实格式信号"""
import json
import urllib.request
import time

SECRET = "528586"
BASE_URL = "http://127.0.0.1:5003/webhook"

def send_signal(payload):
    data = json.dumps(payload).encode('utf-8')
    req = urllib.request.Request(
        BASE_URL,
        data=data,
        headers={'Content-Type': 'application/json'}
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, resp.read().decode('utf-8')
    except Exception as e:
        return 0, str(e)

# ============================================================
# 场景1：ETH LONG（多单），tier=1（中趋势）
# ============================================================
eth_long = {
    "secret": SECRET,
    "action": "LONG",
    "symbol": "ETHUSDT",
    "price": 1900.00,
    "atr": 15.00,
    "stop_loss": 1880.50,  # 距入场19.5 ≈ 1.3倍ATR（中趋势）
    "tp1": 1920.00,
    "tp2": 1935.00,
    "tp3": 1955.00,
    "tier": 1,
    "bot_id": "Trillion_God_v7.2_VPSFinal"
}

# ============================================================
# 场景2：ETH SHORT（空单），tier=0（弱趋势）- 按真实TV信号字段
# ============================================================
eth_short = {
    "secret": SECRET,
    "action": "SHORT",
    "symbol": "ETHUSDT",
    "price": 1903.14,
    "atr": 17.5387324068,
    "stop_loss": 1920.6787324068,
    "tp1": 1885.6012675932,
    "tp2": 1871.5702816677,
    "tp3": 1857.5392957422,
    "tier": 0,
    "bot_id": "Trillion_God_v7.2_VPSFinal"
}

# ============================================================
# 场景3：XAU LONG（多单），tier=2（强趋势）
# ============================================================
xau_long = {
    "secret": SECRET,
    "action": "LONG",
    "symbol": "XAUUSDT",
    "price": 3350.00,
    "atr": 18.00,
    "stop_loss": 3326.60,  # 距入场23.4 ≈ 1.3倍ATR（中趋势）改为tier=2
    "tp1": 3378.00,
    "tp2": 3395.00,
    "tp3": 3415.00,
    "tier": 2,
    "bot_id": "Trillion_God_v7.2_VPSFinal"
}

# ============================================================
# 场景4：XAU SHORT（空单），tier=1（中趋势）
# ============================================================
xau_short = {
    "secret": SECRET,
    "action": "SHORT",
    "symbol": "XAUUSDT",
    "price": 3350.00,
    "atr": 20.00,
    "stop_loss": 3376.00,  # 距入场26 ≈ 1.3倍ATR
    "tp1": 3320.00,
    "tp2": 3300.00,
    "tp3": 3275.00,
    "tier": 1,
    "bot_id": "Trillion_God_v7.2_VPSFinal"
}

scenarios = [
    ("ETH LONG (tier=1)", eth_long),
    ("ETH SHORT (tier=0)", eth_short),
    ("XAU LONG (tier=2)", xau_long),
    ("XAU SHORT (tier=1)", xau_short),
]

print("=" * 70)
print("VPS系统内测 - 20U最小资金模拟盘")
print("=" * 70)

for name, payload in scenarios:
    print(f"\n[{name}]")
    print(f"  Action: {payload['action']}, Symbol: {payload['symbol']}")
    print(f"  Price: {payload['price']}, ATR: {payload['atr']:.4f}")
    print(f"  Stop: {payload['stop_loss']}, Tier: {payload['tier']}")
    print(f"  TPs: {payload['tp1']} / {payload['tp2']} / {payload['tp3']}")

    status, body = send_signal(payload)
    print(f"  HTTP {status}: {body}")

    # 间隔避免瞬间并发
    time.sleep(3)

print("\n" + "=" * 70)
print("所有信号已发送，等待VPS处理...")
print("=" * 70)