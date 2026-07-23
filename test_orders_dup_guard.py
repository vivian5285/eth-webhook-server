#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""v15.5.28：挂单查询失败 fail-closed + 同价去重 + 审计不可读不补挂。"""
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

os.environ["BINANCE_SKIP_BOOTSTRAP"] = "1"

# 避免真实 Binance Client 初始化卡住
sys.modules.setdefault("binance", MagicMock())
sys.modules.setdefault("binance.client", MagicMock())

from binance_client import (  # noqa: E402
    BinanceClient,
    ORDERS_QUERY_FAILED,
    is_orders_query_failed,
)


class TestOrdersDupGuard(unittest.TestCase):
    def test_orders_query_failed_sentinel(self):
        self.assertTrue(is_orders_query_failed(ORDERS_QUERY_FAILED))
        self.assertTrue(is_orders_query_failed(None))
        self.assertFalse(is_orders_query_failed([]))
        self.assertFalse(is_orders_query_failed([{"orderId": 1}]))
        self.assertEqual(list(ORDERS_QUERY_FAILED), [])

    def test_get_open_orders_fail_closed(self):
        c = BinanceClient.__new__(BinanceClient)
        c.client = MagicMock()
        c.client.futures_get_open_orders.side_effect = RuntimeError("ban -1003")
        out = BinanceClient.get_open_orders(c, "ETHUSDT", include_algo=False)
        self.assertTrue(is_orders_query_failed(out))

    def test_place_limit_refuses_when_query_failed(self):
        c = BinanceClient.__new__(BinanceClient)
        c.format_quantity = lambda q, symbol="ETHUSDT": float(q)
        c.format_price = lambda p, symbol="ETHUSDT": f"{float(p):.2f}"
        c.client = MagicMock()
        with patch.object(
            c, "_existing_same_limit", return_value=ORDERS_QUERY_FAILED,
        ):
            self.assertIsNone(
                BinanceClient.place_limit_order(
                    c, "SELL", 0.01, 1895.42, symbol="ETHUSDT",
                )
            )
        c.client.futures_create_order.assert_not_called()

    def test_place_limit_skips_duplicate_price(self):
        c = BinanceClient.__new__(BinanceClient)
        c.format_quantity = lambda q, symbol="ETHUSDT": float(q)
        c.format_price = lambda p, symbol="ETHUSDT": f"{float(p):.2f}"
        c.client = MagicMock()
        exist = {"orderId": 99, "price": "1895.42", "type": "LIMIT", "side": "SELL"}
        with patch.object(c, "_existing_same_limit", return_value=exist):
            out = BinanceClient.place_limit_order(
                c, "SELL", 0.01, 1895.42, symbol="ETHUSDT",
            )
        self.assertEqual(out, exist)
        c.client.futures_create_order.assert_not_called()

    def test_find_protective_returns_none_on_fail(self):
        c = BinanceClient.__new__(BinanceClient)
        with patch.object(c, "get_open_orders", return_value=ORDERS_QUERY_FAILED):
            self.assertIsNone(
                BinanceClient.find_protective_stop_prices(c, "ETHUSDT")
            )


class TestAuditUnreadableNoRepair(unittest.TestCase):
    def test_tp_audit_ok_when_unreadable(self):
        from position_supervisor_binance import PositionSupervisorBinance

        s = PositionSupervisorBinance.__new__(PositionSupervisorBinance)
        s.symbol = "ETHUSDT"
        s.current_side = "LONG"
        s.watched_entry = 1900.0
        s.tv_tps = [1950.0, 2000.0, 0.0]
        s.tp_levels_consumed = []
        audit = {
            "matched_full": 0,
            "expected": 2,
            "levels": [],
            "issues": ["orders_unreadable"],
            "orphans": [],
            "orders_unreadable": True,
        }
        self.assertTrue(s._tp_audit_ok(audit))
        self.assertFalse(s._defense_needs_immediate_fix(audit))

    def test_breath_resize_idempotent_skip(self):
        from position_supervisor_binance import PositionSupervisorBinance

        s = PositionSupervisorBinance.__new__(PositionSupervisorBinance)
        s.symbol = "ETHUSDT"
        s.current_side = "LONG"
        s.current_sl = 1880.0
        s.initial_stop = 1880.0
        s.initial_qty = 0.1
        s.shield_sized_qty = 0.05
        s._last_applied_exchange_sl = 1879.7
        s.breath_profile = None
        s.tp_levels_consumed = [1]
        s._breath_tick_paused = False
        s._resolve_live_qty = lambda q: float(q)
        s._stop_buffer_usd = lambda: 0.3
        s._count_protective_stops = lambda: [1879.7]
        s._purge_all_protective_stops = MagicMock()
        s._place_vps_hard_sl_order = MagicMock(return_value={"orderId": 1})
        s._set_defense_order_id = MagicMock()
        s._save_state = MagicMock()
        s._tag = lambda: "ETHUSDT"
        ok = s._breath_resize_stop_on_tp(0.05, reason="unit")
        self.assertTrue(ok)
        s._purge_all_protective_stops.assert_not_called()
        s._place_vps_hard_sl_order.assert_not_called()


class TestSterileFlatFailClosed(unittest.TestCase):
    def _make_sup(self):
        from position_supervisor_binance import PositionSupervisorBinance
        s = PositionSupervisorBinance.__new__(PositionSupervisorBinance)
        s.symbol = "ETHUSDT"
        s.current_side = None
        s._get_active_position = lambda: None
        return s

    def test_verify_sterile_rejects_unread_book(self):
        s = self._make_sup()
        s._count_open_limits_and_stops = lambda: None
        self.assertFalse(s._verify_sterile_flat())

    def test_verify_sterile_rejects_ghost_limit(self):
        s = self._make_sup()
        ghost = [{"type": "LIMIT", "price": "4162", "orderId": 1, "side": "SELL"}]
        s._count_open_limits_and_stops = lambda: (1, 0, ghost)
        self.assertFalse(s._verify_sterile_flat())

    def test_verify_sterile_ok_when_empty(self):
        s = self._make_sup()
        s._count_open_limits_and_stops = lambda: (0, 0, [])
        s._collect_tp_limit_orders = lambda: []
        self.assertTrue(s._verify_sterile_flat())

    def test_is_tp_limit_treats_any_limit_when_flat(self):
        s = self._make_sup()
        s.current_side = None
        self.assertTrue(
            s._is_tp_limit_order({"type": "LIMIT", "side": "SELL", "reduceOnly": False})
        )


if __name__ == "__main__":
    unittest.main()
