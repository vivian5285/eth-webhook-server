@echo off
ssh -o StrictHostKeyChecking=no -o ConnectTimeout=20 root@187.77.130.144 "cd /home/trading/binance-engine && git add binance_client.py position_supervisor_binance.py dingtalk.py radar_reentry_mixin.py webhook_parser.py reentry_profiles.py 2>&1 && git commit -m feat(v16.9.2): IP冷却重试优化 + 防御查单 + 新重入钉钉通知 2>&1 && git push origin main 2>&1 && echo PUSH_DONE"
