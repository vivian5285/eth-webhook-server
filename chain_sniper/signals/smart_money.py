#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""聪明钱追踪。种子名单存 watchlist/smart_wallets.json，Phase1启动时灌进DB的
watched_wallets表。Phase1用轮询兜底（每轮调 adapter.get_watched_wallet_events），
Phase2再换Helius webhook做近实时推送——接口保持一致，上层不用改。
"""
import json
import logging
import os

import config
import db

logger = logging.getLogger(__name__)

_WATCHLIST_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "watchlist"
)
_MANUAL_PATH = os.path.join(_WATCHLIST_DIR, "smart_wallets.json")
_DISCOVERED_PATH = os.path.join(_WATCHLIST_DIR, "discovered_wallets.json")


def load_seed_watchlist_into_db():
    """启动时把两份名单同步进 DB，幂等（upsert）：
      - smart_wallets.json    人工维护的种子（source='manual'）
      - discovered_wallets.json  discovery/wallet_finder.py 自动发现的，
        只取 score >= cfg.discovery_score_min 的（source='discovery'）
    """
    cfg = config.load_config()
    total = 0

    try:
        with open(_MANUAL_PATH, encoding="utf-8") as f:
            for w in json.load(f).get("wallets", []):
                addr = w.get("address")
                if not addr:
                    continue
                db.upsert_watched_wallet(addr, w.get("chain", "solana"), w.get("label", ""), source="manual")
                total += 1
    except Exception as e:
        logger.warning("load manual watchlist failed: %s", e)

    auto = 0
    try:
        with open(_DISCOVERED_PATH, encoding="utf-8") as f:
            for w in json.load(f).get("wallets", []):
                addr = w.get("address")
                if not addr or float(w.get("score", 0)) < cfg.discovery_score_min:
                    continue
                label = f"auto s{w.get('score')}/rh{w.get('runner_hits')}/runs{w.get('runs_seen', 1)}"
                db.upsert_watched_wallet(addr, w.get("chain", "solana"), label, source="discovery")
                auto += 1
                total += 1
    except FileNotFoundError:
        logger.info("no discovered_wallets.json yet (discovery 还没跑过)")
    except Exception as e:
        logger.warning("load discovered watchlist failed: %s", e)

    logger.info("watchlist synced into DB: %d total (%d auto-discovered, score>=%.0f)",
                total, auto, cfg.discovery_score_min)
    return total


def poll_and_record(adapter, since_ts):
    """轮询 adapter 拿新的钱包事件，写进 wallet_events 表。返回本轮新增事件数。"""
    events = adapter.get_watched_wallet_events(since_ts)
    for ev in events:
        db.record_wallet_event(ev)
    if events:
        logger.info("smart_money poll: %d new wallet events recorded", len(events))
    return len(events)


def count_recent_buys(token_address, chain, window_sec):
    return db.count_recent_wallet_buys(token_address, chain, window_sec)
