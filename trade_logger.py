#!/usr/bin/env python3
# trade_logger.py（完整最终版 - 结构化交易日志）
import json
import os
from datetime import datetime
import logging
from typing import List, Dict, Optional

logger = logging.getLogger(__name__)

TRADE_LOG_FILE = "/home/workdir/artifacts/trade_log.jsonl"


def _ensure_log_file():
    """确保日志目录和文件存在"""
    directory = os.path.dirname(TRADE_LOG_FILE)
    if not os.path.exists(directory):
        os.makedirs(directory, exist_ok=True)
    if not os.path.exists(TRADE_LOG_FILE):
        with open(TRADE_LOG_FILE, "w", encoding="utf-8") as f:
            pass  # 创建空文件


def log_trade(
    action: str,
    side: str,
    qty: float,
    price: float,
    pnl: float = 0.0,
    reason: str = "",
    extra: Optional[Dict] = None
):
    """
    记录结构化交易日志（JSON Lines 格式）

    参数:
        action: 操作类型 (OPEN / PARTIAL_CLOSE / FULL_CLOSE)
        side:   方向 (LONG / SHORT)
        qty:    数量
        price:  成交价格
        pnl:    估算盈亏（USDT）
        reason: 原因/备注
        extra:  额外信息（可选字典）
    """
    _ensure_log_file()

    record = {
        "timestamp": datetime.now().isoformat(),
        "action": action,
        "side": side,
        "qty": round(qty, 4),
        "price": round(price, 2),
        "pnl": round(pnl, 2),
        "reason": reason
    }

    if extra:
        record.update(extra)

    try:
        with open(TRADE_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

        logger.debug(f"[TradeLogger] 已记录: {action} {side} {qty} @ {price} | PnL: {pnl:+.2f}")

    except Exception as e:
        logger.error(f"[TradeLogger] 写入交易日志失败: {e}")


def get_recent_trades(limit: int = 20) -> List[Dict]:
    """
    获取最近的交易记录（用于复盘或展示）
    """
    _ensure_log_file()
    records = []

    try:
        with open(TRADE_LOG_FILE, "r", encoding="utf-8") as f:
            lines = f.readlines()

        # 取最后 N 行
        for line in lines[-limit:]:
            line = line.strip()
            if line:
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    continue

        return records

    except Exception as e:
        logger.error(f"[TradeLogger] 读取交易日志失败: {e}")
        return []


def get_today_trades() -> List[Dict]:
    """获取今天的交易记录"""
    today = datetime.now().date().isoformat()
    all_trades = get_recent_trades(limit=200)
    return [t for t in all_trades if t.get("timestamp", "").startswith(today)]


if __name__ == "__main__":
    # 测试用
    log_trade("TEST", "LONG", 0.5, 2450.5, 12.8, "测试日志")
    print("最近交易记录：")
    for trade in get_recent_trades(5):
        print(trade)
