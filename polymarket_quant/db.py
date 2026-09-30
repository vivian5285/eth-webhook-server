#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""polymarket_quant 状态持久化。SQLite + WAL，同 chain_sniper/db.py 的写法与理由：
常驻进程里数据摄取/下单/结算对账多条路径并发读写同一批数据，用事务级原子性比
"整体读-改-整体写"的JSON文件更安全，同时依然是单文件，不违背独立兄弟目录的简洁性。"""
import os
import sqlite3
import threading
import time
from datetime import datetime, timezone

_lock = threading.Lock()
_conn = None


def get_conn(db_path=None):
    global _conn
    if _conn is not None:
        return _conn
    path = db_path or os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "polymarket_quant.db")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.row_factory = sqlite3.Row
    _conn = conn
    return _conn


def init_db(db_path=None):
    conn = get_conn(db_path)
    with _lock:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS market_windows (
                window_key TEXT PRIMARY KEY,
                symbol TEXT NOT NULL,
                window_minutes INTEGER NOT NULL,
                window_start_ts REAL NOT NULL,
                window_end_ts REAL NOT NULL,
                token_id_up TEXT DEFAULT '',
                token_id_down TEXT DEFAULT '',
                ref_price_chainlink_start TEXT DEFAULT '',
                status TEXT NOT NULL DEFAULT 'OPEN',
                resolved_outcome TEXT,
                resolved_at REAL
            );

            CREATE TABLE IF NOT EXISTS positions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                window_key TEXT NOT NULL,
                side TEXT NOT NULL,
                order_style TEXT NOT NULL,
                entry_price TEXT NOT NULL,
                entry_shares TEXT NOT NULL,
                entry_usd TEXT NOT NULL,
                entry_order_id TEXT DEFAULT '',
                opened_at REAL NOT NULL,
                status TEXT NOT NULL DEFAULT 'OPEN',
                closed_at REAL,
                exit_price TEXT,
                exit_reason TEXT DEFAULT '',
                fees_usd REAL DEFAULT 0,
                realized_pnl_usd REAL
            );
            CREATE INDEX IF NOT EXISTS idx_positions_status ON positions(status);
            CREATE INDEX IF NOT EXISTS idx_positions_window ON positions(window_key);

            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                position_id INTEGER NOT NULL,
                side TEXT NOT NULL,
                price TEXT NOT NULL,
                shares TEXT NOT NULL,
                fee_usd REAL DEFAULT 0,
                order_id TEXT DEFAULT '',
                ts REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS edge_signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                window_key TEXT NOT NULL,
                ts REAL NOT NULL,
                chainlink_ref_price TEXT,
                binance_price TEXT,
                fair_prob_up REAL,
                market_implied_prob_up REAL,
                divergence REAL,
                fee_adjusted_edge REAL,
                action_taken TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_edge_signals_window ON edge_signals(window_key, ts);

            CREATE TABLE IF NOT EXISTS kill_switch (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                active INTEGER NOT NULL DEFAULT 0,
                reason TEXT DEFAULT '',
                activated_at REAL,
                day_bucket TEXT DEFAULT ''
            );
            """
        )
        conn.execute(
            "INSERT OR IGNORE INTO kill_switch (id, active, reason, day_bucket) VALUES (1, 0, '', '')"
        )
        conn.commit()


def _day_bucket(ts=None):
    dt = datetime.fromtimestamp(ts if ts is not None else time.time(), tz=timezone.utc)
    return dt.strftime("%Y-%m-%d")


# ---- market windows ----

def upsert_market_window(mw):
    conn = get_conn()
    with _lock:
        conn.execute(
            "INSERT INTO market_windows (window_key, symbol, window_minutes, window_start_ts, "
            "window_end_ts, token_id_up, token_id_down, ref_price_chainlink_start, status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(window_key) DO UPDATE SET status=excluded.status",
            (mw.window_key, mw.symbol, mw.window_minutes, mw.window_start_ts, mw.window_end_ts,
             mw.token_id_up, mw.token_id_down, str(mw.ref_price_chainlink_start), mw.status),
        )
        conn.commit()


def get_market_window(window_key):
    conn = get_conn()
    row = conn.execute("SELECT * FROM market_windows WHERE window_key=?", (window_key,)).fetchone()
    return dict(row) if row else None


def set_window_resolved(window_key, outcome):
    conn = get_conn()
    with _lock:
        conn.execute(
            "UPDATE market_windows SET status='RESOLVED', resolved_outcome=?, resolved_at=? "
            "WHERE window_key=?",
            (outcome, time.time(), window_key),
        )
        conn.commit()


