#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
多策略并行影子引擎——2026-08-29新增。跟shadow_runner.py的tv_multiscore_v1
(镜像TV真实策略、模拟VPS自己执行的完整TP1/TP2/TP3分批止盈+呼吸阶梯止损)
是并列的两件事，不是互相替代：那条线回答"如果VPS用TV一样的信号、只是
执行更快更优价，能多赚多少"；这条线回答"抛开TV，几套公开知名战法各自
在真实市场上跑得怎么样，互相比谁更强"。

跟tv_multiscore_v1刻意不同的简化：这里所有策略统一用"整仓入场、整仓出场"
的单腿模型(对齐generate_signal接口文档+backtest_runner.py的历史回测同款
简化口径)，不模拟分批止盈——因为要对比的是"这几套公开发表的原始规则
本身谁的信号质量更好"，不该让VPS自己的执行风格(呼吸阶梯/分批止盈)这层
额外的模拟盖过战法本身的差异，否则比较的就不是战法，是"战法+VPS执行"
这个混合体，会失真。

持久化复用shadow_store.py同一张shadow_positions_v2表(schema本来就按
strategy字段分组，天然支持多策略共存)：tp2_price/tp1_done/tp2_done这些
分批止盈专用字段对这批"整仓策略"固定留空/0，realized_frac固定1.0
(一次性全部平仓)，realized_pnl_atr_weighted = 整笔交易的ATR倍数盈亏，
summary_by_strategy()/summary_by_symbol()两个聚合函数对两种模型通用，
不需要额外分支。

跟tv心跳/实盘完全隔离：只读klines.py(纯公开行情端点，无API Key)，不
import position_supervisor_binance，不碰任何账户凭证/真实下单，符合
既定的"不live-import position_supervisor"规矩。
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

from strategy_engine import klines, shadow_store
from strategy_engine.strategies import get_strategy
from strategy_engine.position_sizing import compute_qty

logger = logging.getLogger(__name__)

COMPARISON_TICK_INTERVAL_SEC = 300  # 5分钟一轮，足够及时捕捉最快周期(1h)的新收盘K线
# 2026-08-29：各战法周期不再统一4h(见comparison_roster.py顶部注释)，最长的
# connors_rsi2(1d, SMA200)需要至少201根；bollinger_squeeze_fast(1h)为了
# 跟4h版本口径上保持同样的"日历天数"回看窗口，squeeze_lookback按比例放大
# 到480根(~20天)。550给两边都留出安全余量。
BARS_LIMIT = 550

# in-process内存态：每个(symbol, strategy)当前是否有模拟持仓，避免每个
# tick都查一次sqlite——跟shadow_engine.py的ShadowPosition内存态同一惯例，
# 但这里不需要ShadowPosition那么重的对象，直接存对我们有用的字段。
_open_positions: Dict[tuple, dict] = {}


def _check_stop_tp(pos: dict, bar: dict):
    """跟backtest_runner.py::_check_stop_tp同一套简化口径：一根K线内到底
    先碰到止损还是先碰到止盈无法从OHLC里还原真实顺序，保守假设止损优先。"""
    side = pos["side"]
    stop = pos.get("stop_loss")
    tp1 = pos.get("tp1")
    if side == "LONG":
        hit_stop = stop is not None and float(bar["l"]) <= float(stop)
        hit_tp = tp1 is not None and float(bar["h"]) >= float(tp1)
    else:
        hit_stop = stop is not None and float(bar["h"]) >= float(stop)
        hit_tp = tp1 is not None and float(bar["l"]) <= float(tp1)
    return hit_stop, hit_tp


def _pnl_atr_weighted(pos: dict, exit_price: float) -> float:
    direction = 1.0 if pos["side"] == "LONG" else -1.0
    atr0 = float(pos.get("atr0") or 0)
    if atr0 <= 0:
        return 0.0
    return round(direction * (exit_price - float(pos["entry"])) / atr0, 4)


