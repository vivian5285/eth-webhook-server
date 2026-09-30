#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""chain_sniper 状态持久化。SQLite + WAL，而非 watchdog/ 那种纯 JSON 状态文件——
本进程有钱包事件接收、买入执行、离场轮询三条路径并发读写同一批数据（候选/持仓/交易历史），
JSON "整体读-改-整体写" 在这种场景下有丢更新风险，SQLite 事务给出按行原子性，仍是单文件，
不违背"独立兄弟目录"的简洁性。"""
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
    path = db_path or os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "chain_sniper.db")
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
            CREATE TABLE IF NOT EXISTS watched_wallets (
                address TEXT NOT NULL,
                chain TEXT NOT NULL,
                label TEXT DEFAULT '',
                added_at REAL NOT NULL,
                active INTEGER NOT NULL DEFAULT 1,
                PRIMARY KEY (address, chain)
            );

            CREATE TABLE IF NOT EXISTS wallet_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chain TEXT NOT NULL,
                wallet TEXT NOT NULL,
                token_address TEXT NOT NULL,
                side TEXT NOT NULL,
                amount TEXT NOT NULL,
                tx_hash TEXT NOT NULL,
                ts REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_wallet_events_token
                ON wallet_events(chain, token_address, ts);

            CREATE TABLE IF NOT EXISTS candidates (
                token_address TEXT NOT NULL,
                chain TEXT NOT NULL,
                ref_price TEXT NOT NULL,
                confirmations INTEGER NOT NULL DEFAULT 0,
                first_seen_ts REAL NOT NULL,
                status TEXT NOT NULL DEFAULT 'PENDING',
                PRIMARY KEY (token_address, chain)
            );

            CREATE TABLE IF NOT EXISTS positions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chain TEXT NOT NULL,
                token_address TEXT NOT NULL,
                entry_price TEXT NOT NULL,
                entry_amount TEXT NOT NULL,
                entry_tx TEXT DEFAULT '',
                opened_at REAL NOT NULL,
                status TEXT NOT NULL DEFAULT 'OPEN',
                tp_price TEXT NOT NULL,
                sl_price TEXT NOT NULL,
                closed_at REAL,
                exit_price TEXT,
                exit_tx TEXT DEFAULT '',
                realized_pnl_usd REAL
            );
            CREATE INDEX IF NOT EXISTS idx_positions_status ON positions(status);
            CREATE INDEX IF NOT EXISTS idx_positions_token ON positions(chain, token_address, opened_at);

            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                position_id INTEGER NOT NULL,
                side TEXT NOT NULL,
                amount TEXT NOT NULL,
                price TEXT NOT NULL,
                tx_hash TEXT DEFAULT '',
                ts REAL NOT NULL,
                pnl_usd REAL
            );

            CREATE TABLE IF NOT EXISTS kill_switch (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                active INTEGER NOT NULL DEFAULT 0,
                reason TEXT DEFAULT '',
                activated_at REAL,
                day_bucket TEXT DEFAULT ''
            );

            -- 2026-09-04：真相账本。每次复合门控判定(BUY/WAIT/SKIP)都记一条
            -- signal_events(带当时的上下文快照)；BUY 的额外建一条 signal_outcomes，
            -- 由 outcome_tracker 循环之后 36h 内每 10 分钟回填价格轨迹。这是
            -- "模拟跑一段时间总结"唯一的数据来源。
            CREATE TABLE IF NOT EXISTS signal_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts REAL NOT NULL,
                chain TEXT NOT NULL,
                token_address TEXT NOT NULL,
                gate_action TEXT NOT NULL,          -- BUY / WAIT / SKIP
                gate_reason TEXT DEFAULT '',
                score REAL,
                sm_buys INTEGER,                    -- 窗口内聪明钱买入去重数
                which_wallets TEXT DEFAULT '',      -- 逗号分隔的触发钱包
                unique_buyers_5m INTEGER,
                safety_summary TEXT DEFAULT '',
                ref_price TEXT,
                liq_usd REAL,
                position_id INTEGER
            );
            CREATE INDEX IF NOT EXISTS idx_signal_events_tok ON signal_events(chain, token_address, ts);
            CREATE INDEX IF NOT EXISTS idx_signal_events_ts ON signal_events(ts);

            CREATE TABLE IF NOT EXISTS signal_outcomes (
                signal_event_id INTEGER PRIMARY KEY,
                chain TEXT NOT NULL,
                token_address TEXT NOT NULL,
                signal_ts REAL NOT NULL,
                ref_price TEXT,
                ref_liq_usd REAL,
                price_15m TEXT, price_1h TEXT, price_6h TEXT, price_24h TEXT,
                mfe_pct REAL,                       -- 追踪窗口内最大浮盈 %
                mae_pct REAL,                       -- 最大浮亏 %
                last_price TEXT, last_liq_usd REAL, last_checked REAL,
                rugged INTEGER DEFAULT 0,
                tracking_done INTEGER DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS idx_signal_outcomes_open
                ON signal_outcomes(tracking_done, signal_ts);
            """
        )
        conn.execute(
            "INSERT OR IGNORE INTO kill_switch (id, active, reason, day_bucket) VALUES (1, 0, '', '')"
        )
        # 2026-09-04 迁移：watched_wallets 增加 source 列（manual / discovery），
        # 区分人工维护的种子名单和 discovery/wallet_finder.py 自动发现的地址。
        # sqlite 没有 ADD COLUMN IF NOT EXISTS，用 try/except 吞掉"已存在"。
        try:
            conn.execute("ALTER TABLE watched_wallets ADD COLUMN source TEXT DEFAULT 'manual'")
        except sqlite3.OperationalError as e:
            if "duplicate column" not in str(e).lower():
                raise
        # 2026-09-06 迁移：signal_events 增加 source 列（wallet / token_mom），
        # 区分"聪明钱钱包跟单"和"代币动量"两个模型的信号，方便 review/面板
        # 分开统计。老行默认 'wallet'。
        try:
            conn.execute("ALTER TABLE signal_events ADD COLUMN source TEXT DEFAULT 'wallet'")
        except sqlite3.OperationalError as e:
            if "duplicate column" not in str(e).lower():
                raise
        conn.commit()


