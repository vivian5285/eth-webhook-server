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
import math
import time
from typing import Any, Dict, List, Optional

from strategy_engine import funding, indicators, klines, portfolio_guard, shadow_store
from strategy_engine.strategies import get_strategy, pairs_trading
from strategy_engine.position_sizing import compute_qty
from strategy_engine.volume_sentinel import detect_adverse_volume_spike

logger = logging.getLogger(__name__)

COMPARISON_TICK_INTERVAL_SEC = 300  # 5分钟一轮，足够及时捕捉最快周期(1h)的新收盘K线
# 2026-08-29：各战法周期不再统一4h(见comparison_roster.py顶部注释)，最长的
# connors_rsi2(1d, SMA200)需要至少201根；bollinger_squeeze_fast(1h)为了
# 跟4h版本口径上保持同样的"日历天数"回看窗口，squeeze_lookback按比例放大
# 到480根(~20天)。550给两边都留出安全余量。
BARS_LIMIT = 550

# 2026-09-20新增(宝贝要求给头部趋势战法提升"进场敏捷"，参考实盘级别
# 反应速度)：盘中提前入场的动能门槛，跟shadow_engine.py::EARLY_BODY_ATR_
# MULT同一个值——照抄真实TV Pine源码barstate.isrealtime/useEarlyEntry
# 那套(宝贝确认TV实盘本身也开着这个开关)，本仓库另一套引擎(tv_multiscore_
# v1)已经验证过这个思路，这里port成这65套公开战法都能复用的通用版本。
EARLY_ENTRY_BODY_ATR_MULT = 0.5

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


def _entered_this_bar(pos: dict, bar_time: int) -> bool:
    """2026-09-19修复：止损/止盈/战法自己的CLOSE判断，都不能用"入场那一根
    K线"自己的high/low/close去检——入场价锚定的是这根K线的**收盘价**(比如
    vwap_mean_reversion的2σ偏离判断本来就是拿close算的)，但这根K线的
    high/low横跨的是**整根K线周期**，包含了收盘之前发生的价格路径。宝贝
    实测抓到过一个真实案例(XLMUSDT@45m，vwap_mean_reversion_45m)：45m
    K线由3根15m合成，前15分钟先探底到0.18658，后30分钟才涨到0.19053触发
    SHORT入场——但止盈线0.18717在这根K线自己的低点范围内，被判定"同一根
    K线立刻触及止盈"，实际上那个低点发生在入场信号出现**之前**，现实里
    根本不可能吃到。这是标准的"未来函数"：不能拿入场决策还没做出来之前
    就已经发生的价格路径去判定这笔仓位的止损/止盈——只能从**下一根**K线
    开始才允许离场判断。用 entry_bar_time 全局排查过：几乎全部65套战法
    都不同程度受影响(1.5%~95.8%不等)，不是单个策略的bug，是
    _tick_single_symbol_entry/_tick_universe_entry/_tick_pairs_entry
    三条调度路径共用的同一处架构缺陷，这里统一收口，一次修好全部策略。"""
    entry_bar_time = pos.get("entry_bar_time")
    return entry_bar_time is not None and int(bar_time) <= int(entry_bar_time)


# 2026-09-27: 纯"信号反转退出+固定ATR止损"的战法(跟已经实盘的hma_trend/
# ttm_squeeze/heikin_ashi_trend同门派)完全没有止损跟踪——赢麻了的仓位止损
# 还钉在开仓时的老位置，portfolio_guard按entry-to-stop/mark-to-stop算出
# 来的止损风险因此虚高，冻结开仓不是风控过度保守，是在正确反映"浮盈没被
# 收回保护"这个真实缺口。这里补一个只收紧不放松的一次性保本锁：浮盈到1
# 倍原始风险(1R)，止损上移到保本+覆盖手续费，不影响策略自己"等信号反转
# 才出场"这个核心逻辑，只是不让反转触发前的等待期里，一笔已经赚了1R的
# 仓位被行情倒灌回真实亏损。只对没有自己止盈梯度、也不是均值回归类的
# 战法生效(有tp1/tp2或均值回归退出的战法有自己的一套，加这个是画蛇添足
# 甚至冲突)。
BREAKEVEN_LOCK_R_MULT = 1.0
BREAKEVEN_LOCK_FEE_BUFFER_PCT = 0.0015
BREAKEVEN_LOCK_STRATEGIES = {
    "hma_trend", "ttm_squeeze", "heikin_ashi_trend",
    "kaufman_ama", "keltner_channel", "macd_histogram", "supertrend_adx",
    "vortex_indicator", "ichimoku_cloud", "schaff_trend_cycle", "wavetrend",
    "weinstein_stage", "williams_alligator", "parabolic_sar_flip",
    "livermore_pivotal_point", "chanlun_pivot", "td_sequential", "darvas_box",
    "donchian_reversal", "breakout_retest", "opening_range_breakout",
    "raschke_adx_pullback", "kdj_cross", "obv_divergence", "oi_price_confirm",
    "funding_oi_divergence", "funding_trend", "eth_kdj_exempt_narrow",
    "gold_session_breakout", "gold_trend_pullback", "us_stock_rth_momentum",
    "ehlers_fisher_transform", "fiftytwo_week_high",
    "asset_class_trend_ensemble", "tsmom_agile", "heikin_ashi_adaptive_probe",
    "hma_reversal_experiment",
    # 2026-09-29新增：ttm_squeeze的止损宽度/加仓节奏对照名，为了让它们
    # 跟base ttm_squeeze(已经在这份名单里)只有"自己测的那一个维度"不同，
    # 其余机制对齐——tight/wide_stop只改止损宽度，pyramid只改加仓节奏，
    # 都该保留保本锁。trail(自己就是另一套止损机制，会跟保本锁抢着改
    # 同一个stop字段)和tp(测试的是"要不要固定止盈"，故意不叠加别的止损
    # 改动)刻意不放进来，见ATR_TRAIL_STRATEGIES/ttm_squeeze_variants.py。
    "ttm_squeeze_tight_stop", "ttm_squeeze_wide_stop", "ttm_squeeze_pyramid",
    # 2026-09-29新增：renko_trend、fibonacci_retracement、dual_ema_band_7_25
    # 的1h/90m两个周期都是纯趋势/反转离场，没有自己的止盈梯度。
    "renko_trend", "fibonacci_retracement",
    "dual_ema_band_7_25_1h", "dual_ema_band_7_25_90m",
    # 2026-09-30新增：1倍本金现货式对照组，信号/离场逻辑跟不带_spot的
    # 版本完全一样，同样没有自己的止盈梯度，保本锁一样适用。
    "dual_ema_spot_7_25_1h", "dual_ema_spot_7_25_90m",
}