def _open_from_signal(symbol: str, strategy: str, timeframe: str, sig: dict) -> Optional[int]:
    tier = int(sig.get("tier") or 1)
    equity = shadow_store.get_equity(strategy)
    qty = compute_qty(equity, float(sig["price"]), sig.get("stop_loss"), tier)
    row = {
        "symbol": symbol, "strategy": strategy, "timeframe": timeframe,
        "side": sig["action"], "entry": float(sig["price"]), "atr0": float(sig.get("atr") or 0),
        "tier": tier, "adx": None,
        "entry_bar_time": int(sig["bar_time"]), "score_bar_time": int(sig["bar_time"]),
        "tp1_price": sig.get("tp1"), "tp2_price": None,
        "stop": sig.get("stop_loss"), "last_ratchet_price": None,
        "tp1_done": 0, "tp2_done": 0,
        "realized_frac": 0, "realized_pnl_atr_weighted": 0, "qty": qty,
    }
    pid = shadow_store.insert_open_row(row)
    if pid is None:
        return None
    mem = dict(row)
    mem["id"] = pid
    mem["stop_loss"] = row["stop"]  # _check_stop_tp用的键名
    mem["tp1"] = row["tp1_price"]
    _open_positions[(symbol, strategy)] = mem
    logger.info(
        f"📈 [多策略][{strategy}][{symbol}] 开仓 {sig['action']} @ {sig['price']:.6f} "
        f"qty={qty:.6f}(净值${equity:.2f}·T{tier}) stop={sig.get('stop_loss')} tp1={sig.get('tp1')}"
    )
    return pid


def _close_position(key: tuple, exit_price: float, bar_time: int, reason: str) -> None:
    pos = _open_positions.pop(key, None)
    if not pos:
        return
    pnl = _pnl_atr_weighted(pos, exit_price)
    shadow_store.close_row(
        pos["id"],
        {"exit_price": round(exit_price, 6), "exit_reason": reason,
         "realized_frac": 1.0, "realized_pnl_atr_weighted": pnl},
        bar_time,
    )
    symbol, strategy = key
    new_equity = shadow_store.settle_trade_on_equity(
        strategy, pnl, float(pos.get("atr0") or 0), float(pos.get("qty") or 0),
    )
    pnl_usd = pnl * float(pos.get("atr0") or 0) * float(pos.get("qty") or 0)
    logger.info(
        f"📉 [多策略][{strategy}][{symbol}] 平仓 @ {exit_price:.6f} "
        f"pnl={pnl:+.2f}×ATR(${pnl_usd:+.2f}) 净值→${new_equity:.2f} | {reason}"
    )


def _hydrate_keys_from_db(keys: List[tuple]) -> None:
    """进程重启后从sqlite恢复内存态持仓——跟shadow_engine.py同类恢复逻辑
    同一惯例，避免重启后"账本记得开过仓、内存不知道"导致重复开仓。

    2026-08-29修复：最初只传了single_symbol_roster的(symbol,strategy)
    键，universe_roster(cross_momentum)的键完全没被恢复——实测复现：
    服务重启后cross_momentum把DB里已经开着的8笔仓位当成"没有持仓"，
    同一根K线用同样的价格重新开了一遍一模一样的仓位，产生完全重复的
    行(id 11-18和19-26)。改成调用方把single+universe两边全部(symbol,
    strategy)键都收集好一起传进来，不再分开处理。"""
    for key in keys:
        if key in _open_positions:
            continue
        symbol, strategy = key
        row = shadow_store.get_open_row(symbol, strategy)
        if row:
            row["stop_loss"] = row.get("stop")
            row["tp1"] = row.get("tp1_price")
            _open_positions[key] = row


def _tick_single_symbol_entry(entry: dict) -> None:
    symbol, strategy, timeframe = entry["symbol"], entry["strategy"], entry["timeframe"]
    params = entry.get("params") or {}
    fn = get_strategy(strategy)
    bars = klines.get_bars(symbol, timeframe, limit=BARS_LIMIT)
    if len(bars) < 30:
        return
    key = (symbol, strategy)
    pos = _open_positions.get(key)
    last_bar = bars[-1]

    if pos:
        hit_stop, hit_tp = _check_stop_tp(pos, last_bar)
        if hit_stop:
            _close_position(key, float(pos["stop_loss"]), int(last_bar["t"]), "触及止损")
            return
        if hit_tp:
            _close_position(key, float(pos["tp1"]), int(last_bar["t"]), "触及止盈")
            return
        sig = fn({"base": bars}, params, {
            "side": pos["side"], "entry_price": pos["entry"],
            "entry_bar_time": pos["entry_bar_time"],
        })
        if sig and str(sig.get("action", "")).startswith("CLOSE"):
            _close_position(key, float(sig["price"]), int(sig["bar_time"]), str(sig.get("reason") or sig["action"]))
        return

    sig = fn({"base": bars}, params, None)
    if sig and sig.get("action") in ("LONG", "SHORT"):
        _open_from_signal(symbol, strategy, timeframe, sig)


