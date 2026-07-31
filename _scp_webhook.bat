@echo off
scp -o StrictHostKeyChecking=no -o ConnectTimeout=30 "C:\Users\Administrator\Desktop\eth-webhook-server-main\webhook_parser.py" root@187.77.130.144:/home/trading/binance-engine/webhook_parser.py
echo SCP_WEBHOOK_DONE