def _maybe_lock_breakeven(pos: dict, mark_price: float, bar_time: int) -> None:
    """浮盈够1R就把pos["stop_loss"]原地上移到保本+手续费缓冲，并写回DB。
    只收紧不放松：已经锁过的不会重复处理(用stop相对entry的位置判断)。"""
    strategy = pos.get("strategy")
    if strategy not in BREAKEVEN_LOCK_STRATEGIES:
        return
    side = str(pos.get("side") or "").upper()
    entry = float(pos.get("entry") or 0.0)
    stop = float(pos.get("stop_loss") or 0.0)
    if entry <= 0 or stop <= 0 or side not in ("LONG", "SHORT") or mark_price <= 0:
        return
    risk = abs(entry - stop)
    if risk <= 0:
        return
    d = 1.0 if side == "LONG" else -1.0
    r_multiple = d * (mark_price - entry) / risk
    if r_multiple < BREAKEVEN_LOCK_R_MULT:
        return
    locked_stop = entry + d * entry * BREAKEVEN_LOCK_FEE_BUFFER_PCT
    already_locked = (
        stop >= locked_stop - 1e-9 if side == "LONG" else stop <= locked_stop + 1e-9
    )
    if already_locked:
        return
    pid = pos.get("id")
    if pid is None:
        return
    if shadow_store.add_to_open_row(pid, {"stop": locked_stop}, bar_time):
        pos["stop_loss"] = locked_stop
        pos["stop"] = locked_stop


# 2026-09-29新增：宝贝要求测"出场逻辑本身"(止盈/移动止损规则)，跟"要不要
# 开仓"分开看。只对ttm_squeeze_trail这个新对照名生效(base ttm_squeeze
# 完全不受影响)——用position自己的atr0(入场时的ATR，跟_add_to_position
# 补仓时更新的是同一个字段)做连续ATR吊灯止损(chandelier exit)：每次tick
# 都算一遍"现价-方向×ATR_TRAIL_MULT×atr0"当候选止损，只收紧不放松，
# 靠这个单向棘轮特性天然实现"从最高/最低点回撤ATR_TRAIL_MULT倍才出场"，
# 不需要额外存历史最高价。浮盈够ATR_TRAIL_MIN_R_MULT(0.5R，比保本锁的
# 1R更早启动)才开始跟踪，避免刚入场就被正常波动扫损。
ATR_TRAIL_MULT = 2.0
ATR_TRAIL_MIN_R_MULT = 0.5
ATR_TRAIL_STRATEGIES = {"ttm_squeeze_trail"}


def _maybe_trail_atr(pos: dict, mark_price: float, bar_time: int) -> None:
    strategy = pos.get("strategy")
    if strategy not in ATR_TRAIL_STRATEGIES:
        return
    side = str(pos.get("side") or "").upper()
    entry = float(pos.get("entry") or 0.0)
    stop = float(pos.get("stop_loss") or 0.0)
    atr0 = float(pos.get("atr0") or 0.0)
    if entry <= 0 or stop <= 0 or atr0 <= 0 or side not in ("LONG", "SHORT") or mark_price <= 0:
        return
    risk = abs(entry - stop)
    if risk <= 0:
        return
    d = 1.0 if side == "LONG" else -1.0
    r_multiple = d * (mark_price - entry) / risk
    if r_multiple < ATR_TRAIL_MIN_R_MULT:
        return
    candidate = mark_price - d * atr0 * ATR_TRAIL_MULT
    new_stop = max(stop, candidate) if side == "LONG" else min(stop, candidate)
    if abs(new_stop - stop) < 1e-9:
        return
    pid = pos.get("id")
    if pid is None:
        return
    if shadow_store.add_to_open_row(pid, {"stop": new_stop}, bar_time):
        pos["stop_loss"] = new_stop
        pos["stop"] = new_stop


# 2026-09-29新增：宝贝要求把TV镜像雷达系统真实在用的止损状态机(照抄
# sndk_dual_ma_strategy.py::evaluate_protective_stop，SNDK账户实盘正在
# 跑的同一套逻辑)搬进擂台，给dual_ema_band_7_25_radar这两个新对照名用。
# 三段式：浮盈达1R先保本，达1.5R后启动移动止损，移动止损的呼吸空间按
# ADX分级(ADX>30强趋势给3.5倍ATR空间，ADX<20弱趋势收紧到1.5倍，居中给
# 2.5倍)——这就是"雷达根据趋势ADX不同，呼吸空间不同"。
#
# 跟原版的两处简化(不是偷工减料，是这个引擎的数据结构决定的)：①原版
# 用逐笔持续追踪的extreme_price(仓位生命周期内的最高/最低价)算移动止损
# 基准，这里没有为此单独加一个持久化字段，改用当前markPrice——因为
# 止损只收紧不放松(下面的单向棘轮逻辑)，多轮tick累积下来效果上等价于
# "从峰值回撤N倍ATR才出场"，不需要额外存历史极值。②原版硬止损阶段有
# 15分钟插针防护(击穿要持续够久才真平)，这里没有实现(保本/追踪阶段
# 原版本来就没有这层防护，直接触发；只有最初的硬止损阶段原版才给缓冲)，
# 简化成硬止损也是一碰即触发——这个引擎本来就是5分钟一轮扫描，插针
# 防护的实际意义比15分钟粒度的实盘要小得多。
RADAR_BREAKEVEN_TRIGGER_ATR = 1.0
RADAR_BREAKEVEN_BUFFER_PCT = 0.0005
RADAR_TRAIL_TRIGGER_ATR = 1.5
RADAR_TRAIL_MULT_STRONG = 3.5
RADAR_TRAIL_MULT_WEAK = 1.5
RADAR_TRAIL_MULT_NORMAL = 2.5
RADAR_ADX_STRONG_BOUND = 30.0
RADAR_ADX_WEAK_BOUND = 20.0
RADAR_ADX_PERIOD = 14
# 2026-09-30应宝贝要求("现货1倍这个仓位我们也要加移动雷达跟踪比较好")，
# 现货等价仓位(SPOT_EQUIVALENT_STRATEGIES)也接入同一套雷达移动止盈——
# 双均线本身仍是绝对主控的开平仓方向，雷达只管插针防守，见下面
# SAME_TICK_REVERSAL_STRATEGIES的注释。
RADAR_TRAIL_STRATEGIES = {
    "dual_ema_band_7_25_radar_1h", "dual_ema_band_7_25_radar_90m",
    "dual_ema_spot_7_25_1h", "dual_ema_spot_7_25_90m",
}


