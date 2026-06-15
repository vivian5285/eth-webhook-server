#!/bin/bash
# ==============================================
# 币安系统全域审计版 (Detailed Audit Edition)
# ==============================================

# 配置
PORT=5003
LOG_FILE="supervisor_binance.log"
GATEWAY_LOG="gateway_binance.log"

echo -e "\n\033[1;36m=== 正在执行币安系统详细部署与审计 ===\033[0m"

# [1/4] 清理审计
echo -e "\033[0;33m[1/4] 正在执行端口清理与残留进程剔除...\033[0m"
# 先杀网关，再杀大脑，确保清理彻底
fuser -k $PORT/tcp 2>/dev/null
pkill -f "position_supervisor_binance.py"
echo "  -> 进程与端口已完成清理。"

# [2/4] 代码同步审计
echo -e "\033[0;33m[2/4] 正在同步最新代码库...\033[0m"
git fetch --all
git reset --hard origin/main
echo "  -> 代码库已更新至 HEAD 版本。"

# [3/4] 启动审计
echo -e "\033[0;33m[3/4] 正在启动守护进程...\033[0m"
source venv/bin/activate
nohup gunicorn -b 127.0.0.1:$PORT app:app > "$GATEWAY_LOG" 2>&1 &
nohup python3 -u position_supervisor_binance.py > "$LOG_FILE" 2>&1 &
echo "  -> 网关(Gunicorn)与大脑(Supervisor)已尝试启动。"

# [4/4] 详细健康自检 (重点！)
echo -e "\033[0;33m[4/4] 正在进行详细健康审计...\033[0m"
sleep 2

# 详细审计进程列表
echo -e "  -> 核心进程监听审计:"
ps -ef | grep -E "gunicorn|position_supervisor_binance" | grep -v grep | awk '{print "     PID: "$2", 启动时间: "$5}'

# 详细审计端口监听
if netstat -tuln | grep -q ":$PORT "; then
    echo -e "  -> 端口状态: \033[0;32mLISTEN (Port $PORT)\033[0m"
else
    echo -e "  -> 端口状态: \033[0;31mFAILED (请检查 gateway_binance.log)\033[0m"
    cat "$GATEWAY_LOG" | tail -n 5
fi

echo -e "\n\033[1;36m=== ✅ 币安系统全域启动审计完成 ===\033[0m"