# ---- edge signals (calibration log) ----

def log_edge_signal(window_key, chainlink_ref_price, binance_price, fair_prob_up,
                     market_implied_prob_up, divergence, fee_adjusted_edge, action_taken):
    conn = get_conn()
    with _lock:
        conn.execute(
            "INSERT INTO edge_signals (window_key, ts, chainlink_ref_price, binance_price, "
            "fair_prob_up, market_implied_prob_up, divergence, fee_adjusted_edge, action_taken) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (window_key, time.time(), str(chainlink_ref_price), str(binance_price),
             fair_prob_up, market_implied_prob_up, divergence, fee_adjusted_edge, action_taken),
        )
        conn.commit()


# ---- positions / trades ----

def create_position(window_key, side, order_style, entry_price, entry_shares, entry_usd,
                     entry_order_id="", status="OPEN"):
    conn = get_conn()
    with _lock:
        cur = conn.execute(
            "INSERT INTO positions (window_key, side, order_style, entry_price, entry_shares, "
            "entry_usd, entry_order_id, opened_at, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (window_key, side, order_style, str(entry_price), str(entry_shares), str(entry_usd),
             entry_order_id, time.time(), status),
        )
        conn.commit()
        return cur.lastrowid


def get_open_positions():
    conn = get_conn()
    statuses = ("OPEN", "DRY_RUN")
    rows = conn.execute(
        f"SELECT * FROM positions WHERE status IN ({','.join('?' * len(statuses))})",
        statuses,
    ).fetchall()
    return [dict(r) for r in rows]


def count_open_positions():
    conn = get_conn()
    row = conn.execute(
        "SELECT COUNT(*) AS c FROM positions WHERE status IN ('OPEN','DRY_RUN')"
    ).fetchone()
    return int(row["c"] or 0)


def close_position(position_id, exit_price, exit_reason, fees_usd, realized_pnl_usd, status):
    conn = get_conn()
    with _lock:
        conn.execute(
            "UPDATE positions SET status=?, closed_at=?, exit_price=?, exit_reason=?, "
            "fees_usd=?, realized_pnl_usd=? WHERE id=?",
            (status, time.time(), str(exit_price), exit_reason, fees_usd, realized_pnl_usd, position_id),
        )
        conn.commit()


def record_trade(position_id, side, price, shares, fee_usd=0.0, order_id=""):
    conn = get_conn()
    with _lock:
        conn.execute(
            "INSERT INTO trades (position_id, side, price, shares, fee_usd, order_id, ts) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (position_id, side, str(price), str(shares), fee_usd, order_id, time.time()),
        )
        conn.commit()


def trades_count_since(since_ts):
    conn = get_conn()
    row = conn.execute(
        "SELECT COUNT(*) AS c FROM positions WHERE opened_at>=?", (since_ts,)
    ).fetchone()
    return int(row["c"] or 0)


def get_daily_realized_loss(day_bucket=None):
    conn = get_conn()
    bucket = day_bucket or _day_bucket()
    rows = conn.execute(
        "SELECT realized_pnl_usd, closed_at FROM positions "
        "WHERE realized_pnl_usd IS NOT NULL AND status LIKE 'RESOLVED%'"
    ).fetchall()
    loss = 0.0
    for r in rows:
        if r["closed_at"] is None:
            continue
        if _day_bucket(r["closed_at"]) != bucket:
            continue
        pnl = float(r["realized_pnl_usd"] or 0)
        if pnl < 0:
            loss += -pnl
    return loss


# ---- kill switch ----

def get_kill_switch():
    conn = get_conn()
    row = conn.execute("SELECT * FROM kill_switch WHERE id=1").fetchone()
    return dict(row) if row else {"active": 0, "reason": "", "day_bucket": ""}


def set_kill_switch(active, reason=""):
    conn = get_conn()
    with _lock:
        conn.execute(
            "UPDATE kill_switch SET active=?, reason=?, activated_at=?, day_bucket=? WHERE id=1",
            (1 if active else 0, reason, time.time(), _day_bucket()),
        )
        conn.commit()


def kill_switch_active(auto_reset_daily=True):
    ks = get_kill_switch()
    if not ks.get("active"):
        return False
    if auto_reset_daily and ks.get("day_bucket") and ks.get("day_bucket") != _day_bucket():
        set_kill_switch(False, "")
        return False
    return True
