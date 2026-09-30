#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""polymarket_quant 入口。Phase 0：骨架 + 空跑模式——进程能起、能连DB、能发心跳、
离场循环能跑（暂时没有真实持仓/真实client可管）。

Phase 1 会在这里接入 market_feed.py（WS价格摄取）+ polymarket_client.py（CLOB读写）+
signals/edge.py（公允价值模型），跑出真正的空跑开仓信号。现在 client=None、
edge_evaluator=None 是有意为之，不是遗漏。
"""
import asyncio
import logging
import os
import time

import config
import db
import notifier
import models
import market_feed as market_feed_mod
import polymarket_client as pm_client_mod
import execution.risk_gate as risk_gate
import execution.entry as entry
from execution import exit_manager
from signals import edge as edge_mod

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs", "polymarket_quant.log"),
            encoding="utf-8",
        ),
    ],
)
logger = logging.getLogger("polymarket_quant.main")


def _ensure_window(client, feed, symbol, window_minutes):
    """拿到当前活跃窗口；第一次见到这个窗口时，把当下的参考价（优先Chainlink，
    feed暂时没数据就退化用Binance，Phase1开发环境网络限制下的容错，
    上线前必须确认Chainlink tick能正常到达）捕捉存进DB。"""
    w = client.get_current_window(symbol, window_minutes)
    if w is None:
        return None
    existing = db.get_market_window(w["window_key"])
    if existing:
        return existing

    state = feed.get_state(symbol)
    chainlink_tick = state.latest_chainlink() if state else None
    binance_tick = state.latest_binance() if state else None
    if chainlink_tick:
        ref_price = chainlink_tick[1]
    elif binance_tick:
        ref_price = binance_tick[1]
        logging.getLogger(__name__).warning(
            "window=%s 没有Chainlink tick，退化用Binance价格当参考价（仅限开发环境容错）",
            w["window_key"],
        )
    else:
        return None  # 两路都没数据，宁可不建窗口也不能瞎猜参考价

    mw = models.MarketWindow(
        window_key=w["window_key"], symbol=symbol, window_minutes=window_minutes,
        window_start_ts=w["window_start_ts"], window_end_ts=w["window_end_ts"],
        token_id_up=w["token_id_up"], token_id_down=w["token_id_down"],
        ref_price_chainlink_start=ref_price, status="OPEN",
    )
    db.upsert_market_window(mw)
    return db.get_market_window(w["window_key"])


async def signal_loop(cfg, client, feed, stop_event=None):
    logger.info("signal loop starting (symbol=%s window=%smin)", cfg.symbols, cfg.window_minutes)
    symbol = cfg.symbols.split(",")[0].strip().upper()
    while stop_event is None or not stop_event.is_set():
        try:
            window = _ensure_window(client, feed, symbol, cfg.window_minutes)
            state = feed.get_state(symbol)
            if window is None or state is None or state.is_stale(cfg.feed_stale_timeout_sec):
                await asyncio.sleep(5)
                continue

            spot_tick = state.latest_binance()
            spot_now = spot_tick[1] if spot_tick else None
            midpoint_up = client.get_midpoint(window["token_id_up"])

            gate = edge_mod.evaluate(
                window, window["ref_price_chainlink_start"], spot_now, midpoint_up, cfg,
            )
            if gate.action == "BUY":
                decision = risk_gate.check_can_enter(cfg, net_edge=gate.net_edge)
                if not decision.allowed:
                    notifier.send_skip(decision.reason, window["window_key"])
                    logger.info("BUY signal but risk_gate blocked: %s", decision.reason)
                else:
                    market_price = midpoint_up if gate.side == "BUY_UP" else (1 - float(midpoint_up))
                    entry.enter(cfg, client if not cfg.dry_run else None,
                                window["window_key"], gate.side, gate.order_style, market_price)
            elif gate.reason not in ("insufficient_divergence",):
                # 常见跳过原因不逐条播报（太吵），非常规原因才发Telegram
                logger.info("signal skip window=%s reason=%s", window["window_key"], gate.reason)
        except Exception as e:
            logger.error("signal_loop error: %s", e, exc_info=True)
            try:
                notifier.send_error("signal_loop", str(e))
            except Exception:
                pass
        await asyncio.sleep(5)


async def heartbeat_loop(cfg, stop_event=None):
    interval_sec = max(60.0, float(cfg.heartbeat_interval_hours) * 3600.0)
    while stop_event is None or not stop_event.is_set():
        try:
            open_positions = db.count_open_positions()
            daily_loss = db.get_daily_realized_loss()
            ks = db.kill_switch_active(cfg.kill_switch_auto_reset_daily)
            import time
            trades_today = db.trades_count_since(time.time() - 86400)
            notifier.send_heartbeat(open_positions, -daily_loss, ks, trades_today)
            logger.info(
                "heartbeat sent open=%s daily_loss=%.2f kill_switch=%s trades_today=%s",
                open_positions, daily_loss, ks, trades_today,
            )
        except Exception as e:
            logger.error("heartbeat loop error: %s", e, exc_info=True)
        await asyncio.sleep(interval_sec)


async def main_async():
    cfg = config.load_config()
    logger.info("polymarket_quant starting | dry_run=%s | config=%s", cfg.dry_run, cfg.redacted_dict())

    db.init_db(cfg.db_path)
    logger.info("db ready at %s", cfg.db_path)

    if not (cfg.telegram_bot_token and cfg.telegram_chat_id):
        logger.warning("TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID 未配置，通知将被跳过")
    else:
        notifier.send_text(f"【{cfg.label}】🚀 启动 | dry_run={cfg.dry_run}")

    client = pm_client_mod.PolymarketClient(cfg)
    symbols = [s.strip() for s in cfg.symbols.split(",") if s.strip()]
    feed = market_feed_mod.MarketFeed(cfg.ws_url, symbols, cfg.feed_stale_timeout_sec)

    def edge_evaluator(window):
        """给 exit_manager 的反转早退循环用：复用同一套评估，不重复实现。"""
        symbol = window["symbol"]
        state = feed.get_state(symbol)
        if state is None or state.is_stale(cfg.feed_stale_timeout_sec):
            return None
        spot_tick = state.latest_binance()
        midpoint_up = client.get_midpoint(window["token_id_up"])
        gate = edge_mod.evaluate(
            window, window["ref_price_chainlink_start"],
            spot_tick[1] if spot_tick else None, midpoint_up, cfg,
        )
        return {
            "divergence": (gate.net_edge if gate.side == "BUY_UP" else -gate.net_edge)
            if gate.net_edge is not None else 0.0,
            "market_implied_prob_up": midpoint_up,
        }

    # DRY_RUN 下 exit_manager/entry 只用 client 读数据（查midpoint/查结算），不会真的下单——
    # 下单调用在 execution/entry.py 和 execution/exit_manager.py 里都有 cfg.dry_run 分支拦截
    tasks = [
        asyncio.create_task(feed.run()),
        asyncio.create_task(signal_loop(cfg, client, feed)),
        asyncio.create_task(exit_manager.run_exit_loop(cfg, client, edge_evaluator)),
        asyncio.create_task(heartbeat_loop(cfg)),
    ]
    await asyncio.gather(*tasks)


def main():
    try:
        asyncio.run(main_async())
    except KeyboardInterrupt:
        logger.info("polymarket_quant stopped by KeyboardInterrupt")


if __name__ == "__main__":
    main()
