#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
双均线趋势确认——供"TV方向未变+趋势仍在"自主重入机制使用。

背景——宝贝拍板(2026-09-13)：TV策略源码(ETH双均线15/30锁机制版)本身的
开平仓逻辑就是"多头 close>MA15 and close>MA30，空头反过来；双均线同步
跌破/站上才平仓"。宝贝要的自主重入条件跟这套策略自己的趋势定义完全
一致：VPS空仓时，只要TV心跳最后一个非空方向仍然满足"现价站在这个方向
的双均线之上/之下"，就认为趋势还在，允许VPS自己衡量要不要重入——直到
TV发出全新的真实开仓信号为止(TV是主要方向判断，VPS是执行层+半辅助)。

纯函数，不碰任何真实账户/持仓/网络——klines由调用方自己拉好传进来。
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple


def _sma(values: List[float], n: int) -> float:
    return sum(values[-n:]) / n


def _ema(values: List[float], n: int) -> float:
    """标准EMA：用前n根的SMA做种子，再往后EMA递推——跟大多数图表/Pine
    的ta.ema在数据足够长时会收敛到同一个值，趋势方向判断不需要逐根位
    对齐的精确复刻。"""
    seed = sum(values[:n]) / n
    k = 2.0 / (n + 1)
    m = seed
    for v in values[n:]:
        m = v * k + m * (1.0 - k)
    return m


def dual_ma_trend_ok(
    side: str,
    klines: List[list],
    fast_len: int = 15,
    slow_len: int = 30,
    ma_type: str = "SMA",
) -> Tuple[bool, Dict[str, Any]]:
    """
    双均线趋势确认：多头要求现价同时站上快/慢均线，空头要求同时跌破
    快/慢均线——跟TV策略源码里"close>maFast and close>maSlow"(多)/
    "close<maFast and close<maSlow"(空)完全一致的定义，不是VPS自己另外
    发明的新指标。

    klines: [[open_ms, open, high, low, close, volume], ...]，时间升序，
            最后一根可以是未收盘的成型K线。

    返回 (ok, meta)。meta含close/ma_fast/ma_slow/ma_type，供日志核查；
    数据不够或参数非法时ok=False、meta为空dict(调用方按"趋势不确认"
    保守处理，不当异常抛出)。
    """
    side = str(side or "").upper()
    if side not in ("LONG", "SHORT"):
        return False, {}
    bars = list(klines or [])
    need = max(fast_len, slow_len)
    if len(bars) < need:
        return False, {}

    closes = [float(b[4]) for b in bars]
    ma_type = str(ma_type or "SMA").upper()
    calc = _ema if ma_type == "EMA" else _sma
    try:
        ma_fast = calc(closes, fast_len)
        ma_slow = calc(closes, slow_len)
    except (ZeroDivisionError, ValueError, IndexError):
        return False, {}
    close = closes[-1]

    if side == "LONG":
        ok = close > ma_fast and close > ma_slow
    else:
        ok = close < ma_fast and close < ma_slow

    meta = {
        "close": close, "ma_fast": round(ma_fast, 6), "ma_slow": round(ma_slow, 6),
        "ma_type": ma_type, "fast_len": fast_len, "slow_len": slow_len,
    }
    return ok, meta


# 2026-09-15新增："趋势确认多次重入"用——宝贝观察到OPENAI连续3根阳线
# 放量上涨且站上45分钟双均线，要求这种更强的技术确认可以让VPS自主限价
# 重入，条件持续满足就多次重入。放量口径复用atr_scenario.py/smart_hard_
# stop.py里DUAL_MA_EXIT已经验证过的"最近3根均量≥前20根均量×1.3倍"，
# 就地内联一份而不跨模块import(dual_ma_trend.py两仓库保持字节一致，
# 两边的放量函数分别叫atr_scenario._volume_confirmed/smart_hard_stop.
# _volume_confirmed，模块名不同，跨import会破坏"字节一致"这个约束)。
_VOL_CONFIRM_LOOKBACK = 20
_VOL_CONFIRM_RECENT_N = 3
_VOL_CONFIRM_MULT = 1.3


def _volume_confirmed(bars: List[list]) -> bool:
    if len(bars) < _VOL_CONFIRM_LOOKBACK + _VOL_CONFIRM_RECENT_N:
        return False
    base = bars[-(_VOL_CONFIRM_LOOKBACK + _VOL_CONFIRM_RECENT_N):-_VOL_CONFIRM_RECENT_N]
    recent = bars[-_VOL_CONFIRM_RECENT_N:]
    if not base or not recent:
        return False
    base_avg = sum(float(b[5]) for b in base) / len(base)
    recent_avg = sum(float(b[5]) for b in recent) / len(recent)
    if base_avg <= 0:
        return False
    return recent_avg >= _VOL_CONFIRM_MULT * base_avg


def _consecutive_same_direction_candles(side: str, bars: List[list], n: int = 3) -> bool:
    """最近n根已收盘K线是否都是同向实体——多头都是close>open(阳线)，
    空头都是close<open(阴线)。bars最后一根若是未收盘的成型K线，调用方
    应自行只传"已收盘"部分(或接受把当前成型K线也当最后一根参与判断，
    语义上跟dual_ma_trend_ok对"现价"的处理保持一致，不强制额外过滤)。"""
    side = str(side or "").upper()
    if side not in ("LONG", "SHORT") or len(bars) < n:
        return False
    last_n = bars[-n:]
    if side == "LONG":
        return all(float(b[4]) > float(b[1]) for b in last_n)
    return all(float(b[4]) < float(b[1]) for b in last_n)


def trend_confirmed_with_volume(
    side: str,
    klines: List[list],
    fast_len: int = 15,
    slow_len: int = 30,
    ma_type: str = "SMA",
    candle_run: int = 3,
) -> Tuple[bool, Dict[str, Any]]:
    """"趋势确认多次重入"用的组合确认：现价站上/跌破双均线(见
    dual_ma_trend_ok) + 最近candle_run根同向实体 + 放量确认，三者都满足
    才算真正的强趋势信号，允许VPS自主限价重入。比单纯dual_ma_trend_ok
    更严格，专门给"多次重入"这种会反复自动开仓的场景把关。"""
    ma_ok, ma_meta = dual_ma_trend_ok(side, klines, fast_len=fast_len,
                                       slow_len=slow_len, ma_type=ma_type)
    if not ma_ok:
        return False, {"reason": "dual_ma_not_confirmed", **ma_meta}
    bars = list(klines or [])
    candle_ok = _consecutive_same_direction_candles(side, bars, n=candle_run)
    if not candle_ok:
        return False, {"reason": "candle_run_not_confirmed", **ma_meta}
    vol_ok = _volume_confirmed(bars)
    if not vol_ok:
        return False, {"reason": "volume_not_confirmed", **ma_meta}
    return True, {"reason": "confirmed", "candle_run": candle_run, **ma_meta}
