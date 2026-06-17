#!/bin/bash
# ==============================================
# 币安系统全域审计版 (WSS 毫秒雷达升级版)
# ==============================================

# 配置
PORT=5003
LOG_FILE="supervisor_binance.log"
GATEWAY_LOG="gateway_binance.log"

echo -e "\n\033[1;36m=== 正在执行币安系统详细部署与 WSS 升级 ===\033[0m"

# [1/5] 清理审计
echo -e "\033[0;33m[1/5] 正在执行端口清理与残留进程剔除...\033[0m"
fuser -k $PORT/tcp 2>/dev/null
pkill -f "position_supervisor_binance.py"
echo "  -> 进程与端口已完成强制清理。"

# [2/5] 依赖检查与升级 (新增 WebSocket 库)
echo -e "\033[0;33m[2/5] 检查并安装高级核心依赖...\033[0m"
source venv/bin/activate
pip install -q websocket-client requests flask gunicorn python-binance python-dotenv
echo "  -> 核心依赖 (WebSocket 等) 已确保就绪。"

# [3/5] 代码同步审计
echo -e "\033[0;33m[3/5] 正在同步最新代码库...\033[0m"
git fetch --all
git reset --hard origin/main
echo "  -> 代码库已强制更新至 HEAD 版本。"

# [4/5] 启动审计
echo -e "\033[0;33m[4/5] 正在启动毫秒级守护进程...\033[0m"
# 确保数据持久化目录存在
mkdir -p data
nohup gunicorn -b 127.0.0.1:$PORT app:app > "$GATEWAY_LOG" 2>&1 &
nohup python3 -u position_supervisor_binance.py > "$LOG_FILE" 2>&1 &
echo "  -> 网关(Gunicorn)与大脑(Supervisor)已点火启动。"

# [5/5] 详细健康自检 (重点穿透测试！)
echo -e "\033[0;33m[5/5] 正在进行详细健康与回路审计...\033[0m"
sleep 3

echo -e "  -> 核心进程监听审计:"
ps -ef | grep -E "gunicorn|position_supervisor_binance" | grep -v grep | awk '{print "     PID: "$2", 启动时间: "$5}'

if netstat -tuln | grep -q ":$PORT "; then
    echo -e "  -> 端口状态: \033[0;32mLISTEN (Port $PORT)\033[0m"
    # 发送本地空包弹进行网关回路自检
    echo -e "  -> 本地网关回路测试:"
    HTTP_STATUS=$(curl -s -o /dev/null -w "%{http_code}" -X POST http://127.0.0.1:$PORT/webhook -H "Content-Type: application/json" -d '{"secret": "528586", "action": "PING"}')
    if [ "$HTTP_STATUS" -eq 200 ]; then
        echo -e "     \033[0;32m✅ 本地网关 200 OK，大脑通信回路极度畅通。\033[0m"
    else
        echo -e "     \033[0;31m⚠️ 本地网关响应异常，HTTP 状态码: $HTTP_STATUS\033[0m"
    fi
else
    echo -e "  -> 端口状态: \033[0;31mFAILED (请检查 gateway_binance.log)\033[0m"
    cat "$GATEWAY_LOG" | tail -n 5
fi

echo -e "\n\033[1;36m=== 🚀 币安(Binance)系统实盘升级完成 ===\033[0m"
echo -e "可以通过 \`tail -f supervisor_binance.log\` 查看 WSS 雷达与交易日志。\n"
