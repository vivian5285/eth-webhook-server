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


if __name__ == "__main__":
    unittest.main()