def _maybe_radar_trail(pos: dict, bars: list, mark_price: float, bar_time: int) -> None:
    strategy = pos.get("strategy")
    if strategy not in RADAR_TRAIL_STRATEGIES:
        return
    side = str(pos.get("side") or "").upper()
    entry = float(pos.get("entry") or 0.0)
    stop = float(pos.get("stop_loss") or 0.0)
    atr0 = float(pos.get("atr0") or 0.0)
    if entry <= 0 or stop <= 0 or atr0 <= 0 or side not in ("LONG", "SHORT") or mark_price <= 0:
        return
    if not bars or len(bars) < RADAR_ADX_PERIOD * 2 + 2:
        return
    d = 1.0 if side == "LONG" else -1.0
    profit_atr = d * (mark_price - entry) / atr0
    if profit_atr < RADAR_BREAKEVEN_TRIGGER_ATR:
        return

    candidates = [stop]
    be = entry * (1 + RADAR_BREAKEVEN_BUFFER_PCT) if side == "LONG" else entry * (1 - RADAR_BREAKEVEN_BUFFER_PCT)
    candidates.append(be)

    if profit_atr >= RADAR_TRAIL_TRIGGER_ATR:
        atr_ser = indicators.atr_series(bars, RADAR_ADX_PERIOD)
        if atr_ser:
            atr_now = float(atr_ser[-1])
            if atr_now > 0:
                adx_now = indicators.wilder_adx(bars, RADAR_ADX_PERIOD)
                if adx_now > RADAR_ADX_STRONG_BOUND:
                    mult = RADAR_TRAIL_MULT_STRONG
                elif adx_now < RADAR_ADX_WEAK_BOUND:
                    mult = RADAR_TRAIL_MULT_WEAK
                else:
                    mult = RADAR_TRAIL_MULT_NORMAL
                chandelier = mark_price - d * mult * atr_now
                candidates.append(chandelier)

    new_stop = max(candidates) if side == "LONG" else min(candidates)
    tightened = new_stop > stop if side == "LONG" else new_stop < stop
    if not tightened:
        return
    pid = pos.get("id")
    if pid is None:
        return
    if shadow_store.add_to_open_row(pid, {"stop": new_stop}, bar_time):
        pos["stop_loss"] = new_stop
        pos["stop"] = new_stop


# 2026-09-30：盘中成交量哨兵实验(宝贝要求"先在擂台单独搭一个实验策略
# 验证这套机制真的有用、不会误伤正常波动，再考虑要不要上实盘")——只挂
# 在chanlun_pivot_sentinel这一个独立身份上(见strategies/__init__.py同名
# 注册+comparison_roster.py同名条目的说明)，跟chanlun_pivot本体完全隔离，
# 不影响任何其它65+套战法。用volume_sentinel.py::detect_adverse_volume_spike
# 查还在走的这根K线，量能+价格异动都够格才提前离场，见该模块docstring里
# 2026-09-30记录的"累计量vs整根基线"量纲bug修复过程。
SENTINEL_STRATEGIES = {"chanlun_pivot_sentinel"}


def _maybe_volume_sentinel(pos: dict, key: tuple, symbol: str, timeframe: str, bars: list) -> bool:
    """命中就直接调_close_position平仓，返回True告诉调用方这笔仓位已经
    没了，不要再往下摸pos的其它字段。"""
    strategy = pos.get("strategy")
    if strategy not in SENTINEL_STRATEGIES:
        return False
    side = str(pos.get("side") or "").upper()
    entry = float(pos.get("entry") or 0.0)
    stop = float(pos.get("stop_loss") or 0.0)
    if entry <= 0 or stop <= 0 or side not in ("LONG", "SHORT"):
        return False
    if not bars or len(bars) < 15:
        return False
    current_bar = klines.get_current_bar(symbol, timeframe)
    if not current_bar:
        return False
    bar_duration_min = klines.timeframe_to_minutes(timeframe)
    if not bar_duration_min or bar_duration_min <= 0:
        return False
    elapsed_min = (time.time() * 1000.0 - float(current_bar.get("t") or 0)) / 60000.0
    result = detect_adverse_volume_spike(
        side=side, entry_price=entry, stop_price=stop,
        recent_closed_bars=bars, current_bar=current_bar,
        elapsed_minutes=elapsed_min, bar_duration_minutes=float(bar_duration_min),
    )
    if not result:
        return False
    symbol_, strategy_ = key
    logger.info(
        f"🚨 [多策略][{strategy_}][{symbol_}] 成交量哨兵提前离场 "
        f"vol_ratio={result['volume_ratio']:.1f} progress_frac={result['progress_frac']:.2f} "
        f"close={result['current_close']}"
    )
    _close_position(key, float(result["current_close"]), int(current_bar["t"]), result["reason"])
    return True


def _check_stop_tp(pos: dict, bar: dict):
    """跟backtest_runner.py::_check_stop_tp同一套简化口径：一根K线内到底
    先碰到止损还是先碰到止盈无法从OHLC里还原真实顺序，保守假设止损优先。

    2026-09-24改为组合全仓口径：旧版把每一腿当成独立5x逐仓并计算liq_price，
    会在全仓账户仍有共享保证金时错误地提前判强平。现在不再用这个遗留列做
    单腿强平；风险由组合总敞口、止损热度、相关簇和回撤熔断统一约束。止损
    跳空时按该K线开盘价(更差者)成交，再叠加模拟滑点。"""
    side = pos["side"]
    stop = pos.get("stop_loss")
    tp1 = pos.get("tp1")

    candidates = []
    if stop is not None:
        candidates.append(("stop", float(stop)))

    exit_kind, exit_price = None, None
    if candidates:
        if side == "LONG":
            kind, price = max(candidates, key=lambda kv: kv[1])  # 两个候选都在入场价下方，取更高(更近)的
            hit = float(bar["l"]) <= price
        else:
            kind, price = min(candidates, key=lambda kv: kv[1])  # 两个候选都在入场价上方，取更低(更近)的
            hit = float(bar["h"]) >= price
        if hit:
            exit_kind, exit_price = kind, price
            bar_open = float(bar.get("o") or price)
            # A stop cannot fill at the stale stop level after a gap through it.
            if side == "LONG" and bar_open < price:
                exit_price = bar_open
            elif side == "SHORT" and bar_open > price:
                exit_price = bar_open

    if side == "LONG":
        hit_tp = tp1 is not None and float(bar["h"]) >= float(tp1)
    else:
        hit_tp = tp1 is not None and float(bar["l"]) <= float(tp1)

    return exit_kind, exit_price, hit_tp


def _pnl_atr_weighted(pos: dict, exit_price: float) -> float:
    direction = 1.0 if pos["side"] == "LONG" else -1.0
    atr0 = float(pos.get("atr0") or 0)
    if atr0 <= 0:
        return 0.0
    return round(direction * (exit_price - float(pos["entry"])) / atr0, 4)


