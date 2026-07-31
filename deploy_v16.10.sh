#!/bin/bash
# deploy_v16.10.sh - Run on VPS as: bash deploy_v16.10.sh
set -e
VENV="/home/trading/binance-engine/venv/bin/activate"
APP="/home/trading/binance-engine"
LOG="$APP/logs"
PID_FILE="$LOG/gunicorn_binance.pid"

echo "[1/6] Stopping gunicorn..."
pkill -f gunicorn 2>/dev/null || true
sleep 2

echo "[2/6] Stopping any remaining workers..."
pkill -9 -f 'gunicorn.*binance' 2>/dev/null || true
sleep 1

echo "[3/6] Copying fixed files..."
cp /tmp/api_throttle.py "$APP/api_throttle.py"
cp /tmp/binance_client.py "$APP/binance_client.py"
cp /tmp/position_supervisor_binance.py "$APP/position_supervisor_binance.py"
cp /tmp/radar_reentry_mixin.py "$APP/radar_reentry_mixin.py" 2>/dev/null || true
cp /tmp/smart_reentry_engine.py "$APP/smart_reentry_engine.py"
cp /tmp/reentry_profiles.py "$APP/reentry_profiles.py" 2>/dev/null || true
cp /tmp/webhook_parser.py "$APP/webhook_parser.py" 2>/dev/null || true

echo "[4/6] Verifying Python syntax..."
source "$VENV"
python3 -m py_compile "$APP/api_throttle.py" && echo "  api_throttle.py OK"
python3 -m py_compile "$APP/binance_client.py" && echo "  binance_client.py OK"
python3 -m py_compile "$APP/position_supervisor_binance.py" && echo "  supervisor OK"
python3 -m py_compile "$APP/radar_reentry_mixin.py" && echo "  radar OK"
python3 -m py_compile "$APP/smart_reentry_engine.py" && echo "  reentry OK"
python3 -m py_compile "$APP/reentry_profiles.py" && echo "  profiles OK"
python3 -m py_compile "$APP/webhook_parser.py" && echo "  parser OK"

echo "[5/6] Starting gunicorn..."
cd "$APP"
nohup source "$VENV" && gunicorn \
  -w 4 -b 0.0.0.0:5003 --timeout 120 \
  --pid "$PID_FILE" \
  --log-file "$LOG/gunicorn_error.log" \
  app:app > /dev/null 2>&1 &
GUNICORN_PID=$!
echo "Gunicorn PID: $GUNICORN_PID"
sleep 5

echo "[6/6] Health check..."
for i in 1 2 3 4 5; do
  sleep 2
  HEALTH=$(curl -s http://127.0.0.1:5003/health 2>/dev/null || echo "FAIL")
  echo "  Attempt $i: $HEALTH"
  if echo "$HEALTH" | grep -q '"status":"ok"'; then
    echo "SUCCESS: System is healthy!"
    exit 0
  fi
done
echo "WARNING: Health check did not return OK. Check logs."
tail -20 "$LOG/gunicorn_error.log"
exit 1
