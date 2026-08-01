#!/bin/bash
set -e
cd /home/trading/binance-engine

echo "=== 币安系统 · 干净重部署开始 [$(date '+%Y%m%d-%H%M%S')] ==="
echo "  工作目录: $(pwd)"
echo "  目标端口: 5003"

# 0. 强制从 GitHub 拉取最新代码
echo ""
echo "[0] 强制拉取 GitHub 最新代码..."
git fetch origin
git reset --hard origin/main
echo "  -> 当前 HEAD: $(git log -1 --oneline)"

# 1. 强制清场 - 多轮 kill 确保干净
echo ""
echo "[1] 强制清场：端口 5003 + 全部币安残留进程..."
for i in 1 2 3 4 5; do
    echo "  -> 清场第 $i/5 轮..."
    pkill -f "gunicorn.*binance" 2>/dev/null || true
    pkill -9 -f "gunicorn.*binance" 2>/dev/null || true
    pkill -f "python.*binance" 2>/dev/null || true
    pkill -9 -f "python.*binance" 2>/dev/null || true
    sleep 2
done

# 2. 检查端口是否释放
echo ""
echo "[2] 检查端口 5003 状态..."
if ss -tlnp | grep -q ":5003 "; then
    echo "  ⚠️  端口 5003 仍被占用，当前监听进程:"
    ss -tlnp | grep 5003 || true
    echo ""
    echo "  尝试使用 fuser 强制释放..."
    fuser -k 5003/tcp 2>/dev/null || true
    sleep 3
    
    if ss -tlnp | grep -q ":5003 "; then
        echo "  ❌ 经过所有清场，端口 5003 仍被占用，部署中止"
        echo "  请手动检查: lsof -i :5003 或 netstat -tlnp | grep 5003"
        exit 1
    fi
fi
echo "  ✅ 端口 5003 已释放"

# 3. 语法检查
echo ""
echo "[3] 语法检查..."
ALL_OK=true
for f in api_throttle binance_client position_supervisor_binance dingtalk console_api account_profiles radar_reentry_mixin reentry_profiles webhook_parser breath_stop breath_profiles defense_profiles smart_reentry_engine order_idempotency pipeline_bridge; do
    if [ -f "$f.py" ]; then
        if python3 -m py_compile "$f.py" 2>/dev/null; then
            echo "  OK: $f"
        else
            echo "  FAIL: $f"
            ALL_OK=false
        fi
    fi
done

if [ "$ALL_OK" = false ]; then
    echo "  ❌ 语法检查失败，部署中止"
    exit 1
fi

# 4. 启动服务
echo ""
echo "[4] 启动 gunicorn..."
bash -c 'source venv/bin/activate && gunicorn -b 0.0.0.0:5003 --workers 1 --threads 10 --timeout 120 --graceful-timeout 30 --log-file logs/gunicorn_error.log --access-logfile logs/gunicorn_access.log --daemon app:app'
sleep 5

# 5. 健康检查
echo ""
echo "[5] 健康检查..."
for i in 1 2 3 4 5; do
    sleep 2
    HEALTH=$(curl -s --max-time 5 http://127.0.0.1:5003/health 2>/dev/null || echo "FAILED")
    if [ "$HEALTH" != "FAILED" ] && [ -n "$HEALTH" ]; then
        echo "  ✅ 服务启动成功: $HEALTH"
        break
    fi
    if [ $i -eq 5 ]; then
        echo "  ❌ 健康检查失败，请检查日志: tail -50 logs/gunicorn_error.log"
        exit 1
    fi
    echo "  -> 重试 $i/5..."
done

# 6. 内部 + 外部连通性测试
echo ""
echo "[6] 连通性测试..."
echo "  -> 内部测试 (127.0.0.1)..."
INTERNAL=$(curl -s --max-time 5 http://127.0.0.1:5003/health 2>/dev/null)
if [ -n "$INTERNAL" ]; then
    echo "  ✅ 内部连通正常: $INTERNAL"
else
    echo "  ❌ 内部连通失败"
fi

echo "  -> 外部测试 (187.77.130.144:5003)..."
EXTERNAL=$(curl -s --max-time 5 http://187.77.130.144:5003/health 2>/dev/null)
if [ -n "$EXTERNAL" ]; then
    echo "  ✅ 外部连通正常: $EXTERNAL"
else
    echo "  ⚠️  外部连通失败 (请检查防火墙/端口开放)"
fi

echo ""
echo "=== 部署完成 [$(date '+%Y%m%d-%H%M%S')] ==="
echo "  TradingView webhook: http://187.77.130.144:5003/binance/webhook"