def _compute_universe_returns(symbols: List[str], timeframe: str, lookback_bars: int) -> Dict[str, float]:
    out = {}
    for s in symbols:
        bars = klines.get_bars(s, timeframe, limit=lookback_bars + 5)
        if len(bars) < lookback_bars + 1:
            continue
        c_now = float(bars[-1]["c"])
        c_then = float(bars[-1 - lookback_bars]["c"])
        if c_then > 0:
            out[s] = c_now / c_then - 1.0
    return out


def _tick_universe_entry(entry: dict) -> None:
    strategy, timeframe = entry["strategy"], entry["timeframe"]
    symbols = entry["symbols"]
    lookback = int(entry.get("lookback_bars") or 20)
    fn = get_strategy(strategy)
    universe_returns = _compute_universe_returns(symbols, timeframe, lookback)
    if len(universe_returns) < 2:
        return
    bars_cache: Dict[str, list] = {}
    for symbol in symbols:
        bars = bars_cache.get(symbol) or klines.get_bars(symbol, timeframe, limit=BARS_LIMIT)
        bars_cache[symbol] = bars
        if len(bars) < 30:
            continue
        key = (symbol, strategy)
        pos = _open_positions.get(key)
        last_bar = bars[-1]
        params = {"symbol": symbol, "universe_returns": universe_returns, "lookback_bars": lookback}

        if pos:
            hit_stop, hit_tp = _check_stop_tp(pos, last_bar)
            if hit_stop:
                _close_position(key, float(pos["stop_loss"]), int(last_bar["t"]), "触及止损")
                continue
            if hit_tp:
                _close_position(key, float(pos["tp1"]), int(last_bar["t"]), "触及止盈")
                continue
            sig = fn({"base": bars}, params, {
                "side": pos["side"], "entry_price": pos["entry"],
                "entry_bar_time": pos["entry_bar_time"],
            })
            if sig and str(sig.get("action", "")).startswith("CLOSE"):
                _close_position(key, float(sig["price"]), int(sig["bar_time"]), str(sig.get("reason") or sig["action"]))
            continue

        sig = fn({"base": bars}, params, None)
        if sig and sig.get("action") in ("LONG", "SHORT"):
            _open_from_signal(symbol, strategy, timeframe, sig)


def run_comparison_once(single_roster: List[dict], universe_roster: List[dict]) -> None:
    keys = [(e["symbol"], e["strategy"]) for e in single_roster]
    for u in universe_roster:
        keys.extend((s, u["strategy"]) for s in u["symbols"])
    _hydrate_keys_from_db(keys)
    for entry in single_roster:
        try:
            _tick_single_symbol_entry(entry)
        except Exception as e:
            logger.warning(f"[多策略][{entry['strategy']}][{entry['symbol']}] 本轮巡检异常，跳过: {e}")
    for entry in universe_roster:
        try:
            _tick_universe_entry(entry)
        except Exception as e:
            logger.warning(f"[多策略][{entry['strategy']}] 篮子巡检异常，跳过: {e}")


def main_loop():
    from strategy_engine.comparison_roster import SINGLE_SYMBOL_ROSTER, UNIVERSE_ROSTER
    logger.info(
        f"[多策略] 启动，单品种战法{len(SINGLE_SYMBOL_ROSTER)}条 + "
        f"篮子战法{len(UNIVERSE_ROSTER)}条，间隔{COMPARISON_TICK_INTERVAL_SEC}s"
    )
    while True:
        t0 = time.time()
        run_comparison_once(SINGLE_SYMBOL_ROSTER, UNIVERSE_ROSTER)
        elapsed = time.time() - t0
        time.sleep(max(1.0, COMPARISON_TICK_INTERVAL_SEC - elapsed))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] MultiStrategy: %(message)s")
    main_loop()
