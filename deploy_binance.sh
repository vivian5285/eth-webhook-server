#!/bin/bash
# ==============================================
# 币安系统全域部署脚本 (V2.5 完整版)
# 作用: 清理、同步、部署、审计
# ==============================================

# 配置区域
PROJECT_DIR="/home/binance/binance-hft-server"
PORT=5003
LOG_FILE="supervisor_binance.log"
GATEWAY_LOG="gateway_binance.log"

echo -e "\033[1;34m=== 🚀 正在启动：币安系统全域部署 (Port: $PORT) ===\033[0m"

# 1. 进入目录
cd $PROJECT_DIR || { echo "目录不存在"; exit 1; }

# 2. 强力清理旧进程
echo "[1/5] 正在清理旧进程..."
sudo pkill -f "gunicorn -b 127.0.0.1:$PORT"
sudo pkill -f "position_supervisor_binance.py"
sleep 2

# 3. 强制同步最新代码 (覆盖式)
echo "[2/5] 正在同步最新代码..."
git fetch --all
git reset --hard origin/main

# 4. 环境部署与后台启动
echo "[3/5] 正在激活环境并启动服务..."
source venv/bin/activate
pip install -r requirements.txt --quiet

# 启动信号网关
nohup gunicorn -b 127.0.0.1:$PORT app:app > $GATEWAY_LOG 2>&1 &
# 启动交易大脑
nohup python3 -u position_supervisor_binance.py > $LOG_FILE 2>&1 &

# 5. 全域自检 (直接调用你的自检逻辑)
echo "[4/5] 正在执行全域链路自检..."
sleep 3

# 检查端口
if netstat -tuln | grep -q ":$PORT "; then
    echo -e "\033[0;32m✅ 端口 $PORT 监听正常\033[0m"
else
    echo -e "\033[0;31m❌ 端口 $PORT 启动失败！请查看 $GATEWAY_LOG\033[0m"
fi

# 检查大脑进程
if pgrep -f "position_supervisor_binance.py" > /dev/null; then
    echo -e "\033[0;32m✅ 币安交易大脑运行中\033[0m"
else
    echo -e "\033[0;31m❌ 交易大脑未能启动！请查看 $LOG_FILE\033[0m"
fi

echo -e "\033[1;34m=== ✅ 部署完成，币安系统已就绪 ===\033[0m"
