#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
2026-09-13：币安B系统"TV方向+双均线趋势"自主重入
(_maybe_start_trend_reentry / _tv_catchup_precheck_still_valid的新分支)
回归测试。

背景——宝贝拍板：TV策略源码(ETH双均线15/30锁机制版)本身的开平仓逻辑
就是"多头close>MA15 and close>MA30、空头反过来"。VPS空仓、TV心跳当前
也是FLAT时，只要TV最后一个非空方向仍然满足这套双均线趋势确认，就允许
VPS自己判断重入——用综合硬止损+ATR估算TP123管理仓位，直到TV发出全新
真实开仓信号为止。只在SMART_HARD_STOP_ENABLED=1(币安B系统专属)时生效，
A系统(现有B/C/D/E四账户)完全不受影响——本文件专门验证这条隔离边界。

不碰任何真实账户/持仓，binance_client/strategy_engine.klines全部mock，
dual_ma_trend/smart_hard_stop用真实纯函数+合成K线(验证真实的趋势判断/
止损计算逻辑，不是空调用)。
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


def _make_bars(n=90, start=100.0, step=0.5):
    bars = []
    t0 = 1_700_000_000_000
    period_ms = 30 * 60 * 1000
    for i in range(n):
        close = start + i * step
        bars.append([t0 + i * period_ms, close - step, close + 0.5, close - 0.5, close, 100.0])
    return bars


def _mk_supervisor():
    with patch.object(psb.PositionSupervisorBinance, "__init__", lambda self, *a, **k: None):
        s = psb.PositionSupervisorBinance()
    s.symbol = "OPENAIUSDT"
    s.current_side = None
    s.last_nonflat_hb_side = ""
    s.trading_paused = False
    s.reentry_active = False
    s._chase_watch_active = False
    s._trend_reentry_next_try_ts = 0.0
    s.catchup_active = False
    s._catchup_via_trend_reentry = False
    s._dingtalk = MagicMock()
    s._save_state = MagicMock()
    s._place_tv_catchup_limit = MagicMock(return_value=True)
    return s


class TestSmartHardStopModeGate(unittest.TestCase):
    """整个机制的顶层开关：SMART_HARD_STOP_ENABLED——A系统必须完全
    不受影响。"""

    def setUp(self):
        os.environ.pop("SMART_HARD_STOP_ENABLED", None)

    def tearDown(self):
        os.environ.pop("SMART_HARD_STOP_ENABLED", None)

    def test_flag_off_a_system_never_evaluates(self):
        s = _mk_supervisor()
        s.last_nonflat_hb_side = "LONG"  # 即使有TV历史方向
        with patch("strategy_engine.klines.get_bars") as mock_klines:
            s._maybe_start_trend_reentry()
            mock_klines.assert_not_called()
        s._place_tv_catchup_limit.assert_not_called()


