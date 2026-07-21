#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
VPS 行情引擎：合成 90m K 线 → ATR(14) / ADX(14)

币安无原生 90m，用 30m×3 合并（O=首开 H=最高 L=最低 C=末收 V=量和）。
ATR/ADX 用 Wilder 平滑，与 TradingView 默认 RMA 对齐。
止损决策只认本模块数值；webhook 不传 ATR/ADX。
"""
from __future__ import annotations

import logging
import threading
import time
from typing import List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

BASE_INTERVAL = "30m"
BARS_PER_SYNTH = 3  # 3×30m = 90m
TARGET_PERIOD_MIN = 90
ATR_PERIOD = 14
ADX_PERIOD = 14
# 合成 90m 至少需要 period*3 + 暖机；拉 30m 约 200 根 → ~66 根 90m
FETCH_30M_LIMIT = 220
REFRESH_MIN_SEC = 30.0  # 防抖
ATR_COMPARE_ALERT_PCT = 0.20  # 与 TV 隐含 ATR 差超 20% 仅告警


def _f(x, default=0.0) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return float(default)


def merge_30m_to_90m(klines_30m: Sequence) -> List[list]:
    """
    币安 kline 行: [openTime, o, h, l, c, vol, closeTime, ...]
    按时间顺序每 3 根合成 1 根 90m；不足 3 根的尾部丢弃（未闭合）。
    """
    rows = list(klines_30m or [])
    if len(rows) < BARS_PER_SYNTH:
        return []
    # 对齐到 90m 边界：openTime 为 UTC ms；90m=5400000ms
    period_ms = TARGET_PERIOD_MIN * 60 * 1000
    out = []
    i = 0
    n = len(rows)
    while i + BARS_PER_SYNTH <= n:
        chunk = rows[i : i + BARS_PER_SYNTH]
        try:
            t0 = int(chunk[0][0])
            # 若未对齐 90m 边界，滑动到下一根再试
            if t0 % period_ms != 0:
                i += 1
                continue
            o = _f(chunk[0][1])
            h = max(_f(c[2]) for c in chunk)
            l = min(_f(c[3]) for c in chunk)
            c = _f(chunk[-1][4])
            v = sum(_f(c[5]) for c in chunk)
            close_t = int(chunk[-1][6]) if len(chunk[-1]) > 6 else t0 + period_ms - 1
            out.append([t0, o, h, l, c, v, close_t])
            i += BARS_PER_SYNTH
        except (IndexError, TypeError, ValueError):
            i += 1
    return out


def _true_ranges(bars: Sequence) -> List[float]:
    trs = []
    for i in range(1, len(bars)):
        h = _f(bars[i][2])
        l = _f(bars[i][3])
        pc = _f(bars[i - 1][4])
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    return trs


def wilder_atr(bars: Sequence, period: int = ATR_PERIOD) -> float:
    """Wilder ATR；bars 为 OHLCV 行列表。"""
    if not bars or len(bars) < period + 1:
        return 0.0
    trs = _true_ranges(bars)
    if len(trs) < period:
        return 0.0
    atr = sum(trs[:period]) / period
    for tr in trs[period:]:
        atr = (atr * (period - 1) + tr) / period
    return float(atr)


def wilder_adx(bars: Sequence, period: int = ADX_PERIOD) -> float:
    """
    Wilder ADX(period)。需要足够暖机：建议 >= period*3 根已合成 K 线。
    smTR / sm+DM / sm-DM 首值为 period 根求和，其后 Wilder 递推。
    """
    n = len(bars or [])
    if n < period * 2 + 2:
        return 0.0

    plus_dm = []
    minus_dm = []
    trs = []
    for i in range(1, n):
        h = _f(bars[i][2])
        l = _f(bars[i][3])
        ph = _f(bars[i - 1][2])
        pl = _f(bars[i - 1][3])
        pc = _f(bars[i - 1][4])
        up = h - ph
        down = pl - l
        plus_dm.append(up if up > down and up > 0 else 0.0)
        minus_dm.append(down if down > up and down > 0 else 0.0)
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))

    if len(trs) < period:
        return 0.0

    sm_tr = sum(trs[:period])
    sm_plus = sum(plus_dm[:period])
    sm_minus = sum(minus_dm[:period])

    def _di(sp, sm, st):
        if st <= 0:
            return 0.0, 0.0
        return 100.0 * sp / st, 100.0 * sm / st

    dx_list = []
    pdi, mdi = _di(sm_plus, sm_minus, sm_tr)
    denom = pdi + mdi
    dx_list.append(100.0 * abs(pdi - mdi) / denom if denom > 0 else 0.0)

    for i in range(period, len(trs)):
        sm_tr = sm_tr - sm_tr / period + trs[i]
        sm_plus = sm_plus - sm_plus / period + plus_dm[i]
        sm_minus = sm_minus - sm_minus / period + minus_dm[i]
        pdi, mdi = _di(sm_plus, sm_minus, sm_tr)
        denom = pdi + mdi
        dx_list.append(100.0 * abs(pdi - mdi) / denom if denom > 0 else 0.0)

    if len(dx_list) < period:
        return 0.0
    adx = sum(dx_list[:period]) / period
    for dx in dx_list[period:]:
        adx = (adx * (period - 1) + dx) / period
    return float(adx)


def implied_atr_from_stop(entry: float, stop_loss: float, mult: float = 1.5) -> float:
    """由 TV stop_loss 反推隐含 ATR（仅调试）。"""
    entry = _f(entry)
    sl = _f(stop_loss)
    mult = _f(mult, 1.5) or 1.5
    if entry <= 0 or sl <= 0 or mult <= 0:
        return 0.0
    return abs(entry - sl) / mult


def atr_divergence_pct(vps_atr: float, tv_implied_atr: float) -> float:
    v = _f(vps_atr)
    t = _f(tv_implied_atr)
    if v <= 0 or t <= 0:
        return 0.0
    return abs(v - t) / v


class MarketEngine:
    """
    每品种一份：缓存最新 ATR/ADX，K 线闭合后刷新。
    线程安全。
    """

    def __init__(self, symbol: str, fetch_klines=None):
        self.symbol = str(symbol or "").upper()
        self._fetch_klines = fetch_klines  # callable(symbol, interval, limit) -> list
        self._lock = threading.RLock()
        self.atr = 0.0
        self.adx = 0.0
        self.last_bar_open_ms = 0
        self.last_refresh_ts = 0.0
        self.last_error = ""
        self.bars_90m_count = 0

    def bind_fetcher(self, fetch_klines):
        self._fetch_klines = fetch_klines

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "symbol": self.symbol,
                "atr": float(self.atr),
                "adx": float(self.adx),
                "last_bar_open_ms": int(self.last_bar_open_ms),
                "bars_90m": int(self.bars_90m_count),
                "updated_at": float(self.last_refresh_ts),
                "error": self.last_error,
            }

    def refresh(self, force: bool = False) -> Tuple[float, float]:
        """拉取 30m → 合成 90m → 更新 atr/adx。返回 (atr, adx)。"""
        now = time.time()
        with self._lock:
            if (
                not force
                and self.last_refresh_ts > 0
                and (now - self.last_refresh_ts) < REFRESH_MIN_SEC
                and self.atr > 0
            ):
                return float(self.atr), float(self.adx)

        if not callable(self._fetch_klines):
            self.last_error = "no_fetcher"
            return float(self.atr), float(self.adx)

        try:
            raw = self._fetch_klines(self.symbol, BASE_INTERVAL, FETCH_30M_LIMIT)
        except Exception as e:
            self.last_error = str(e)
            logger.warning(f"[行情引擎] {self.symbol} 拉K失败: {e}")
            return float(self.atr), float(self.adx)

        bars90 = merge_30m_to_90m(raw)
        atr = wilder_atr(bars90, ATR_PERIOD)
        adx = wilder_adx(bars90, ADX_PERIOD)
        bar_open = int(bars90[-1][0]) if bars90 else 0

        with self._lock:
            if atr > 0:
                self.atr = atr
            if adx > 0:
                self.adx = adx
            if bar_open > 0:
                self.last_bar_open_ms = bar_open
            self.bars_90m_count = len(bars90)
            self.last_refresh_ts = now
            self.last_error = "" if atr > 0 else "atr_zero"
            logger.info(
                f"[行情引擎] {self.symbol} 90m合成={len(bars90)}根 | "
                f"ATR({ATR_PERIOD})={self.atr:.4f} ADX({ADX_PERIOD})={self.adx:.2f}"
            )
            return float(self.atr), float(self.adx)


# 全局注册表
_ENGINES = {}
_ENGINES_LOCK = threading.Lock()


def get_market_engine(symbol: str, fetch_klines=None) -> MarketEngine:
    sym = str(symbol or "").upper()
    with _ENGINES_LOCK:
        eng = _ENGINES.get(sym)
        if eng is None:
            eng = MarketEngine(sym, fetch_klines=fetch_klines)
            _ENGINES[sym] = eng
        elif fetch_klines is not None:
            eng.bind_fetcher(fetch_klines)
        return eng
