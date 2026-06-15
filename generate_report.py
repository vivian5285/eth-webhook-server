#!/usr/bin/env python3
# generate_report.py（简单复盘统计脚本）
import json
from datetime import datetime
from collections import defaultdict

TRADE_LOG_FILE = "/home/workdir/artifacts/trade_log.jsonl"


def load_trades():
    trades = []
    try:
        with open(TRADE_LOG_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    trades.append(json.loads(line))
    except FileNotFoundError:
        print("交易日志文件不存在")
        return []
    return trades


def generate_report(trades):
    if not trades:
        print("暂无交易记录")
        return

    total_trades = len(trades)
    total_pnl = sum(t.get("pnl", 0) for t in trades)
    winning_trades = [t for t in trades if t.get("pnl", 0) > 0]
    losing_trades = [t for t in trades if t.get("pnl", 0) < 0]

    win_rate = len(winning_trades) / total_trades * 100 if total_trades > 0 else 0
    avg_pnl = total_pnl / total_trades if total_trades > 0 else 0

    # 按日期统计
    daily_pnl = defaultdict(float)
    for t in trades:
        day = t["timestamp"][:10]
        daily_pnl[day] += t.get("pnl", 0)

    print("=" * 60)
    print("📊 交易复盘统计报告")
    print("=" * 60)
    print(f"统计时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"总交易次数: {total_trades}")
    print(f"总盈亏: {total_pnl:+.2f} USDT")
    print(f"胜率: {win_rate:.1f}% ({len(winning_trades)}胜 / {len(losing_trades)}负)")
    print(f"平均每笔盈亏: {avg_pnl:+.2f} USDT")
    print("-" * 60)
    print("最近5天每日盈亏:")
    for day in sorted(daily_pnl.keys())[-5:]:
        print(f"  {day}: {daily_pnl[day]:+.2f} USDT")
    print("=" * 60)


if __name__ == "__main__":
    trades = load_trades()
    generate_report(trades)
