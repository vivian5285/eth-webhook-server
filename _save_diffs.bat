@echo off
git -C "C:\Users\Administrator\Desktop\eth-webhook-server-main" diff binance_client.py > "C:\Users\Administrator\Desktop\eth-webhook-server-main\_diff_binance.txt"
git -C "C:\Users\Administrator\Desktop\eth-webhook-server-main" diff position_supervisor_binance.py > "C:\Users\Administrator\Desktop\eth-webhook-server-main\_diff_supervisor.txt"
git -C "C:\Users\Administrator\Desktop\eth-webhook-server-main" diff dingtalk.py > "C:\Users\Administrator\Desktop\eth-webhook-server-main\_diff_dingtalk.txt"
git -C "C:\Users\Administrator\Desktop\eth-webhook-server-main" diff radar_reentry_mixin.py > "C:\Users\Administrator\Desktop\eth-webhook-server-main\_diff_radar.txt"
git -C "C:\Users\Administrator\Desktop\eth-webhook-server-main" diff reentry_profiles.py > "C:\Users\Administrator\Desktop\eth-webhook-server-main\_diff_reentry.txt"
git -C "C:\Users\Administrator\Desktop\eth-webhook-server-main" diff webhook_parser.py > "C:\Users\Administrator\Desktop\eth-webhook-server-main\_diff_webhook.txt"
echo DONE
