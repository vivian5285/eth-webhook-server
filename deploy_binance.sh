#!/bin/bash
echo -e "\033[0;34m=== 正在维护：币安 (Binance) 系统 ===\033[0m"

# 1. 精确清理：只杀 binance 目录下的进程
sudo pkill -f "gunicorn -b 127.0.0.1:5003"
sudo pkill -f "position_supervisor_binance.py"

# 2. 同步与激活
cd /home/binance/binance-hft-server
git pull origin main
source venv/bin/activate

# 3. 部署
nohup gunicorn -b 127.0.0.1:5003 app:app > gateway_binance.log 2>&1 &
nohup python3 -u position_supervisor_binance.py > supervisor_binance.log 2>&1 &

echo -e "\033[0;34m✅ 币安系统已在 5003 端口就绪\033[0m"
