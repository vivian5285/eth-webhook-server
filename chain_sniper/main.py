#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""chain_sniper 入口。Phase 1：接入真实 SolanaAdapter，跑通"轮询聪明钱事件 →
复合门控 → 风控闸门 → DRY_RUN开仓 → 离场轮询"整条链路。Phase 2 会再加 BscAdapter
进 adapters 字典（signal_loop/exit_manager已经是链无关写法，加一条链不用改这两个循环）。

2026-09-04：加了真相账本——signal_loop 每次门控判定都写 signal_events，对决策
相关的币建 signal_outcomes，由 outcome_tracker 循环之后 36h 内追踪真实价格轨迹。
这是"模拟跑一段时间总结"唯一的数据来源。
"""
import asyncio
import logging
import logging.handlers
import os
import time

import config
import db
import notifier
from execution import exit_manager
import execution.risk_gate as risk_gate
import execution.buyer as buyer
from adapters.solana import SolanaAdapter
from adapters.bsc import BscAdapter
import signals.smart_money as smart_money
import signals.scorer as scorer
import signals.price_feed as price_feed
import signals.token_feed as token_feed
import signals.token_momentum as token_momentum
from signals.outcome_tracker import run_outcome_tracker

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        # 2026-09-05修复：原来是普通FileHandler，没有任何轮转/大小上限——
        # chain_sniper常驻运行、日志只增不减，实测已经攒到291MB还在涨。
        # 换成RotatingFileHandler，单文件封顶50MB、保留5份历史(封顶约
        # 300MB磁盘占用，之后不再无限增长)，不影响日志内容/格式，只是
        # 加了轮转边界。
        logging.handlers.RotatingFileHandler(
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs", "chain_sniper.log"),
            maxBytes=50 * 1024 * 1024,
            backupCount=5,
            encoding="utf-8",
        ),
    ],
)
logger = logging.getLogger("chain_sniper.main")

# (chain, token) -> (last_action, last_record_ts)：折叠 WAIT 每 45s 重复记录
_last_recorded = {}
_RECORD_COOLDOWN = 300.0


def _record_signal(cfg, chain, token, gate):
    """写 signal_events（同一个币同一个动作 5 分钟内不重复记）；对 BUY/WAIT/
    (SKIP 且有聪明钱买过) 的币，补一次 DexScreener 流动性快照，并（非 BUY）
    建 signal_outcome 行。返回 (signal_event_id 或 None, liq_usd, ref_price)。"""
    key = (chain, token)
    prev = _last_recorded.get(key)
    now = time.time()
    if prev and prev[0] == gate.action and (now - prev[1]) < _RECORD_COOLDOWN:
        return None, None, gate.ref_price  # 同币同动作，冷却期内，不重复记

    interesting = gate.action in ("BUY", "WAIT") or (gate.action == "SKIP" and (gate.sm_buys or 0) >= 1)
    liq_usd = None
    ref_price = gate.ref_price
    if interesting:
        snap = price_feed.dex_snapshot(chain, [token]).get(token) or {}
        liq_usd = snap.get("liq_usd")
        if ref_price is None:
            ref_price = snap.get("price")

    which = ",".join(db.recent_wallet_buyers(token, chain, cfg.sm_window_sec)[:8])
    sid = db.record_signal_event(
        chain, token, gate.action, gate.reason, gate.score,
        sm_buys=gate.sm_buys, which_wallets=which, unique_buyers_5m=gate.unique_buyers_5m,
        safety_summary=gate.safety_summary, ref_price=ref_price, liq_usd=liq_usd,
    )
    _last_recorded[key] = (gate.action, now)

    # 每个"决策相关"的币在追踪窗口内建一条 outcome 行（create_signal_outcome
    # 内部按 (chain,token) 去重）。BUY 的额外由 buyer.buy 关联 position。
    if interesting and ref_price:
        db.create_signal_outcome(sid, chain, token, ref_price, liq_usd,
                                 dedup_window_hours=cfg.outcome_track_hours)
    return sid, liq_usd, ref_price


async def signal_loop(cfg, adapters, stop_event=None):
    logger.info("signal loop starting")
    since_ts = time.time() - cfg.sm_window_sec
    while stop_event is None or not stop_event.is_set():
        try:
            for chain_name, adapter in adapters.items():
                smart_money.poll_and_record(adapter, since_ts)
                for token in db.get_recent_event_tokens(chain_name, cfg.sm_window_sec):
                    gate = scorer.evaluate(token, adapter, cfg)
                    sid, liq_usd, ref_price = _record_signal(cfg, chain_name, token, gate)

                    if gate.action == "BUY":
                        decision = risk_gate.check_can_buy(cfg, token, chain_name)
                        if not decision.allowed:
                            notifier.send_skip(decision.reason, token, chain_name)
                            logger.info("BUY signal but risk_gate blocked token=%s: %s", token, decision.reason)
                            continue
                        px = ref_price or adapter.get_current_price(token)
                        if px is None:
                            continue
                        buyer.buy(cfg, adapter, token, px, liq_usd=liq_usd, signal_event_id=sid)
                    elif gate.action == "SKIP":
                        logger.info("signal skip token=%s reason=%s", token, gate.reason)
            since_ts = time.time()
        except Exception as e:
            logger.error("signal_loop error: %s", e, exc_info=True)
            try:
                notifier.send_error("signal_loop", str(e))
            except Exception:
                pass
        await asyncio.sleep(getattr(cfg, "signal_loop_interval_sec", 45))


_tm_last_recorded = {}  # (chain, token) -> (action, ts)，折叠代币动量重复记录


async def token_momentum_loop(cfg, adapters, stop_event=None):
    """模型B：代币动量模拟盘。定期拉趋势代币榜（token_feed），逐个过
    token_momentum 门控，BUY 的走跟模型A完全一样的 risk_gate -> buyer.buy
    -> 真相账本。默认关闭（TOKEN_MOM_ENABLED），开启后目前只跑 BSC。"""
    if not cfg.token_mom_enabled:
        logger.info("token_momentum_loop 未启用 (TOKEN_MOM_ENABLED=false)")
        return
    chains = cfg.token_mom_chains_list or ["bsc"]
    logger.info("token_momentum_loop starting, chains=%s poll=%ss tp/sl=%.0f/%.0f%%",
                chains, cfg.token_mom_poll_sec, cfg.token_mom_tp_pct * 100, cfg.token_mom_sl_pct * 100)
    while stop_event is None or not stop_event.is_set():
        try:
            for chain_name in chains:
                adapter = adapters.get(chain_name)
                feed = token_feed.get_trending(chain_name, cfg)
                for entry in feed:
                    token = entry["address"]
                    gate = token_momentum.evaluate(entry, cfg)

                    key = (chain_name, token.lower())
                    prev = _tm_last_recorded.get(key)
                    now = time.time()
                    if not (prev and prev[0] == gate.action and (now - prev[1]) < _RECORD_COOLDOWN):
                        which = "src:" + ",".join(entry.get("sources") or [])
                        sid = db.record_signal_event(
                            chain_name, token, gate.action, gate.reason, gate.score,
                            which_wallets=which, safety_summary=gate.safety_summary,
                            ref_price=gate.ref_price, liq_usd=entry.get("liq_usd"),
                            source="token_mom",
                        )
                        _tm_last_recorded[key] = (gate.action, now)
                        if gate.ref_price and gate.action in ("BUY", "WAIT", "SKIP"):
                            db.create_signal_outcome(sid, chain_name, token, gate.ref_price,
                                                     entry.get("liq_usd"),
                                                     dedup_window_hours=cfg.outcome_track_hours)
                    else:
                        sid = None

                    if gate.action == "BUY":
                        decision = risk_gate.check_can_buy(cfg, token, chain_name)
                        if not decision.allowed:
                            logger.info("token-mom BUY but risk_gate blocked token=%s: %s",
                                        token, decision.reason)
                            continue
                        px = gate.ref_price or (adapter.get_current_price(token) if adapter else None)
                        if px is None:
                            continue
                        buyer.buy(cfg, adapter, token, px, liq_usd=entry.get("liq_usd"),
                                  signal_event_id=sid, tp_pct=cfg.token_mom_tp_pct,
                                  sl_pct=cfg.token_mom_sl_pct)
        except Exception as e:
            logger.error("token_momentum_loop error: %s", e, exc_info=True)
            try:
                notifier.send_error("token_momentum_loop", str(e))
            except Exception:
                pass
        await asyncio.sleep(cfg.token_mom_poll_sec)


async def heartbeat_loop(cfg, stop_event=None):
    interval_sec = max(60.0, float(cfg.heartbeat_interval_hours) * 3600.0)
    while stop_event is None or not stop_event.is_set():
        try:
            open_positions = db.count_open_positions()
            daily_loss = db.get_daily_realized_loss()
            ks = db.kill_switch_active(cfg.kill_switch_auto_reset_daily)
            watchlist_size = len(db.load_watchlist())
            notifier.send_heartbeat(open_positions, -daily_loss, ks, watchlist_size)
            logger.info(
                "heartbeat sent open=%s daily_loss=%.2f kill_switch=%s watchlist=%s",
                open_positions, daily_loss, ks, watchlist_size,
            )
        except Exception as e:
            logger.error("heartbeat loop error: %s", e, exc_info=True)
        await asyncio.sleep(interval_sec)


async def main_async():
    cfg = config.load_config()
    logger.info("chain_sniper starting | dry_run=%s | config=%s", cfg.dry_run, cfg.redacted_dict())

    db.init_db(cfg.db_path)
    logger.info("db ready at %s", cfg.db_path)

    if not (cfg.telegram_bot_token and cfg.telegram_chat_id):
        logger.warning("TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID 未配置，通知将被跳过")
    else:
        notifier.send_text(f"【{cfg.label}】🚀 启动 | dry_run={cfg.dry_run}")

    smart_money.load_seed_watchlist_into_db()

    # 两条链共用一套 signal_loop/exit_manager/outcome_tracker（链无关）。
    # BSC 走免费公共节点 eth_getLogs，没有 BSC 监控钱包时 get_watched_wallet_events
    # 直接返回空，几乎零开销。
    adapters = {"solana": SolanaAdapter(cfg), "bsc": BscAdapter(cfg)}

    tasks = [
        asyncio.create_task(signal_loop(cfg, adapters)),
        asyncio.create_task(exit_manager.run_exit_loop(cfg, adapters)),
        asyncio.create_task(run_outcome_tracker(cfg)),
        asyncio.create_task(heartbeat_loop(cfg)),
        asyncio.create_task(token_momentum_loop(cfg, adapters)),
    ]
    await asyncio.gather(*tasks)


def main():
    try:
        asyncio.run(main_async())
    except KeyboardInterrupt:
        logger.info("chain_sniper stopped by KeyboardInterrupt")


if __name__ == "__main__":
    main()
