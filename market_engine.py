#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
VPS 行情引擎：30m×3 合成 90m → ATR(14) / ADX(14)

币安无原生 90m；拉 30m K 线合并后算 Wilder ATR/ADX（与 TV RMA 对齐）。
止损决策只认本模块数值；webhook 不传 ATR/ADX。
"""
from __future__ import annotations

import logging
import threading
time_mod = __import__("time")
from typing import List, Sequence, Tuple

logger = logging.getLogger(__name__)

KLINE_INTERVAL = "30m"  # 拉取周期；合成后等效 90m
SYNTH_INTERVAL = "90m"
ATR_PERIOD = 14
ADX_PERIOD = 14
FETCH_LIMIT = 220
REFRESH_MIN_SEC = 60.0
ATR_COMPARE_ALERT_PCT = 0.20


def _f(x, default=0.0) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return float(default)


def merge_30m_to_90m(klines_30m: Sequence) -> List[list]:
    """每 3 根 30m 合并为 1 根 90m：[open_time, o, h, l, c, volume, ...]."""
    rows = list(klines_30m or [])
    if len(rows) < 3:
        return []
    out = []
    # 对齐到完整三元组
    n = len(rows) - (len(rows) % 3)
    for i in range(0, n, 3):
        a, b, c = rows[i], rows[i + 1], rows[i + 2]
        out.append([
            a[0],
            a[1],
            max(_f(a[2]), _f(b[2]), _f(c[2])),
            min(_f(a[3]), _f(b[3]), _f(c[3])),
            c[4],
            _f(a[5]) + _f(b[5]) + _f(c[5]),
        ])
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
    n = len(bars or [])
    if n < period * 2 + 2:
        return 0.0

    plus_dm, minus_dm, trs = [], [], []
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
    def __init__(self, symbol: str, fetch_klines=None):
        self.symbol = str(symbol or "").upper()
        self._fetch_klines = fetch_klines
        self._lock = threading.RLock()
        self.atr = 0.0
        self.adx = 0.0
        self.last_bar_open_ms = 0
        self.last_refresh_ts = 0.0
        self.last_error = ""
        self.bars_count = 0

    def bind_fetcher(self, fetch_klines):
        self._fetch_klines = fetch_klines

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "symbol": self.symbol,
                "atr": float(self.atr),
                "adx": float(self.adx),
                "interval": SYNTH_INTERVAL,
                "source_interval": KLINE_INTERVAL,
                "last_bar_open_ms": int(self.last_bar_open_ms),
                "bars": int(self.bars_count),
                "updated_at": float(self.last_refresh_ts),
                "error": self.last_error,
            }

    def refresh(self, force: bool = False) -> Tuple[float, float]:
        now = time_mod.time()
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
            raw = self._fetch_klines(self.symbol, KLINE_INTERVAL, FETCH_LIMIT)
        except Exception as e:
            self.last_error = str(e)
            logger.warning(f"[行情引擎] {self.symbol} 拉K失败: {e}")
            return float(self.atr), float(self.adx)

        bars = merge_30m_to_90m(raw or [])
        atr = wilder_atr(bars, ATR_PERIOD)
        adx = wilder_adx(bars, ADX_PERIOD)
        bar_open = int(bars[-1][0]) if bars else 0

        with self._lock:
            if atr > 0:
                self.atr = atr
            if adx > 0:
                self.adx = adx
            if bar_open > 0:
                self.last_bar_open_ms = bar_open
            self.bars_count = len(bars)
            self.last_refresh_ts = now
            self.last_error = "" if atr > 0 else "atr_zero"
            logger.info(
                f"[行情引擎] {self.symbol} 90m={len(bars)}根(←30m) | "
                f"ATR({ATR_PERIOD})={self.atr:.4f} ADX({ADX_PERIOD})={self.adx:.2f}"
            )
            return float(self.atr), float(self.adx)


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
