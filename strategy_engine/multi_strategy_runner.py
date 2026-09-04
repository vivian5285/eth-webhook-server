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

from strategy_engine import indicators, klines, shadow_store
from strategy_engine.strategies import get_strategy, pairs_trading
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

# 2026-08-31新增：配对交易(pairs_trading)专用内存态——两条腿绑定同开
# 同平，跟_open_positions(单腿战法用，键是symbol+strategy)是完全独立
# 的一套状态，不能塞进同一个dict里（一笔配对仓位对应两行DB记录，但
# 概念上是"一笔交易"）。当前版本一次只做一笔配对(None=空仓)，简化
# 状态管理——想同时跑多对，以后再扩成dict[pair_key]->pair_state。
_open_pair: Optional[dict] = None


def _cached_bars(cache: Dict[tuple, list], symbol: str, timeframe: str, limit: int) -> list:
    """2026-09-04新增：单轮run_comparison_once内的K线拉取去重缓存。

    背景：宝贝新增7套战法 + 4个品种(XRP/SOL/LINK/UNI)后，每轮巡检的
    klines.get_bars调用数从~200涨到~400+，而且大量是重复的——同一个
    (symbol, timeframe, limit)被十几套战法各自拉一遍(比如SOLUSDT@4h被
    turtle/ema_cross/supertrend/breakout_retest/bollinger_squeeze/...
    全都要)。币安期货公开接口的IP限流是2400 request-weight/分钟，
    klines按limit计权重(limit>500记5)，不去重的话一轮峰值能顶到
    ~2000 weight挤在几十秒内打完，余量太薄、偶发429。

    缓存键含limit：不同战法可能要不同根数，键不同就各拉各的，只有
    完全相同的(symbol,timeframe,limit)才复用。缓存**每轮新建**(见
    run_comparison_once)，不跨轮——跨轮必须重新拉最新已收盘K线。
    """
    key = (symbol, timeframe, int(limit))
    if key not in cache:
        cache[key] = klines.get_bars(symbol, timeframe, limit=int(limit))
    return cache[key]


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


