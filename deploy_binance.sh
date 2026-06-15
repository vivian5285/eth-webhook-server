#!/bin/bash
# ==================================================
# 币安全域部署脚本 (Git 自适应版)
# 作用: 自动识别当前目录、清理进程、同步代码、部署
# ==================================================

# 自动获取当前脚本所在目录
PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
PORT=5003
LOG_FILE="supervisor_binance.log"
GATEWAY_LOG="gateway_binance.log"

echo -e "\033[1;34m=== 🚀 正在启动：币安系统 (自适应模式) ===\033[0m"
echo "部署目录: $PROJECT_DIR"

# 1. 强力清理旧进程
echo "[1/4] 清理旧进程..."
sudo pkill -f "gunicorn -b 127.0.0.1:$PORT"
sudo pkill -f "position_supervisor_binance.py"
sleep 2

# 2. 同步代码 (Git 流程)
echo "[2/4] 同步最新代码..."
cd "$PROJECT_DIR"
git fetch --all
git reset --hard origin/main

# 3. 环境准备与启动
echo "[3/4] 启动服务..."
source venv/bin/activate
pip install -r requirements.txt --quiet

# 启动网关与大脑
nohup gunicorn -b 127.0.0.1:$PORT app:app > "$GATEWAY_LOG" 2>&1 &
nohup python3 -u position_supervisor_binance.py > "$LOG_FILE" 2>&1 &

# 4. 健康自检
echo "[4/4] 健康自检..."
sleep 3
if netstat -tuln | grep -q ":$PORT "; then
    echo -e "\033[0;32m✅ 币安端口 $PORT 已就绪\033[0m"
else
    echo -e "\033[0;31m❌ 端口 $PORT 启动失败！请检查 $GATEWAY_LOG\033[0m"
fi
