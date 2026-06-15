#!/bin/bash

# ==============================================
# 量化交易系统 - 一键部署检查脚本 (已适配 2026-06-15 架构)
# 用法: ./deploy_check.sh
# ==============================================

SERVICE_NAME="eth-webhook.service"
# 接口已更新为 /health
STATUS_URL="http://127.0.0.1:5000/health"
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo "=============================================="
echo "🚀 量化交易系统部署自检脚本"
echo "时间: $(date '+%Y-%m-%d %H:%M:%S')"
echo "=============================================="

# 1. 重启服务
echo -e "\n[1/6] 正在重启服务 ${SERVICE_NAME} ..."
sudo systemctl restart ${SERVICE_NAME}
sleep 4

# 2. 检查服务状态
echo -e "\n[2/6] 检查服务状态..."
if systemctl is-active --quiet ${SERVICE_NAME}; then
    echo -e "${GREEN}✅ 服务 ${SERVICE_NAME} 运行正常${NC}"
else
    echo -e "${RED}❌ 服务 ${SERVICE_NAME} 启动失败！${NC}"
    echo "请执行以下命令查看详细错误："
    echo "journalctl -u ${SERVICE_NAME} -n 50 --no-pager"
    exit 1
fi

# 3. 检查 TP 监控是否启动
echo -e "\n[3/6] 检查后台监控模块日志..."
# 放宽了 grep 的匹配词，适配新的日志输出
TP_LOG=$(journalctl -u ${SERVICE_NAME} --since "2 minutes ago" --no-pager | grep -E "监控|初始化完成|Supervisor")
if [ -n "$TP_
