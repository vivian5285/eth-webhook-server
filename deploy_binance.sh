#!/bin/bash
# ==============================================
# 币安系统全域部署版 (Gunicorn 工业级并发引擎)
# ==============================================

# 配置
PORT=5003
LOG_FILE="supervisor_binance.log"

echo -e "\n\033[1;36m=== 🚀 正在执行币安系统详细部署与 Gunicorn 升级 ===\033[0m"

# [1/5] 清理审计 (精准绞杀旧进程)
echo -e "\033[0;33m[1/5] 正在执行端口清理与残留进程剔除...\033[0m"
# 强杀占用端口的进程
if command -v fuser >/dev/null 2>&1; then
    fuser -k -9 $PORT/tcp >/dev/null 2>&1
else
    lsof -t -i:$PORT | xargs -r kill -9 >/dev/null 2>&1
fi
# 强杀所有残余的币安大脑和 gunicorn 进程
pkill -9 -f "position_supervisor_binance.py" >/dev/null 2>&1
pkill -9 -f "gunicorn.*$PORT" >/dev/null 2>&1
sleep 1.5
echo "  -> 进程与端口已完成强制清理。"

# [2/5] 依赖检查与升级
echo -e "\033[0;33m[2/5] 检查并安装高级核心依赖...\033[0m"
source venv/bin/activate
pip install -q websocket-client requests flask gunicorn python-binance python-dotenv
echo "  -> 核心依赖 (Gunicorn, WebSocket 等) 已确保就绪。"

# [3/5] 代码同步审计
echo -e "\033[0;33m[3/5] 正在同步最新代码库...\033[0m"
git fetch --all >/dev/null 2>&1
git reset --hard origin/main >/dev/null 2>&1
echo "  -> 代码库已强制更新至 HEAD 版本。"

# [4/5] 启动审计 (启用 1进程 10线程 的保时捷引擎)
echo -e "\033[0;33m[4/5] 正在启动毫秒级并发守护进程...\033[0m"
mkdir -p data
# 🚀 核心升级：1个Worker保证大脑唯一，10个Threads保证瞬间秒回TV信号
nohup gunicorn --workers 1 --threads 10 -b 127.0.0.1:$PORT app:app > "$LOG_FILE" 2>&1 &
GUNICORN_PID=$!
echo "  -> 工业级网关(Gunicorn)已点火启动 (PID: $GUNICORN_PID)。"

# [5/5] 详细健康自检
echo -e "\033[0;33m[5/5] 正在进行详细健康与回路审计...\033[0m"
sleep 3

if lsof -Pi :$PORT -sTCP:LISTEN -t >/dev/null 2>&1 ; then
    echo -e "  -> 端口状态: \033[0;32mLISTEN (Port $PORT)\033[0m"
    # 发送本地空包弹进行网关回路自检
    echo -e "  -> 本地网关回路测试:"
    HTTP_STATUS=$(curl -s -o /dev/null -w "%{http_code}" -X POST http://127.0.0.1:$PORT/webhook -H "Content-Type: application/json" -d '{"secret": "528586", "action": "PING"}')
    if [ "$HTTP_STATUS" -eq 200 ]; then
        echo -e "     \033[0;32m✅ 本地网关 200 OK，并发通信回路极度畅通。\033[0m"
    else
        echo -e "     \033[0;31m⚠️ 本地网关响应异常，HTTP 状态码: $HTTP_STATUS\033[0m"
    fi
else
    echo -e "  -> 端口状态: \033[0;31mFAILED (请检查 $LOG_FILE)\033[0m"
    tail -n 5 "$LOG_FILE"
fi

echo -e "\n\033[1;36m=== 🚀 币安(Binance)系统实盘并发升级完成 ===\033[0m"
echo -e "可以通过 \`tail -f $LOG_FILE\` 查看极速交易日志。\n"
