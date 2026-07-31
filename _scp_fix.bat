@echo off
scp -o StrictHostKeyChecking=no -o ConnectTimeout=60 "C:\Users\Administrator\Desktop\eth-webhook-server-main\reentry_profiles.py" root@187.77.130.144:/home/trading/binance-engine/reentry_profiles.py
echo REENTRY_PROFILES_DONE
scp -o StrictHostKeyChecking=no -o ConnectTimeout=60 "C:\Users\Administrator\Desktop\eth-webhook-server-main\smart_reentry_engine.py" root@187.77.130.144:/home/trading/binance-engine/smart_reentry_engine.py
echo SMART_REENTRY_DONE
scp -o StrictHostKeyChecking=no -o ConnectTimeout=60 "C:\Users\Administrator\Desktop\eth-webhook-server-main\position_supervisor_binance.py" root@187.77.130.144:/home/trading/binance-engine/position_supervisor_binance.py
echo POSITION_SUPERVISOR_DONE
echo === ALL FILES UPLOADED ===
