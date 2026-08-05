#!/bin/bash
echo "=== binanceB/binance-engine git log ==="
su - binanceB -c 'cd ~/binance-engine && git log -3 --oneline'
echo ""
echo "=== binanceB/binance-engine gunicorn.conf.py bind ==="
su - binanceB -c 'cd ~/binance-engine && grep -i bind gunicorn.conf.py'
echo ""
echo "=== binanceB/eth-webhook-server git log -3 ==="
su - binanceB -c 'cd ~/eth-webhook-server && git log -3 --oneline'
echo ""
echo "=== trading/binance-engine gunicorn error log (last 20) ==="
su - trading -c 'cd ~/binance-engine && tail -20 logs/gunicorn_error.log 2>/dev/null || echo NO_ERROR_LOG'
echo ""
echo "=== 5003 process detail ==="
su - trading -c 'ps aux | grep gunicorn | grep 5003'
