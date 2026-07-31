#!/bin/bash
# check_status.sh - 检查系统状态

echo "=== Health Check ==="
curl -s http://127.0.0.1:5003/health

echo ""
echo "=== API Throttle Status ==="
su - trading -c "cd ~/binance-engine && source venv/bin/activate && python3 -c 'from api_throttle import AccountThrottle; t = AccountThrottle._instances.get(\"binance\"); print(t.get_status() if t else \"No instance\")'" 2>/dev/null || echo "Cannot get throttle status"

echo ""
echo "=== Trading Pause Status ==="
grep -c "trading_paused.*true" ~/binance-engine/binance_vps_state_*.json 2>/dev/null || echo "Checking..."
