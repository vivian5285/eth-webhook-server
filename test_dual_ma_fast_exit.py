#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
2026-09-13：币安B系统"双均线破位快速平仓"(_maybe_fast_exit_on_dual_ma_break)
回归测试。

背景——宝贝拍板：币安B系统OPENAI的ATR跟踪止损雷达在反弹时被打出，同一
时刻CoinW的OPENAI止损还没被打到、仍在持仓；宝贝指出雷达不该只是单一的
ATR跟踪系数去锁利润，还要主动看这个品种自己真实周期的裸K是否跌破/站上
快慢双均线(8/20)——真实放量确认破位时直接市价快速平仓，没放量确认(疑似
假突破)时只适度收紧止损、不强平，给行情留时间验证是否真反转。只在
SMART_HARD_STOP_ENABLED=1(币安B系统专属)时生效，A系统完全不受影响。

不碰任何真实账户/持仓，binance_client/strategy_engine.klines/dingtalk
全部mock，dual_ma_trend/smart_hard_stop用真实纯函数+合成K线(验证真实的
趋势判断/放量确认逻辑，不是空调用)。
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


def _make_bars(decline_n=40, decline_step=-1.0, rally_n=10, rally_step=3.0,
               start=100.0, surge=False, period_min=120):
    """先跌后涨的V形合成K线：rally_n=0时纯下跌(趋势仍成立)，rally_n>0时
    尾部拉回(可能破位)。surge=True时最后3根量能放大到3倍，触发真放量确认。"""
    bars = []
    t0 = 1_700_000_000_000
    period_ms = period_min * 60 * 1000
    px = start
    i = 0
    for _ in range(decline_n):
        px += decline_step
        bars.append([t0 + i * period_ms, px - decline_step, px + 0.5, px - 0.5, px, 100.0])
        i += 1
    for j in range(rally_n):
        px += rally_step
        vol = 300.0 if (surge and j >= rally_n - 3) else 100.0
        bars.append([t0 + i * period_ms, px - rally_step, px + 0.5, px - 0.5, px, vol])
        i += 1
    return bars


def _mk_supervisor(symbol="OPENAIUSDT", side="SHORT"):
    with patch.object(psb.PositionSupervisorBinance, "__init__", lambda self, *a, **k: None):
        s = psb.PositionSupervisorBinance()
    s.symbol = symbol
    s.current_side = side
    s.trading_paused = False
    s.api_monitor_only = False
    s.current_atr = 2.0
    s._dual_ma_exit_last_check_ts = 0.0
    s._dual_ma_exit_closed_bar = 0
    s._get_locked_initial_atr = MagicMock(return_value=2.0)
    s._dingtalk = MagicMock()
    s._close_all = MagicMock()
    return s


class TestSmartHardStopModeGate(unittest.TestCase):
    """顶层开关：SMART_HARD_STOP_ENABLED——A系统必须完全不受影响。"""

    def setUp(self):
        os.environ.pop("SMART_HARD_STOP_ENABLED", None)

    def tearDown(self):
        os.environ.pop("SMART_HARD_STOP_ENABLED", None)

    def test_flag_off_a_system_returns_candidate_unchanged(self):
        s = _mk_supervisor()
        with patch("strategy_engine.klines.get_bars") as mock_klines:
            out = s._maybe_fast_exit_on_dual_ma_break(90.0, 95.0)
            mock_klines.assert_not_called()
        self.assertEqual(out, 95.0)
        s._close_all.assert_not_called()