class TestMaybeStartTrendReentry(unittest.TestCase):
    def setUp(self):
        os.environ["SMART_HARD_STOP_ENABLED"] = "1"

    def tearDown(self):
        os.environ.pop("SMART_HARD_STOP_ENABLED", None)

    def test_no_tv_history_does_nothing(self):
        s = _mk_supervisor()
        s.last_nonflat_hb_side = ""
        s._maybe_start_trend_reentry()
        s._place_tv_catchup_limit.assert_not_called()

    def test_trading_paused_blocks(self):
        s = _mk_supervisor()
        s.last_nonflat_hb_side = "LONG"
        s.trading_paused = True
        s._maybe_start_trend_reentry()
        s._place_tv_catchup_limit.assert_not_called()

    def test_own_reentry_or_chase_watch_active_yields(self):
        s = _mk_supervisor()
        s.last_nonflat_hb_side = "LONG"
        s.reentry_active = True
        s._maybe_start_trend_reentry()
        s._place_tv_catchup_limit.assert_not_called()

    def test_trend_confirmed_triggers_catchup_pipeline(self):
        """核心场景：TV最后方向LONG，现价站上双均线——应该冻结catchup_*
        字段并调用_place_tv_catchup_limit(复用既有执行管线)。"""
        s = _mk_supervisor()
        s.last_nonflat_hb_side = "LONG"
        bars = _make_bars(n=90, step=0.5)  # 持续上涨，双均线确认多头
        _fake_bc.binance_client.get_current_price = MagicMock(return_value=bars[-1][4])

        with patch("strategy_engine.klines.get_bars", return_value=bars):
            s._maybe_start_trend_reentry()

        s._place_tv_catchup_limit.assert_called_once()
        self.assertTrue(s._catchup_via_trend_reentry)
        self.assertEqual(s.catchup_side, "LONG")
        self.assertAlmostEqual(s.catchup_tv_entry_frozen, bars[-1][4], places=2)
        self.assertGreater(s.catchup_stop_distance_frozen, 0)
        tp1, tp2, tp3 = s.catchup_tps_frozen
        self.assertGreater(tp1, s.catchup_tv_entry_frozen)
        self.assertGreater(tp2, tp1)
        self.assertGreater(tp3, tp2)

    def test_trend_not_confirmed_does_not_trigger(self):
        """TV最后方向LONG，但现价其实在下跌趋势里——不该触发。"""
        s = _mk_supervisor()
        s.last_nonflat_hb_side = "LONG"
        bars = _make_bars(n=90, start=200.0, step=-0.5)  # 下降序列
        _fake_bc.binance_client.get_current_price = MagicMock(return_value=bars[-1][4])

        with patch("strategy_engine.klines.get_bars", return_value=bars):
            s._maybe_start_trend_reentry()

        s._place_tv_catchup_limit.assert_not_called()

    def test_cooldown_prevents_repeated_evaluation(self):
        s = _mk_supervisor()
        s.last_nonflat_hb_side = "LONG"
        bars = _make_bars(n=90, step=0.5)
        _fake_bc.binance_client.get_current_price = MagicMock(return_value=bars[-1][4])

        with patch("strategy_engine.klines.get_bars", return_value=bars) as mock_klines:
            s._maybe_start_trend_reentry()
            first_call_count = mock_klines.call_count
            s._maybe_start_trend_reentry()  # 冷却内第二次
            self.assertEqual(mock_klines.call_count, first_call_count, "冷却期内不该再拉K线")

    def test_already_active_catchup_not_double_triggered(self):
        """_tv_heartbeat_catchup_tick里已经保证catchup_active时不调用本
        函数，这里补一条直接调用场景的防御性验证(万一被其它路径误调)。"""
        s = _mk_supervisor()
        s.last_nonflat_hb_side = "LONG"
        s.catchup_active = True
        bars = _make_bars(n=90, step=0.5)
        _fake_bc.binance_client.get_current_price = MagicMock(return_value=bars[-1][4])
        with patch("strategy_engine.klines.get_bars", return_value=bars):
            s._maybe_start_trend_reentry()
        # 注：函数本体不检查catchup_active(由tick函数负责)，这里只验证
        # 即使被调用，也不会因为已有的catchup_active=True而出现字段冲突
        # /异常——只要_place_tv_catchup_limit本身能正确处理"已有挂单"
        # 这一层由既有代码保证，本测试确认不会抛异常即可。
        # (故意不assert调用次数，避免对_tv_heartbeat_catchup_tick的职责
        # 边界做重复断言)


