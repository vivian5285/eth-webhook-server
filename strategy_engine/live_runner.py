#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
常驻影子循环：按 symbol_registry 配置，定期检查每个品种是否出现新的已收盘
K线，喂给对应策略，把信号和模拟持仓生命周期写进 shadow_log（run_type='live'）。

只读市场数据、只写自己的sqlite，不 import 任何账户代码、不需要任何API Key，
不会也不能触发任何真实下单——这是设计上的硬边界，不是约定。
"""
from __future__ import annotations

import logging
import time

from strategy_engine import klines, shadow_log
from strategy_engine.strategies import get_strategy
from strategy_engine.symbol_registry import SYMBOLS

logger = logging.getLogger(__name__)

POLL_INTERVAL_SEC = 60  # 目前最细的品种周期是50m，1分钟轮询足够及时且省API配额
BARS_LOOKBACK = 300

# symbol -> 上次已经处理过信号判断的那根K线开盘时间，避免同一根K线重复触发
_last_bar_time: dict = {}


def _apply_position_lifecycle(symbol, strategy, timeframe, run_type, run_id, signal):
    action = str(signal.get("action") or "").upper()
    price = signal.get("price")
    bar_time = signal.get("bar_time")
    open_pos = shadow_log.get_open_position(symbol, strategy, timeframe, run_type, run_id)

    if action in ("LONG", "SHORT"):
        if open_pos and str(open_pos["side"]).upper() != action:
            shadow_log.close_position(open_pos["id"], price, bar_time, "reverse_signal")
            open_pos = None
        if not open_pos:
            shadow_log.open_position(symbol, strategy, timeframe, run_type, action, price, bar_time, run_id)
    elif action.startswith("CLOSE"):
        if open_pos:
            shadow_log.close_position(open_pos["id"], price, bar_time, "signal_close")


def process_symbol(symbol: str, cfg: dict) -> None:
    strategy_name = cfg["strategy"]
    timeframe = cfg["timeframe"]
    params = cfg.get("params") or {}
    fn = get_strategy(strategy_name)

    bars = klines.get_bars(symbol, timeframe, limit=BARS_LOOKBACK)
    if not bars:
        logger.debug(f"[live_runner] {symbol} 暂时拉不到K线，跳过本轮")
        return

    latest_bar_time = bars[-1]["t"]
    key = (symbol, strategy_name, timeframe)
    if _last_bar_time.get(key) == latest_bar_time:
        return  # 这根K线已经判断过了，避免重复触发

    signal = fn(bars, params)
    _last_bar_time[key] = latest_bar_time
    if not signal:
        return

    logger.info(f"[live_runner] {symbol}({timeframe}/{strategy_name}) 信号: {signal.get('action')} @ {signal.get('price')}")
    shadow_log.record_signal(symbol, strategy_name, timeframe, "live", signal)
    _apply_position_lifecycle(symbol, strategy_name, timeframe, "live", None, signal)


def run_once() -> None:
    for symbol, cfg in SYMBOLS.items():
        try:
            process_symbol(symbol, cfg)
        except Exception as e:
            logger.warning(f"[live_runner] {symbol} 处理异常，跳过: {e}")


def run_forever() -> None:
    logger.info(f"[live_runner] 启动，{len(SYMBOLS)} 个品种，轮询间隔 {POLL_INTERVAL_SEC}s")
    while True:
        run_once()
        time.sleep(POLL_INTERVAL_SEC)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] ShadowEngine: %(message)s")
    run_forever()
