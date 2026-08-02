#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
插针触激活线但 TP1 限价未成交 → sticky 武装仍应成立。
覆盖：pause 期间记极值；回落后 live_only 现价不够时靠 sticky。
"""
from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

os.environ["BINANCE_SKIP_BOOTSTRAP"] = "1"

_fake_bc = MagicMock()
sys.modules.setdefault("binance_client", _fake_bc)
_fake_bc.binance_client = MagicMock()
_fake_bc.is_position_query_failed = lambda x: False
_fake_bc.is_orders_query_failed = lambda x: False

_fake_dt = MagicMock()
sys.modules.setdefault("dingtalk", _fake_dt)

import position_supervisor_binance as psb  # noqa: E402


class TestRadarStickySpike(unittest.TestCase):
    ENTRY = 1951.54
    ACT = 1968.97
    TP1 = 1977.05
    ATR = 14.345

    def _stub(self):
        with patch.object(
            psb.PositionSupervisorBinance, "__init__", lambda self, *a, **k: None
        ):
            s = psb.PositionSupervisorBinance()
        s.symbol = "ETHUSDT"
        s.current_side = "LONG"
        s.watched_entry = self.ENTRY
        s.watched_qty = 0.868
        s.best_price = self.ENTRY
        s.radar_activation_price = self.ACT
        s.radar_activation_frac = 0.9
        s.radar_activation_sticky = False
        s.radar_activated = False
        s.radar_pending_arm = True
        s.tp_levels_consumed = []
        s.tv_tps = [self.TP1, 1997.13, 2022.96]
        s.open_atr = self.ATR
        s.initial_stop = 1944.37
        s.current_sl = 1930.09
        s.frozen_hard_sl_px = 1930.09
        s._post_recover_radar_pulse = False
        s._radar_arm_ding_sent = False
        s._radar_notify_pending = False
        s.reentry_attempt = 0
        s._save_state = MagicMock()
        return s

    def test_spike_then_revert_sets_sticky_without_tp_fill(self):
        s = self._stub()
        self.assertEqual(s.tp_levels_consumed, [])
        self.assertTrue(s._note_mark_extremum(self.TP1))
        self.assertTrue(s.radar_activation_sticky)
        self.assertGreaterEqual(s.best_price, self.ACT)
        self.assertTrue(s._post_recover_radar_pulse)

        drop = 1942.0
        self.assertFalse(
            s._price_reached_radar_activation(drop, live_only=True)
        )
        self.assertTrue(s._activation_reached_for_arm(drop))

    def test_arm_gate_ignores_tp1_fill_status(self):
        s = self._stub()
        s._note_mark_extremum(self.ACT)
        s.tp_levels_consumed = []
        self.assertTrue(s._activation_reached_for_arm(1940.0))

        placed = []

        def _ensure(init, live_qty=0, for_handoff=False):
            placed.append(init)
            return True

        s._ensure_radar_sl = _ensure
        s._apply_tier_breath_overlay = lambda: None
        s._report_radar_first_activation = MagicMock()
        s._get_locked_initial_atr = lambda: self.ATR

        ok = s._maybe_arm_radar_on_activation(
            0.868, 1940.0, source="测试·插针回撤",
        )
        self.assertTrue(ok)
        self.assertTrue(s.radar_activated)
        self.assertEqual(len(placed), 1)
        self.assertEqual(s.tp_levels_consumed, [])

    def test_force_radar_after_sticky_not_fill(self):
        s = self._stub()
        s.radar_activation_sticky = True
        s.best_price = self.TP1
        self.assertTrue(s._should_force_radar_after_tp_progress(0.868, 1942.0))

    def test_pause_window_still_records_extremum(self):
        """假暂停休眠窗：只记 best/sticky，不依赖 TP 成交。"""
        s = self._stub()
        s.trading_paused = True
        # 模拟 pause 采样循环里的极值更新
        for px in (1955.0, 1960.0, self.TP1, 1945.0):
            s._note_mark_extremum(px)
        self.assertAlmostEqual(s.best_price, self.TP1)
        self.assertTrue(s.radar_activation_sticky)
        # 恢复后现价已回落，仍应武装闸打开
        self.assertTrue(s._activation_reached_for_arm(1940.0))

    def test_ws_hard_sl_hint_survives_price_drift(self):
        s = self._stub()
        s.frozen_hard_sl_px = 1929.79
        s._latch_ws_hard_sl_fill(fill_px=1929.50, stop_px=1929.79, source="test")
        # 醒来时 mark 已远离硬止损价
        self.assertTrue(s._exit_px_near_hard(1945.0))

    def test_version_bump(self):
        self.assertIn("false-pause-iron", psb.BINANCE_VPS_VERSION)
        self.assertIn("sticky", open(
            os.path.join(ROOT, "position_supervisor_binance.py"), encoding="utf-8"
        ).read())


if __name__ == "__main__":
    unittest.main()
