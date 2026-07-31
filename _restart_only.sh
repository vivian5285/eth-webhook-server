#!/bin/bash
cd /home/trading/binance-engine
pkill -f gunicorn 2>/dev/null || true
sleep 3
pkill -9 -f gunicorn 2>/dev/null || true
sleep 2
bash -c 'source venv/bin/activate && gunicorn -b 0.0.0.0:5003 --workers 1 --threads 10 --timeout 120 --graceful-timeout 30 --log-file logs/gunicorn_error.log --access-logfile logs/gunicorn_access.log --daemon app:app'
sleep 8
curl -s --max-time 5 http://127.0.0.1:5003/health
