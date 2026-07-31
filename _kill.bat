@echo off
ssh -o StrictHostKeyChecking=no -o ConnectTimeout=30 root@187.77.130.144 "pkill -9 -f 'gunicorn.*5003' 2>/dev/null; pkill -9 -f 'position_supervisor_binance' 2>/dev/null; sleep 2; ss -tlnp | grep 5003 && echo PORT_STILL_OPEN || echo PORT_FREE"