def _day_bucket(ts=None):
    dt = datetime.fromtimestamp(ts if ts is not None else time.time(), tz=timezone.utc)
    return dt.strftime("%Y-%m-%d")


# ---- wallet events / smart money ----

def record_wallet_event(event):
    conn = get_conn()
    with _lock:
        conn.execute(
            "INSERT INTO wallet_events (chain, wallet, token_address, side, amount, tx_hash, ts) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (event.chain, event.wallet, event.token_address, event.side,
             str(event.amount), event.tx_hash, event.ts),
        )
        conn.commit()


def get_recent_event_tokens(chain, window_sec):
    """最近窗口内被监控钱包买过的代币地址去重列表，供 scorer 逐个评估用。"""
    conn = get_conn()
    since = time.time() - window_sec
    rows = conn.execute(
        "SELECT DISTINCT token_address FROM wallet_events WHERE chain=? AND side='buy' AND ts>=?",
        (chain, since),
    ).fetchall()
    return [r["token_address"] for r in rows if r["token_address"]]


def count_recent_wallet_buys(token_address, chain, window_sec):
    conn = get_conn()
    since = time.time() - window_sec
    row = conn.execute(
        "SELECT COUNT(DISTINCT wallet) AS c FROM wallet_events "
        "WHERE chain=? AND token_address=? AND side='buy' AND ts>=?",
        (chain, token_address, since),
    ).fetchone()
    return int(row["c"] or 0)


def recent_wallet_buyers(token_address, chain, window_sec):
    """最近窗口内买过这个币的监控钱包地址(去重)。"""
    conn = get_conn()
    since = time.time() - window_sec
    rows = conn.execute(
        "SELECT DISTINCT wallet FROM wallet_events "
        "WHERE chain=? AND token_address=? AND side='buy' AND ts>=?",
        (chain, token_address, since),
    ).fetchall()
    return [r["wallet"] for r in rows]


# ---- 真相账本：signal_events / signal_outcomes ----

