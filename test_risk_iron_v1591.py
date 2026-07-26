#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""v15.9.1：exit_ownership / 防御标签 / 硬上限 单元烟雾。"""
import os
import sys
import unittest

os.environ["BINANCE_SKIP_BOOTSTRAP"] = "1"
sys.modules.setdefault("binance", __import__("unittest.mock").mock.MagicMock())
sys.modules.setdefault("binance.client", __import__("unittest.mock").mock.MagicMock())

from order_idempotency import (  # noqa: E402
    MAX_OPEN_ORDERS_HARD_CAP,
    blank_ownership_state,
    make_defense_client_order_id,
)


class TestOrderIdempotency(unittest.TestCase):
    def test_cap_is_five(self):
        self.assertEqual(MAX_OPEN_ORDERS_HARD_CAP, 5)

    def test_blank_ownership(self):
        s = blank_ownership_state()
        self.assertEqual(s["exit_ownership"], "NONE")
        self.assertEqual(s["pending_order_tags"], {})

    def test_tag_unique_enough(self):
        a = make_defense_client_order_id("ETHUSDT", "TP1", 1900.0, ts=1.0)
        b = make_defense_client_order_id("ETHUSDT", "TP2", 1900.0, ts=1.0)
        self.assertNotEqual(a, b)
        self.assertLessEqual(len(a), 36)


class TestOwnershipGate(unittest.TestCase):
    def test_refuse_tp3_when_radar_owns(self):
        from position_supervisor_binance import PositionSupervisorBinance

        s = PositionSupervisorBinance.__new__(PositionSupervisorBinance)
        s.symbol = "ETHUSDT"
        s.exit_ownership = "RADAR_STOP"
        s._pending_order_tags = {}
        s._orders_book_readable = lambda: True
        out = PositionSupervisorBinance._place_defense_tp_limit(
            s, "SELL", 0.1, 2000.0, 3, retries=0,
        )
        self.assertIsNone(out)

    def test_refuse_when_pending_tag(self):
        from position_supervisor_binance import PositionSupervisorBinance

        s = PositionSupervisorBinance.__new__(PositionSupervisorBinance)
        s.symbol = "ETHUSDT"
        s.exit_ownership = "NONE"
        s._pending_order_tags = {
            "DETP1abc": {"kind": "TP1", "status": "open", "order_id": "1"},
        }
        s._orders_book_readable = lambda: True
        out = PositionSupervisorBinance._place_defense_tp_limit(
            s, "SELL", 0.1, 2000.0, 1, retries=0,
        )
        self.assertIsNone(out)

    def test_pause_on_mutex_race(self):
        from unittest.mock import patch
        from position_supervisor_binance import PositionSupervisorBinance

        s = PositionSupervisorBinance.__new__(PositionSupervisorBinance)
        s.symbol = "ETHUSDT"
        s.trading_paused = False
        s.trading_pause_reason = ""
        s._defense_order_ids = {"radar_stop": "99", "hard_stop": "1", "stop": "99"}
        s.radar_activated = True
        s.radar_pending_arm = True
        s._radar_handoff_done = False
        s._pending_order_tags = {}
        calls = {"save": 0, "recon": 0, "ding": 0}

        def _save():
            calls["save"] += 1

        def _recon(**kwargs):
            calls["recon"] += 1
            return True

        def _ding(*a, **k):
            calls["ding"] += 1

        s._save_state = _save
        s._force_reconcile_position_vs_local = _recon
        s._call_dingtalk = _ding
        s._clear_pending_tags_for_kind = lambda *a, **k: None

        class _BC:
            @staticmethod
            def cancel_order(*a, **k):
                raise RuntimeError("Order filled / -2011")

        with patch("binance_client.binance_client", _BC):
            out = PositionSupervisorBinance._mutex_on_tp3_filled(s, source="unit")
        self.assertTrue(out.get("race"))
        self.assertTrue(s.trading_paused)
        self.assertIn("tp3_radar_race", s.trading_pause_reason)
        self.assertGreaterEqual(calls["recon"], 1)


class TestRestThrottle(unittest.TestCase):
    def test_rest_min_interval_constant(self):
        from binance_client import REST_MIN_INTERVAL_SEC
        self.assertGreaterEqual(float(REST_MIN_INTERVAL_SEC), 0.1)

    def test_tiers_json_loaded(self):
        from reentry_profiles import ACTIVATION_FRACS, ETH_TIERS, REENTRY_TIERS_JSON
        import os
        self.assertTrue(os.path.isfile(REENTRY_TIERS_JSON))
        # 规格：模式标记 0=首次中点 / 1=重入TP2
        self.assertEqual(list(ACTIVATION_FRACS), [0.0, 1.0])
        self.assertEqual(len(ETH_TIERS), 3)


if __name__ == "__main__":
    unittest.main()
