#!/bin/bash
set -e
cd /home/trading/binance-engine
echo "[1] killing gunicorn..."
pkill -f "gunicorn.*binance" 2>/dev/null || true
sleep 3
pkill -9 -f "gunicorn.*binance" 2>/dev/null || true
sleep 2
echo "[2] port check..."
ss -tlnp | grep 5003 && echo "STILL_UP" || echo "PORT_FREE"
echo "[3] pull latest code from github..."
git pull origin main
echo "[4] syntax check..."
for f in api_throttle binance_client position_supervisor_binance dingtalk console_api account_profiles radar_reentry_mixin reentry_profiles webhook_parser breath_stop breath_profiles defense_profiles smart_reentry_engine order_idempotency pipeline_bridge; do
    python3 -m py_compile $f.py && echo "OK:$f" || echo "FAIL:$f"
done
echo "[5] start gunicorn..."
bash -c 'source venv/bin/activate && gunicorn -b 0.0.0.0:5003 --workers 1 --threads 10 --timeout 120 --graceful-timeout 30 --log-file logs/gunicorn_error.log --access-logfile logs/gunicorn_access.log --daemon app:app'
sleep 10
echo "[6] health check..."
curl -s --max-time 5 http://127.0.0.1:5003/health
echo ""
echo "[DONE]"