def record_signal_event(chain, token_address, gate_action, gate_reason="", score=None,
                         sm_buys=None, which_wallets="", unique_buyers_5m=None,
                         safety_summary="", ref_price=None, liq_usd=None, position_id=None,
                         source="wallet"):
    """每次门控判定记一条。返回新行 id。source: 'wallet'(聪明钱跟单) /
    'token_mom'(代币动量)。"""
    conn = get_conn()
    with _lock:
        cur = conn.execute(
            "INSERT INTO signal_events (ts, chain, token_address, gate_action, gate_reason, score, "
            "sm_buys, which_wallets, unique_buyers_5m, safety_summary, ref_price, liq_usd, position_id, source) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (time.time(), chain, token_address, gate_action, gate_reason,
             (float(score) if score is not None else None),
             (int(sm_buys) if sm_buys is not None else None), which_wallets,
             (int(unique_buyers_5m) if unique_buyers_5m is not None else None),
             safety_summary, (str(ref_price) if ref_price is not None else None),
             (float(liq_usd) if liq_usd is not None else None),
             (int(position_id) if position_id is not None else None),
             str(source or "wallet")),
        )
        conn.commit()
        return cur.lastrowid


def set_signal_event_position(signal_event_id, position_id):
    conn = get_conn()
    with _lock:
        conn.execute("UPDATE signal_events SET position_id=? WHERE id=?",
                     (int(position_id), int(signal_event_id)))
        conn.commit()


def create_signal_outcome(signal_event_id, chain, token_address, ref_price, ref_liq_usd=None,
                           dedup_window_hours=36):
    """建一条待追踪的 outcome 行。同一个币在 dedup_window_hours 内已有行就跳过
    （一个币会先经历若干个 WAIT signal_event 再到 BUY/SKIP，只追踪一条即可，
    从价格轨迹看差几分钟无所谓；BUY 的模拟盈亏另在 positions 表里算）。"""
    conn = get_conn()
    with _lock:
        exists = conn.execute(
            "SELECT 1 FROM signal_outcomes WHERE chain=? AND token_address=? AND signal_ts>=? LIMIT 1",
            (chain, token_address, time.time() - dedup_window_hours * 3600),
        ).fetchone()
        if exists:
            return
        conn.execute(
            "INSERT OR IGNORE INTO signal_outcomes "
            "(signal_event_id, chain, token_address, signal_ts, ref_price, ref_liq_usd, "
            " mfe_pct, mae_pct, last_checked) VALUES (?,?,?,?,?,?,0,0,?)",
            (int(signal_event_id), chain, token_address, time.time(),
             (str(ref_price) if ref_price is not None else None),
             (float(ref_liq_usd) if ref_liq_usd is not None else None), time.time()),
        )
        conn.commit()


def outcome_exists_for_token(chain, token_address, within_hours):
    """这个币在最近 within_hours 内是否已经有 signal_outcome 行（避免同一个币
    每 45s 的 WAIT 重复建行）。"""
    conn = get_conn()
    since = time.time() - within_hours * 3600
    row = conn.execute(
        "SELECT 1 FROM signal_outcomes WHERE chain=? AND token_address=? AND signal_ts>=? LIMIT 1",
        (chain, token_address, since),
    ).fetchone()
    return row is not None


def get_active_outcomes(max_age_hours):
    """还在追踪窗口内、没标 done 的 outcome 行。"""
    conn = get_conn()
    cutoff = time.time() - max_age_hours * 3600
    rows = conn.execute(
        "SELECT * FROM signal_outcomes WHERE tracking_done=0 AND signal_ts>=? ORDER BY signal_ts",
        (cutoff,),
    ).fetchall()
    return [dict(r) for r in rows]


def update_signal_outcome(signal_event_id, fields: dict):
    if not fields:
        return
    conn = get_conn()
    cols = ", ".join(f"{k}=?" for k in fields)
    with _lock:
        conn.execute(f"UPDATE signal_outcomes SET {cols} WHERE signal_event_id=?",
                     (*fields.values(), int(signal_event_id)))
        conn.commit()


def mark_stale_outcomes_done(max_age_hours):
    """超过追踪窗口的自动收尾。"""
    conn = get_conn()
    cutoff = time.time() - max_age_hours * 3600
    with _lock:
        n = conn.execute(
            "UPDATE signal_outcomes SET tracking_done=1 WHERE tracking_done=0 AND signal_ts<?",
            (cutoff,),
        ).rowcount
        conn.commit()
    return n


