#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SNDK 91分钟双均线(EMA7/30)策略——纯信号/风控逻辑，不碰任何账户/网络。

2026-09-23从sndk_dual_ma_live.py拆出来：回测脚本(sndk_dual_ma_backtest.py)
需要跟实盘完全同一套开平仓/止损判定，不能自己另外抄一遍容易跟实盘悄悄
走样。sndk_dual_ma_live.py原来在模块顶部`from binance_client import
binance_client`，import它就会顺带触发真实账户client初始化(读.env凭证、
可能发起网络请求)——纯计算逻辑不该有这个副作用，所以把不碰账户/网络的
那部分(参数常量+EMA/ATR/ADX信号判定+止损状态机)拆到这个零依赖模块，
sndk_dual_ma_live.py和sndk_dual_ma_backtest.py都从这里import，"验证的是
什么，实盘跑的就是什么"。

只依赖market_engine.py的wilder_atr/wilder_adx(同样零账户依赖，纯K线
数组计算)。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from market_engine import wilder_atr, wilder_adx

# ==================== 策略参数(照Pine源码"EMA7 & EMA30 纯裸K微结构突破"逐条对齐) ====================
SYMBOL = "SNDKUSDT"
# 宝贝确认TradingView回测用的是91分钟K线，不是文字计划书写的90分钟——
# 跟radar_reentry_mixin.py::DUAL_MA_EXIT_INTERVAL_MIN里SNDKUSDT登记的
# 91分钟(2026-09-19照真实TV警报截图校准)完全对得上，独立信息源互相印证。
PERIOD_MIN = 91
PERIOD_MS = PERIOD_MIN * 60 * 1000
PERIOD_STR = f"{PERIOD_MIN}m"
FAST_LEN = 7
SLOW_LEN = 30  # 源码是EMA30，不是文字计划书写的EMA25
BREAKOUT_LOOKBACK = 5      # breakoutBars
BODY_MIN_ATR_MULT = 0.2    # bodyMulti：|close-open| >= 此倍数×ATR才算有效实体
STRUCT_LOOKBACK = 20
ATR_PERIOD = 14
ADX_PERIOD = 14

INITIAL_STOP_ATR_MULT = 2.5
BREAKEVEN_TRIGGER_ATR = 1.0
BREAKEVEN_BUFFER_PCT = 0.0005  # 0.05%，覆盖手续费
TRAIL_TRIGGER_ATR = 1.5
TRAIL_MULT_STRONG = 3.5   # ADX > 30
TRAIL_MULT_WEAK = 1.5     # ADX < 20
TRAIL_MULT_NORMAL = 2.5   # 其余
ADX_STRONG_BOUND = 30.0
ADX_WEAK_BOUND = 20.0
STRUCT_BUFFER_ATR = 0.2

SPIKE_FORCE_CLOSE_SEC = 15 * 60  # 硬止损击穿持续这么久仍未收回 → 强制平仓

EXCHANGE_LEVERAGE = 1     # 交易所真实杠杆锁1倍，等同于现货满仓
EQUITY_USAGE_PCT = 0.98   # 账户权益98%开仓，留2%手续费缓冲(源码要求)

DEEP_BARS_TARGET = 1000   # 目标91m bar深度，供EMA/ATR/ADX warmup(误差<0.1%要求)
MIN_BARS_NEEDED = max(SLOW_LEN, STRUCT_LOOKBACK, ADX_PERIOD * 2 + 2) + BREAKOUT_LOOKBACK


# ==================== EMA(本地连续递归，跟Pine ta.ema同一套SMA种子+递归写法) ====================

def ema_last(closes: List[float], n: int) -> float:
    """closes末尾对应"当前bar"；传closes[:-1]即可拿到"上一根bar的EMA值"
    (跟Pine里emaFast[1]同一个含义)——因为种子用的是同一批最早的n个值，
    只是递归少走一步，数学上等价于同一条连续EMA序列往回退一格，不是
    从别的起点重新播种。"""
    if len(closes) < n:
        return 0.0
    seed = sum(closes[:n]) / n
    k = 2.0 / (n + 1)
    m = seed
    for v in closes[n:]:
        m = v * k + m * (1.0 - k)
    return m


