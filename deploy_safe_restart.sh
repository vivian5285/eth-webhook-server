#!/usr/bin/env bash
# 部署安全重启：轮询三个币安账户 /health 的 deploy_safe 字段，
# 确认没有品种正在开仓执行中(_open_in_progress)才重启，避免撞上
# "市价单已成交但仓位查询/TP绑定尚未走完"的窗口——那个窗口内重启
# 会把仓位打成孤儿仓，靠闪电接管兜底而非正常TV关联流程。
# 背景：2026-08-10 BNBUSDT开仓中途被部署重启命中过一次，接管兜住了
# 但走的是应急通道，故加这道显式的可轮询安全阀。
#
# 用法: ./deploy_safe_restart.sh
set -uo pipefail

PORTS=(5007 5008 5009)
SERVICES=(binanceB-engine binanceC-engine binanceD-engine)
MAX_WAIT_SEC=90
POLL_INTERVAL=3

wait_deploy_safe() {
    local port="$1"
    local waited=0
    while [ "$waited" -lt "$MAX_WAIT_SEC" ]; do
        local body safe
        body="$(curl -sf --max-time 3 "http://127.0.0.1:${port}/health" 2>/dev/null || echo "")"
        if [ -z "$body" ]; then
            echo "  端口 ${port} /health 无响应（可能尚未启动），视为安全跳过"
            return 0
        fi
        safe="$(echo "$body" | grep -o '"deploy_safe": *true' || true)"
        if [ -n "$safe" ]; then
            echo "  端口 ${port} deploy_safe=true，可以重启"
            return 0
        fi
        echo "  端口 ${port} 仍有品种开仓执行中，等待 ${waited}/${MAX_WAIT_SEC}s..."
        sleep "$POLL_INTERVAL"
        waited=$((waited + POLL_INTERVAL))
    done
    echo "  端口 ${port} 等待超时(${MAX_WAIT_SEC}s)仍不安全，继续重启（避免无限卡死，请人工确认无异常）"
    return 1
}

echo "=== 部署安全重启：逐端口检查开仓执行状态 ==="
for port in "${PORTS[@]}"; do
    echo "检查端口 ${port} ..."
    wait_deploy_safe "$port"
done

echo ""
echo "=== 全部端口检查完毕，执行重启 ==="
systemctl restart "${SERVICES[@]}"
sleep 5
echo ""
echo "=== 重启后状态 ==="
systemctl is-active "${SERVICES[@]}"
