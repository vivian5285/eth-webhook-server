#!/bin/bash
# Check version on both Binance accounts
echo "=== trading/binance-engine version ==="
su - trading -c 'cd ~/binance-engine && grep -m1 BINANCE_VPS_VERSION position_supervisor_binance.py'
echo ""

echo "=== binanceB/binance-engine version ==="
su - binanceB -c 'cd ~/binance-engine && grep -m1 BINANCE_VPS_VERSION position_supervisor_binance.py'
echo ""

echo "=== binanceB ports ==="
su - binanceB -c 'ss -tlnp | grep python3'
echo ""

echo "=== 5003 health (trading) ==="
curl -s --max-time 5 http://127.0.0.1:5003/health
echo ""

echo "=== 5007 health (binanceB) ==="
curl -s --max-time 5 http://127.0.0.1:5007/health
echo ""
