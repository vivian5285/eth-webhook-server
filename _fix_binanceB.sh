#!/bin/bash
# Fix binanceB/binance-engine and deploy
set -e

echo "=== [1] Git pull binanceB/binance-engine ==="
su - binanceB -c "cd ~/binance-engine && git fetch origin && git reset --hard origin/main && git log -1 --oneline"
echo ""

echo "=== [2] Fix gunicorn.conf.py bind to 5007 ==="
su - binanceB -c "cd ~/binance-engine && sed -i 's/bind\s*=\s*\"[^\"]*500[0-9]\"/bind = \"0.0.0.0:5007\"/' gunicorn.conf.py && grep bind gunicorn.conf.py"
echo ""

echo "=== [3] Kill old gunicorn processes ==="
su - binanceB -c "pkill -f 'binance-engine.*gunicorn' 2>/dev/null || true"
sleep 2
echo ""

echo "=== [4] Start gunicorn on 5007 ==="
su - binanceB -c "cd ~/binance-engine && source venv/bin/activate && nohup gunicorn -b 0.0.0.0:5007 --workers 1 --threads 1 --timeout 120 --graceful-timeout 30 --log-file logs/gunicorn_error.log --access-logfile logs/gunicorn_access.log --daemon app:app 2>/dev/null"
sleep 5
echo ""

echo "=== [5] Check 5007 health ==="
curl -s --max-time 5 http://127.0.0.1:5007/health | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('version','?'), d.get('status','?'))"
echo ""

echo "=== All done ==="
