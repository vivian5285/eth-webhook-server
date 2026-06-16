#!/bin/bash
# ============================================================
# 币安系统 一键部署 + 全链路自检脚本 (Enhanced Audit Edition)
# ============================================================

set -e

PORT=5003
LOG_DIR="$HOME/binance-engine/logs"
GATEWAY_LOG="$LOG_DIR/gateway_binance.log"
SUPERVISOR_LOG="$LOG_DIR/supervisor_binance.log"
HEALTH_URL="http://127.0.0.1:$PORT/health"

echo -e "\n\033[1;36m============================================================\033[0m"
echo -e "\033[1;36m     币安量化系统 - 一键部署 + 全链路自检 (V2.5)\033[0m"
echo -e "\033[1;36m============================================================\033[0m"

mkdir -p "$LOG_DIR"

# ==================== [1/6] 清理环境 ====================
echo -e "\n\033[0;33m[1/6] 正在清理旧进程与端口...\033[0m"
fuser -k $PORT/tcp 2>/dev/null || true
pkill -f "position_supervisor_binance.py" 2>/dev/null || true
pkill -f "gunicorn.*app:app" 2>/dev/null || true
sleep 1
echo "  -> 旧进程与端口已清理完成"

# ==================== [2/6] 代码更新 ====================
echo -e "\n\033[0;33m[2/6] 正在拉取最新代码...\033[0m"
git fetch --all
git reset --hard origin/main
echo "  -> 代码已更新至最新版本"

# ==================== [3/6] 启动服务 ====================
echo -e "\n\033[0;33m[3/6] 正在启动服务...\033[0m"
source venv/bin/activate

# 启动 Gunicorn 网关
nohup gunicorn -b 127.0.0.1:$PORT \
    --workers 2 \
    --timeout 120 \
    --access-logfile "$LOG_DIR/gunicorn_access.log" \
    --error-logfile "$GATEWAY_LOG" \
    app:app > /dev/null 2>&1 &

# 启动监督层
nohup python3 -u position_supervisor_binance.py > "$SUPERVISOR_LOG" 2>&1 &

sleep 3
echo "  -> Gunicorn (端口 $PORT) 与 监督层已启动"

# ==================== [4/6] 进程与端口检查 ====================
echo -e "\n\033[0;33m[4/6] 正在进行进程与端口自检...\033[0m"

if pgrep -f "gunicorn.*app:app" > /dev/null; then
    echo "  [PASS] Gunicorn 网关进程正在运行"
else
    echo "  [FAIL] Gunicorn 网关未启动，请检查 $GATEWAY_LOG"
    exit 1
fi

if pgrep -f "position_supervisor_binance.py" > /dev/null; then
    echo "  [PASS] 监督层 (position_supervisor_binance.py) 正在运行"
else
    echo "  [FAIL] 监督层未启动，请检查 $SUPERVISOR_LOG"
    exit 1
fi

if netstat -tuln | grep -q ":$PORT "; then
    echo "  [PASS] 端口 $PORT 正在监听"
else
    echo "  [FAIL] 端口 $PORT 未监听"
    exit 1
fi

# ==================== [5/6] 健康接口与账户检查 ====================
echo -e "\n\033[0;33m[5/6] 正在进行健康接口与账户数据自检...\033[0m"

sleep 2

# 检查 /health 接口
HEALTH_RESP=$(curl -s "$HEALTH_URL" || echo "FAILED")
if echo "$HEALTH_RESP" | grep -q '"status": "healthy"'; then
    echo "  [PASS] /health 接口返回 healthy"
    echo "$HEALTH_RESP" | python3 -m json.tool | head -n 20
else
    echo "  [FAIL] /health 接口异常"
    echo "$HEALTH_RESP"
    exit 1
fi

# 检查账户余额和权益
python3 -c "
from binance_client import binance_client
balance = binance_client.get_available_balance('USDT')
equity = binance_client.get_total_equity()
print(f'  [INFO] 可用余额: {balance:.2f} USDT')
print(f'  [INFO] 账户总权益: {equity:.2f} USDT')
if balance > 0 and equity > 0:
    print('  [PASS] 账户数据获取正常')
else:
    print('  [WARN] 账户余额或权益为0，请检查API权限')
" 2>/dev/null || echo "  [FAIL] 账户数据获取异常"

# ==================== [6/6] 模拟信号测试（可选但推荐） ====================
echo -e "\n\033[0;33m[6/6] 可选：模拟发送测试信号...\033[0m"
echo "  如需测试，请手动执行以下命令："
echo ""
echo "  curl -X POST http://127.0.0.1:$PORT/webhook \\"
echo "    -H 'Content-Type: application/json' \\"
echo "    -d '{\"secret\": \"528586\", \"action\": \"LONG\"}'"
echo ""
echo "  然后观察 supervisor_binance.log 是否正常计算仓位并尝试下单。"

echo -e "\n\033[1;32m============================================================\033[0m"
echo -e "\033[1;32m     ✅ 币安系统部署 + 全链路自检完成\033[0m"
echo -e "\033[1;32m============================================================\033[0m"

echo ""
echo "常用查看命令："
echo "  tail -f $SUPERVISOR_LOG"
echo "  tail -f $GATEWAY_LOG"
echo "  curl $HEALTH_URL"
echo ""
