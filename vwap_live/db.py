#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""vwap_live 持久化层。sqlite3 + 线程锁 + 确定性关闭(WAL + 用完就 close，
跟擂台 shadow_store 2026-09-05 那次 FD 泄漏教训一致)。"""
import os
import sqlite3
import threading
import time
from typing import Any, Dict, List, Optional

_lock = threading.Lock()
_DB_PATH = None


class _AutoClose(sqlite3.Connection):
    def __exit__(self, et, ev, tb):
        try:
            return super().__exit__(et, ev, tb)
        finally:
            self.close()


def _conn():
    os.makedirs(os.path.dirname(_DB_PATH), exist_ok=True)
    c = sqlite3.connect(_DB_PATH, timeout=10, factory=_AutoClose)
    c.row_factory = sqlite3.Row
    try:
        c.execute("PRAGMA journal_mode=WAL")
    except Exception:
        pass
    return c


def init_db(path):
    global _DB_PATH
    _DB_PATH = path
    with _lock, _conn() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS decisions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts REAL NOT NULL, symbol TEXT NOT NULL, bar_time INTEGER NOT NULL,
            action TEXT NOT NULL, side TEXT, price REAL, reason TEXT,
            armed INTEGER NOT NULL, order_result TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_dec_sym ON decisions(symbol, ts);

        CREATE TABLE IF NOT EXISTS positions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL, side TEXT NOT NULL,
            entry_price REAL NOT NULL, qty REAL NOT NULL,
            stop_price REAL, opened_at REAL NOT NULL,
            status TEXT NOT NULL DEFAULT 'OPEN',
            closed_at REAL, exit_price REAL, exit_reason TEXT,
            realized_pnl_usd REAL, armed INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_pos_status ON positions(status);

        CREATE TABLE IF NOT EXISTS daily_pnl (
            day_bucket TEXT PRIMARY KEY,
            realized_pnl_usd REAL DEFAULT 0,
            trades INTEGER DEFAULT 0,
            halted INTEGER DEFAULT 0
        );
        """)
        c.commit()
        # 2026-09-12 迁移：stop_confirmed 标记这笔仓位的止损单是否已经
        # 确认挂到交易所——入场单成交后立刻建这条记录(stop_confirmed=0)，
        # 止损单真正挂成功才置 1。旧库没有这列，ALTER 失败(已存在)就忽略。
        try:
            c.execute("ALTER TABLE positions ADD COLUMN stop_confirmed INTEGER NOT NULL DEFAULT 1")
            c.commit()
        except sqlite3.OperationalError:
            pass
        # 2026-09-12：止盈也要有交易所侧兜底单——tp_price 记录目标价，
        # tp_confirmed 记录是否已确认挂上，跟 stop_confirmed 是同一套模式。
        try:
            c.execute("ALTER TABLE positions ADD COLUMN tp_price REAL")
            c.commit()
        except sqlite3.OperationalError:
            pass
        try:
            c.execute("ALTER TABLE positions ADD COLUMN tp_confirmed INTEGER NOT NULL DEFAULT 1")
            c.commit()
        except sqlite3.OperationalError:
            pass


def _day():
    return time.strftime("%Y-%m-%d", time.gmtime())


def record_decision(row: Dict[str, Any]) -> int:
    with _lock, _conn() as c:
        cur = c.execute(
            "INSERT INTO decisions (ts,symbol,bar_time,action,side,price,reason,armed,order_result) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (time.time(), row["symbol"], int(row["bar_time"]), row["action"], row.get("side"),
             row.get("price"), row.get("reason"), int(bool(row.get("armed"))), row.get("order_result")))
        c.commit()
        return cur.lastrowid


def create_position(row: Dict[str, Any]) -> int:
    with _lock, _conn() as c:
        cur = c.execute(
            "INSERT INTO positions (symbol,side,entry_price,qty,stop_price,opened_at,status,armed,"
            "stop_confirmed,tp_price,tp_confirmed) VALUES (?,?,?,?,?,?,'OPEN',?,?,?,?)",
            (row["symbol"], row["side"], float(row["entry_price"]), float(row["qty"]),
             row.get("stop_price"), time.time(), int(bool(row.get("armed"))),
             int(bool(row.get("stop_confirmed", True))),
             row.get("tp_price"), int(bool(row.get("tp_confirmed", True)))))
        c.commit()
        return cur.lastrowid