def _guarded_qty(
    symbol: str,
    strategy: str,
    side: str,
    desired_qty: float,
    price: float,
    stop_price: Optional[float],
    equity: float,
    bars: Optional[List[dict]],
) -> tuple[float, portfolio_guard.GuardDecision]:
    baseline = shadow_store.get_strategy_risk_baseline(strategy, equity)
    decision = portfolio_guard.evaluate_entry(
        symbol=symbol,
        side=side,
        desired_qty=desired_qty,
        price=price,
        stop_price=stop_price,
        equity=equity,
        open_rows=shadow_store.list_open(strategy=strategy),
        bars=bars or [],
        peak_equity=baseline["peak_equity"],
        daily_start_equity=baseline["daily_start_equity"],
        minimum_regime=baseline.get("last_regime"),
    )
    shadow_store.save_strategy_guard_state(strategy, decision.to_dict())
    return decision.allowed_qty, decision


# 2026-09-30新增：宝贝要求搭一套完全独立于compute_qty(风险资金×5倍参考
# 杠杆)的仓位公式——"永远用当下本金的1倍去开单"，本金1000元开仓就是
# 1000元名义敞口，不因为止损距离远近而变、也不乘那个隐藏的参考杠杆，
# 更贴近"就是拿这笔钱去买这只股票"的现货直觉。同时开2-3个品种时，每笔
# 都各自按"当下权益"算，天然叠加成2-3倍名义敞口(每个策略在擂台里本来
# 就是自己独立一份净值，不是共用同一份，"当下权益"不会因为已经开着
# 别的仓位而减少)——对应宝贝原话"开2个美股就相当于买了2个美股持仓"。
# 只对下面这个专属品种池生效(SNDK/OPENAI/MU三个)，不影响其余任何策略/
# 品种的正常compute_qty。注意：portfolio_guard的regime性风险上限依然
# 会在后面_guarded_qty这一步生效——这是刻意保留的，不是设计疏漏，1倍
# 本金只是"目标仓位怎么算"，不代表要绕开账户级的止损热度/资产类别上限
# 这类真实风险防线。
SPOT_EQUIVALENT_STRATEGIES = {"dual_ema_spot_7_25_1h", "dual_ema_spot_7_25_90m"}


def _open_from_signal(
    symbol: str,
    strategy: str,
    timeframe: str,
    sig: dict,
    bars: Optional[List[dict]] = None,
) -> Optional[int]:
    tier = int(sig.get("tier") or 1)
    equity = shadow_store.get_net_equity(strategy)
    raw_price = float(sig["price"])
    entry_price = shadow_store.apply_simulated_slippage(raw_price, sig["action"], True)
    if strategy in SPOT_EQUIVALENT_STRATEGIES:
        desired_qty = (equity / entry_price) if entry_price > 0 else 0.0
    else:
        desired_qty = compute_qty(equity, entry_price, sig.get("stop_loss"), tier)
    desired_qty *= float(sig.get("position_fraction") or 1.0)
    qty, guard = _guarded_qty(
        symbol, strategy, sig["action"], desired_qty, entry_price,
        sig.get("stop_loss"), equity, bars,
    )
    if qty <= 0:
        logger.info(
            f"⚠️ [多策略][{strategy}][{symbol}] portfolio_guard拒绝开仓 "
            f"regime={guard.regime} reason={guard.reason} 净权益=${equity:.2f}"
        )
        return None
    # 2026-09-05新增：开仓那一刻按LEVERAGE(5x，跟compute_qty同一套杠杆
    # 假设)算好模拟强平价存下来，_check_stop_tp用它跟战法自己的止损
    # 取更紧的那个当真正生效的止损线(见该函数注释)。
    entry_fee = abs(entry_price * qty) * shadow_store.SIM_TAKER_FEE_RATE
    row = {
        "symbol": symbol, "strategy": strategy, "timeframe": timeframe,
        "side": sig["action"], "entry": entry_price, "atr0": float(sig.get("atr") or 0),
        "tier": tier, "adx": None,
        "entry_bar_time": int(sig["bar_time"]), "score_bar_time": int(sig["bar_time"]),
        "tp1_price": sig.get("tp1"), "tp2_price": None,
        "stop": sig.get("stop_loss"), "last_ratchet_price": None,
        "tp1_done": 0, "tp2_done": 0,
        "realized_frac": 0, "realized_pnl_atr_weighted": 0, "qty": qty,
        "liq_price": None,
        "entry_stage": sig.get("entry_stage") or "confirmed",
        "context_bar_time": sig.get("context_bar_time"),
        "fee_usd": entry_fee,
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
        f"📈 [多策略][{strategy}][{symbol}] 开仓 {sig['action']} @ {entry_price:.6f} "
        f"qty={qty:.6f}(净权益${equity:.2f}·T{tier}) stop={sig.get('stop_loss')} "
        f"guard={guard.regime}/{guard.gross_cap_mult:.1f}x fee=${entry_fee:.4f} cross-margin"
    )
    return pid


def _close_position(key: tuple, exit_price: float, bar_time: int, reason: str) -> None:
    pos = _open_positions.pop(key, None)
    if not pos:
        return
    fill_price = shadow_store.apply_simulated_slippage(exit_price, pos["side"], False)
    pnl = _pnl_atr_weighted(pos, fill_price)
    qty = float(pos.get("qty") or 0)
    entry_fee = pos.get("fee_usd")
    if entry_fee is None:
        entry_fee = abs(float(pos.get("entry") or 0) * qty) * shadow_store.SIM_TAKER_FEE_RATE
    total_fee = float(entry_fee or 0) + abs(fill_price * qty) * shadow_store.SIM_TAKER_FEE_RATE
    funding_pnl = funding.estimate_funding_pnl_usd(
        pos.get("symbol") or key[0], pos["side"], qty,
        int(pos.get("entry_bar_time") or 0), int(bar_time), float(pos.get("entry") or 0),
    )
    shadow_store.close_row(
        pos["id"],
        {"exit_price": round(fill_price, 6), "exit_reason": reason,
         "realized_frac": 1.0, "realized_pnl_atr_weighted": pnl,
         "fee_usd": total_fee, "funding_pnl_usd": funding_pnl},
        bar_time,
    )
    symbol, strategy = key
    shadow_store.settle_trade_on_equity(
        strategy, pnl, float(pos.get("atr0") or 0), float(pos.get("qty") or 0),
    )
    new_equity = shadow_store.get_net_equity(strategy)
    pnl_usd = pnl * float(pos.get("atr0") or 0) * float(pos.get("qty") or 0)
    logger.info(
        f"📉 [多策略][{strategy}][{symbol}] 平仓 @ {fill_price:.6f} "
        f"毛pnl={pnl:+.2f}×ATR(${pnl_usd:+.2f}) fee=${total_fee:.2f} "
        f"funding=${funding_pnl:+.2f} 净权益→${new_equity:.2f} | {reason}"
    )


