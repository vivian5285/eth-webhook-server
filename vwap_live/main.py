#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""vwap_live 主循环——独立项目，只做一件事：在指定的小批品种上跑
vwap_mean_reversion 逻辑，武装(key+LIVE_TRADING 都打开)才真下单，否则
只记账、发通知，供宝贝在真实行情上先观察几天再决定要不要真开。

跟擂台/chain_sniper/llm_trader 一样的"啥都没配也不崩"原则：没 key 时
只观察；有 key 但 LIVE_TRADING=false 时也只观察(两道闸门都要打开)。
"""
import json
import logging
import logging.handlers
import os
import sys
import time

import db
import executor
import market_data as md
import notifier
import strategy
from config import load_config

LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")


def _setup_logging():
    os.makedirs(LOG_DIR, exist_ok=True)
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s")
    fh = logging.handlers.RotatingFileHandler(
        os.path.join(LOG_DIR, "vwap_live.log"), maxBytes=20 * 1024 * 1024, backupCount=5, encoding="utf-8")
    fh.setFormatter(fmt)
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    root.handlers[:] = [fh, sh]


log = logging.getLogger("main")

_last_bar_time = {}  # symbol -> 上一次评估过的 bar_time，避免同一根K线内重复开仓


def _startup_reconcile(cfg):
    """启动时只读核对：① 这个账户是双向(Hedge)还是单向(One-way)持仓模式
    ——两种模式下单参数完全不同，必须先知道再下单，日志里明确报出来；
    ② 白名单品种里交易所端是否已经有仓位，有的话只报警，不自动接管
    ——这是全新代码，宁可要求人工确认也不要猜错方向。"""
    if not cfg.api_key_present:
        return
    try:
        import binance_futures as bf
        hedge = bf.get_position_mode(cfg)
        log.info("账户持仓模式: %s", "双向(Hedge Mode)" if hedge else "单向(One-way)")
    except Exception as e:
        log.error("查持仓模式失败: %s —— 武装前必须先解决这个问题，否则下单必然被拒", e)
    for symbol in cfg.symbol_list:
        try:
            live = bf_get_position_safe(cfg, symbol)
        except Exception as e:
            log.warning("启动对账 %s 查询失败: %s", symbol, e)
            continue
        if not live:
            continue
        # 2026-09-12 修复：之前这里不看本地账本，只要交易所有仓位就报"未
        # 知持仓"——SOLUSDT/BCHUSDT 人工补录后这里仍然天天误报，容易让人
        # 以为补录没生效。真正该警告的只是"本地确实没有这条记录"的情况。
        local = db.get_open_positions(symbol)
        if local:
            continue
        log.warning("⚠️ 启动对账：交易所 %s 已有持仓 %s qty=%s，本地账本没有记录！"
                    "不会自动接管，需要人工核实这是不是别的程序开的。", symbol, live["side"], live["qty"])
        notifier.send(cfg, f"<b>{cfg.label}</b> ⚠️ 启动发现 {symbol} 交易所已有未知持仓，需人工核实")


def bf_get_position_safe(cfg, symbol):
    import binance_futures as bf
    return bf.get_position(cfg, symbol)


def _decision_pass(cfg):
    for symbol in cfg.symbol_list:
        try:
            bars = md.klines(symbol, cfg.timeframe, 550, cfg.binance_base_url)
            if len(bars) < 30:
                continue
            bar_time = bars[-1]["t"]
            if _last_bar_time.get(symbol) == bar_time:
                continue  # 这根K线已经评估过

            open_pos = db.get_open_positions(symbol)
            position = ({"side": open_pos[0]["side"]} if open_pos else None)
            sig = strategy.evaluate(cfg, symbol, bars, position)
            if not sig:
                continue
            _last_bar_time[symbol] = bar_time

            if sig["action"] == "OPEN" and not open_pos:
                executor.open_position(cfg, symbol, sig)
            elif sig["action"] == "CLOSE" and open_pos:
                executor.close_position(cfg, open_pos[0], sig["reason"], sig["price"])
            elif sig["action"] == "SKIP":
                # 经济性门槛拦下的信号——不是"没信号"，是"信号在但不值当"，
                # 记进决策日志让面板看得到，跟risk.check_can_open的SKIP
                # 是同一个可见性诉求(宝贝要求异常/跳过状态都要能复盘)。
                log.info("%s 经济性门槛拦下: %s", symbol, sig["reason"])
                db.record_decision({"symbol": symbol, "bar_time": sig["bar_time"], "action": "SKIP",
                                     "side": sig.get("side"), "price": sig["price"],
                                     "reason": sig["reason"], "armed": cfg.is_armed})
        except Exception:
            log.exception("处理 %s 出错", symbol)


def main():
    _setup_logging()
    cfg = load_config()
    db.init_db(cfg.db_path)
    log.info("vwap_live 启动 | %s", json.dumps(cfg.redacted(), ensure_ascii=False, default=str))
    if not cfg.is_armed:
        log.warning("未武装(api_key_present=%s, live_trading=%s) —— 观察模式：只记账/发通知，不下真实单。"
                    "两道闸门都打开才会下单：填 BINANCE_API_KEY/SECRET + LIVE_TRADING=true 后 systemctl restart。",
                    cfg.api_key_present, cfg.live_trading)
    _startup_reconcile(cfg)
    notifier.send(cfg, f"<b>{cfg.label}</b> 启动 · 品种 {'/'.join(cfg.symbol_list)} · "
                       f"{'🔴 已武装(真实下单)' if cfg.is_armed else '🟡 观察模式'} · "
                       f"仓位=权益×{cfg.position_size_pct:.0%}×{cfg.leverage:.0f}x · "
                       f"总敞口上限=权益×{cfg.max_total_notional_mult:.0f} · 日亏熔断${cfg.daily_loss_limit_usd:.0f}")

    last_exit = 0.0
    while True:
        cycle = time.time()
        try:
            if cycle - last_exit >= 60:
                executor.check_exits_and_reconcile(cfg)
                last_exit = cycle
            _decision_pass(cfg)
        except Exception:
            log.exception("主循环异常")
        time.sleep(max(15.0, cfg.loop_check_sec - (time.time() - cycle)))


if __name__ == "__main__":
    main()
