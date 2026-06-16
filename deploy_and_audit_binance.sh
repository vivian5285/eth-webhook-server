#!/bin/bash
# ============================================================
# 币安系统 一键部署 + 全链路自检脚本（优化版 V2.6）
# ============================================================

set -e

PORT=5003
LOG_DIR="$HOME/binance-engine/logs"
GATEWAY_LOG="$LOG_DIR/gateway_binance.log"
HEALTH_URL="http://127.0.0.1:$PORT/health"

echo -e "\n\033[1;36m============================================================\033[0m"
echo -e "\033[1;36m     币安量化系统 - 一键部署 + 全链路自检（优化版）\033[0m"
echo -e "\033[1;36m============================================================\033[0m"

mkdir -p "$LOG_DIR"

# ==================== [1/5] 清理环境 ====================
echo -e "\n\033[0;33m[1/5] 正在彻底清理旧进程与端口...\033[0m"
pkill -9 -f gunicorn || true
pkill -9 -f position_supervisor_binance.py || true
fuser -k $PORT/tcp || true
sleep 2
echo "  -> 旧进程与端口已清理完成"

# ==================== [2/5] 代码更新 ====================
echo -e "\n\033[0;33m[2/5] 正在拉取最新代码...\033[0m"
git fetch --all
git reset --hard origin/main
echo "  -> 代码已更新至最新版本"

# ==================== [3/5] 启动服务 ====================
echo -e "\n\033[0;33m[3/5] 正在启动 Gunicorn 服务...\033[0m"
source venv/bin/activate

nohup gunicorn -b 127.0.0.1:$PORT \
    --workers 2 \
    --timeout 120 \
    --access-logfile "$LOG_DIR/gunicorn_access.log" \
    --error-logfile "$GATEWAY_LOG" \
    app:app > /dev/null 2>&1 &

sleep 3
echo "  -> Gunicorn (端口 $PORT) 已启动"

# ==================== [4/5] 进程与端口检查 ====================
echo -e "\n\033[0;33m[4/5] 正在进行进程与端口自检...\033[0m"

if pgrep -f "gunicorn.*app:app" > /dev/null; then
    echo "  [PASS] Gunicorn 进程正在运行"
else
    echo "  [FAIL] Gunicorn 未启动，请检查 $GATEWAY_LOG"
    exit 1
fi

if netstat -tuln | grep -q ":$PORT "; then
    echo "  [PASS] 端口 $PORT 正在监听"
else
    echo "  [FAIL] 端口 $PORT 未监听"
    exit 1
fi

# ==================== [5/5] 健康检查 + 快速测试 ====================
echo -e "\n\033[0;33m[5/5] 正在进行健康接口与快速测试...\033[0m"
sleep 2

HEALTH_RESP=$(curl -s "$HEALTH_URL" || echo "FAILED")
if echo "$HEALTH_RESP" | grep -q '"status": "healthy"'; then
    echo "  [PASS] /health 接口返回 healthy"
else
    echo "  [FAIL] /health 接口异常"
    echo "$HEALTH_RESP"
    exit 1
fi

echo ""
echo -e "\033[1;32m============================================================\033[0m"
echo -e "\033[1;32m     ✅ 部署 + 自检完成，系统已就绪\033[0m"
echo -e "\033[1;32m============================================================\033[0m"

echo ""
echo "常用命令："
echo "  tail -f $GATEWAY_LOG"
echo "  curl $HEALTH_URL"
echo "  curl -X POST http://127.0.0.1:$PORT/webhook -H 'Content-Type: application/json' -d '{\"secret\": \"528586\", \"action\": \"LONG\"}'"
echo ""