def _same_bar_reentry_blocked(symbol: str, strategy: str, sig: dict) -> bool:
    """跟shadow_engine.py同一套2026-08-29的修复口径(shadow_store.
    get_last_closed_meta本身就是那次修复新增的，但当时只接进了
    shadow_engine.py，multi_strategy_runner.py这条线漏接了)：同一根
    本地K线(bar_time没变)、同一方向，如果上一次就是从这根K线开仓又被
    平掉的，说明这份行情数据没有任何新信息——直接重开只会原样重演
    上次的结果。2026-08-31实盘复现：turtle_breakout在XMRUSDT用一根
    单K线内触发突破入场+2N止损的极端行情，5分钟一轮的巡检在这根K线
    仍是"最新已收盘K线"的整个窗口期内(最长能撑到下一根4h/1d收盘)反复
    开平了24次，把回测口径的真实成交频率硬生生撑高了几十倍——
    cross_momentum同一晚也复现了3组，82.6%的"成交"是这个bug刷出来的
    重复行，不是真实信号质量。只堵"完全相同的(方向,K线)"，方向变了
    或者K线真收出新的一根都不受影响。"""
    last_closed = shadow_store.get_last_closed_meta(symbol, strategy)
    return bool(
        last_closed
        and str(last_closed.get("side")) == str(sig.get("action"))
        and int(last_closed.get("entry_bar_time") or -1) == int(sig["bar_time"])
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


def _tick_single_symbol_entry(entry: dict, cache: Dict[tuple, list]) -> None:
    symbol, strategy, timeframe = entry["symbol"], entry["strategy"], entry["timeframe"]
    params = entry.get("params") or {}
    fn = get_strategy(strategy)
    # 2026-09-02新增：单条roster条目可选覆盖默认BARS_LIMIT(550)——
    # vegas_tunnel需要EMA676，550根连算出第一个值都不够，其它战法不传
    # 这个字段时行为完全不变(仍用全局默认值)。
    bars_limit = int(entry.get("bars_limit") or BARS_LIMIT)
    bars = _cached_bars(cache, symbol, timeframe, bars_limit)
    if len(bars) < 30:
        return

    # 2026-09-04新增：多周期(MTF)支持。roster条目带 "mtf": ["1h", ...] 时
    # 额外拉这些周期的K线，一起塞进 bars_by_tf(键=周期字符串，跟
    # backtest_runner.py/symbol_registry.py早就在用的同名机制一致)。
    # 不带 mtf 字段的战法：bars_by_tf 就只有 {"base": bars}，跟改动前
    # 逐字节等价。目前只有 mtf_ema_pullback 用到(高周期1h定潮汐方向)。
    bars_by_tf = {"base": bars}
    for tf in (entry.get("mtf") or []):
        bars_by_tf[str(tf)] = _cached_bars(cache, symbol, tf, bars_limit)
    # symbol 注入 params——funding_trend 需要靠它去拉对应品种的资金费率
    # (跟 _tick_universe_entry 给 cross_momentum 注入 symbol/universe_returns
    # 同一个做法)。其它战法用 {**DEFAULT_PARAMS, **params} 合并，多一个
    # 用不到的 symbol 键完全无害。
    call_params = {**params, "symbol": symbol}

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
        sig = fn(bars_by_tf, call_params, {
            "side": pos["side"], "entry_price": pos["entry"],
            "entry_bar_time": pos["entry_bar_time"],
        })
        if sig and str(sig.get("action", "")).startswith("CLOSE"):
            _close_position(key, float(sig["price"]), int(sig["bar_time"]), str(sig.get("reason") or sig["action"]))
        return

    sig = fn(bars_by_tf, call_params, None)
    if sig and sig.get("action") in ("LONG", "SHORT") and not _same_bar_reentry_blocked(symbol, strategy, sig):
        _open_from_signal(symbol, strategy, timeframe, sig)


def _compute_universe_returns(symbols: List[str], timeframe: str, lookback_bars: int, cache: Dict[tuple, list]) -> Dict[str, float]:
    out = {}
    for s in symbols:
        # 2026-09-04：改成走 _cached_bars(拉 BARS_LIMIT 根)，跟同一轮里
        # 该品种@该周期的信号用K线共用缓存，少打一次接口。只用末尾的
        # bars[-1] / bars[-1-lookback] 两个点，多拉的历史不影响结果。
        bars = _cached_bars(cache, s, timeframe, BARS_LIMIT)
        if len(bars) < lookback_bars + 1:
            continue
        c_now = float(bars[-1]["c"])
        c_then = float(bars[-1 - lookback_bars]["c"])
        if c_then > 0:
            out[s] = c_now / c_then - 1.0
    return out


def _tick_universe_entry(entry: dict, cache: Dict[tuple, list]) -> None:
    strategy, timeframe = entry["strategy"], entry["timeframe"]
    symbols = entry["symbols"]
    lookback = int(entry.get("lookback_bars") or 20)
    fn = get_strategy(strategy)
    universe_returns = _compute_universe_returns(symbols, timeframe, lookback, cache)
    if len(universe_returns) < 2:
        return
    for symbol in symbols:
        bars = _cached_bars(cache, symbol, timeframe, BARS_LIMIT)
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
        if sig and sig.get("action") in ("LONG", "SHORT") and not _same_bar_reentry_blocked(symbol, strategy, sig):
            _open_from_signal(symbol, strategy, timeframe, sig)


def _pair_leg_pnl(side: str, entry: float, atr0: float, exit_price: float) -> float:
    direction = 1.0 if side == "LONG" else -1.0
    if atr0 <= 0:
        return 0.0
    return round(direction * (exit_price - entry) / atr0, 4)


def _hydrate_pair_from_db(strategy: str) -> None:
    """重启恢复配对交易持仓——跟_hydrate_keys_from_db(单腿战法用)是平行
    的独立恢复路径。两条腿共享同一个pair_key，不能套用(symbol,strategy)
    这个键去查，得按pair_key分组把两条腿凑回一笔逻辑上的配对交易。"""
    global _open_pair
    if _open_pair is not None:
        return
    rows = shadow_store.list_open(strategy=strategy)
    if len(rows) < 2:
        return
    by_key: Dict[str, List[dict]] = {}
    for r in rows:
        k = r.get("pair_key")
        if k:
            by_key.setdefault(k, []).append(r)
    for pair_key, legs in by_key.items():
        if len(legs) != 2:
            continue
        a, b = legs[0], legs[1]
        _open_pair = {
            "pair_key": pair_key,
            "symbol_a": a["symbol"], "symbol_b": b["symbol"],
            "id_a": a["id"], "id_b": b["id"],
            "side_a": a["side"], "side_b": b["side"],
            "entry_a": a["entry"], "entry_b": b["entry"],
            "atr0_a": a["atr0"], "atr0_b": b["atr0"],
            "qty_a": a.get("qty") or 0.0, "qty_b": b.get("qty") or 0.0,
            "base_price_a": a.get("pair_base_price") or 0.0,
            "base_price_b": b.get("pair_base_price") or 0.0,
            "formation_mean": a.get("pair_formation_mean") or 0.0,
            "formation_std": a.get("pair_formation_std") or 0.0,
            "stop_a": a.get("stop"), "stop_b": b.get("stop"),
            "entry_bar_time": a.get("entry_bar_time"),
            "hold_bars": 0,  # 重启后重新计数，宁可少算一点持有时长也不去猜历史
        }
        logger.info(
            f"🔄 [多策略][{strategy}] 重启恢复配对持仓 "
            f"{a['symbol']}/{b['symbol']} pair_key={pair_key}"
        )
        return


def _close_pair(exit_price_a: float, exit_price_b: float, bar_time: int, reason: str, strategy: str) -> None:
    global _open_pair
    p = _open_pair
    if not p:
        return
    pnl_a = _pair_leg_pnl(p["side_a"], p["entry_a"], p["atr0_a"], exit_price_a)
    pnl_b = _pair_leg_pnl(p["side_b"], p["entry_b"], p["atr0_b"], exit_price_b)
    shadow_store.close_row(
        p["id_a"], {"exit_price": round(exit_price_a, 6), "exit_reason": reason,
                     "realized_frac": 1.0, "realized_pnl_atr_weighted": pnl_a}, bar_time,
    )
    shadow_store.close_row(
        p["id_b"], {"exit_price": round(exit_price_b, 6), "exit_reason": reason,
                     "realized_frac": 1.0, "realized_pnl_atr_weighted": pnl_b}, bar_time,
    )
    shadow_store.settle_trade_on_equity(strategy, pnl_a, p["atr0_a"], p["qty_a"])
    new_equity = shadow_store.settle_trade_on_equity(strategy, pnl_b, p["atr0_b"], p["qty_b"])
    pnl_usd = pnl_a * p["atr0_a"] * p["qty_a"] + pnl_b * p["atr0_b"] * p["qty_b"]
    logger.info(
        f"📉 [多策略][{strategy}] 配对平仓 {p['symbol_a']}({p['side_a']}@{exit_price_a:.4f})/"
        f"{p['symbol_b']}({p['side_b']}@{exit_price_b:.4f}) "
        f"合计pnl=${pnl_usd:+.2f} 净值→${new_equity:.2f} | {reason}"
    )
    _open_pair = None


def _tick_pairs_entry(entry: dict, cache: Dict[tuple, list]) -> None:
    """配对交易(distance method)专用调度——跟_tick_single_symbol_entry/
    _tick_universe_entry是并列的第三条巡检路径，两条腿绑定同开同平，
    接口/状态管理都不一样，不能复用那两个函数。"""
    global _open_pair
    strategy, timeframe = entry["strategy"], entry["timeframe"]
    symbols = entry["symbols"]
    dp = pairs_trading.DEFAULT_PARAMS
    formation_bars = int(entry.get("formation_bars") or dp["formation_bars"])
    min_formation_bars = int(entry.get("min_formation_bars") or dp["min_formation_bars"])
    entry_std_mult = float(entry.get("entry_std_mult") or dp["entry_std_mult"])
    exit_std_mult = float(entry.get("exit_std_mult") if entry.get("exit_std_mult") is not None else dp["exit_std_mult"])
    max_hold_bars = int(entry.get("max_hold_bars") or dp["max_hold_bars"])
    atr_len = int(entry.get("atr_len") or dp["atr_len"])
    atr_stop_mult = float(entry.get("atr_stop_mult") or dp["atr_stop_mult"])

    _hydrate_pair_from_db(strategy)

    bars_cache: Dict[str, list] = {}
    for s in symbols:
        bars_cache[s] = _cached_bars(cache, s, timeframe, max(BARS_LIMIT, formation_bars + 10))

    if _open_pair:
        p = _open_pair
        bars_a = bars_cache.get(p["symbol_a"])
        bars_b = bars_cache.get(p["symbol_b"])
        if not bars_a or not bars_b:
            return
        last_bar_time = max(int(bars_a[-1]["t"]), int(bars_b[-1]["t"]))
        price_a, price_b = float(bars_a[-1]["c"]), float(bars_b[-1]["c"])

        # 安全网1：任一腿碰到ATR止损——配对逻辑本身靠价差收敛离场，这条
        # 只防极端脱钩(比如某个品种下架/插针)，故意给宽(atr_stop_mult默认3)
        stop_a, stop_b = p.get("stop_a"), p.get("stop_b")
        hit_a = stop_a is not None and (
            (p["side_a"] == "LONG" and float(bars_a[-1]["l"]) <= float(stop_a))
            or (p["side_a"] == "SHORT" and float(bars_a[-1]["h"]) >= float(stop_a))
        )
        hit_b = stop_b is not None and (
            (p["side_b"] == "LONG" and float(bars_b[-1]["l"]) <= float(stop_b))
            or (p["side_b"] == "SHORT" and float(bars_b[-1]["h"]) >= float(stop_b))
        )
        if hit_a or hit_b:
            _close_pair(price_a, price_b, last_bar_time, "任一腿触及ATR止损(配对脱钩)", strategy)
            return

        # 安全网2：持有太久——原始论文用固定6个月交易期，这里改成根数上限，
        # 避免配对关系失效后无限期占着仓位不放
        p["hold_bars"] = int(p.get("hold_bars", 0)) + 1
        if p["hold_bars"] >= max_hold_bars:
            _close_pair(price_a, price_b, last_bar_time, f"持有超过{max_hold_bars}根强制平仓", strategy)
            return

        # 正常离场：价差z-score收敛回形成期均值附近
        if pairs_trading.evaluate_pair_exit(
            price_a, price_b, p["base_price_a"], p["base_price_b"],
            p["formation_mean"], p["formation_std"], exit_std_mult,
        ):
            _close_pair(price_a, price_b, last_bar_time, "价差收敛", strategy)
        return

    # 空仓：找走势最贴合的一对，判断价差是否已经偏离到位
    closes_by_symbol = {s: [float(b["c"]) for b in (bars_cache.get(s) or [])] for s in symbols}
    ranked = pairs_trading.rank_pairs_by_distance(closes_by_symbol, formation_bars, min_formation_bars)
    if not ranked:
        return
    sym_a, sym_b, _dist = ranked[0]
    bars_a, bars_b = bars_cache[sym_a], bars_cache[sym_b]
    closes_a = [float(b["c"]) for b in bars_a]
    closes_b = [float(b["c"]) for b in bars_b]
    sig = pairs_trading.evaluate_pair_entry(closes_a, closes_b, formation_bars, entry_std_mult)
    if not sig:
        return

    bar_time = max(int(bars_a[-1]["t"]), int(bars_b[-1]["t"]))
    # 跟_same_bar_reentry_blocked同一套防护(2026-08-31那次turtle_breakout/
    # cross_momentum同款bug的教训)：同一根K线、同一方向的配对刚被平掉过，
    # 说明这份行情数据没有任何新信息，直接重开只会原样重演上次结果。
    last_closed = shadow_store.get_last_closed_meta(sym_a, strategy)
    if (
        last_closed
        and str(last_closed.get("side")) == str(sig["side_a"])
        and int(last_closed.get("entry_bar_time") or -1) == bar_time
    ):
        return
    pair_key = f"{sym_a}|{sym_b}|{bar_time}"
    price_a, price_b = closes_a[-1], closes_b[-1]

    atr_a = indicators.wilder_atr(bars_a, atr_len)
    atr_b = indicators.wilder_atr(bars_b, atr_len)
    if atr_a <= 0 or atr_b <= 0:
        return

    equity = shadow_store.get_equity(strategy)
    dir_a = 1.0 if sig["side_a"] == "LONG" else -1.0
    dir_b = 1.0 if sig["side_b"] == "LONG" else -1.0
    stop_a = round(price_a - dir_a * atr_stop_mult * atr_a, 6)
    stop_b = round(price_b - dir_b * atr_stop_mult * atr_b, 6)
    qty_a = compute_qty(equity, price_a, stop_a, tier=1)
    qty_b = compute_qty(equity, price_b, stop_b, tier=1)
    if qty_a <= 0 or qty_b <= 0:
        return

    id_a = shadow_store.insert_open_row({
        "symbol": sym_a, "strategy": strategy, "timeframe": timeframe,
        "side": sig["side_a"], "entry": price_a, "atr0": atr_a, "tier": 1,
        "entry_bar_time": bar_time, "score_bar_time": bar_time,
        "qty": qty_a, "stop": stop_a,
        "pair_key": pair_key, "pair_base_price": sig["base_price_a"],
        "pair_formation_mean": sig["formation_mean"], "pair_formation_std": sig["formation_std"],
    })
    id_b = shadow_store.insert_open_row({
        "symbol": sym_b, "strategy": strategy, "timeframe": timeframe,
        "side": sig["side_b"], "entry": price_b, "atr0": atr_b, "tier": 1,
        "entry_bar_time": bar_time, "score_bar_time": bar_time,
        "qty": qty_b, "stop": stop_b,
        "pair_key": pair_key, "pair_base_price": sig["base_price_b"],
        "pair_formation_mean": sig["formation_mean"], "pair_formation_std": sig["formation_std"],
    })
    if id_a is None or id_b is None:
        return

    _open_pair = {
        "pair_key": pair_key, "symbol_a": sym_a, "symbol_b": sym_b,
        "id_a": id_a, "id_b": id_b,
        "side_a": sig["side_a"], "side_b": sig["side_b"],
        "entry_a": price_a, "entry_b": price_b,
        "atr0_a": atr_a, "atr0_b": atr_b,
        "qty_a": qty_a, "qty_b": qty_b,
        "base_price_a": sig["base_price_a"], "base_price_b": sig["base_price_b"],
        "formation_mean": sig["formation_mean"], "formation_std": sig["formation_std"],
        "stop_a": stop_a, "stop_b": stop_b,
        "entry_bar_time": bar_time, "hold_bars": 0,
    }
    logger.info(
        f"📈 [多策略][{strategy}] 配对开仓 {sym_a}({sig['side_a']}@{price_a:.4f})/"
        f"{sym_b}({sig['side_b']}@{price_b:.4f}) z={sig['zscore']:+.2f} "
        f"qty={qty_a:.4f}/{qty_b:.4f}(净值${equity:.2f})"
    )


def run_comparison_once(
    single_roster: List[dict], universe_roster: List[dict], pairs_roster: Optional[List[dict]] = None,
) -> None:
    keys = [(e["symbol"], e["strategy"]) for e in single_roster]
    for u in universe_roster:
        keys.extend((s, u["strategy"]) for s in u["symbols"])
    _hydrate_keys_from_db(keys)
    # 本轮共享的K线拉取缓存，键=(symbol, timeframe, limit)。每轮新建、
    # 不跨轮(见_cached_bars注释)。
    cache: Dict[tuple, list] = {}
    for entry in single_roster:
        try:
            _tick_single_symbol_entry(entry, cache)
        except Exception as e:
            logger.warning(f"[多策略][{entry['strategy']}][{entry['symbol']}] 本轮巡检异常，跳过: {e}")
    for entry in universe_roster:
        try:
            _tick_universe_entry(entry, cache)
        except Exception as e:
            logger.warning(f"[多策略][{entry['strategy']}] 篮子巡检异常，跳过: {e}")
    for entry in (pairs_roster or []):
        try:
            _tick_pairs_entry(entry, cache)
        except Exception as e:
            logger.warning(f"[多策略][{entry['strategy']}] 配对巡检异常，跳过: {e}")


def main_loop():
    from strategy_engine.comparison_roster import SINGLE_SYMBOL_ROSTER, UNIVERSE_ROSTER, PAIRS_ROSTER
    logger.info(
        f"[多策略] 启动，单品种战法{len(SINGLE_SYMBOL_ROSTER)}条 + "
        f"篮子战法{len(UNIVERSE_ROSTER)}条 + 配对战法{len(PAIRS_ROSTER)}条，"
        f"间隔{COMPARISON_TICK_INTERVAL_SEC}s"
    )
    while True:
        t0 = time.time()
        run_comparison_once(SINGLE_SYMBOL_ROSTER, UNIVERSE_ROSTER, PAIRS_ROSTER)
        elapsed = time.time() - t0
        time.sleep(max(1.0, COMPARISON_TICK_INTERVAL_SEC - elapsed))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] MultiStrategy: %(message)s")
    main_loop()
