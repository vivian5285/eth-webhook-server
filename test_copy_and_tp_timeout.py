#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""用户可见文案：禁止 R1-R4；平仓 reason 不含档位编号。"""
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
sys.modules["binance_client"] = _fake_bc
_fake_bc.binance_client = MagicMock()
_fake_bc.is_position_query_failed = lambda pos: False

import dingtalk as dt  # noqa: E402
import position_supervisor_binance as psb  # noqa: E402


class TestNoRegimeInUserCopy(unittest.TestCase):
    def test_get_regime_name_no_rn(self):
        for r in (0, 1, 2, 3, 4, None):
            name = dt.get_regime_name(r)
            self.assertNotRegex(str(name), r"R[1-4]")
            self.assertIn("RISK20", str(name))

    def test_format_close_extra_no_regime(self):
        with patch.object(psb.PositionSupervisorBinance, "__init__", lambda self, *a, **k: None):
            s = psb.PositionSupervisorBinance()
        extra = s._format_close_extra(
            "LONG", -0.12, 1928.01, regime=3, atr=14.0,
        )
        self.assertNotIn("R3", extra)
        self.assertNotIn("档位", extra)
        self.assertIn("1928.01", extra)
        self.assertIn("ATR", extra)

    def test_close_classify_protect_title_strips_clean_reason(self):
        theme = dt._classify_close(
            "🧹 反转保护：WEBHOOK_E2E_LIVE_TEST_CLOSE | TV档位 R3 | ATR 14.00 | TV价 1928.01",
            close_type="quick_exit",
            close_action="CLOSE_QUICK_EXIT",
            tv_reason="WEBHOOK_E2E_LIVE_TEST_CLOSE | TV档位 R3",
        )
        joined = str(theme)
        self.assertNotRegex(joined, r"R[1-4]")
        self.assertIn("反转保护", theme["title"])
        self.assertNotIn("TV档位", theme["title"])


class TestTpTimeoutGate(unittest.TestCase):
    def test_timeout_skips_when_price_not_reached(self):
        with patch.object(psb.PositionSupervisorBinance, "__init__", lambda self, *a, **k: None):
            s = psb.PositionSupervisorBinance()
        s.symbol = "ETHUSDT"
        s.tv_tps = [1953.73, 1969.83, 1983.82]
        s.current_side = "LONG"
        s.watched_entry = 1929.6
        s.tp_levels_consumed = []
        s.tp_levels_radar_handoff = []
        s._tp_order_placed_ts = {"1": 1.0, "2": 1.0}  # ancient → timed out
        s._save_state = lambda: None
        called = {"cancel": 0}

        def fake_cancel(level, handoff_radar=True):
            called["cancel"] += 1
            return True

        s._cancel_tp_level_if_still_open = fake_cancel
        s._price_reached_tp_zone = lambda level, curr_px=0.0, live_only=False, **k: False
        s._tp_level_consumed = lambda level: False
        s._check_tp_order_timeouts(curr_px=1929.0)
        self.assertEqual(called["cancel"], 0)

    def test_timeout_cancels_when_price_reached(self):
        with patch.object(psb.PositionSupervisorBinance, "__init__", lambda self, *a, **k: None):
            s = psb.PositionSupervisorBinance()
        s.symbol = "ETHUSDT"
        s.tv_tps = [1953.73, 1969.83, 1983.82]
        s.current_side = "LONG"
        s.tp_levels_consumed = []
        s.tp_levels_radar_handoff = []
        s._tp_order_placed_ts = {"1": 1.0}  # ancient but >0
        s._save_state = lambda: None
        called = {"cancel": 0}

        def fake_cancel(level, handoff_radar=True):
            called["cancel"] += 1
            return True

        s._cancel_tp_level_if_still_open = fake_cancel
        s._price_reached_tp_zone = lambda level, curr_px=0.0, live_only=False, **k: True
        s._tp_level_consumed = lambda level: False
        s._check_tp_order_timeouts(curr_px=1954.0)
        self.assertEqual(called["cancel"], 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
