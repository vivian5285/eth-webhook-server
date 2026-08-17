#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
常驻影子循环：按 symbol_registry 配置，定期检查每个品种是否出现新的已收盘
K线，喂给对应策略，把信号和模拟持仓生命周期写进 shadow_log（run_type='live'）。

只读市场数据、只写自己的sqlite，不 import 任何账户代码、不需要任何API Key，
不会也不能触发任何真实下单——这是设计上的硬边界，不是约定。

2026-08-17：接入第一个真实策略（多周期+持仓状态）后补两件事：
1. 按品种 symbol_registry 里的 "mtf" 配置额外拉取4H/日线等周期，组成
   bars_by_tf 传给策略函数（之前只有单一周期）。
2. 补上通用止损/TP1价格触碰检查——之前只有 backtest_runner.py 有这段，
   live这边完全没做，导致实时影子仓位只会在策略主动发CLOSE信号或反手时
   平仓，永远不会因为摸到止损/止盈价自己出场，跟回测口径不一致。
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
MTF_LOOKBACK = 250

# symbol -> 上次已经处理过信号判断的那根K线开盘时间，避免同一根K线重复触发
_last_bar_time: dict = {}


def _fetch_bars_by_tf(symbol: str, cfg: dict) -> dict:
    bars_by_tf = {"base": klines.get_bars(symbol, cfg["timeframe"], limit=BARS_LOOKBACK)}
    for tf in cfg.get("mtf") or []:
        bars_by_tf[tf] = klines.get_bars(symbol, tf, limit=MTF_LOOKBACK)
    return bars_by_tf


def _check_generic_stop_tp(pos: dict, bar: dict):
    side = str(pos.get("side") or "").upper()
    stop = pos.get("stop_loss")
    tp1 = pos.get("tp1")
    if side == "LONG":
        hit_stop = stop is not None and float(bar["l"]) <= float(stop)
        hit_tp = tp1 is not None and float(bar["h"]) >= float(tp1)
    else:
        hit_stop = stop is not None and float(bar["h"]) >= float(stop)
        hit_tp = tp1 is not None and float(bar["l"]) <= float(tp1)
    return hit_stop, hit_tp


def _apply_position_lifecycle(symbol, strategy, timeframe, run_type, run_id, signal, open_pos):
    action = str(signal.get("action") or "").upper()
    price = signal.get("price")
    bar_time = signal.get("bar_time")

    if action in ("LONG", "SHORT"):
        if open_pos and str(open_pos["side"]).upper() != action:
            shadow_log.close_position(open_pos["id"], price, bar_time, "reverse_signal")
            open_pos = None
        if not open_pos:
            shadow_log.open_position(
                symbol, strategy, timeframe, run_type, action, price, bar_time, run_id,
                stop_loss=signal.get("stop_loss"), tp1=signal.get("tp1"),
            )
    elif action.startswith("CLOSE"):
        if open_pos:
            shadow_log.close_position(open_pos["id"], price, bar_time, "signal_close")


def process_symbol(symbol: str, cfg: dict) -> None:
    strategy_name = cfg["strategy"]
    timeframe = cfg["timeframe"]
    params = cfg.get("params") or {}
    fn = get_strategy(strategy_name)

    bars_by_tf = _fetch_bars_by_tf(symbol, cfg)
    base_bars = bars_by_tf.get("base") or []
    if not base_bars:
        logger.debug(f"[live_runner] {symbol} 暂时拉不到K线，跳过本轮")
        return

    latest_bar_time = base_bars[-1]["t"]
    key = (symbol, strategy_name, timeframe)
    if _last_bar_time.get(key) == latest_bar_time:
        return  # 这根K线已经判断过了，避免重复触发
    _last_bar_time[key] = latest_bar_time

    bar = base_bars[-1]
    open_pos = shadow_log.get_open_position(symbol, strategy_name, timeframe, "live", None)

    # 通用止损/TP1价格触碰检查——策略函数本身只负责"主动离场"，价格穿越
    # 止损/止盈价这件事跟策略逻辑无关，统一在这里处理（回测同款逻辑）
    if open_pos:
        hit_stop, hit_tp = _check_generic_stop_tp(open_pos, bar)
        if hit_stop:
            shadow_log.close_position(open_pos["id"], open_pos["stop_loss"], bar["t"], "stop_loss")
            open_pos = None
        elif hit_tp:
            shadow_log.close_position(open_pos["id"], open_pos["tp1"], bar["t"], "take_profit")
            open_pos = None

    position_arg = None
    if open_pos:
        position_arg = {
            "side": open_pos["side"],
            "entry_price": open_pos["entry_price"],
            "entry_bar_time": open_pos["entry_bar_time"],
        }

    signal = fn(bars_by_tf, params, position_arg)
    if not signal:
        return

    logger.info(f"[live_runner] {symbol}({timeframe}/{strategy_name}) 信号: {signal.get('action')} @ {signal.get('price')} ({signal.get('reason','')})")
    shadow_log.record_signal(symbol, strategy_name, timeframe, "live", signal)
    _apply_position_lifecycle(symbol, strategy_name, timeframe, "live", None, signal, open_pos)


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
