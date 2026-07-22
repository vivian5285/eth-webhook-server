#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
v15.5.11 regression:
1) empty ledger must NOT invent stop from default ATR=30
2) TP timeout handoff must survive stale-consumed clear (Gemini-class loop)
"""
from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# 禁止 import 触发实盘 bootstrap_supervisors()
os.environ["BINANCE_SKIP_BOOTSTRAP"] = "1"

# Avoid live Binance Client() on import (local SOCKS / no keys)
_fake_bc = MagicMock()
sys.modules.setdefault("binance_client", _fake_bc)
_fake_bc.binance_client = MagicMock()

_fake_dt = MagicMock()
sys.modules.setdefault("dingtalk", _fake_dt)

try:
    import position_supervisor_binance as psb
    from breath_stop import initial_stop_price

    IMPORT_OK = True
    IMPORT_ERR = ""
except Exception as exc:  # pragma: no cover
    IMPORT_OK = False
    IMPORT_ERR = str(exc)
    psb = None  # type: ignore
    initial_stop_price = None  # type: ignore


@unittest.skipUnless(IMPORT_OK, f"import failed: {IMPORT_ERR}")
class TestRestartStopNoAtr30Invent(unittest.TestCase):
    def _make_sup(self):
        with patch.object(psb.PositionSupervisorBinance, "__init__", lambda self, *a, **k: None):
            s = psb.PositionSupervisorBinance()
        s.symbol = "ETHUSDT"
        s.watched_entry = 1931.53
        s.current_side = "LONG"
        s.current_sl = 0.0
        s.initial_stop = 0.0
        s.tv_sl = 0.0
        s.tv_sl_ref = 0.0
        s.open_atr = 0.0
        s.current_atr = 0.0
        s.tv_price = 0.0
        s.state_file = os.path.join(ROOT, "_tmp_test_state_restart.json")
        s._last_applied_exchange_sl = 0.0
        s._save_state = lambda: None
        return s

    def test_math_atr30_equals_1886(self):
        # Documented incident: entry - 1.5*30 = 1886.53
        px = initial_stop_price("LONG", 1931.53, 30.0)
        self.assertAlmostEqual(float(px), 1886.53, places=2)

    def test_tv_hard_sl_target_no_invent_by_default(self):
        s = self._make_sup()
        s.open_atr = 30.0
        self.assertEqual(s._tv_hard_sl_target(), 0.0)
        invented = s._tv_hard_sl_target(allow_atr_invent=True)
        self.assertAlmostEqual(invented, 1886.53, places=2)

    def test_sanitize_refuses_default_atr30_invent(self):
        s = self._make_sup()
        with patch.object(psb.binance_client, "find_protective_stop_prices", return_value=[]):
            ok = s._sanitize_vps_hard_sl_ledger(source="unit")
        self.assertFalse(ok)
        self.assertEqual(float(s.current_sl or 0), 0.0)

    def test_sanitize_adopts_exchange_before_invent(self):
        s = self._make_sup()
        with patch.object(
            psb.binance_client, "find_protective_stop_prices", return_value=[1910.18]
        ):
            ok = s._sanitize_vps_hard_sl_ledger(source="unit")
        self.assertTrue(ok)
        self.assertAlmostEqual(float(s.current_sl), 1910.18, places=2)


@unittest.skipUnless(IMPORT_OK, f"import failed: {IMPORT_ERR}")
class TestTpTimeoutHandoffSurvivesClear(unittest.TestCase):
    def _make_sup(self):
        with patch.object(psb.PositionSupervisorBinance, "__init__", lambda self, *a, **k: None):
            s = psb.PositionSupervisorBinance()
        s.symbol = "ETHUSDT"
        s.watched_entry = 1931.53
        s.current_side = "LONG"
        s.tv_tps = [1950.53, 1967.60, 1985.0]
        s.tp_levels_consumed = [1]
        s.tp_levels_radar_handoff = [1]
        s.state_file = os.path.join(ROOT, "_tmp_test_state_restart.json")
        s._save_state = lambda: None
        return s

    def test_handoff_blocks_rehang_after_stale_clear(self):
        s = self._make_sup()
        # Without handoff, full-size + not past price would wipe consumed (Gemini loop fuel)
        s.tp_levels_radar_handoff = []
        with patch.object(psb.binance_client, "get_current_price", return_value=1920.0), \
             patch.object(s, "_infer_tp_consumed_sequential", return_value=[]), \
             patch.object(s, "_price_reached_tp_zone", return_value=False), \
             patch.object(s, "_has_tp_limit_at_price", return_value=False):
            wiped = s._reconcile_stale_tp_consumed(0.033, 0.033, curr_px=1920.0)
        self.assertTrue(wiped)
        self.assertEqual(s.tp_levels_consumed, [])

        # With handoff, same wipe path must KEEP the level → still blocks rehang
        s.tp_levels_consumed = [1]
        s.tp_levels_radar_handoff = [1]
        with patch.object(psb.binance_client, "get_current_price", return_value=1920.0), \
             patch.object(s, "_infer_tp_consumed_sequential", return_value=[]), \
             patch.object(s, "_price_reached_tp_zone", return_value=False), \
             patch.object(s, "_has_tp_limit_at_price", return_value=False):
            cleared = s._reconcile_stale_tp_consumed(0.033, 0.033, curr_px=1920.0)
        self.assertFalse(cleared)  # nothing unsafe to drop
        self.assertEqual(s.tp_levels_consumed, [1])
        self.assertTrue(s._tp_level_consumed(1))


if __name__ == "__main__":
    unittest.main()