# ==================== 信号判定(91分钟收盘时评估，逐条对齐Pine源码条件) ====================

def entry_signal(bars: List[list]) -> Optional[Dict[str, Any]]:
    """最新已收盘91m bar是否满足开多/开空条件。跟Pine源码longCondition/
    shortCondition逐条对齐：快线斜率+阳阴线+站上/跌破双均线+实体过滤+
    突破前5根高低点，六个条件全部满足才算数。None=无信号。bars最后一
    元素视为"当前已收盘bar"，调用方(实盘/回测)负责保证这一点。"""
    if len(bars) < MIN_BARS_NEEDED:
        return None
    closes = [float(b[4]) for b in bars]
    cur = bars[-1]
    o, c = float(cur[1]), float(cur[4])

    atr_now = wilder_atr(bars, ATR_PERIOD)
    if atr_now <= 0:
        return None

    ema_fast_now = ema_last(closes, FAST_LEN)
    ema_fast_prev = ema_last(closes[:-1], FAST_LEN)
    ema_slow_now = ema_last(closes, SLOW_LEN)

    prior5 = bars[-(BREAKOUT_LOOKBACK + 1):-1]
    prior5_high = max(float(b[2]) for b in prior5)
    prior5_low = min(float(b[3]) for b in prior5)

    body_size = abs(c - o)
    is_body_valid = body_size >= atr_now * BODY_MIN_ATR_MULT
    is_bull = c > o
    is_bear = c < o
    ema_fast_up = ema_fast_now > ema_fast_prev
    ema_fast_down = ema_fast_now < ema_fast_prev

    long_ok = (
        ema_fast_up and is_bull and c > ema_fast_now and c > ema_slow_now
        and is_body_valid and c > prior5_high
    )
    short_ok = (
        ema_fast_down and is_bear and c < ema_fast_now and c < ema_slow_now
        and is_body_valid and c < prior5_low
    )
    if long_ok:
        return {"action": "LONG", "price": c, "bar_time": int(cur[0]), "atr": atr_now}
    if short_ok:
        return {"action": "SHORT", "price": c, "bar_time": int(cur[0]), "atr": atr_now}
    return None


def exit_signal(bars: List[list], side: str) -> Optional[Dict[str, Any]]:
    """持仓方向在最新91m收盘时是否该"纯平仓"或"反手"。closeLongCondition/
    closeShortCondition跟Pine源码一致：只看是否跌破/站上双均线，不要求
    实体/突破——那两条只在判断"要不要反手"时才需要，走entry_signal同一
    份完整六条件判定。"""
    if len(bars) < MIN_BARS_NEEDED:
        return None
    closes = [float(b[4]) for b in bars]
    cur = bars[-1]
    c = float(cur[4])
    ema_fast_now = ema_last(closes, FAST_LEN)
    ema_slow_now = ema_last(closes, SLOW_LEN)
    entry_sig = entry_signal(bars)

    if side == "LONG":
        close_cond = c < ema_fast_now and c < ema_slow_now
        if not close_cond:
            return None
        if entry_sig and entry_sig["action"] == "SHORT":
            return {
                "action": "REVERSE_SHORT", "price": c, "bar_time": int(cur[0]),
                "atr": entry_sig["atr"],
            }
        return {"action": "CLOSE_ONLY", "price": c, "bar_time": int(cur[0])}
    else:
        close_cond = c > ema_fast_now and c > ema_slow_now
        if not close_cond:
            return None
        if entry_sig and entry_sig["action"] == "LONG":
            return {
                "action": "REVERSE_LONG", "price": c, "bar_time": int(cur[0]),
                "atr": entry_sig["atr"],
            }
        return {"action": "CLOSE_ONLY", "price": c, "bar_time": int(cur[0])}


# ==================== 止损状态机(intrabar，用调用方传入的现价/缓存指标) ====================

