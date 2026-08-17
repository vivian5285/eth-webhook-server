#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
按需触发的历史回测：批量拉历史K线，逐bar把"截至当前的历史"喂给策略
（模拟"只能看到过去"，不会用未来数据算出当前信号，即无lookahead），
把结果写进 shadow_log（run_type='backtest'），跟实时影子共用同一套
backtest_stats.py 统计口径。

⚠️ 口径简化说明：止损/止盈是否命中用K线的最高/最低价粗略判断，无法得知
一根K线内价格实际的先后顺序，保守假设"止损先触发"；只用 TP1 作为止盈退出
（不模拟真实执行链路的 TP1/TP2/TP3 分批止盈 + 雷达追踪止损）。这是"策略
方向对不对"的快速评估工具，不是精确盈亏模拟——跟真实策略数据接进来后，
如果需要更精确的回测，再针对性升级这部分。
"""
from __future__ import annotations

import logging
import time
import uuid
from typing import Optional

from strategy_engine import klines, shadow_log
from strategy_engine.strategies import get_strategy
from strategy_engine.symbol_registry import get_symbol_config

logger = logging.getLogger(__name__)

DEFAULT_WARMUP_BARS = 60


def _check_stop_tp(pos: dict, bar: dict):
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


def run_backtest(symbol: str, days: int = 30, warmup_bars: int = DEFAULT_WARMUP_BARS) -> dict:
    cfg = get_symbol_config(symbol)
    strategy_name = cfg["strategy"]
    timeframe = cfg["timeframe"]
    params = cfg.get("params") or {}
    fn = get_strategy(strategy_name)

    minutes = klines.timeframe_to_minutes(timeframe) or 60
    bars_needed = int(days * 24 * 60 / minutes) + warmup_bars
    all_bars = klines.get_bars(symbol, timeframe, limit=min(bars_needed, 1500))
    if len(all_bars) < warmup_bars + 5:
        return {"ok": False, "message": f"历史K线不足({len(all_bars)}根)，无法回测"}

    run_id = f"{symbol}-{int(time.time())}-{uuid.uuid4().hex[:6]}"
    open_pos: Optional[dict] = None
    signal_count = 0

    for i in range(warmup_bars, len(all_bars)):
        window = all_bars[: i + 1]
        bar = window[-1]

        if open_pos:
            hit_stop, hit_tp = _check_stop_tp(open_pos, bar)
            if hit_stop:
                shadow_log.close_position(open_pos["id"], open_pos["stop_loss"], bar["t"], "stop_loss")
                open_pos = None
            elif hit_tp:
                shadow_log.close_position(open_pos["id"], open_pos["tp1"], bar["t"], "take_profit")
                open_pos = None

        signal = fn(window, params)
        if not signal:
            continue
        signal_count += 1
        shadow_log.record_signal(symbol, strategy_name, timeframe, "backtest", signal, run_id=run_id)
        action = str(signal.get("action") or "").upper()

        if action in ("LONG", "SHORT"):
            if open_pos and open_pos["side"] != action:
                shadow_log.close_position(open_pos["id"], signal["price"], signal["bar_time"], "reverse_signal")
                open_pos = None
            if not open_pos:
                pos_id = shadow_log.open_position(
                    symbol, strategy_name, timeframe, "backtest",
                    action, signal["price"], signal["bar_time"], run_id,
                )
                open_pos = {
                    "id": pos_id, "side": action,
                    "stop_loss": signal.get("stop_loss"), "tp1": signal.get("tp1"),
                }
        elif action.startswith("CLOSE") and open_pos:
            shadow_log.close_position(open_pos["id"], signal["price"], signal["bar_time"], "signal_close")
            open_pos = None

    logger.info(f"[backtest] {symbol} run_id={run_id} 用了{len(all_bars)-warmup_bars}根K线，产出{signal_count}个信号")
    return {"ok": True, "run_id": run_id, "bars_used": len(all_bars) - warmup_bars, "signal_count": signal_count}


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] Backtest: %(message)s")
    sym = sys.argv[1] if len(sys.argv) > 1 else "ETHUSDT"
    print(run_backtest(sym, days=30))
