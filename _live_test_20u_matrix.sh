#!/bin/bash
# 20U 模拟TV测试脚本
# VPS上执行: ./live_test_20u_matrix.sh

WEBHOOK_SECRET="528586"
WEBHOOK_URL="http://127.0.0.1:5003/webhook"

echo "=========================================="
echo "20U 模拟TV开单测试"
echo "=========================================="

# 1. ETHUSDT LONG
echo ""
echo "=== 测试1: ETHUSDT LONG ==="
curl -s -X POST "$WEBHOOK_URL" \
  -H "Content-Type: application/json" \
  -d '{
    "secret": "528586",
    "action": "LONG",
    "symbol": "ETHUSDT.P",
    "price": 1920.00,
    "atr": 15.0,
    "stop_loss": 1905.00,
    "tp1": 1935.00,
    "tp2": 1945.00,
    "tp3": 1960.00,
    "tier": 1,
    "tier_label": "中",
    "bot_id": "test_20u",
    "ticker": "ETHUSDT.P",
    "side": "LONG",
    "adx_tier": 1,
    "entry_type": "OPEN",
    "leverage": 5.0,
    "qty_ratio": 1.0,
    "_schema": "v6.5.6"
  }'
echo ""
sleep 3

# 2. XAUUSDT LONG
echo ""
echo "=== 测试2: XAUUSDT LONG ==="
curl -s -X POST "$WEBHOOK_URL" \
  -H "Content-Type: application/json" \
  -d '{
    "secret": "528586",
    "action": "LONG",
    "symbol": "XAUUSDT.P",
    "price": 4080.00,
    "atr": 20.0,
    "stop_loss": 4060.00,
    "tp1": 4100.00,
    "tp2": 4110.00,
    "tp3": 4130.00,
    "tier": 1,
    "tier_label": "中",
    "bot_id": "test_20u",
    "ticker": "XAUUSDT.P",
    "side": "LONG",
    "adx_tier": 1,
    "entry_type": "OPEN",
    "leverage": 5.0,
    "qty_ratio": 1.0,
    "_schema": "v6.5.6"
  }'
echo ""
sleep 3

echo ""
echo "=== 等待5秒观察持仓 ==="
sleep 5

# 3. ETHUSDT SHORT (平多反手)
echo ""
echo "=== 测试3: ETHUSDT SHORT (平多反手) ==="
curl -s -X POST "$WEBHOOK_URL" \
  -H "Content-Type: application/json" \
  -d '{
    "secret": "528586",
    "action": "SHORT",
    "symbol": "ETHUSDT.P",
    "price": 1910.00,
    "atr": 15.0,
    "stop_loss": 1925.00,
    "tp1": 1895.00,
    "tp2": 1885.00,
    "tp3": 1870.00,
    "tier": 1,
    "tier_label": "中",
    "bot_id": "test_20u",
    "ticker": "ETHUSDT.P",
    "side": "SHORT",
    "adx_tier": 1,
    "entry_type": "OPEN",
    "leverage": 5.0,
    "qty_ratio": 1.0,
    "_schema": "v6.5.6"
  }'
echo ""
sleep 3

# 4. XAUUSDT SHORT (平多反手)
echo ""
echo "=== 测试4: XAUUSDT SHORT (平多反手) ==="
curl -s -X POST "$WEBHOOK_URL" \
  -H "Content-Type: application/json" \
  -d '{
    "secret": "528586",
    "action": "SHORT",
    "symbol": "XAUUSDT.P",
    "price": 4070.00,
    "atr": 20.0,
    "stop_loss": 4090.00,
    "tp1": 4050.00,
    "tp2": 4040.00,
    "tp3": 4020.00,
    "tier": 1,
    "tier_label": "中",
    "bot_id": "test_20u",
    "ticker": "XAUUSDT.P",
    "side": "SHORT",
    "adx_tier": 1,
    "entry_type": "OPEN",
    "leverage": 5.0,
    "qty_ratio": 1.0,
    "_schema": "v6.5.6"
  }'
echo ""
sleep 3

echo ""
echo "=========================================="
echo "测试完成，检查最终状态..."
echo "=========================================="
curl -s http://127.0.0.1:5003/health | python3 -m json.tool

echo ""
echo "=== ETHUSDT 状态 ==="
cat /home/trading/binance-engine/binance_vps_state_ETHUSDT.json | python3 -m json.tool

echo ""
echo "=== XAUUSDT 状态 ==="
cat /home/trading/binance-engine/binance_vps_state_XAUUSDT.json | python3 -m json.tool