def _add_to_position(
    key: tuple,
    timeframe: str,
    sig: dict,
    bars: Optional[List[dict]] = None,
) -> bool:
    pos = _open_positions.get(key)
    if not pos or str(pos.get("entry_stage") or "") != "probe":
        return False
    symbol, strategy = key
    side = str(pos.get("side") or "").upper()
    if str(sig.get("side") or side).upper() != side:
        return False
    equity = shadow_store.get_net_equity(strategy)
    raw_price = float(sig["price"])
    add_price = shadow_store.apply_simulated_slippage(raw_price, side, True)
    tier = int(sig.get("tier") or pos.get("tier") or 1)
    desired_add = compute_qty(equity, add_price, sig.get("stop_loss"), tier)
    desired_add *= float(sig.get("position_fraction") or (2.0 / 3.0))
    add_qty, guard = _guarded_qty(
        symbol, strategy, side, desired_add, add_price,
        sig.get("stop_loss"), equity, bars,
    )
    if add_qty <= 0:
        logger.info(
            f"⚠️ [多策略][{strategy}][{symbol}] probe确认但补仓被portfolio_guard拒绝 "
            f"regime={guard.regime} reason={guard.reason}"
        )
        if shadow_store.add_to_open_row(
            pos["id"], {"entry_stage": "confirmed"}, int(sig["bar_time"]),
        ):
            pos["entry_stage"] = "confirmed"
        return False
    old_qty = float(pos.get("qty") or 0)
    new_qty = old_qty + add_qty
    if new_qty <= 0:
        return False
    new_entry = (float(pos["entry"]) * old_qty + add_price * add_qty) / new_qty
    old_atr = float(pos.get("atr0") or 0)
    add_atr = float(sig.get("atr") or old_atr)
    new_atr = (old_atr * old_qty + add_atr * add_qty) / new_qty if new_qty > 0 else old_atr
    old_stop = float(pos.get("stop") or 0)
    add_stop = float(sig.get("stop_loss") or old_stop)
    if old_stop > 0 and add_stop > 0:
        new_stop = max(old_stop, add_stop) if side == "LONG" else min(old_stop, add_stop)
    else:
        new_stop = add_stop or old_stop
    new_fee = float(pos.get("fee_usd") or 0) + abs(add_price * add_qty) * shadow_store.SIM_TAKER_FEE_RATE
    updates = {
        "entry": new_entry, "atr0": new_atr, "qty": new_qty, "stop": new_stop,
        "liq_price": None, "entry_stage": "confirmed", "fee_usd": new_fee,
    }
    if not shadow_store.add_to_open_row(pos["id"], updates, int(sig["bar_time"])):
        return False
    pos.update(updates)
    pos["stop_loss"] = new_stop
    logger.info(
        f"📈 [多策略][{strategy}][{symbol}] 4h确认补仓 @ {add_price:.6f} "
        f"add_qty={add_qty:.6f} total_qty={new_qty:.6f} guard={guard.regime}"
    )
    return True


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
    if (
        strategy in {"hma_trend_reverse_strong", "hma_trend_reverse_tiered"}
        and last_closed
        and int(last_closed.get("exit_bar_time") or -1) == int(sig["bar_time"])
    ):
        return True
    return bool(
        last_closed
        and str(last_closed.get("side")) == str(sig.get("action"))
        and int(last_closed.get("entry_bar_time") or -1) == int(sig["bar_time"])
    )


