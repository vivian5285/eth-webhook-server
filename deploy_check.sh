#!/bin/bash

# ==============================================
# 量化交易系统 - 终极部署检查脚本 (V2.5 双层穿透版)
# 用法: ./deploy_check.sh
# ==============================================

SERVICE_NAME="eth-webhook.service"
# 内部 5000 端口直连测试
INTERNAL_URL="http://127.0.0.1:5000/health"
# 外部 80 端口 Nginx 转发测试
EXTERNAL_URL="http://127.0.0.1/health"

GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo "=============================================="
echo "🛡️ 量化交易系统全域自检 (含反向代理)"
echo "时间: $(date '+%Y-%m-%d %H:%M:%S')"
echo "=============================================="

# 1. 检查底层 Gunicorn/Flask 服务状态
echo -e "\n[1/8] 检查内部交易引擎状态..."
if systemctl is-active --quiet ${SERVICE_NAME}; then
    echo -e "${GREEN}✅ 服务 ${SERVICE_NAME} 运行正常${NC}"
else
    echo -e "${RED}❌ 服务 ${SERVICE_NAME} 未运行！${NC}"
fi

# 2. 检查 Nginx 守护进程
echo -e "\n[2/8] 检查 Nginx 反向代理进程..."
if systemctl is-active --quiet nginx; then
    echo -e "${GREEN}✅ Nginx 守护进程运行正常${NC}"
else
    echo -e "${RED}❌ Nginx 服务未运行！外部信号将被阻断！${NC}"
fi

# 3. 检查内部 5000 端口可用性
echo -e "\n[3/8] 测试引擎内网穿透 (端口 5000)..."
INTERNAL_RESPONSE=$(curl -s --max-time 5 ${INTERNAL_URL})
if [ $? -eq 0 ] && echo "$INTERNAL_RESPONSE" | grep -q "healthy"; then
    echo -e "${GREEN}✅ 内部交易接口响应正常${NC}"
else
    echo -e "${RED}❌ 内部接口无响应！${NC}"
fi

# 4. 检查 Nginx 80 端口转发可用性 (极其关键)
echo -e "\n[4/8] 测试 Nginx 公网模拟转发 (端口 80 -> 5000)..."
EXTERNAL_RESPONSE=$(curl -s --max-time 5 ${EXTERNAL_URL} -H "Host: localhost")
if [ $? -eq 0 ] && echo "$EXTERNAL_RESPONSE" | grep -q "healthy"; then
    echo -e "${GREEN}✅ Nginx 完美连通内网，外部 TV 信号畅通无阻！${NC}"
else
    echo -e "${RED}❌ Nginx 转发失败！请检查 /etc/nginx/sites-available 配置${NC}"
fi

# 5. 检查 TP 监控与 V2.5 队列初始化
echo -e "\n[5/8] 检查核心逻辑层后台日志..."
TP_LOG=$(journalctl -u ${SERVICE_NAME} --since "2 minutes ago" --no-pager | grep -E "监控|初始化|Worker|Cron")
if [ -n "$TP_LOG" ]; then
    echo -e "${GREEN}✅ V2.5 异步队列与风控 Cron 线程已就绪${NC}"
else
    echo -e "${YELLOW}⚠️ 未抓取到后台线程启动日志 (可能已被刷走，不影响运行)${NC}"
fi

# 6. 检查最近是否有严重错误
echo -e "\n[6/8] 检查最近 2 分钟内的核心报错..."
ERROR_LOG=$(journalctl -u ${SERVICE_NAME} --since "2 minutes ago" --no-pager | grep -iE "ERROR|Exception|Traceback")
if [ -n "$ERROR_LOG" ]; then
    echo -e "${YELLOW}⚠️ 发现以下错误日志：${NC}"
    echo "$ERROR_LOG"
else
    echo -e "${GREEN}✅ 最近 2 分钟内未发现明显错误${NC}"
fi

# 7. 检查状态持久化文件
echo -e "\n[7/8] 检查本地持久化系统..."
if [ -f "data/trading_state.json" ]; then
    echo -e "${GREEN}✅ data/trading_state.json 文件挂载正常${NC}"
else
    echo -e "${YELLOW}ℹ️  状态文件暂未生成 (系统接单后自动生成)${NC}"
fi

# 8. 最终结论
echo ""
echo "=============================================="
echo "🎯 自检完成总结"
echo "=============================================="

if systemctl is-active --quiet ${SERVICE_NAME} && systemctl is-active --quiet nginx && echo "$EXTERNAL_RESPONSE" | grep -q "healthy"; then
    echo -e "${GREEN}✅ 【全域绿灯】Nginx 与内部引擎已无缝咬合，万亿战神准备接管实盘！${NC}"
else
    echo -e "${RED}❌ 系统存在链路断层，请根据上方红字排查${NC}"
fi
echo "=============================================="
