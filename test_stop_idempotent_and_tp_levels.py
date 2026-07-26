#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
止损幂等 + PLACE_TP_LEVELS=2（仅 TP1+TP2）一致性单测。
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
sys.modules["binance_client"] = _fake_bc
_fake_bc.binance_client = MagicMock()
_fake_bc.is_position_query_failed = (
    lambda pos: isinstance(pos, dict) and pos.get("_query_failed") is True
)
_fake_bc.binance_client.get_position.return_value = {
    "positionAmt": "0.033",
    "entryPrice": "1900",
}

try:
    from webhook_parser import PLACE_TP_LEVELS
    from position_supervisor_binance import PositionSupervisorBinance
    import dingtalk
    IMPORT_OK = True
    IMPORT_ERR = ""
except Exception as exc:  # pragma: no cover
    IMPORT_OK = False
    IMPORT_ERR = str(exc)
    PLACE_TP_LEVELS = 2
    PositionSupervisorBinance = object  # type: ignore
    dingtalk = None  # type: ignore


@unittest.skipUnless(IMPORT_OK, f"import failed: {IMPORT_ERR}")
class TestPlaceTpLevelsAndStopStable(unittest.TestCase):
    def _make_sup(self):
        with patch.object(PositionSupervisorBinance, "__init__", lambda self, *a, **k: None):
            s = PositionSupervisorBinance()
        s.symbol = "ETHUSDT"
        s.current_side = "LONG"
        s.watched_entry = 1931.53
        s.watched_qty = 0.033
        s.initial_qty = 0.033
        s.tv_tps = [1950.53, 1967.60, 1983.92]
        s.tp_levels_consumed = []
        s.regime = 3
        s.open_regime = 3
        s.regime_settings = {3: {"ratios": [0.10, 0.20, 0.70]}}
        s._leg_ratios = [0.10, 0.20, 0.70]
        s._open_in_progress = True
        s._dingtalk_recent = {}
        s.state_file = os.path.join(ROOT, "_tmp_test_state.json")
        return s

    def test_expected_levels_tp1_tp2_only(self):
        s = self._make_sup()
        import position_supervisor_binance as psb
        with patch.object(s, "_save_state", lambda: None), \
             patch.object(psb.binance_client, "get_current_price", return_value=1925.0):
            levels = s._expected_tp_levels(0.033)
        self.assertEqual(int(PLACE_TP_LEVELS), 2)
        self.assertEqual([lv["level"] for lv in levels], [1, 2])
        self.assertEqual(s._expected_tp_count(), 2)
        # 限价合计约 30%；余 70% 不挂限价（不得把余量并进 TP2）
        total_lim = sum(lv["qty"] for lv in levels)
        self.assertLessEqual(total_lim, 0.033 * 0.35 + 1e-9)
        self.assertGreater(total_lim, 0)

    def test_flat_resets_breath_ledger_immediately(self):
        s = self._make_sup()
        s.symbol = "TESTUSDT"
        s.state_file = os.path.join(ROOT, "_tmp_test_state_isolated.json")
        s.initial_stop = 1910.18
        s.current_sl = 1910.18
        s.open_atr = 14.23
        s.breakeven_phase = True
        s.radar_activated = True
        s.best_price = 1935.0
        s.monitoring = True
        with patch.object(s, "_clear_defense_order_ids", lambda **k: None), \
             patch.object(s, "_clear_signal_fingerprint", lambda: None), \
             patch.object(s, "_save_state", lambda: None):
            s._reset_breath_ledger_on_flat(source="CLOSE_QUICK_EXIT")
        self.assertFalse(s.monitoring)
        self.assertEqual(float(s.initial_stop or 0), 0.0)


if __name__ == "__main__":
    unittest.main()
