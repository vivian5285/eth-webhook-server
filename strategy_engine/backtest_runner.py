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

2026-08-17：接入第一个多周期策略后新增——回测时如果策略需要4H/日线等大
周期数据（symbol_registry里该品种的"mtf"配置），预先批量拉取这些周期的
完整历史，逐bar回放时用 _slice_closed_before() 只暴露"这根base K线的
开盘时刻为止、已经完全走完"的大周期K线，避免未来函数（比如用当天还没走
完的日线去判断"日线是不是多头趋势"，这在实盘是不可能提前知道的）。
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

# 常用周期的毫秒数，用来判断一根大周期K线是否已经"完全走完"
_PERIOD_MS = {
    "4h": 4 * 60 * 60 * 1000,
    "12h": 12 * 60 * 60 * 1000,
    "1d": 24 * 60 * 60 * 1000,
}


def _period_ms_of(tf: str) -> int:
    if tf in _PERIOD_MS:
        return _PERIOD_MS[tf]
    minutes = klines.timeframe_to_minutes(tf)
    return int(minutes * 60 * 1000) if minutes else 0


def _slice_closed_before(mtf_bars: list, tf: str, cutoff_open_ms: int) -> list:
    """只保留在 cutoff_open_ms（当前base K线开盘时间）之前就已经完全收盘
    的大周期K线——一根K线的收盘时间是它自己的开盘时间+周期长度。"""
    period_ms = _period_ms_of(tf)
    if period_ms <= 0:
        return mtf_bars
    return [b for b in mtf_bars if b["t"] + period_ms <= cutoff_open_ms]


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
    mtf = cfg.get("mtf") or []
    params = cfg.get("params") or {}
    fn = get_strategy(strategy_name)

    minutes = klines.timeframe_to_minutes(timeframe) or 60
    bars_needed = int(days * 24 * 60 / minutes) + warmup_bars
    # get_bars内部会自动分页突破币安单次1500根上限，这里不能再额外clamp到1500，
    # 否则超过~1500根的回测区间(比如跨度一年多)会被悄悄截断成最近那一小段。
    all_base = klines.get_bars(symbol, timeframe, limit=bars_needed)
    if len(all_base) < warmup_bars + 5:
        return {"ok": False, "message": f"历史K线不足({len(all_base)}根)，无法回测"}

    # 预取大周期完整历史，覆盖跟base同样的时间跨度，多拉50根做缓冲
    mtf_all = {}
    if mtf:
        span_ms = (all_base[-1]["t"] - all_base[0]["t"]) if len(all_base) > 1 else 0
        for tf in mtf:
            tf_minutes = klines.timeframe_to_minutes(tf) or 240
            tf_limit = int(span_ms / (tf_minutes * 60 * 1000)) + 50 if span_ms else 500
            mtf_all[tf] = klines.get_bars(symbol, tf, limit=tf_limit, end_time_ms=all_base[-1]["t"] + 1)

    run_id = f"{symbol}-{int(time.time())}-{uuid.uuid4().hex[:6]}"
    open_pos: Optional[dict] = None
    signal_count = 0

    for i in range(warmup_bars, len(all_base)):
        window = all_base[: i + 1]
        bar = window[-1]

        bars_by_tf = {"base": window}
        for tf in mtf:
            bars_by_tf[tf] = _slice_closed_before(mtf_all.get(tf, []), tf, bar["t"])

        if open_pos:
            hit_stop, hit_tp = _check_stop_tp(open_pos, bar)
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
                    stop_loss=signal.get("stop_loss"), tp1=signal.get("tp1"),
                )
                open_pos = {
                    "id": pos_id, "side": action,
                    "entry_price": signal["price"], "entry_bar_time": signal["bar_time"],
                    "stop_loss": signal.get("stop_loss"), "tp1": signal.get("tp1"),
                }
        elif action.startswith("CLOSE") and open_pos:
            shadow_log.close_position(open_pos["id"], signal["price"], signal["bar_time"], "signal_close")
            open_pos = None

    logger.info(f"[backtest] {symbol} run_id={run_id} 用了{len(all_base)-warmup_bars}根K线，产出{signal_count}个信号")
    return {"ok": True, "run_id": run_id, "bars_used": len(all_base) - warmup_bars, "signal_count": signal_count}


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] Backtest: %(message)s")
    sym = sys.argv[1] if len(sys.argv) > 1 else "ZECUSDT"
    print(run_backtest(sym, days=30))
