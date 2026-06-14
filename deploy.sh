#!/bin/bash
# deploy.sh - 终极版一键部署脚本（带端口清理）

set -e

echo "========================================"
echo "开始部署 ETH Webhook Trading Server"
echo "========================================"

echo ""
echo "[1/6] 停止 systemd 服务..."
sudo systemctl stop eth-webhook.service || true

echo ""
echo "[2/6] 强制清理残留 Gunicorn 进程..."
pkill -f gunicorn || true
sleep 2

echo ""
echo "[3/6] 确认端口 5000 是否释放..."
if ss -tlnp | grep -q ":5000"; then
    echo "端口仍被占用，尝试强制释放..."
    fuser -k 5000/tcp || true
    sleep 2
fi

echo ""
echo "[4/6] 拉取最新代码..."
git pull origin main

echo ""
echo "[5/6] 安装依赖..."
source venv/bin/activate
pip install -r requirements.txt --quiet

echo ""
echo "[6/6] 启动服务..."
sudo systemctl daemon-reload
sudo systemctl restart eth-webhook.service
sleep 3

echo ""
echo "========== 服务状态 =========="
sudo systemctl status eth-webhook.service --no-pager | head -n 15

echo ""
echo "========== 健康检查 =========="
python3 check_system.py

echo ""
echo "========================================"
echo "部署完成！"
echo "========================================"
