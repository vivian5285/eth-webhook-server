#!/bin/bash
# deploy.sh - 一键拉取 + 部署 + 检查脚本

set -e

echo "=== [1/5] 拉取最新代码 ==="
git pull origin main

echo "=== [2/5] 安装/更新依赖 ==="
pip install -r requirements.txt --quiet

echo "=== [3/5] 重启服务 ==="
sudo systemctl restart eth-webhook.service
sleep 3

echo "=== [4/5] 检查服务状态 ==="
sudo systemctl status eth-webhook.service --no-pager | head -n 15

echo "=== [5/5] 执行系统检查 ==="
python3 check_system.py

echo ""
echo "✅ 部署完成！"
echo "建议使用以下命令查看完整状态："
echo "  curl http://localhost:5000/status | jq"
