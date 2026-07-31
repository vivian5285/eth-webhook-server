@echo off
scp -o StrictHostKeyChecking=no -o ConnectTimeout=20 "C:\Users\Administrator\Desktop\eth-webhook-server-main\binance_client.py" root@187.77.130.144:/home/trading/binance-engine/binance_client.py
scp -o StrictHostKeyChecking=no -o ConnectTimeout=20 "C:\Users\Administrator\Desktop\eth-webhook-server-main\position_supervisor_binance.py" root@187.77.130.144:/home/trading/binance-engine/position_supervisor_binance.py
scp -o StrictHostKeyChecking=no -o ConnectTimeout=20 "C:\Users\Administrator\Desktop\eth-webhook-server-main\dingtalk.py" root@187.77.130.144:/home/trading/binance-engine/dingtalk.py
scp -o StrictHostKeyChecking=no -o ConnectTimeout=20 "C:\Users\Administrator\Desktop\eth-webhook-server-main\radar_reentry_mixin.py" root@187.77.130.144:/home/trading/binance-engine/radar_reentry_mixin.py
scp -o StrictHostKeyChecking=no -o ConnectTimeout=20 "C:\Users\Administrator\Desktop\eth-webhook-server-main\webhook_parser.py" root@187.77.130.144:/home/trading/binance-engine/webhook_parser.py
scp -o StrictHostKeyChecking=no -o ConnectTimeout=20 "C:\Users\Administrator\Desktop\eth-webhook-server-main\reentry_profiles.py" root@187.77.130.144:/home/trading/binance-engine/reentry_profiles.py
echo SCP_DONE
