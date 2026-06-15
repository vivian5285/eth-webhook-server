#!/bin/bash
# 存放路径: /home/binance/binance-hft-server/
LOG_FILE="supervisor_binance.log"
PORT=5003

echo -e "\033[0;34m=== 正在全量部署：币安系统 ===\033[0m"

# 1. 强力清理
sudo pkill -f "gunicorn -b 127.0.0.1:$PORT"
sudo pkill -f "position_supervisor_binance.py"
sleep 2

# 2. 同步代码
git fetch --all && git reset --hard origin/main

# 3. 部署
source venv/bin/activate
nohup gunicorn -b 127.0.0.1:$PORT app:app > gateway_binance.log 2>&1 &
nohup python3 -u position_supervisor_binance.py > $LOG_FILE 2>&1 &

# 4. 深度审计
sleep 3
if netstat -tuln | grep -q ":$PORT "; then
    echo -e "\033[0;34m✅ 币安端口 $PORT 就绪\033[0m"
else
    echo -e "\033[0;31m❌ 币安端口 $PORT 启动失败！\033[0m"
fi

if pgrep -f "position_supervisor_binance.py" > /dev/null; then
    echo -e "\033[0;34m✅ 币安执行大脑运行中\033[0m"
else
    echo -e "\033[0;31m❌ 币安大脑启动异常！请务必检查日志：cat $LOG_FILE\033[0m"
fi
