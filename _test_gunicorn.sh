#!/bin/bash
cd /home/trading/binance-engine
source venv/bin/activate
fuser -k 5003/tcp || true
sleep 2
python -c "import app; print('app.py OK')"
/home/trading/binance-engine/venv/bin/gunicorn -w 1 --threads 1 -b 0.0.0.0:5003 app:app 2>&1
