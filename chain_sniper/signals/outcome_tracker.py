#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""真相账本追踪循环 —— 2026-09-04。

对每一条 signal_outcomes（BUY / WAIT / 聪明钱买了但被我们 SKIP 的安全否决），
在信号后 outcome_track_hours（默认 36h）内每 outcome_poll_sec（默认 10 分钟）
用 DexScreener 快照该币的价格 + 流动性，回填：
  - price_15m / price_1h / price_6h / price_24h（各只填一次）
  - mfe_pct / mae_pct（追踪窗口内最大浮盈 / 浮亏 %）
  - rugged（流动性相对信号时刻暴跌到 <15%，或长时间查不到池子）
rug 掉的币，把它上面还开着的 DRY_RUN 模拟仓强平（假设只剩 3% 残值）。

这是"模拟跑一段时间总结"的数据集。没有这个循环，引擎跑再久也没法复盘。
"""
import asyncio
import logging
import time
from collections import defaultdict

import db
import notifier
import signals.price_feed as price_feed

logger = logging.getLogger(__name__)

_MARKS = ((0.25, "price_15m"), (1.0, "price_1h"), (6.0, "price_6h"), (24.0, "price_24h"))


def _force_close_rugged(chain, rugged_tokens):
    for pos in db.get_open_positions(chain):
        if pos.get("token_address") not in rugged_tokens:
            continue
        if str(pos.get("status")) != "DRY_RUN":
            continue
        entry_amt = float(pos.get("entry_amount") or 0)
        entry_px = float(pos.get("entry_price") or 0)
        exit_px = entry_px * 0.03  # 假设 rug 后只能砸出 ~3% 残值
        pnl = (exit_px - entry_px) * entry_amt
        db.close_position(pos["id"], exit_px, "", pnl, status="DRY_RUN_RUG")
        try:
            notifier.send_exit(dict(pos, realized_pnl_usd=pnl),
                               type("R", (), {"tx_hash": ""})(), reason="RUG", dry_run=True)
        except Exception:
            pass
        logger.warning("rug force-close DRY_RUN pos id=%s token=%s pnl=%.2f",
                       pos["id"], pos["token_address"], pnl)


def _tick(cfg):
    db.mark_stale_outcomes_done(cfg.outcome_track_hours)
    rows = db.get_active_outcomes(cfg.outcome_track_hours)
    if not rows:
        return
    by_chain = defaultdict(list)
    for r in rows:
        by_chain[r["chain"]].append(r)

    now = time.time()
    for chain, chain_rows in by_chain.items():
        tokens = list({r["token_address"] for r in chain_rows})
        snap = {}
        for i in range(0, len(tokens), 30):
            snap.update(price_feed.dex_snapshot(chain, tokens[i:i + 30]))
            time.sleep(1.0)

        rugged = set()
        for r in chain_rows:
            sid = r["signal_event_id"]
            tok = r["token_address"]
            ref = float(r["ref_price"] or 0)
            ref_liq = float(r["ref_liq_usd"] or 0)
            elapsed_h = (now - r["signal_ts"]) / 3600.0
            info = snap.get(tok)
            f = {"last_checked": now}

            if not info or not info.get("price"):
                # 池子查不到：可能只是 DexScreener 抖动。信号 2h 后还查不到 → 判 rug。
                if elapsed_h > 2.0:
                    f["rugged"] = 1
                    f["tracking_done"] = 1
                    rugged.add(tok)
                db.update_signal_outcome(sid, f)
                continue

            px = info["price"]
            liq = info["liq_usd"]
            f["last_price"] = str(px)
            f["last_liq_usd"] = liq
            if ref > 0:
                chg = (px / ref - 1.0) * 100.0
                f["mfe_pct"] = max(float(r.get("mfe_pct") or 0), chg)
                f["mae_pct"] = min(float(r.get("mae_pct") or 0), chg)
            for mark_h, col in _MARKS:
                if elapsed_h >= mark_h and not r.get(col):
                    f[col] = str(px)
            if ref_liq > 0 and liq < ref_liq * cfg.outcome_rug_liq_frac:
                f["rugged"] = 1
                f["tracking_done"] = 1
                rugged.add(tok)
            db.update_signal_outcome(sid, f)

        if rugged:
            _force_close_rugged(chain, rugged)
        logger.info("outcome_tracker [%s]: %d rows, %d tokens, %d rugged",
                    chain, len(chain_rows), len(tokens), len(rugged))


async def run_outcome_tracker(cfg, stop_event=None):
    logger.info("outcome_tracker loop starting, interval=%ss track=%sh",
                cfg.outcome_poll_sec, cfg.outcome_track_hours)
    while stop_event is None or not stop_event.is_set():
        try:
            _tick(cfg)
        except Exception as e:
            logger.error("outcome_tracker error: %s", e, exc_info=True)
        await asyncio.sleep(cfg.outcome_poll_sec)
