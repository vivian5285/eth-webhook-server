#!/bin/bash

# ==============================================
# 量化交易系统 - 一键部署检查脚本
# 用法: ./deploy_check.sh
# ==============================================

SERVICE_NAME="eth-webhook.service"
STATUS_URL="http://127.0.0.1:5000/status"
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

# 3. 检查 TP 监控是否启动（关键检查）
echo -e "\n[3/6] 检查 TP 监控模块是否启动..."
TP_LOG=$(journalctl -u ${SERVICE_NAME} --since "2 minutes ago" --no-pager | grep -E "TP监控已启动|tp_monitor 已启动")
if [ -n "$TP_LOG" ]; then
    echo -e "${GREEN}✅ TP 监控模块已成功启动${NC}"
else
    echo -e "${YELLOW}⚠️  未在最近日志中检测到 TP 监控启动信息${NC}"
    echo "可能原因：tp_monitor.start() 未被调用，或启动时间较早"
fi

# 4. 检查 Webhook 接口
echo -e "\n[4/6] 检查 Webhook /status 接口..."
STATUS_RESPONSE=$(curl -s --max-time 5 ${STATUS_URL})
if [ $? -eq 0 ] && echo "$STATUS_RESPONSE" | grep -q "running"; then
    echo -e "${GREEN}✅ Webhook 服务接口响应正常${NC}"
    echo "返回内容: $STATUS_RESPONSE"
else
    echo -e "${RED}❌ Webhook 接口无法访问或返回异常${NC}"
fi

# 5. 检查最近是否有严重错误
echo -e "\n[5/6] 检查最近 2 分钟内的错误日志..."
ERROR_LOG=$(journalctl -u ${SERVICE_NAME} --since "2 minutes ago" --no-pager | grep -iE "ERROR|Exception|失败|异常|Traceback")
if [ -n "$ERROR_LOG" ]; then
    echo -e "${YELLOW}⚠️  发现以下错误日志：${NC}"
    echo "$ERROR_LOG"
else
    echo -e "${GREEN}✅ 最近 2 分钟内未发现明显错误${NC}"
fi

# 6. 检查仓位文件
echo -e "\n[6/6] 检查本地仓位状态文件..."
if [ -f "current_position.json" ]; then
    echo -e "${GREEN}✅ current_position.json 文件存在${NC}"
else
    echo -e "${YELLOW}ℹ️  current_position.json 文件不存在（当前可能无持仓，属于正常情况）${NC}"
fi

# 最终总结
echo ""
echo "=============================================="
echo "🎯 自检完成总结"
echo "=============================================="

if systemctl is-active --quiet ${SERVICE_NAME} && curl -s --max-time 3 ${STATUS_URL} | grep -q "running"; then
    echo -e "${GREEN}✅ 系统整体运行正常，可以开始实盘测试！${NC}"
else
    echo -e "${RED}❌ 系统存在问题，请根据上方提示排查${NC}"
    echo ""
    echo "常用排查命令："
    echo "  journalctl -u ${SERVICE_NAME} -f"
    echo "  sudo systemctl status ${SERVICE_NAME}"
fi

echo "=============================================="