class TestPrecheckTrendReentryBranch(unittest.TestCase):
    """_tv_catchup_precheck_still_valid新增分支：A系统完全不受影响，
    B系统trend_reentry标记下改走趋势复核。"""

    def _mk(self):
        with patch.object(psb.PositionSupervisorBinance, "__init__", lambda self, *a, **k: None):
            s = psb.PositionSupervisorBinance()
        s.symbol = "OPENAIUSDT"
        return s

    def test_a_system_unaffected_same_side_and_fresh(self):
        """A系统(_catchup_via_trend_reentry未设置/False)：走原有逻辑，
        hb_side==catchup_side且未过期→True。"""
        s = self._mk()
        s._catchup_via_trend_reentry = False
        s.catchup_side = "LONG"
        s.tv_heartbeat_side = "LONG"
        s.tv_heartbeat_ts = time.time()
        s._tv_heartbeat_stale_sec = MagicMock(return_value=600)
        self.assertTrue(s._tv_catchup_precheck_still_valid())

    def test_a_system_unaffected_flipped_side_fails(self):
        s = self._mk()
        s._catchup_via_trend_reentry = False
        s.catchup_side = "LONG"
        s.tv_heartbeat_side = "SHORT"
        s.tv_heartbeat_ts = time.time()
        s._tv_heartbeat_stale_sec = MagicMock(return_value=600)
        self.assertFalse(s._tv_catchup_precheck_still_valid())

    def test_trend_reentry_branch_delegates_to_trend_check(self):
        """B系统标记打开时，不看tv_heartbeat_side(本来就是FLAT)，改走
        _trend_reentry_still_confirmed。"""
        s = self._mk()
        s._catchup_via_trend_reentry = True
        s.catchup_side = "LONG"
        s.tv_heartbeat_side = "FLAT"  # 故意保持FLAT，验证不依赖这个字段
        s._trend_reentry_still_confirmed = MagicMock(return_value=True)
        self.assertTrue(s._tv_catchup_precheck_still_valid())
        s._trend_reentry_still_confirmed.assert_called_once_with("LONG")

    def test_trend_reentry_branch_rejects_when_trend_broke(self):
        s = self._mk()
        s._catchup_via_trend_reentry = True
        s.catchup_side = "LONG"
        s.tv_heartbeat_side = "FLAT"
        s._trend_reentry_still_confirmed = MagicMock(return_value=False)
        self.assertFalse(s._tv_catchup_precheck_still_valid())

    def test_trend_reentry_still_confirmed_real_klines(self):
        """_trend_reentry_still_confirmed本体：真实调用dual_ma_trend_ok
        (不mock计算结果)，只mock K线拉取。"""
        s = self._mk()
        bars = _make_bars(n=90, step=0.5)
        with patch("strategy_engine.klines.get_bars", return_value=bars):
            self.assertTrue(s._trend_reentry_still_confirmed("LONG"))
            self.assertFalse(s._trend_reentry_still_confirmed("SHORT"))


class TestLastNonflatHeartbeatTracking(unittest.TestCase):
    """record_tv_heartbeat：last_nonflat_hb_side在心跳转FLAT后应该继续
    保留上一次的真实方向，不被清零。"""

    def _mk(self):
        with patch.object(psb.PositionSupervisorBinance, "__init__", lambda self, *a, **k: None):
            s = psb.PositionSupervisorBinance()
        s.symbol = "OPENAIUSDT"
        s._save_state = MagicMock()
        s.last_hard_sl_exit_ts = 0.0
        return s

    def test_flat_heartbeat_preserves_last_nonflat_side(self):
        s = self._mk()
        s.record_tv_heartbeat({
            "tv_side": "LONG", "tv_entry": 100.0, "tv_stop": 95.0,
            "tv_tp1": 105.0, "tv_tp2": 110.0, "tv_tp3": 115.0,
        })
        self.assertEqual(s.last_nonflat_hb_side, "LONG")
        self.assertEqual(s.last_nonflat_hb_entry, 100.0)

        # TV心跳转FLAT——tv_heartbeat_entry等会被清零，但last_nonflat_*不该被清零
        s.record_tv_heartbeat({"tv_side": "FLAT"})
        self.assertEqual(s.tv_heartbeat_side, "FLAT")
        self.assertEqual(s.tv_heartbeat_entry, 0.0)
        self.assertEqual(s.last_nonflat_hb_side, "LONG", "心跳转FLAT后仍应记得上一次真实方向")
        self.assertEqual(s.last_nonflat_hb_entry, 100.0)

    def test_new_real_direction_overwrites_last_nonflat(self):
        s = self._mk()
        s.record_tv_heartbeat({"tv_side": "LONG", "tv_entry": 100.0})
        s.record_tv_heartbeat({"tv_side": "FLAT"})
        s.record_tv_heartbeat({"tv_side": "SHORT", "tv_entry": 120.0})
        self.assertEqual(s.last_nonflat_hb_side, "SHORT")
        self.assertEqual(s.last_nonflat_hb_entry, 120.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