def _try_early_entry(symbol: str, strategy: str, timeframe: str, bars_by_tf: Dict[str, list],
                      call_params: dict, fn) -> Optional[dict]:
    """盘中提前入场——只在roster条目显式带"early_entry":True时才会被
    _tick_single_symbol_entry/_tick_universe_entry调用，默认关闭，对现有
    65套战法的行为逐字不变。

    思路照抄shadow_engine.py::check_early_trigger + 真实TV Pine源码
    barstate.isrealtime/useEarlyEntry(宝贝确认TV实盘本身也开着这个开关)：
    不等base周期这根K线真正收盘，用klines.get_current_bar查"当前还没走
    完的那根K线"，接在已闭合K线序列末尾当"提前收盘"，重新跑一遍战法
    自己的generate_signal——如果这根K线真收盘时战法本来就会给出信号，
    现在用当前实时价提前确认，不用再等到它真正收盘(4h/90m/150m这些
    周期下，这一等可能是几个小时)。加一道最小实体/ATR的动能门槛
    (EARLY_ENTRY_BODY_ATR_MULT=0.5，跟shadow_engine同一个值)，只有
    当前这根还在走的K线已经有足够动能时才值得多算一次，避免任何一点点
    无意义的抖动都去重跑战法本身、也避免白打API。

    跟f49ec73那次修的"未来函数"是两回事，方向相反：那次是拿入场信号
    出现**之后**才发生的K线high/low去判离场(用了决策时刻还不存在的
    数据)；这里是拿入场信号出现**之前**、当前这一刻已经真实成交的
    实时价格去判入场(决策时刻确实存在、确实可得的数据)，不会重蹈
    同一个bug。"""
    base_bars = bars_by_tf.get("base") or []
    if len(base_bars) < 20:
        return None
    atr = indicators.wilder_atr(base_bars, 14)
    if atr <= 0:
        return None
    current_bar = klines.get_current_bar(symbol, timeframe)
    if not current_bar:
        return None
    o, c = float(current_bar["o"]), float(current_bar["c"])
    if abs(c - o) < EARLY_ENTRY_BODY_ATR_MULT * atr:
        return None  # 动能不够，先不提前，等真正收盘再说

    provisional = dict(bars_by_tf)
    provisional["base"] = base_bars + [current_bar]
    sig = fn(provisional, call_params, None)
    if not sig or sig.get("action") not in ("LONG", "SHORT"):
        return None
    sig = dict(sig)
    sig["price"] = c
    sig["bar_time"] = int(current_bar["t"])
    return sig


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
    if int(last_bar["t"]) <= int(entry.get("experiment_start_after_bar") or 0):
        return

    if pos:
        _maybe_lock_breakeven(pos, float(last_bar["c"]), int(last_bar["t"]))
        _maybe_trail_atr(pos, float(last_bar["c"]), int(last_bar["t"]))
        _maybe_radar_trail(pos, bars, float(last_bar["c"]), int(last_bar["t"]))
        if _maybe_volume_sentinel(pos, key, symbol, timeframe, bars):
            return
        strategy_position = dict(pos)
        strategy_position["entry_price"] = pos["entry"]
        if str(pos.get("entry_stage") or "") == "probe":
            fast_bars = bars_by_tf.get("1h") or []
            if fast_bars and not _entered_this_bar(pos, int(fast_bars[-1]["t"])):
                exit_kind, exit_price, hit_tp = _check_stop_tp(pos, fast_bars[-1])
                if exit_kind:
                    _close_position(key, exit_price, int(fast_bars[-1]["t"]), "试探仓触及止损")
                    return
                if hit_tp:
                    _close_position(key, float(pos["tp1"]), int(fast_bars[-1]["t"]), "试探仓触及止盈")
                    return
            sig = fn(bars_by_tf, call_params, strategy_position)
            if sig and sig.get("action") == "ADD":
                _add_to_position(key, timeframe, sig, bars)
            elif sig and str(sig.get("action", "")).startswith("CLOSE"):
                _close_position(
                    key, float(sig["price"]), int(sig["bar_time"]),
                    str(sig.get("reason") or sig["action"]),
                )
            return
        if _entered_this_bar(pos, last_bar["t"]):
            return  # 入场那根K线自己的high/low/close不能用来判离场，见_entered_this_bar
        exit_kind, exit_price, hit_tp = _check_stop_tp(pos, last_bar)
        if exit_kind:
            reason = "触及模拟强平(5x杠杆)" if exit_kind == "liq" else "触及止损"
            _close_position(key, exit_price, int(last_bar["t"]), reason)
            return
        if hit_tp:
            _close_position(key, float(pos["tp1"]), int(last_bar["t"]), "触及止盈")
            return
        sig = fn(bars_by_tf, call_params, strategy_position)
        if sig and str(sig.get("action", "")).startswith("CLOSE") and int(sig["bar_time"]) > int(pos["entry_bar_time"]):
            # 2026-09-20修复：早前的_entered_this_bar用last_bar(base周期
            # 自己的bar_time)当门槛，对vwap_ema_regime这种战法自己内部用
            # mtf(15m)时钟报bar_time的策略不生效(base是2h、内部时钟是15m，
            # 两个时钟不是同一个东西，早前那道门槛形同虚设)——实测复现:
            # vwap_ema_regime即便已经去掉了range腿的tp1(2026-09-13那次
            # 修复)，自己的CLOSE_QUICK_EXIT信号还是报出过exit_bar_time早于
            # entry_bar_time的记录，因为门槛检查用的时钟跟信号自己报的
            # 时钟对不上。这里改成直接校验战法自己报的bar_time(不管是
            # 什么时钟来源)必须晚于入场时间，不信任任何单一"当前last_bar"
            # 代理判断，从根上堵死这类时钟不一致的战法也可能触发的同一
            # 类问题。
            _close_position(key, float(sig["price"]), int(sig["bar_time"]), str(sig.get("reason") or sig["action"]))
            # 2026-09-30应宝贝要求("反手和平仓可以同时进行...平仓和反手
            # 开仓几乎是一个东西")新增双均线三兄弟(band/band_radar/spot，
            # spot复用band的generate_signal同一个函数对象)：跟hma_trend_
            # reverse_*同一套机制，刚平仓那一刻立即用position=None重新
            # 问一遍战法有没有新鲜的反向信号，opposite.reverse_now由战法
            # 自己在entry_signals里打(dual_ema_band_7_25.py/dual_ema_
            # band_7_25_radar.py，非radar版本离场条件跟反向入场条件数学
            # 恒等，radar版本会真的重新校验快慢线是否已经排好队)。
            if strategy in {
                "hma_trend_reverse_strong", "hma_trend_reverse_tiered",
                "dual_ema_band_7_25_1h", "dual_ema_band_7_25_90m",
                "dual_ema_band_7_25_radar_1h", "dual_ema_band_7_25_radar_90m",
                "dual_ema_spot_7_25_1h", "dual_ema_spot_7_25_90m",
            }:
                opposite = fn(bars_by_tf, call_params, None)
                if (
                    opposite and opposite.get("reverse_now")
                    and opposite.get("action") in ("LONG", "SHORT")
                    and opposite["action"] != pos["side"]
                    and int(opposite["bar_time"]) == int(sig["bar_time"])
                    and shadow_store.get_open_row(symbol, strategy) is None
                ):
                    _open_from_signal(symbol, strategy, timeframe, opposite, bars)
        return

    sig = fn(bars_by_tf, call_params, None)
    if not sig and entry.get("early_entry"):
        sig = _try_early_entry(symbol, strategy, timeframe, bars_by_tf, call_params, fn)
    if sig and sig.get("action") in ("LONG", "SHORT") and not _same_bar_reentry_blocked(symbol, strategy, sig):
        _open_from_signal(symbol, strategy, timeframe, sig, bars)


_TIMEFRAME_BARS_PER_YEAR = {
    "15m": 365 * 24 * 4, "30m": 365 * 24 * 2, "1h": 365 * 24, "2h": 365 * 12,
    "4h": 365 * 6, "6h": 365 * 4, "8h": 365 * 3, "12h": 365 * 2, "1d": 365,
}


def _compute_universe_returns(
    symbols: List[str], timeframe: str, lookback_bars: int, cache: Dict[tuple, list],
    vol_scale: bool = False, clenow: bool = False,
) -> Dict[str, float]:
    """vol_scale=False(默认，原行为逐字不变)：原始收益率排名——高波动品种
    天然更容易冲进"最强/最弱"区间，本质上更像"选高波动品种"而不是纯动量。
    vol_scale=True(2026-09-19新增，cross_momentum_v2/dual_momentum_v2用)：
    收益率除以自身ATR%做波动率标准化(Moskowitz/Barroso-Santa-Clara一类
    截面动量文献的标准做法)，让不同波动特征的品种排名可比，不是新拍的
    经验参数，是量纲修正。

    clenow=True(2026-09-19新增，clenow_momentum.py用)：Andreas Clenow
    《Stocks on the Move》(2015年公开出版)的原版公式——对整个lookback窗口
    的ln(close)做线性回归，动量分=年化(exp(slope)-1)×R²，不再是vol_scale
    那种"只看两个端点"的简化。R²高=价格沿趋势线走得干净(低噪音)，年化
    slope越陡说明趋势越强，两者相乘同时惩罚"趋势弱"和"趋势脏"。年化
    倍数按timeframe换算成每年根数(_TIMEFRAME_BARS_PER_YEAR)，不是硬编码
    的252(那是股票交易日惯例，加密货币24/7)。"""
    out = {}
    bars_per_year = _TIMEFRAME_BARS_PER_YEAR.get(str(timeframe or "").lower(), 365)
    for s in symbols:
        # 2026-09-04：改成走 _cached_bars(拉 BARS_LIMIT 根)，跟同一轮里
        # 该品种@该周期的信号用K线共用缓存，少打一次接口。只用末尾的
        # bars[-1] / bars[-1-lookback] 两个点，多拉的历史不影响结果。
        bars = _cached_bars(cache, s, timeframe, BARS_LIMIT)
        if len(bars) < lookback_bars + 1:
            continue
        if clenow:
            window = bars[-lookback_bars:]
            closes_ln = [math.log(float(b["c"])) for b in window if float(b["c"]) > 0]
            if len(closes_ln) < lookback_bars:
                continue
            slope, r2 = indicators.linreg_slope_r2(closes_ln)
            try:
                annualized = math.exp(slope * bars_per_year) - 1.0
            except OverflowError:
                continue
            out[s] = annualized * r2
            continue
        c_now = float(bars[-1]["c"])
        c_then = float(bars[-1 - lookback_bars]["c"])
        if c_then <= 0:
            continue
        ret = c_now / c_then - 1.0
        if vol_scale:
            atr = indicators.wilder_atr(bars, 14)
            if atr > 0 and c_now > 0:
                ret = ret / (atr / c_now)
            else:
                continue
        out[s] = ret
    return out


