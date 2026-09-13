#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
2026-09-13：币安B系统"突发放量反转K线快速锁保本"
(_maybe_fast_lock_on_impulse_candle) 回归测试。

背景——宝贝实盘截图复盘BNBUSDT.P发现："等阳线站上双均线再平仓就已经晚
了"：等K线收盘价真正站上/跌破双均线才反应，对一根走势凌厉的反转K线来
说太慢，等确认时行情往往已经跑掉大半。这是三层防线里最快的一层：不等
双均线正式突破确认，只看最新一根K线自己够不够"决定性"(实体够大+真放
量)，够的话立刻把止损棘轮到保本价，不直接平仓(避免单根插针误杀)。
只在SMART_HARD_STOP_ENABLED=1(币安B系统专属)生效。

不碰任何真实账户/持仓，strategy_engine.klines/dingtalk全部mock，
breath_stop.initial_stop_price用真实纯函数。
"""
from __future__ import annotations

import os
import sys
import time
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

os.environ["BINANCE_SKIP_BOOTSTRAP"] = "1"
_fake_bc = sys.modules.setdefault("binance_client", MagicMock())
_fake_bc.binance_client = MagicMock()
_fake_bc.is_position_query_failed = lambda x: False
_fake_bc.is_orders_query_failed = lambda x: False
sys.modules.setdefault("dingtalk", MagicMock())

import position_supervisor_binance as psb  # noqa: E402


def _make_bars(n=25, base_vol=100.0, last_body_ratio=0.7, last_vol_mult=2.0,
               bullish_last=True, period_min=45):
    bars = []
    t0 = 1_700_000_000_000
    period_ms = period_min * 60 * 1000
    px = 100.0
    for i in range(n - 1):
        o = px
        c = px - 0.1
        h = max(o, c) + 1.0
        l = min(o, c) - 1.0
        bars.append([t0 + i * period_ms, o, h, l, c, base_vol])
        px = c
    o = px
    rng = 5.0
    body = rng * last_body_ratio
    wick_each = (rng - body) / 2.0
    if bullish_last:
        c = o + body
        h = c + wick_each
        l = o - wick_each
    else:
        c = o - body
        h = o + wick_each
        l = c - wick_each
    bars.append([t0 + (n - 1) * period_ms, o, h, l, c, base_vol * last_vol_mult])
    return bars


def _mk_supervisor(symbol="BNBUSDT", side="SHORT"):
    with patch.object(psb.PositionSupervisorBinance, "__init__", lambda self, *a, **k: None):
        s = psb.PositionSupervisorBinance()
    s.symbol = symbol
    s.current_side = side
    s.trading_paused = False
    s.api_monitor_only = False
    s.current_atr = 2.0
    s.watched_entry = 100.0
    s.breath_profile = {}
    s._impulse_exit_last_check_ts = 0.0
    s._impulse_exit_alerted_bar = 0
    s._get_locked_initial_atr = MagicMock(return_value=2.0)
    s._dingtalk = MagicMock()
    return s


class TestSmartHardStopModeGate(unittest.TestCase):
    def setUp(self):
        os.environ.pop("SMART_HARD_STOP_ENABLED", None)

    def tearDown(self):
        os.environ.pop("SMART_HARD_STOP_ENABLED", None)

    def test_flag_off_a_system_returns_candidate_unchanged(self):
        s = _mk_supervisor()
        with patch("strategy_engine.klines.get_bars") as mock_klines:
            out = s._maybe_fast_lock_on_impulse_candle(90.0, 95.0)
            mock_klines.assert_not_called()
        self.assertEqual(out, 95.0)


class TestImpulseCandleLock(unittest.TestCase):
    def setUp(self):
        os.environ["SMART_HARD_STOP_ENABLED"] = "1"

    def tearDown(self):
        os.environ.pop("SMART_HARD_STOP_ENABLED", None)

    def test_no_position_does_nothing(self):
        s = _mk_supervisor(side=None)
        with patch("strategy_engine.klines.get_bars") as mock_klines:
            out = s._maybe_fast_lock_on_impulse_candle(90.0, 95.0)
            mock_klines.assert_not_called()
        self.assertEqual(out, 95.0)

    def test_trading_paused_blocks(self):
        s = _mk_supervisor()
        s.trading_paused = True
        with patch("strategy_engine.klines.get_bars") as mock_klines:
            s._maybe_fast_lock_on_impulse_candle(90.0, 95.0)
            mock_klines.assert_not_called()

    def test_decisive_bullish_candle_with_volume_locks_breakeven_for_short(self):
        """空头持仓：最新一根K线是决定性阳线(实体大+真放量) → 止损锁到
        保本价(entry=100,ATR=2 → initial_stop_price≈99.91)。"""
        s = _mk_supervisor(side="SHORT")
        bars = _make_bars(bullish_last=True, last_body_ratio=0.9, last_vol_mult=2.0)
        with patch("strategy_engine.klines.get_bars", return_value=bars):
            out = s._maybe_fast_lock_on_impulse_candle(bars[-1][4], 200.0)
        self.assertAlmostEqual(out, 99.91, places=2)

    def test_decisive_bearish_candle_with_volume_locks_breakeven_for_long(self):
        s = _mk_supervisor(side="LONG")
        bars = _make_bars(bullish_last=False, last_body_ratio=0.9, last_vol_mult=2.0)
        with patch("strategy_engine.klines.get_bars", return_value=bars):
            out = s._maybe_fast_lock_on_impulse_candle(bars[-1][4], 0.0)
        self.assertAlmostEqual(out, 100.09, places=2)

    def test_small_body_indecisive_candle_no_action(self):
        """实体太小(十字星/长影线类)——不算决定性，不触发。"""
        s = _mk_supervisor(side="SHORT")
        bars = _make_bars(bullish_last=True, last_body_ratio=0.2, last_vol_mult=2.0)
        with patch("strategy_engine.klines.get_bars", return_value=bars):
            out = s._maybe_fast_lock_on_impulse_candle(bars[-1][4], 200.0)
        self.assertEqual(out, 200.0)

    def test_decisive_candle_without_volume_no_action(self):
        """实体够大但量能没有真的放大——不触发，避免假信号。"""
        s = _mk_supervisor(side="SHORT")
        bars = _make_bars(bullish_last=True, last_body_ratio=0.9, last_vol_mult=1.0)
        with patch("strategy_engine.klines.get_bars", return_value=bars):
            out = s._maybe_fast_lock_on_impulse_candle(bars[-1][4], 200.0)
        self.assertEqual(out, 200.0)

    def test_decisive_candle_in_favor_of_position_no_action(self):
        """决定性K线方向跟持仓一致(顺势大阳线，SHORT遇到大阴线)——不算
        逆势反转，不触发。"""
        s = _mk_supervisor(side="SHORT")
        bars = _make_bars(bullish_last=False, last_body_ratio=0.9, last_vol_mult=2.0)
        with patch("strategy_engine.klines.get_bars", return_value=bars):
            out = s._maybe_fast_lock_on_impulse_candle(bars[-1][4], 200.0)
        self.assertEqual(out, 200.0)

    def test_does_not_loosen_already_tighter_candidate(self):
        """candidate_sl已经比保本价更紧——不倒退放松。"""
        s = _mk_supervisor(side="SHORT")
        bars = _make_bars(bullish_last=True, last_body_ratio=0.9, last_vol_mult=2.0)
        with patch("strategy_engine.klines.get_bars", return_value=bars):
            out = s._maybe_fast_lock_on_impulse_candle(bars[-1][4], 50.0)  # 已经比99.91更紧
        self.assertEqual(out, 50.0)

    def test_same_bar_alert_not_repeated_but_lock_still_applies(self):
        s = _mk_supervisor(side="SHORT")
        bars = _make_bars(bullish_last=True, last_body_ratio=0.9, last_vol_mult=2.0)
        with patch("strategy_engine.klines.get_bars", return_value=bars):
            s._maybe_fast_lock_on_impulse_candle(bars[-1][4], 200.0)
            s._impulse_exit_last_check_ts = 0.0  # 模拟节流窗口已过期
            out2 = s._maybe_fast_lock_on_impulse_candle(bars[-1][4], 200.0)
        self.assertAlmostEqual(out2, 99.91, places=2)
        self.assertEqual(s._dingtalk.call_count, 1)  # 同一根K线只报警一次

    def test_throttle_skips_refetch_within_window(self):
        s = _mk_supervisor()
        s._impulse_exit_last_check_ts = time.time()
        with patch("strategy_engine.klines.get_bars") as mock_klines:
            out = s._maybe_fast_lock_on_impulse_candle(90.0, 95.0)
            mock_klines.assert_not_called()
        self.assertEqual(out, 95.0)

    def test_klines_fetch_failure_is_safe_noop(self):
        s = _mk_supervisor()
        with patch("strategy_engine.klines.get_bars", side_effect=RuntimeError("boom")):
            out = s._maybe_fast_lock_on_impulse_candle(90.0, 95.0)
        self.assertEqual(out, 95.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
