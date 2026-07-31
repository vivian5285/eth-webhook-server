#!/bin/bash
# VPS状态检查脚本
# 使用方法: ./check_vps.sh

echo "=== VPS系统信息 ==="
echo "当前用户: $(whoami)"
echo "Home目录: $HOME"
echo ""

echo "=== /root 目录内容 ==="
ls -la /root/ 2>/dev/null || echo "无法访问 /root"
echo ""

echo "=== /home/trading 目录内容 ==="
ls -la /home/trading/ 2>/dev/null || echo "trading用户不存在"
echo ""

echo "=== 检查trading用户的binance-engine项目 ==="
if [ -d "/home/trading/binance-engine" ]; then
    echo "项目目录存在: /home/trading/binance-engine"
    ls -la /home/trading/binance-engine/ | head -20
else
    echo "trading用户下没有binance-engine项目"
fi
echo ""

echo "=== 检查supervisor服务 ==="
supervisorctl status 2>/dev/null || echo "supervisor未运行或无法访问"
echo ""

echo "=== 检查gunicorn进程 ==="
ps aux | grep -E "(gunicorn|webhook)" | grep -v grep || echo "未找到相关进程"
echo ""

echo "=== 检查端口监听 ==="
netstat -tlnp 2>/dev/null | grep -E "(5003|5000|80)" || ss -tlnp | grep -E "(5003|5000|80)" || echo "端口检查完成"
