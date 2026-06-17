#!/usr/bin/env python3
# trade_logger.py（SQLite 工业级数据库版）
import sqlite3
import os
from datetime import datetime
import logging
from typing import List, Dict, Optional

logger = logging.getLogger(__name__)

# 数据存放路径
DB_DIR = "/home/trading/binance-engine/data"
DB_FILE = os.path.join(DB_DIR, "trade_log.db")

def _init_db():
    """初始化 SQLite 数据库与表结构"""
    if not os.path.exists(DB_DIR):
        os.makedirs(DB_DIR, exist_ok=True)
    
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            action TEXT NOT NULL,
            side TEXT NOT NULL,
            qty REAL NOT NULL,
            price REAL NOT NULL,
            pnl REAL NOT NULL,
            reason TEXT
        )
    ''')
    # 创建索引加快未来查询
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_timestamp ON trades (timestamp)')
    conn.commit()
    conn.close()

# 启动时自动建表
_init_db()

def log_trade(action: str, side: str, qty: float, price: float, pnl: float = 0.0, reason: str = "", extra: Optional[Dict] = None):
    """记录交易到 SQLite 数据库"""
    timestamp = datetime.now().isoformat()
    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO trades (timestamp, action, side, qty, price, pnl, reason)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (timestamp, action, side, qty, price, pnl, reason))
        conn.commit()
        conn.close()
        logger.info(f"[TradeLogger] 数据库已存入: {action} {side} | PnL: {pnl:+.2f}")
    except Exception as e:
        logger.error(f"[TradeLogger] 写入 SQLite 数据库失败: {e}")

def get_recent_trades(limit: int = 20) -> List[Dict]:
    """从数据库抓取最近记录"""
    try:
        conn = sqlite3.connect(DB_FILE)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM trades ORDER BY id DESC LIMIT ?', (limit,))
        rows = cursor.fetchall()
        conn.close()
        return [dict(row) for row in rows][::-1] # 逆序返回
    except Exception as e:
        logger.error(f"[TradeLogger] 读取 SQLite 失败: {e}")
        return []
