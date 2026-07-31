@echo off
echo === SCP FILES ===
scp -o StrictHostKeyChecking=no -o ConnectTimeout=60 "C:\Users\Administrator\Desktop\eth-webhook-server-main\reentry_profiles.py" root@187.77.130.144:/home/trading/binance-engine/reentry_profiles.py
scp -o StrictHostKeyChecking=no -o ConnectTimeout=60 "C:\Users\Administrator\Desktop\eth-webhook-server-main\smart_reentry_engine.py" root@187.77.130.144:/home/trading/binance-engine/smart_reentry_engine.py
scp -o StrictHostKeyChecking=no -o ConnectTimeout=60 "C:\Users\Administrator\Desktop\eth-webhook-server-main\position_supervisor_binance.py" root@187.77.130.144:/home/trading/binance-engine/position_supervisor_binance.py
echo FILES_SENT
echo === RESTART TRADING SERVICE ===
ssh -o StrictHostKeyChecking=no -o ConnectTimeout=30 -o BatchMode=yes root@187.77.130.144 "cd /home/trading/binance-engine && echo PORT_BEFORE && ss -tlnp | grep 5003 || echo FREE && echo KILL_OLD && pkill -9 -u trading gunicorn 2>/dev/null; sleep 2 && echo STARTING && bash -c 'source venv/bin/activate && gunicorn -w 1 --threads 1 -b 0.0.0.0:5003 --timeout 300 --log-file logs/gunicorn_error.log --access-logfile logs/gunicorn_access.log --daemon app:app' && sleep 8 && echo PROCESSES && ps aux | grep gunicorn | grep trading && echo HEALTH && curl -s --max-time 5 http://127.0.0.1:5003/health"
echo === ALL DONE ===
