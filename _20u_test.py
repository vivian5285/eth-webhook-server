#!/usr/bin/env python3
"""20U最小资金全链路内测：ETH多空 + XAU多空 + 平仓，验证完整流水线逻辑"""
import json, urllib.request, time, sys

# TV webhook 路径（nginx反向代理）
BASE_URL = "http://187.77.130.144/binance/webhook"
SECRET   = "528586"

def send_signal(payload, label=""):
    data = json.dumps(payload).encode('utf-8')
    req = urllib.request.Request(BASE_URL, data=data,
                                 headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read().decode('utf-8')
            print(f"  [{label}] HTTP {resp.status} | {body}")
            return resp.status, body
    except Exception as e:
        print(f"  [{label}] FAIL: {e}")
        return 0, str(e)

# ====================== 阶段1: 平仓（确保空仓状态）======================
print("=" * 70)
print("【阶段1】平仓 - 确保系统处于空仓状态")
print("=" * 70)
for sym in ["ETHUSDT", "XAUUSDT"]:
    for action in ["CLOSE_QUICK_EXIT", "CLOSE_RSI_EXIT"]:
        print(f"\n>>> 平仓 {sym} [{action}]")
        send_signal({"secret": SECRET, "action": action, "symbol": sym,
                     "price": 1900.0, "atr": 15.0, "stop_loss": 0,
                     "tp1": 0, "tp2": 0, "tp3": 0}, f"{sym}-{action}")
        time.sleep(2)
time.sleep(5)

# ====================== 阶段2: 20U内测开仓 ======================
print("\n" + "=" * 70)
print("【阶段2】20U内测 - 开仓 ETH/XAU 多空各一单")
print("=" * 70)

scenarios = [
    # ETH LONG (中趋势 tier=1)
    ("ETH_LONG", {
        "secret": SECRET, "action": "LONG", "symbol": "ETHUSDT",
        "price": 1900.00, "atr": 15.00, "stop_loss": 1880.50,
        "tp1": 1920.00, "tp2": 1935.00, "tp3": 1955.00,
        "tier": 1, "bot_id": "Trillion_God_v7.2_VPSFinal"
    }),
    # ETH SHORT (弱趋势 tier=0)
    ("ETH_SHORT", {
        "secret": SECRET, "action": "SHORT", "symbol": "ETHUSDT",
        "price": 1903.14, "atr": 17.54, "stop_loss": 1920.68,
        "tp1": 1885.60, "tp2": 1871.57, "tp3": 1857.54,
        "tier": 0, "bot_id": "Trillion_God_v7.2_VPSFinal"
    }),
    # XAU LONG (强趋势 tier=2)
    ("XAU_LONG", {
        "secret": SECRET, "action": "LONG", "symbol": "XAUUSDT",
        "price": 3350.00, "atr": 18.00, "stop_loss": 3326.60,
        "tp1": 3378.00, "tp2": 3395.00, "tp3": 3415.00,
        "tier": 2, "bot_id": "Trillion_God_v7.2_VPSFinal"
    }),
    # XAU SHORT (中趋势 tier=1)
    ("XAU_SHORT", {
        "secret": SECRET, "action": "SHORT", "symbol": "XAUUSDT",
        "price": 3350.00, "atr": 20.00, "stop_loss": 3376.00,
        "tp1": 3320.00, "tp2": 3300.00, "tp3": 3275.00,
        "tier": 1, "bot_id": "Trillion_God_v7.2_VPSFinal"
    }),
]

for name, payload in scenarios:
    print(f"\n>>> 开仓 {name}")
    status, _ = send_signal(payload, name)
    if status != 200:
        print(f"  ! 开仓失败，退出测试")
        sys.exit(1)
    time.sleep(8)  # 等待流水线处理

print("\n" + "=" * 70)
print("【阶段3】等待60秒，观察订单状态...")
print("=" * 70)
time.sleep(60)

# ====================== 阶段4: 平仓 ======================
print("\n" + "=" * 70)
print("【阶段4】全平仓 - 结束测试")
print("=" * 70)
for name, payload in scenarios:
    sym = payload["symbol"]
    action = "CLOSE_QUICK_EXIT"
    print(f"\n>>> 平仓 {sym}")
    send_signal({"secret": SECRET, "action": action, "symbol": sym,
                 "price": payload["price"], "atr": payload["atr"],
                 "stop_loss": 0, "tp1": 0, "tp2": 0, "tp3": 0}, f"CLOSE-{sym}")
    time.sleep(5)

print("\n" + "=" * 70)
print("内测完成！请查看 VPS app.log 分析流水线逻辑")
print("=" * 70)
