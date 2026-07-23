#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""持仓查询失败不得当空仓：marker + supervisor 关键路径。"""
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
_fake_bc.POSITION_QUERY_FAILED = {
    "_query_failed": True,
    "positionAmt": None,
    "entryPrice": None,
}
_fake_bc.ORDERS_QUERY_FAILED = []


def _is_failed(pos):
    return isinstance(pos, dict) and pos.get("_query_failed") is True


_fake_bc.is_position_query_failed = _is_failed
_fake_bc.is_orders_query_failed = lambda x: False

import position_supervisor_binance as psb  # noqa: E402


class TestPositionQueryFailSafe(unittest.TestCase):
    def test_marker(self):
        self.assertTrue(_is_failed(dict(_fake_bc.POSITION_QUERY_FAILED)))
        self.assertFalse(_is_failed(None))
        self.assertFalse(_is_failed({"positionAmt": "0"}))

    def test_supervisor_not_flat_on_query_failed(self):
        _fake_bc.binance_client.get_position.return_value = dict(
            _fake_bc.POSITION_QUERY_FAILED
        )
        with patch.object(
            psb.PositionSupervisorBinance, "__init__", lambda self, *a, **k: None
        ):
            s = psb.PositionSupervisorBinance()
        s.symbol = "ETHUSDT"
        s._pos_query_fail_alert_ts = 0.0
        s._call_dingtalk = lambda *a, **k: None

        self.assertEqual(s._get_active_position(), "QUERY_FAILED")
        self.assertIsNone(s._live_position_qty())
        self.assertFalse(s._confirm_position_flat(retries=1, delay=0))
        self.assertFalse(s._verify_flat())

    def test_close_all_fail_closed_on_query_failed(self):
        """QUERY_FAILED 不得 float(None) 崩溃；必须 fail-closed 返回 False。"""
        with patch.object(
            psb.PositionSupervisorBinance, "__init__", lambda self, *a, **k: None
        ):
            s = psb.PositionSupervisorBinance()
        s.symbol = "XAUUSDT"
        s.current_side = None
        s.watched_qty = 0.0
        s._purge_all_defense_orders_on_flat = MagicMock(return_value={"ok": True})
        s._save_state = MagicMock()
        s._get_active_position = MagicMock(return_value="QUERY_FAILED")

        with patch.object(
            psb.position_manager,
            "get_position",
            return_value=dict(_fake_bc.POSITION_QUERY_FAILED),
        ):
            ok = s._close_all("单测·QUERY_FAILED", reset_state=True)
        self.assertFalse(ok)
        self.assertIn("QUERY_FAILED", getattr(s, "_last_sterile_flat_fail_detail", ""))

    def test_sterile_gate_refuses_open_on_query_failed(self):
        """开仓前查仓失败：直接拒开，禁止误判残留去强平。"""
        with patch.object(
            psb.PositionSupervisorBinance, "__init__", lambda self, *a, **k: None
        ):
            s = psb.PositionSupervisorBinance()
        s.symbol = "XAUUSDT"
        s.current_side = None
        s._purge_all_defense_orders_on_flat = MagicMock(
            return_value={"ok": True, "rounds": 0, "tp_cancelled": 0}
        )
        s._get_active_position = MagicMock(return_value="QUERY_FAILED")
        s._close_all = MagicMock(return_value=True)

        ok = s._sterile_flat_gate(reason_tag="单测", force_close=True, notify=False)
        self.assertFalse(ok)
        s._close_all.assert_not_called()
        self.assertIn("QUERY_FAILED", getattr(s, "_last_sterile_flat_fail_detail", ""))

    def test_idle_patrol_interval_raised(self):
        self.assertGreaterEqual(psb.IDLE_PATROL_INTERVAL_SEC, 45)
        self.assertGreaterEqual(psb.IDLE_PATROL_BACKOFF_SEC, 60)


if __name__ == "__main__":
    unittest.main(verbosity=2)
