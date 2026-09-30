#!/usr/bin/env bash
# 夜间后台巡检脚本：一次性检查三个币安账户(B/5007, C/5008, D/5009)的
# 健康状态、持仓、雷达/止损状态、真实ERROR日志，供 /loop 定时调用。
# 2026-08-11 创建：宝贝睡觉期间小宝盯盘用，避免每轮手写一堆分散的SSH命令。
set -uo pipefail

ACCOUNTS=(B C D)
PORTS=(5007 5008 5009)

echo "=== 巡检时间: $(date -u '+%Y-%m-%d %H:%M:%S UTC') ==="
echo

for i in "${!ACCOUNTS[@]}"; do
    acct="${ACCOUNTS[$i]}"
    port="${PORTS[$i]}"
    echo "--- ${acct}账户 (端口${port}) ---"

    health="$(curl -sf --max-time 5 "http://127.0.0.1:${port}/health" 2>/dev/null || echo "")"
    if [ -z "$health" ]; then
        echo "  ⚠️ /health 无响应"
    else
        status=$(echo "$health" | grep -o '"status":"[^"]*"' | head -1)
        paused=$(echo "$health" | grep -o '"trading_paused":{[^}]*}')
        echo "  ${status} | ${paused}"
    fi

    cd "/home/binance${acct}/binance-engine" || continue
    ./venv/bin/python -c "
from binance_client import binance_client
found = False
for sym in ('ETHUSDT','XAUUSDT','BNBUSDT','ZECUSDT','BCHUSDT'):
    p = binance_client.get_position(sym, prefer_ws=False, force_rest=True)
    if p and float(p.get('positionAmt', 0) or 0) != 0:
        found = True
        side = 'LONG' if float(p['positionAmt']) > 0 else 'SHORT'
        print(f\"  {sym} {side} qty={abs(float(p['positionAmt'])):.3f} entry={p.get('entryPrice')} mark={p.get('markPrice')} uPnL={p.get('unRealizedProfit')}\")
if not found:
    print('  (空仓)')
" 2>/dev/null

    echo "  最近10分钟真实ERROR:"
    journalctl -u "binance${acct}-engine" --no-pager -S '10 min ago' 2>/dev/null \
        | grep -iE 'ERROR' \
        | grep -viE "AttributeError: 'Client' object has no attribute 'session'|NoneType.*sock.*goodbye|穿价 TP1 推离市价|止损单.*-4509" \
        | tail -5 \
        | sed 's/^/    /'
    echo
done

echo "=== 全账户幽灵单排查(仓位空但挂单在) ==="
for acct in "${ACCOUNTS[@]}"; do
    cd "/home/binance${acct}/binance-engine" || continue
    ./venv/bin/python -c "
from binance_client import binance_client
for sym in ('ETHUSDT','XAUUSDT','BNBUSDT','ZECUSDT','BCHUSDT'):
    p = binance_client.get_position(sym)
    orders = binance_client.get_open_orders(sym)
    flat = not p or float(p.get('positionAmt', 0) or 0) == 0
    if flat and orders:
        print(f'  !!! ${acct} {sym}: 仓位空但有{len(orders)}张挂单 !!!')
" 2>/dev/null
done
echo "(无输出=干净)"
