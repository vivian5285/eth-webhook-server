#!/bin/bash
# deploy.sh - 终极版一键部署脚本（带端口清理 & Nginx联动）

set -e

echo "========================================"
echo "开始部署 ETH Webhook Trading Server (V2.5)"
echo "========================================"

echo ""
echo "[1/7] 停止 systemd 服务..."
sudo systemctl stop eth-webhook.service || true

echo ""
echo "[2/7] 强制清理残留 Gunicorn 进程..."
pkill -f gunicorn || true
sleep 2

echo ""
echo "[3/7] 确认端口 5000 是否释放..."
if ss -tlnp | grep -q ":5000"; then
    echo "端口仍被占用，尝试强制释放..."
    fuser -k 5000/tcp || true
    sleep 2
fi

echo ""
echo "[4/7] 拉取最新代码..."
git pull origin main

echo ""
echo "[5/7] 安装依赖..."
source venv/bin/activate
pip install -r requirements.txt --quiet

echo ""
echo "[6/7] 启动内部引擎服务..."
sudo systemctl daemon-reload
sudo systemctl restart eth-webhook.service
sleep 3

echo ""
echo "[7/7] 测试并重载 Nginx 反向代理..."
sudo nginx -t && sudo systemctl reload nginx || echo "⚠️ Nginx 配置有误，请手动检查！"

echo ""
echo "========== 核心服务状态 =========="
sudo systemctl status eth-webhook.service --no-pager | head -n 12

echo ""
echo "========================================"
echo "🚀 部署与重载完成！正在执行全域自检..."
echo "========================================"
./deploy_check.sh