class TestDualMaFastExit(unittest.TestCase):
    def setUp(self):
        os.environ["SMART_HARD_STOP_ENABLED"] = "1"

    def tearDown(self):
        os.environ.pop("SMART_HARD_STOP_ENABLED", None)

    def test_no_position_does_nothing(self):
        s = _mk_supervisor(side=None)
        with patch("strategy_engine.klines.get_bars") as mock_klines:
            out = s._maybe_fast_exit_on_dual_ma_break(90.0, 95.0)
            mock_klines.assert_not_called()
        self.assertEqual(out, 95.0)

    def test_trading_paused_blocks(self):
        s = _mk_supervisor()
        s.trading_paused = True
        with patch("strategy_engine.klines.get_bars") as mock_klines:
            s._maybe_fast_exit_on_dual_ma_break(90.0, 95.0)
            mock_klines.assert_not_called()

    def test_trend_still_intact_no_action(self):
        """空头仍在双均线下方(纯下跌，没有拉回)——不触发任何动作。"""
        s = _mk_supervisor()
        bars = _make_bars(decline_n=50, rally_n=0)
        with patch("strategy_engine.klines.get_bars", return_value=bars):
            out = s._maybe_fast_exit_on_dual_ma_break(bars[-1][4], 60.0)
        self.assertEqual(out, 60.0)
        s._close_all.assert_not_called()

    def test_break_with_volume_confirmed_triggers_close_all(self):
        """空头收盘价拉回站上双均线 + 真实放量确认 → 直接市价平仓。"""
        s = _mk_supervisor()
        s.current_sl = 55.0  # _close_all后应该读取这个"平仓后"的值
        bars = _make_bars(decline_n=40, rally_n=10, surge=True)
        with patch("strategy_engine.klines.get_bars", return_value=bars):
            out = s._maybe_fast_exit_on_dual_ma_break(bars[-1][4], 60.0)
        s._close_all.assert_called_once()
        self.assertIn("双均线破位", s._close_all.call_args.kwargs.get("reason", ""))
        self.assertEqual(out, 55.0)  # 返回_close_all之后的current_sl，不是平仓前的candidate

    def test_break_without_volume_confirmation_only_tightens(self):
        """空头收盘价拉回站上双均线，但量能没有真实放大(疑似假突破)——不
        强平，只适度收紧candidate_sl。"""
        s = _mk_supervisor()
        bars = _make_bars(decline_n=40, rally_n=10, surge=False)
        close_px = bars[-1][4]
        with patch("strategy_engine.klines.get_bars", return_value=bars):
            out = s._maybe_fast_exit_on_dual_ma_break(close_px, 200.0)
        s._close_all.assert_not_called()
        # SHORT: tighter = close_px + 0.3×ATR(2.0) = close_px + 0.6，且比candidate(200)更紧
        self.assertAlmostEqual(out, close_px + 0.6, places=4)
        self.assertLess(out, 200.0)

    def test_break_confirmed_same_bar_not_retriggered(self):
        """同一根K线已经触发过市价平仓——即使再次调用(节流窗口过期后)，
        同一根K线不应该重复调用_close_all。"""
        s = _mk_supervisor()
        bars = _make_bars(decline_n=40, rally_n=10, surge=True)
        with patch("strategy_engine.klines.get_bars", return_value=bars):
            s._maybe_fast_exit_on_dual_ma_break(bars[-1][4], 60.0)
            s._dual_ma_exit_last_check_ts = 0.0  # 模拟节流窗口已过期
            s._maybe_fast_exit_on_dual_ma_break(bars[-1][4], 60.0)
        s._close_all.assert_called_once()

    def test_throttle_skips_refetch_within_window(self):
        s = _mk_supervisor()
        s._dual_ma_exit_last_check_ts = time.time()
        with patch("strategy_engine.klines.get_bars") as mock_klines:
            out = s._maybe_fast_exit_on_dual_ma_break(90.0, 95.0)
            mock_klines.assert_not_called()
        self.assertEqual(out, 95.0)

    def test_long_side_mirrors_short_logic(self):
        """多头：站上双均线才算趋势成立；跌破+放量确认 → 平仓。"""
        s = _mk_supervisor(side="LONG")
        s.current_sl = 45.0
        # 上涨然后回落破位，模拟多头趋势反转
        bars = _make_bars(decline_n=0, rally_n=0, start=50.0)
        # 构造：先涨后跌
        bars = []
        t0 = 1_700_000_000_000
        period_ms = 120 * 60 * 1000
        px = 50.0
        i = 0
        for _ in range(40):
            px += 1.0
            bars.append([t0 + i * period_ms, px - 1.0, px + 0.5, px - 0.5, px, 100.0])
            i += 1
        for j in range(10):
            px -= 3.0
            vol = 300.0 if j >= 7 else 100.0
            bars.append([t0 + i * period_ms, px + 3.0, px + 0.5, px - 0.5, px, vol])
            i += 1
        with patch("strategy_engine.klines.get_bars", return_value=bars):
            out = s._maybe_fast_exit_on_dual_ma_break(bars[-1][4], 40.0)
        s._close_all.assert_called_once()
        self.assertEqual(out, 45.0)

    def test_klines_fetch_failure_is_safe_noop(self):
        s = _mk_supervisor()
        with patch("strategy_engine.klines.get_bars", side_effect=RuntimeError("boom")):
            out = s._maybe_fast_exit_on_dual_ma_break(90.0, 95.0)
        self.assertEqual(out, 95.0)
        s._close_all.assert_not_called()

    def test_uses_symbol_specific_interval(self):
        """OPENAI应该用120分钟(2h)，不是重入用的固定30分钟。"""
        from radar_reentry_mixin import DUAL_MA_EXIT_INTERVAL_MIN
        s = _mk_supervisor(symbol="OPENAIUSDT")
        bars = _make_bars(decline_n=50, rally_n=0)
        with patch("strategy_engine.klines.get_bars", return_value=bars) as mock_klines:
            s._maybe_fast_exit_on_dual_ma_break(bars[-1][4], 60.0)
            args, kwargs = mock_klines.call_args
            self.assertEqual(args[1], f"{DUAL_MA_EXIT_INTERVAL_MIN['OPENAIUSDT']}m")
            self.assertEqual(DUAL_MA_EXIT_INTERVAL_MIN["OPENAIUSDT"], 120)


if __name__ == "__main__":
    unittest.main(verbosity=2)
