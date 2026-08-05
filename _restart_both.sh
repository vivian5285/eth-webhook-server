#!/bin/bash
# restart both accounts after git pull
# Port 5003 = trading user (Binance account A)
# Port 5007 = binanceB user (Binance account B)
ACCTS=("trading:5003" "binanceB:5007")
for acct in "${ACCTS[@]}"; do
  user="${acct%%:*}"
  port="${acct##*:}"
  echo "=== Restarting $user on port $port ==="
  su - "$user" -c "cd ~/binance-engine && pkill -f gunicorn 2>/dev/null; sleep 2 && source venv/bin/activate && nohup gunicorn -b 0.0.0.0:$port --workers 1 --threads 1 --timeout 120 --graceful-timeout 30 --log-file logs/gunicorn_error.log --access-logfile logs/gunicorn_access.log --daemon app:app && sleep 4 && curl -s http://127.0.0.1:$port/health"
  echo ""
done
echo "=== All done ==="
