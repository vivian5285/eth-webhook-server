#!/usr/bin/env python3
# generate_report.py（增强版 - 2026-06-15）
import json
from datetime import datetime
from collections import defaultdict
from typing import List, Dict

TRADE_LOG_FILE = "/home/workdir/artifacts/trade_log.jsonl"


def load_trades() -> List[Dict]:
    trades = []
    try:
        with open(TRADE_LOG_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    trades.append(json.loads(line))
    except FileNotFoundError:
        return []
    return trades


def generate_report(trades: List[Dict]):
    if not trades:
        print("暂无交易记录")
        return

    total_trades = len(trades)
    total_pnl = sum(t.get("pnl", 0) for t in trades)
    wins = [t for t in trades if t.get("pnl", 0) > 0]
    losses = [t for t in trades if t.get("pnl", 0) < 0]

    win_rate = len(wins) / total_trades * 100 if total_trades > 0 else 0
    profit_factor = (sum(t["pnl"] for t in wins) / abs(sum(t["pnl"] for t in losses))) if losses else float('inf')

    avg_win = sum(t["pnl"] for t in wins) / len(wins) if wins else 0
    avg_loss = sum(t["pnl"] for t in losses) / len(losses) if losses else 0

    # 最大连续盈利 / 亏损
    max_consec_win = max_consec_loss = current_win = current_loss = 0
    for t in trades:
        if t.get("pnl", 0) > 0:
            current_win += 1
            current_loss = 0
            max_consec_win = max(max_consec_win, current_win)
        elif t.get("pnl", 0) < 0:
            current_loss += 1
            current_win = 0
            max_consec_loss = max(max_consec_loss, current_loss)

    # 按方向统计
    long_trades = [t for t in trades if t.get("side") == "LONG"]
    short_trades = [t for t in trades if t.get("side") == "SHORT"]

    long_pnl = sum(t.get("pnl", 0) for t in long_trades)
    short_pnl = sum(t.get("pnl", 0) for t in short_trades)

    # 累计权益曲线（简单）
    equity = 0
    equity_curve = []
    for t in trades:
        equity += t.get("pnl", 0)
        equity_curve.append(round(equity, 2))

    print("=" * 70)
    print("📊 ETH 量化策略复盘统计报告（增强版）")
    print("=" * 70)
    print(f"统计时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"总交易次数: {total_trades}")
    print(f"总盈亏: {total_pnl:+.2f} USDT")
    print(f"胜率: {win_rate:.1f}% ({len(wins)}胜 / {len(losses)}负)")
    print(f"Profit Factor: {profit_factor:.2f}")
    print(f"平均盈利: {avg_win:+.2f} USDT | 平均亏损: {avg_loss:+.2f} USDT")
    print(f"最大连续盈利: {max_consec_win} 次 | 最大连续亏损: {max_consec_loss} 次")
    print("-" * 70)
    print(f"LONG 表现: {len(long_trades)}笔 | 总盈亏 {long_pnl:+.2f} USDT")
    print(f"SHORT 表现: {len(short_trades)}笔 | 总盈亏 {short_pnl:+.2f} USDT")
    print("-" * 70)
    print("最近10笔交易累计权益曲线（USDT）:")
    print(equity_curve[-10:] if len(equity_curve) > 10 else equity_curve)
    print("=" * 70)


if __name__ == "__main__":
    trades = load_trades()
    generate_report(trades)