def mark_stop_confirmed(pid: int, stop_price: Optional[float] = None):
    """止损单确认挂到交易所后调用——把这笔仓位从"入场了但止损待定"
    转正。stop_price 传值时一并回填(比如首次下单失败、重试时用了新算
    出来的触发价)。"""
    with _lock, _conn() as c:
        if stop_price is not None:
            c.execute("UPDATE positions SET stop_confirmed=1, stop_price=? WHERE id=?",
                      (float(stop_price), int(pid)))
        else:
            c.execute("UPDATE positions SET stop_confirmed=1 WHERE id=?", (int(pid),))
        c.commit()


def mark_tp_confirmed(pid: int):
    """止盈兜底单确认挂到交易所后调用，跟 mark_stop_confirmed 对称。"""
    with _lock, _conn() as c:
        c.execute("UPDATE positions SET tp_confirmed=1 WHERE id=?", (int(pid),))
        c.commit()


def get_positions_missing_stop() -> List[dict]:
    """入场已经成交、止损单还没确认挂上的仓位——每轮循环拿这份清单去
    重试 place_stop_market，直到成功为止，不能让仓位一直裸奔。"""
    with _conn() as c:
        rows = c.execute(
            "SELECT * FROM positions WHERE status='OPEN' AND armed=1 AND stop_confirmed=0").fetchall()
        return [dict(r) for r in rows]


def get_positions_missing_tp() -> List[dict]:
    """止盈兜底单还没确认挂上的仓位——优先级比止损低(不是本金安全问题，
    是"进程挂了会不会自动落袋"的问题)，但同样每轮重试到成功为止。"""
    with _conn() as c:
        rows = c.execute(
            "SELECT * FROM positions WHERE status='OPEN' AND armed=1 AND tp_confirmed=0 "
            "AND tp_price IS NOT NULL").fetchall()
        return [dict(r) for r in rows]


def get_open_positions(symbol: Optional[str] = None) -> List[dict]:
    with _conn() as c:
        if symbol:
            rows = c.execute("SELECT * FROM positions WHERE status='OPEN' AND symbol=?", (symbol,)).fetchall()
        else:
            rows = c.execute("SELECT * FROM positions WHERE status='OPEN'").fetchall()
        return [dict(r) for r in rows]


def count_open_positions() -> int:
    with _conn() as c:
        return c.execute("SELECT COUNT(*) n FROM positions WHERE status='OPEN'").fetchone()["n"]


def close_position(pid: int, exit_price: float, reason: str, pnl_usd: float):
    with _lock, _conn() as c:
        c.execute("UPDATE positions SET status='CLOSED',closed_at=?,exit_price=?,exit_reason=?,"
                  "realized_pnl_usd=? WHERE id=?",
                  (time.time(), float(exit_price), reason, float(pnl_usd), int(pid)))
        c.commit()
    add_daily_pnl(pnl_usd)


def add_daily_pnl(pnl_usd: float):
    d = _day()
    with _lock, _conn() as c:
        c.execute("INSERT INTO daily_pnl (day_bucket,realized_pnl_usd,trades) VALUES (?,?,1) "
                  "ON CONFLICT(day_bucket) DO UPDATE SET "
                  "realized_pnl_usd=realized_pnl_usd+excluded.realized_pnl_usd, trades=trades+1",
                  (d, float(pnl_usd)))
        c.commit()


def today_pnl() -> float:
    with _conn() as c:
        r = c.execute("SELECT realized_pnl_usd FROM daily_pnl WHERE day_bucket=?", (_day(),)).fetchone()
        return float(r["realized_pnl_usd"]) if r else 0.0


def is_halted_today() -> bool:
    with _conn() as c:
        r = c.execute("SELECT halted FROM daily_pnl WHERE day_bucket=?", (_day(),)).fetchone()
        return bool(r["halted"]) if r else False


def set_halted_today(halted: bool = True):
    d = _day()
    with _lock, _conn() as c:
        c.execute("INSERT INTO daily_pnl (day_bucket,halted) VALUES (?,?) "
                  "ON CONFLICT(day_bucket) DO UPDATE SET halted=excluded.halted",
                  (d, int(halted)))
        c.commit()
