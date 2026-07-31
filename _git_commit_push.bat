@echo off
git -C "C:\Users\Administrator\Desktop\eth-webhook-server-main" add binance_client.py position_supervisor_binance.py dingtalk.py radar_reentry_mixin.py webhook_parser.py reentry_profiles.py
git -C "C:\Users\Administrator\Desktop\eth-webhook-server-main" commit -m "feat(v16.9.2): IP冷却重试优化 + 防御查单 + 新重入钉钉通知"
git -C "C:\Users\Administrator\Desktop\eth-webhook-server-main" push origin main
echo GIT_PUSH_DONE
