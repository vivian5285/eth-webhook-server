#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""归因诚实化回归：未登记仓位文案 + 平仓须贴止损线才标止损。"""
from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

os.environ["BINANCE_SKIP_BOOTSTRAP"] = "1"

# 只挡交易所客户端；钉钉用真实模块测文案
_fake_bc = MagicMock()
sys.modules["binance_client"] = _fake_bc
_fake_bc.binance_client = MagicMock()

import position_supervisor_binance as psb  # noqa: E402
import dingtalk as dt  # noqa: E402


class TestAttributionHonest(unittest.TestCase):
    def _sup(self):
        with patch.object(psb.PositionSupervisorBinance, "__init__", lambda self, *a, **k: None):
            s = psb.PositionSupervisorBinance()
        s.symbol = "ETHUSDT"
        s.current_side = "LONG"
        s.watched_entry = 1918.95
        s.watched_qty = 0.023
        s.current_sl = 1890.0
        s.initial_stop = 1890.0
        s.tv_sl = 1890.0
        s._last_applied_exchange_sl = 1890.0
        s.radar_activated = True
        s._radar_handoff_done = True
        s.breakeven_phase = False
        s.shield_active = True
        s.last_tv_signal = None
        s.tp_levels_consumed = []
        s._radar_trigger_gate = ""
        s._save_state = lambda: None
        return s

    def test_favorable_flat_not_labeled_stop(self):
        s = self._sup()
        src, note = s._resolve_exit_source(curr_px=1920.61, hint_reason="脚本全平")
        self.assertEqual(src, "manual")
        self.assertNotIn("止损平仓", note)

    def test_near_stop_still_stop(self):
        s = self._sup()
        src, note = s._resolve_exit_source(curr_px=1890.05)
        self.assertEqual(src, "sl_initial")
        self.assertIn("止损平仓", note)

    def test_manual_dingtalk_wording(self):
        captured = {}

        def fake_send(title, data, *a, **k):
            captured["title"] = title
            captured["data"] = data

        with patch.object(dt, "send_alert", fake_send):
            dt.report_manual_position_change(
                "检测到未登记来源的仓位，来源待核实",
                0.0, 0.023, 1918.95, verify_note="unit", verified=True,
            )
        self.assertIn("data", captured)
        joined = str(captured["data"])
        self.assertIn("来源待核实", joined)
        self.assertNotIn("人工开仓 · 系统接管", joined)
        self.assertNotIn("中势推升", joined)

    def test_regime_name_no_legacy_tier(self):
        name = dt.get_regime_name(3)
        self.assertIsInstance(name, str)
        self.assertNotIn("中势推升", name)
        self.assertIn("RISK20", name)


if __name__ == "__main__":
    unittest.main()