def _tick_universe_entry(entry: dict, cache: Dict[tuple, list]) -> None:
    strategy, timeframe = entry["strategy"], entry["timeframe"]
    symbols = entry["symbols"]
    lookback = int(entry.get("lookback_bars") or 20)
    if entry.get("require_aligned_bars"):
        bar_times = set()
        for symbol in symbols:
            bars = _cached_bars(cache, symbol, timeframe, BARS_LIMIT)
            if len(bars) < max(30, lookback + 1):
                return
            bar_times.add(int(bars[-1]["t"]))
        minutes = klines.timeframe_to_minutes(timeframe)
        if len(bar_times) != 1 or not minutes:
            return
        last_bar_time = next(iter(bar_times))
        if time.time() * 1000 > last_bar_time + 2 * minutes * 60_000 + 120_000:
            return
    fn = get_strategy(strategy)
    universe_returns = _compute_universe_returns(
        symbols, timeframe, lookback, cache,
        vol_scale=bool(entry.get("vol_scale_rank")), clenow=bool(entry.get("clenow_rank")),
    )
    if len(universe_returns) < 2:
        return
    for symbol in symbols:
        bars = _cached_bars(cache, symbol, timeframe, BARS_LIMIT)
        if len(bars) < 30:
            continue
        key = (symbol, strategy)
        pos = _open_positions.get(key)
        last_bar = bars[-1]
        # 2026-09-10：允许 UNIVERSE_ROSTER 条目带 "params" 覆盖战法默认参数
        # （跟 _tick_single_symbol_entry 早就在做的 call_params 合并对齐）。
        # 现有条目都不带 "params" 字段 → 行为逐字不变。用途：cross_momentum_
        # runwin / dual_momentum_runwin 传 use_fixed_tp=False 做去止盈封顶对照。
        params = {"symbol": symbol, "universe_returns": universe_returns, "lookback_bars": lookback,
                  **(entry.get("params") or {})}

        if pos:
            if _entered_this_bar(pos, last_bar["t"]):
                continue  # 见_entered_this_bar：入场那根K线自己不能用来判离场
            exit_kind, exit_price, hit_tp = _check_stop_tp(pos, last_bar)
            if exit_kind:
                reason = "触及模拟强平(5x杠杆)" if exit_kind == "liq" else "触及止损"
                _close_position(key, exit_price, int(last_bar["t"]), reason)
                continue
            if hit_tp:
                _close_position(key, float(pos["tp1"]), int(last_bar["t"]), "触及止盈")
                continue
            strategy_position = dict(pos)
            strategy_position["entry_price"] = pos["entry"]
            sig = fn({"base": bars}, params, strategy_position)
            if sig and str(sig.get("action", "")).startswith("CLOSE") and int(sig["bar_time"]) > int(pos["entry_bar_time"]):
                # 见_tick_single_symbol_entry同一处2026-09-20修复的注释
                _close_position(key, float(sig["price"]), int(sig["bar_time"]), str(sig.get("reason") or sig["action"]))
            continue

        sig = fn({"base": bars}, params, None)
        if not sig and entry.get("early_entry"):
            sig = _try_early_entry(symbol, strategy, timeframe, {"base": bars}, params, fn)
        if sig and sig.get("action") in ("LONG", "SHORT") and not _same_bar_reentry_blocked(symbol, strategy, sig):
            _open_from_signal(symbol, strategy, timeframe, sig, bars)


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
    fill_a = shadow_store.apply_simulated_slippage(exit_price_a, p["side_a"], False)
    fill_b = shadow_store.apply_simulated_slippage(exit_price_b, p["side_b"], False)
    pnl_a = _pair_leg_pnl(p["side_a"], p["entry_a"], p["atr0_a"], fill_a)
    pnl_b = _pair_leg_pnl(p["side_b"], p["entry_b"], p["atr0_b"], fill_b)
    fee_a = (abs(p["entry_a"] * p["qty_a"]) + abs(fill_a * p["qty_a"])) * shadow_store.SIM_TAKER_FEE_RATE
    fee_b = (abs(p["entry_b"] * p["qty_b"]) + abs(fill_b * p["qty_b"])) * shadow_store.SIM_TAKER_FEE_RATE
    funding_a = funding.estimate_funding_pnl_usd(
        p["symbol_a"], p["side_a"], p["qty_a"], p["entry_bar_time"], bar_time, p["entry_a"],
    )
    funding_b = funding.estimate_funding_pnl_usd(
        p["symbol_b"], p["side_b"], p["qty_b"], p["entry_bar_time"], bar_time, p["entry_b"],
    )
    shadow_store.close_row(
        p["id_a"], {"exit_price": round(fill_a, 6), "exit_reason": reason,
                     "realized_frac": 1.0, "realized_pnl_atr_weighted": pnl_a,
                     "fee_usd": fee_a, "funding_pnl_usd": funding_a}, bar_time,
    )
    shadow_store.close_row(
        p["id_b"], {"exit_price": round(fill_b, 6), "exit_reason": reason,
                     "realized_frac": 1.0, "realized_pnl_atr_weighted": pnl_b,
                     "fee_usd": fee_b, "funding_pnl_usd": funding_b}, bar_time,
    )
    shadow_store.settle_trade_on_equity(strategy, pnl_a, p["atr0_a"], p["qty_a"])
    shadow_store.settle_trade_on_equity(strategy, pnl_b, p["atr0_b"], p["qty_b"])
    new_equity = shadow_store.get_net_equity(strategy)
    pnl_usd = pnl_a * p["atr0_a"] * p["qty_a"] + pnl_b * p["atr0_b"] * p["qty_b"]
    logger.info(
        f"📉 [多策略][{strategy}] 配对平仓 {p['symbol_a']}({p['side_a']}@{fill_a:.4f})/"
        f"{p['symbol_b']}({p['side_b']}@{fill_b:.4f}) 合计毛pnl=${pnl_usd:+.2f} "
        f"fee=${fee_a + fee_b:.2f} funding=${funding_a + funding_b:+.2f} "
        f"净权益→${new_equity:.2f} | {reason}"
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

        if _entered_this_bar(p, last_bar_time):
            return  # 见_entered_this_bar：入场那根K线自己不能用来判离场

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
            exit_a, exit_b = price_a, price_b
            if hit_a:
                stop = float(stop_a)
                bar_open = float(bars_a[-1].get("o") or stop)
                exit_a = min(stop, bar_open) if p["side_a"] == "LONG" else max(stop, bar_open)
            if hit_b:
                stop = float(stop_b)
                bar_open = float(bars_b[-1].get("o") or stop)
                exit_b = min(stop, bar_open) if p["side_b"] == "LONG" else max(stop, bar_open)
            _close_pair(exit_a, exit_b, last_bar_time, "任一腿触及ATR止损(配对脱钩)", strategy)
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
    price_a = shadow_store.apply_simulated_slippage(closes_a[-1], sig["side_a"], True)
    price_b = shadow_store.apply_simulated_slippage(closes_b[-1], sig["side_b"], True)

    atr_a = indicators.wilder_atr(bars_a, atr_len)
    atr_b = indicators.wilder_atr(bars_b, atr_len)
    if atr_a <= 0 or atr_b <= 0:
        return

    equity = shadow_store.get_net_equity(strategy)
    dir_a = 1.0 if sig["side_a"] == "LONG" else -1.0
    dir_b = 1.0 if sig["side_b"] == "LONG" else -1.0
    stop_a = round(price_a - dir_a * atr_stop_mult * atr_a, 6)
    stop_b = round(price_b - dir_b * atr_stop_mult * atr_b, 6)
    desired_a = compute_qty(equity, price_a, stop_a, tier=1)
    desired_b = compute_qty(equity, price_b, stop_b, tier=1)
    baseline = shadow_store.get_strategy_risk_baseline(strategy, equity)
    open_rows = shadow_store.list_open(strategy=strategy)
    decision_a = portfolio_guard.evaluate_entry(
        symbol=sym_a, side=sig["side_a"], desired_qty=desired_a, price=price_a,
        stop_price=stop_a, equity=equity, open_rows=open_rows, bars=bars_a,
        peak_equity=baseline["peak_equity"], daily_start_equity=baseline["daily_start_equity"],
        minimum_regime=baseline.get("last_regime"),
    )
    provisional_a = {
        "symbol": sym_a, "side": sig["side_a"], "entry": price_a,
        "qty": decision_a.allowed_qty, "stop": stop_a,
    }
    decision_b = portfolio_guard.evaluate_entry(
        symbol=sym_b, side=sig["side_b"], desired_qty=desired_b, price=price_b,
        stop_price=stop_b, equity=equity, open_rows=open_rows + [provisional_a], bars=bars_b,
        peak_equity=baseline["peak_equity"], daily_start_equity=baseline["daily_start_equity"],
        minimum_regime=baseline.get("last_regime"),
    )
    shadow_store.save_strategy_guard_state(strategy, decision_b.to_dict())
    qty_a, qty_b = decision_a.allowed_qty, decision_b.allowed_qty
    if qty_a <= 0 or qty_b <= 0:
        return

    id_a = shadow_store.insert_open_row({
        "symbol": sym_a, "strategy": strategy, "timeframe": timeframe,
        "side": sig["side_a"], "entry": price_a, "atr0": atr_a, "tier": 1,
        "entry_bar_time": bar_time, "score_bar_time": bar_time,
        "qty": qty_a, "stop": stop_a,
        "fee_usd": abs(price_a * qty_a) * shadow_store.SIM_TAKER_FEE_RATE,
        "pair_key": pair_key, "pair_base_price": sig["base_price_a"],
        "pair_formation_mean": sig["formation_mean"], "pair_formation_std": sig["formation_std"],
    })
    id_b = shadow_store.insert_open_row({
        "symbol": sym_b, "strategy": strategy, "timeframe": timeframe,
        "side": sig["side_b"], "entry": price_b, "atr0": atr_b, "tier": 1,
        "entry_bar_time": bar_time, "score_bar_time": bar_time,
        "qty": qty_b, "stop": stop_b,
        "fee_usd": abs(price_b * qty_b) * shadow_store.SIM_TAKER_FEE_RATE,
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
        f"qty={qty_a:.4f}/{qty_b:.4f}(净权益${equity:.2f}, guard={decision_b.regime})"
    )


def _refresh_guard_states(
    single_roster: List[dict],
    universe_roster: List[dict],
    pairs_roster: List[dict],
    cache: Dict[tuple, list],
) -> None:
    """Persist each strategy's worst current closed-bar regime once per round."""
    observed: Dict[str, List[str]] = {}

    def consider(strategy: str, symbol: str, timeframe: str, limit: int) -> None:
        bars = cache.get((symbol, timeframe, int(limit))) or []
        if not bars:
            return
        regime = portfolio_guard.classify_regime(bars)
        observed.setdefault(strategy, []).append(regime)

    for entry in single_roster:
        consider(
            entry["strategy"], entry["symbol"], entry["timeframe"],
            int(entry.get("bars_limit") or BARS_LIMIT),
        )
    for entry in universe_roster:
        for symbol in entry["symbols"]:
            consider(entry["strategy"], symbol, entry["timeframe"], BARS_LIMIT)
    for entry in pairs_roster:
        limit = max(BARS_LIMIT, int(entry.get("formation_bars") or pairs_trading.DEFAULT_PARAMS["formation_bars"]) + 10)
        for symbol in entry["symbols"]:
            consider(entry["strategy"], symbol, entry["timeframe"], limit)

    for strategy, regimes in observed.items():
        regime = portfolio_guard.aggregate_regimes(regimes)
        equity = shadow_store.get_net_equity(strategy)
        baseline = shadow_store.get_strategy_risk_baseline(strategy, equity)
        decision = portfolio_guard.status_for_regime(
            regime, equity, baseline["peak_equity"], baseline["daily_start_equity"],
        )
        shadow_store.save_strategy_guard_state(strategy, decision.to_dict())


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
    _refresh_guard_states(single_roster, universe_roster, pairs_roster or [], cache)


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