def evaluate_protective_stop(pos: Dict[str, Any], price: float, atr_now: float,
                              adx_now: float, struct_low: Optional[float],
                              struct_high: Optional[float], now_ts: float) -> Tuple[bool, str]:
    """返回(是否该市价平仓, 原因)。硬止损阶段做防插针(持续击穿超15分钟
    才真平)；保本/追踪阶段一碰即触发(源码只在初始硬止损这层用TV内部
    固定止损做兜底，后两档是VPS这边"立刻市价全平")。会就地修改传入的
    pos dict(extreme_price/breakeven_active/trail_active/current_stop/
    spike_breach_start_ts)，调用方(实盘/回测)负责持久化。"""
    side = pos["side"]
    direction = 1.0 if side == "LONG" else -1.0
    entry = pos["entry_price"]
    atr0 = pos.get("atr_at_entry") or atr_now

    if side == "LONG":
        pos["extreme_price"] = max(pos.get("extreme_price", entry), price)
    else:
        pos["extreme_price"] = min(pos.get("extreme_price", entry), price)

    profit_atr = direction * (price - entry) / atr0 if atr0 > 0 else 0.0

    if not pos.get("breakeven_active") and profit_atr >= BREAKEVEN_TRIGGER_ATR:
        pos["breakeven_active"] = True

    if not pos.get("trail_active") and profit_atr >= TRAIL_TRIGGER_ATR:
        pos["trail_active"] = True

    candidates = [pos["hard_stop"]]

    if pos.get("breakeven_active"):
        be = entry * (1 + BREAKEVEN_BUFFER_PCT) if side == "LONG" else entry * (1 - BREAKEVEN_BUFFER_PCT)
        candidates.append(be)

    if pos.get("trail_active") and atr_now > 0:
        if adx_now > ADX_STRONG_BOUND:
            mult = TRAIL_MULT_STRONG
        elif adx_now < ADX_WEAK_BOUND:
            mult = TRAIL_MULT_WEAK
        else:
            mult = TRAIL_MULT_NORMAL
        chandelier = pos["extreme_price"] - direction * mult * atr_now

        if struct_low is not None and struct_high is not None:
            if side == "LONG":
                struct_level = struct_low - STRUCT_BUFFER_ATR * atr_now
                trail_final = max(chandelier, struct_level)
            else:
                struct_level = struct_high + STRUCT_BUFFER_ATR * atr_now
                trail_final = min(chandelier, struct_level)
            candidates.append(trail_final)
        else:
            candidates.append(chandelier)

    active_stop = max(candidates) if side == "LONG" else min(candidates)
    pos["current_stop"] = active_stop

    breached = (price <= active_stop) if side == "LONG" else (price >= active_stop)

    if not breached:
        pos["spike_breach_start_ts"] = None
        return False, ""

    if pos.get("breakeven_active") or pos.get("trail_active"):
        return True, ("移动追踪止损触发" if pos.get("trail_active") else "保本止损触发")

    if pos.get("spike_breach_start_ts") is None:
        pos["spike_breach_start_ts"] = now_ts
        return False, ""
    elapsed = now_ts - pos["spike_breach_start_ts"]
    if elapsed >= SPIKE_FORCE_CLOSE_SEC:
        return True, f"硬止损击穿持续{elapsed / 60:.1f}分钟未收回"
    return False, ""


def new_position(side: str, fill_price: float, atr: float, bar_time: int,
                  opened_at: float) -> Dict[str, Any]:
    """构造一笔新仓位的初始状态dict，实盘/回测共用同一个字段集。"""
    direction = 1.0 if side == "LONG" else -1.0
    hard_stop = fill_price - direction * INITIAL_STOP_ATR_MULT * atr
    return {
        "side": side,
        "entry_price": fill_price,
        "atr_at_entry": atr,
        "entry_bar_time": bar_time,
        "hard_stop": hard_stop,
        "extreme_price": fill_price,
        "breakeven_active": False,
        "trail_active": False,
        "current_stop": hard_stop,
        "spike_breach_start_ts": None,
        "opened_at": opened_at,
    }
