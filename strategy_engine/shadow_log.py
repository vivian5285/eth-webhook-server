#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
影子信号/模拟持仓存储。跟 webhook_log.py 同款模式：sqlite3 + 线程锁 +
_connect/_init_db，所有写操作异常自吞，绝不抛给调用方影响主循环。

两张表：
  shadow_signals   —— 策略每次算出的信号，run_type 区分 live(实时影子)/
                       backtest(历史回测)，run_id 给回测分组用（同一次回测
                       跑出来的所有信号共用一个 run_id）
  shadow_positions —— 模拟持仓生命周期（开仓->平仓配对），平仓后落 pnl，
                       是 backtest_stats.py 算胜率/盈亏比/回撤的数据来源
"""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "data" / "shadow.db"

_lock = threading.Lock()


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def _init_db() -> None:
    with _lock, _connect() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS shadow_signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                strategy TEXT NOT NULL,
                timeframe TEXT NOT NULL,
                run_type TEXT NOT NULL,      -- 'live' | 'backtest'
                run_id TEXT,                 -- 回测批次id；live模式为空
                action TEXT NOT NULL,
                price REAL, atr REAL,
                tp1 REAL, tp2 REAL, tp3 REAL, stop_loss REAL,
                tier INTEGER,
                bar_time INTEGER,            -- 触发信号那根K线的开盘时间(ms)
                created_at REAL NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS shadow_positions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                strategy TEXT NOT NULL,
                timeframe TEXT NOT NULL,
                run_type TEXT NOT NULL,
                run_id TEXT,
                side TEXT NOT NULL,
                entry_price REAL NOT NULL,
                entry_bar_time INTEGER NOT NULL,
                exit_price REAL,
                exit_bar_time INTEGER,
                exit_reason TEXT,            -- 'stop_loss' | 'take_profit' | 'reverse_signal'
                pnl_pct REAL,
                status TEXT NOT NULL,        -- 'open' | 'closed'
                created_at REAL NOT NULL,
                closed_at REAL
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_sig_symbol ON shadow_signals (symbol, run_type, run_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_pos_symbol ON shadow_positions (symbol, run_type, run_id, status)")
        conn.commit()


_init_db()


def record_signal(
    symbol: str, strategy: str, timeframe: str, run_type: str,
    signal: Dict[str, Any], run_id: Optional[str] = None,
) -> Optional[int]:
    try:
        with _lock, _connect() as conn:
            cur = conn.execute(
                """INSERT INTO shadow_signals
                   (symbol, strategy, timeframe, run_type, run_id, action, price, atr,
                    tp1, tp2, tp3, stop_loss, tier, bar_time, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    symbol, strategy, timeframe, run_type, run_id,
                    signal.get("action"), signal.get("price"), signal.get("atr"),
                    signal.get("tp1"), signal.get("tp2"), signal.get("tp3"), signal.get("stop_loss"),
                    signal.get("tier"), signal.get("bar_time"), time.time(),
                ),
            )
            conn.commit()
            return int(cur.lastrowid)
    except Exception as e:
        logger.warning(f"[shadow_log] record_signal 跳过: {e}")
        return None


def open_position(
    symbol: str, strategy: str, timeframe: str, run_type: str,
    side: str, entry_price: float, entry_bar_time: int, run_id: Optional[str] = None,
) -> Optional[int]:
    try:
        with _lock, _connect() as conn:
            cur = conn.execute(
                """INSERT INTO shadow_positions
                   (symbol, strategy, timeframe, run_type, run_id, side, entry_price,
                    entry_bar_time, status, created_at)
                   VALUES (?,?,?,?,?,?,?,?, 'open', ?)""",
                (symbol, strategy, timeframe, run_type, run_id, side, entry_price, entry_bar_time, time.time()),
            )
            conn.commit()
            return int(cur.lastrowid)
    except Exception as e:
        logger.warning(f"[shadow_log] open_position 跳过: {e}")
        return None


def get_open_position(symbol: str, strategy: str, timeframe: str, run_type: str, run_id: Optional[str] = None) -> Optional[dict]:
    try:
        with _connect() as conn:
            row = conn.execute(
                """SELECT * FROM shadow_positions
                   WHERE symbol=? AND strategy=? AND timeframe=? AND run_type=? AND run_id IS ? AND status='open'
                   ORDER BY id DESC LIMIT 1""",
                (symbol, strategy, timeframe, run_type, run_id),
            ).fetchone()
            return dict(row) if row else None
    except Exception as e:
        logger.warning(f"[shadow_log] get_open_position 失败: {e}")
        return None


def close_position(position_id: int, exit_price: float, exit_bar_time: int, exit_reason: str) -> None:
    try:
        with _lock, _connect() as conn:
            row = conn.execute("SELECT * FROM shadow_positions WHERE id=?", (position_id,)).fetchone()
            if not row:
                return
            entry = float(row["entry_price"])
            direction = 1.0 if str(row["side"]).upper() == "LONG" else -1.0
            pnl_pct = ((exit_price - entry) / entry) * 100.0 * direction if entry else 0.0
            conn.execute(
                """UPDATE shadow_positions SET exit_price=?, exit_bar_time=?, exit_reason=?,
                   pnl_pct=?, status='closed', closed_at=? WHERE id=?""",
                (exit_price, exit_bar_time, exit_reason, pnl_pct, time.time(), position_id),
            )
            conn.commit()
    except Exception as e:
        logger.warning(f"[shadow_log] close_position 跳过: {e}")


def list_positions(symbol: str, run_type: str, run_id: Optional[str] = None, limit: int = 500) -> List[dict]:
    try:
        with _connect() as conn:
            rows = conn.execute(
                """SELECT * FROM shadow_positions
                   WHERE symbol=? AND run_type=? AND run_id IS ?
                   ORDER BY entry_bar_time ASC LIMIT ?""",
                (symbol, run_type, run_id, limit),
            ).fetchall()
            return [dict(r) for r in rows]
    except Exception as e:
        logger.warning(f"[shadow_log] list_positions 失败: {e}")
        return []


def list_signals(symbol: str, run_type: str, run_id: Optional[str] = None, limit: int = 200) -> List[dict]:
    try:
        with _connect() as conn:
            rows = conn.execute(
                """SELECT * FROM shadow_signals
                   WHERE symbol=? AND run_type=? AND run_id IS ?
                   ORDER BY id DESC LIMIT ?""",
                (symbol, run_type, run_id, limit),
            ).fetchall()
            return [dict(r) for r in rows]
    except Exception as e:
        logger.warning(f"[shadow_log] list_signals 失败: {e}")
        return []


def summary_by_symbol() -> List[dict]:
    """dashboard 列表视图用：每个品种最近一次(live)信号时间 + 已平仓的模拟交易数。"""
    try:
        with _connect() as conn:
            rows = conn.execute("""
                SELECT symbol,
                       MAX(CASE WHEN run_type='live' THEN bar_time END) AS last_live_bar_time,
                       COUNT(CASE WHEN run_type='live' THEN 1 END) AS live_signal_count
                FROM shadow_signals GROUP BY symbol
            """).fetchall()
            return [dict(r) for r in rows]
    except Exception as e:
        logger.warning(f"[shadow_log] summary_by_symbol 失败: {e}")
        return []
