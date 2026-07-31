@echo off
ssh -o StrictHostKeyChecking=no -o ConnectTimeout=30 -o BatchMode=yes root@187.77.130.144 "cd /home/trading/binance-engine && echo ERR_LOG && tail -50 logs/gunicorn_error.log 2>/dev/null && echo PORT_NOW && ss -tlnp | grep 5003"