def load_watchlist(chain=None):
    conn = get_conn()
    if chain:
        rows = conn.execute(
            "SELECT * FROM watched_wallets WHERE active=1 AND chain=?", (chain,)
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM watched_wallets WHERE active=1").fetchall()
    return [dict(r) for r in rows]


def upsert_watched_wallet(address, chain, label="", source="manual"):
    conn = get_conn()
    with _lock:
        conn.execute(
            "INSERT INTO watched_wallets (address, chain, label, added_at, active, source) "
            "VALUES (?, ?, ?, ?, 1, ?) "
            "ON CONFLICT(address, chain) DO UPDATE SET label=excluded.label, active=1, source=excluded.source",
            (address, chain, label, time.time(), source),
        )
        conn.commit()


def deactivate_wallet(address, chain, reason=""):
    """复盘发现某钱包表现太差时停用（不删，留痕）。"""
    conn = get_conn()
    with _lock:
        conn.execute(
            "UPDATE watched_wallets SET active=0, label=label||' [stopped:'||?||']' "
            "WHERE address=? AND chain=?",
            (reason, address, chain),
        )
        conn.commit()


# ---- candidates (delayed confirmation) ----

def get_or_create_candidate(token_address, chain, ref_price):
    conn = get_conn()
    with _lock:
        row = conn.execute(
            "SELECT * FROM candidates WHERE token_address=? AND chain=?",
            (token_address, chain),
        ).fetchone()
        if row:
            return dict(row)
        conn.execute(
            "INSERT INTO candidates (token_address, chain, ref_price, confirmations, first_seen_ts, status) "
            "VALUES (?, ?, ?, 0, ?, 'PENDING')",
            (token_address, chain, str(ref_price), time.time()),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM candidates WHERE token_address=? AND chain=?",
            (token_address, chain),
        ).fetchone()
        return dict(row)


def bump_candidate_confirmation(token_address, chain):
    conn = get_conn()
    with _lock:
        conn.execute(
            "UPDATE candidates SET confirmations = confirmations + 1 "
            "WHERE token_address=? AND chain=?",
            (token_address, chain),
        )
        conn.commit()


def set_candidate_status(token_address, chain, status):
    conn = get_conn()
    with _lock:
        conn.execute(
            "UPDATE candidates SET status=? WHERE token_address=? AND chain=?",
            (status, token_address, chain),
        )
        conn.commit()


# ---- positions / trades ----

def create_position(chain, token_address, entry_price, entry_amount, entry_tx,
                     tp_price, sl_price, status="OPEN"):
    conn = get_conn()
    with _lock:
        cur = conn.execute(
            "INSERT INTO positions (chain, token_address, entry_price, entry_amount, entry_tx, "
            "opened_at, status, tp_price, sl_price) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (chain, token_address, str(entry_price), str(entry_amount), entry_tx,
             time.time(), status, str(tp_price), str(sl_price)),
        )
        conn.commit()
        return cur.lastrowid


def get_open_positions(chain=None):
    conn = get_conn()
    statuses = ("OPEN", "DRY_RUN")
    if chain:
        rows = conn.execute(
            f"SELECT * FROM positions WHERE status IN ({','.join('?' * len(statuses))}) AND chain=?",
            (*statuses, chain),
        ).fetchall()
    else:
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


def last_position_opened_at(token_address, chain):
    conn = get_conn()
    row = conn.execute(
        "SELECT MAX(opened_at) AS t FROM positions WHERE token_address=? AND chain=?",
        (token_address, chain),
    ).fetchone()
    return row["t"]


def close_position(position_id, exit_price, exit_tx, realized_pnl_usd, status="CLOSED"):
    conn = get_conn()
    with _lock:
        conn.execute(
            "UPDATE positions SET status=?, closed_at=?, exit_price=?, exit_tx=?, realized_pnl_usd=? "
            "WHERE id=?",
            (status, time.time(), str(exit_price), exit_tx, realized_pnl_usd, position_id),
        )
        conn.commit()


def record_trade(position_id, side, amount, price, tx_hash="", pnl_usd=None):
    conn = get_conn()
    with _lock:
        conn.execute(
            "INSERT INTO trades (position_id, side, amount, price, tx_hash, ts, pnl_usd) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (position_id, side, str(amount), str(price), tx_hash, time.time(), pnl_usd),
        )
        conn.commit()


def get_daily_realized_loss(day_bucket=None):
    """当日已实现净亏损（正数=亏损金额，未亏损则为0）。"""
    conn = get_conn()
    bucket = day_bucket or _day_bucket()
    rows = conn.execute(
        "SELECT realized_pnl_usd, closed_at FROM positions "
        "WHERE status='CLOSED' AND realized_pnl_usd IS NOT NULL"
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
