#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
shadow_engine.py 的纯持久化层（sqlite3 + 线程锁，跟 shadow_log.py 同款
模式）。只做行级CRUD，不知道ShadowPosition类长什么样——marshalling在
shadow_engine.py里做，这里只管存/取dict，避免两个模块互相import。

跟遗留的shadow_log.py用不同的db文件(shadow_v2.db)，因为schema完全不同
(这次要存tp1_done/tp2_done/呼吸阶梯状态这些第一版没有的字段，硬塞进老
表意义不大，新开一张干净的表)。老的shadow.db/shadow_log.py保持原样不动，
dashboard"策略"tab如果还在读，不受影响。
"""
from __future__ import annotations

import logging
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "data" / "shadow_v2.db"

_lock = threading.Lock()


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def _init_db() -> None:
    with _lock, _connect() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS shadow_positions_v2 (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                strategy TEXT NOT NULL,
                timeframe TEXT NOT NULL,
                side TEXT NOT NULL,
                entry REAL NOT NULL,
                atr0 REAL NOT NULL,
                tier INTEGER NOT NULL,
                adx REAL,
                entry_bar_time INTEGER NOT NULL,
                last_bar_time INTEGER,
                tp1_price REAL, tp2_price REAL,
                stop REAL, last_ratchet_price REAL,
                tp1_done INTEGER DEFAULT 0, tp2_done INTEGER DEFAULT 0,
                realized_frac REAL DEFAULT 0,
                realized_pnl_atr_weighted REAL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'open',
                exit_price REAL, exit_bar_time INTEGER, exit_reason TEXT,
                created_at REAL NOT NULL,
                closed_at REAL
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_sp2_open "
            "ON shadow_positions_v2 (symbol, strategy, status)"
        )
        conn.commit()


_init_db()

_UPDATABLE_FIELDS = (
    "tp1_price", "tp2_price", "stop", "last_ratchet_price",
    "tp1_done", "tp2_done", "realized_frac", "realized_pnl_atr_weighted",
)


def get_open_row(symbol: str, strategy: str) -> Optional[dict]:
    try:
        with _connect() as conn:
            row = conn.execute(
                """SELECT * FROM shadow_positions_v2
                   WHERE symbol=? AND strategy=? AND status='open'
                   ORDER BY id DESC LIMIT 1""",
                (symbol, strategy),
            ).fetchone()
            return dict(row) if row else None
    except Exception as e:
        logger.warning(f"[shadow_store] get_open_row 失败: {e}")
        return None


def insert_open_row(row: Dict[str, Any]) -> Optional[int]:
    try:
        with _lock, _connect() as conn:
            cur = conn.execute(
                """INSERT INTO shadow_positions_v2
                   (symbol, strategy, timeframe, side, entry, atr0, tier, adx,
                    entry_bar_time, last_bar_time, tp1_price, tp2_price,
                    stop, last_ratchet_price, tp1_done, tp2_done,
                    realized_frac, realized_pnl_atr_weighted, status, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'open',?)""",
                (
                    row["symbol"], row["strategy"], row["timeframe"], row["side"],
                    row["entry"], row["atr0"], row["tier"], row.get("adx"),
                    row["entry_bar_time"], row["entry_bar_time"],
                    row.get("tp1_price"), row.get("tp2_price"),
                    row.get("stop"), row.get("last_ratchet_price"),
                    int(row.get("tp1_done") or 0), int(row.get("tp2_done") or 0),
                    row.get("realized_frac") or 0, row.get("realized_pnl_atr_weighted") or 0,
                    time.time(),
                ),
            )
            conn.commit()
            return int(cur.lastrowid)
    except Exception as e:
        logger.warning(f"[shadow_store] insert_open_row 跳过: {e}")
        return None


def update_row(position_id: int, updates: Dict[str, Any], bar_time: int) -> None:
    try:
        fields = [f for f in _UPDATABLE_FIELDS if f in updates]
        if not fields:
            return
        set_clause = ", ".join(f"{f}=?" for f in fields) + ", last_bar_time=?"
        values = [updates[f] for f in fields] + [bar_time, position_id]
        with _lock, _connect() as conn:
            conn.execute(
                f"UPDATE shadow_positions_v2 SET {set_clause} WHERE id=?", values,
            )
            conn.commit()
    except Exception as e:
        logger.warning(f"[shadow_store] update_row 跳过: {e}")


def close_row(position_id: int, updates: Dict[str, Any], bar_time: int) -> None:
    try:
        fields = [f for f in _UPDATABLE_FIELDS if f in updates]
        set_clause = ", ".join(f"{f}=?" for f in fields)
        values = [updates[f] for f in fields]
        with _lock, _connect() as conn:
            conn.execute(
                f"""UPDATE shadow_positions_v2
                    SET {set_clause}, last_bar_time=?, status='closed',
                        exit_price=?, exit_bar_time=?, exit_reason=?, closed_at=?
                    WHERE id=?""",
                values + [
                    bar_time, updates.get("exit_price"), bar_time,
                    updates.get("exit_reason"), time.time(), position_id,
                ],
            )
            conn.commit()
    except Exception as e:
        logger.warning(f"[shadow_store] close_row 跳过: {e}")


def list_closed(symbol: Optional[str] = None, strategy: Optional[str] = None,
                 limit: int = 500) -> List[dict]:
    try:
        with _connect() as conn:
            q = "SELECT * FROM shadow_positions_v2 WHERE status='closed'"
            params: List[Any] = []
            if symbol:
                q += " AND symbol=?"
                params.append(symbol)
            if strategy:
                q += " AND strategy=?"
                params.append(strategy)
            q += " ORDER BY entry_bar_time ASC LIMIT ?"
            params.append(limit)
            rows = conn.execute(q, params).fetchall()
            return [dict(r) for r in rows]
    except Exception as e:
        logger.warning(f"[shadow_store] list_closed 失败: {e}")
        return []


def list_open(strategy: Optional[str] = None) -> List[dict]:
    try:
        with _connect() as conn:
            q = "SELECT * FROM shadow_positions_v2 WHERE status='open'"
            params: List[Any] = []
            if strategy:
                q += " AND strategy=?"
                params.append(strategy)
            rows = conn.execute(q, params).fetchall()
            return [dict(r) for r in rows]
    except Exception as e:
        logger.warning(f"[shadow_store] list_open 失败: {e}")
        return []


def summary_by_symbol(strategy: str) -> List[dict]:
    """每个品种：已平仓模拟交易数、胜率、平均/累计blended_pnl_pct——
    汇报脚本/dashboard用。"""
    try:
        with _connect() as conn:
            rows = conn.execute(
                """SELECT symbol,
                          COUNT(*) AS trades,
                          SUM(CASE WHEN realized_pnl_atr_weighted > 0 THEN 1 ELSE 0 END) AS wins,
                          ROUND(SUM(realized_pnl_atr_weighted), 4) AS total_pnl_pct,
                          ROUND(AVG(realized_pnl_atr_weighted), 4) AS avg_pnl_pct
                   FROM shadow_positions_v2
                   WHERE status='closed' AND strategy=?
                   GROUP BY symbol ORDER BY symbol""",
                (strategy,),
            ).fetchall()
            return [dict(r) for r in rows]
    except Exception as e:
        logger.warning(f"[shadow_store] summary_by_symbol 失败: {e}")
        return []
