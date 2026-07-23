#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""v15.7.4：挂单 fail-closed + 同价去重；查单失败禁止挂单（防 50×叠单击穿）。"""
import os
import sys
import threading
import time
import unittest
from unittest.mock import MagicMock, patch

os.environ["BINANCE_SKIP_BOOTSTRAP"] = "1"

sys.modules.setdefault("binance", MagicMock())
sys.modules.setdefault("binance.client", MagicMock())

from binance_client import (  # noqa: E402
    BinanceClient,
    ORDERS_QUERY_FAILED,
    is_orders_query_failed,
)


def _bare_client():
    c = BinanceClient.__new__(BinanceClient)
    c.format_quantity = lambda q, symbol="ETHUSDT": float(q)
    c.format_price = lambda p, symbol="ETHUSDT": f"{float(p):.2f}"
    c.client = MagicMock()
    c._recent_limit_place = {}
    c._recent_stop_place = {}
    c._place_dedupe_lock = threading.Lock()
    c.get_open_orders = MagicMock(return_value=[])
    return c


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

    def test_place_limit_fail_closed_when_query_failed(self):
        """查单失败 → 禁止挂限价（废除「允许首挂」）。"""
        c = _bare_client()
        with patch.object(
            c, "_existing_same_limit", return_value=ORDERS_QUERY_FAILED,
        ):
            first = BinanceClient.place_limit_order(
                c, "SELL", 0.01, 1895.42, symbol="ETHUSDT",
            )
            second = BinanceClient.place_limit_order(
                c, "SELL", 0.01, 1895.42, symbol="ETHUSDT",
            )
        self.assertIsNone(first)
        self.assertIsNone(second)
        self.assertEqual(c.client.futures_create_order.call_count, 0)

    def test_place_limit_reuses_local_cache_when_query_failed(self):
        c = _bare_client()
        cached = {"orderId": 7, "price": "1895.42"}
        c._recent_limit_place[("ETHUSDT", "SELL", 1895.42)] = (time.time(), cached)
        with patch.object(
            c, "_existing_same_limit", return_value=ORDERS_QUERY_FAILED,
        ):
            out = BinanceClient.place_limit_order(
                c, "SELL", 0.01, 1895.42, symbol="ETHUSDT",
            )
        self.assertEqual(out, cached)
        self.assertEqual(c.client.futures_create_order.call_count, 0)

    def test_place_stop_fail_closed_when_query_failed(self):
        c = _bare_client()
        with patch.object(
            c, "_existing_same_stop", return_value=ORDERS_QUERY_FAILED,
        ):
            first = BinanceClient.place_stop_market_order(
                c, "SELL", 1890.0, symbol="ETHUSDT", quantity=0.01,
            )
            second = BinanceClient.place_stop_market_order(
                c, "SELL", 1890.0, symbol="ETHUSDT", quantity=0.01,
            )
        self.assertIsNone(first)
        self.assertIsNone(second)
        self.assertEqual(c.client.futures_create_order.call_count, 0)

    def test_place_limit_skips_duplicate_price(self):
        c = _bare_client()
        exist = {"orderId": 99, "price": "1895.42", "type": "LIMIT", "side": "SELL"}
        with patch.object(c, "_existing_same_limit", return_value=exist):
            out = BinanceClient.place_limit_order(
                c, "SELL", 0.01, 1895.42, symbol="ETHUSDT",
            )
        self.assertEqual(out, exist)
        c.client.futures_create_order.assert_not_called()

    def test_place_limit_ok_when_no_duplicate(self):
        """None=无同价单，应正常下单（不可把 None 当查单失败）。"""
        c = _bare_client()
        placed = {"orderId": 11, "price": "1920.00"}
        c.client.futures_create_order.return_value = placed
        with patch.object(c, "_existing_same_limit", return_value=None):
            out = BinanceClient.place_limit_order(
                c, "SELL", 0.01, 1920.0, symbol="ETHUSDT",
            )
        self.assertEqual(out, placed)
        self.assertEqual(c.client.futures_create_order.call_count, 1)

    def test_place_limit_fuse_when_too_many_limits(self):
        c = _bare_client()
        fake_book = [
            {"type": "LIMIT", "orderId": i, "price": str(1900 + i)}
            for i in range(6)
        ]
        c.get_open_orders = MagicMock(return_value=fake_book)
        with patch.object(c, "_existing_same_limit", return_value=None):
            out = BinanceClient.place_limit_order(
                c, "SELL", 0.01, 1910.0, symbol="ETHUSDT",
            )
        self.assertIsNone(out)
        self.assertEqual(c.client.futures_create_order.call_count, 0)

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

    def test_stop_near_false_when_unreadable_no_cache(self):
        """挂单不可读且无本地缓存 → 不得谎称已有硬止损。"""
        from position_supervisor_binance import PositionSupervisorBinance
        import binance_client as bc

        s = PositionSupervisorBinance.__new__(PositionSupervisorBinance)
        s.symbol = "XAUUSDT"
        s.current_side = "SHORT"
        bc.binance_client.get_open_orders = MagicMock(
            return_value=ORDERS_QUERY_FAILED,
        )
        bc.binance_client._recent_stop_place = {}
        self.assertFalse(s._has_stop_sl_near(4080.0))

    def test_rebuild_aborts_when_book_unreadable(self):
        from position_supervisor_binance import PositionSupervisorBinance

        s = PositionSupervisorBinance.__new__(PositionSupervisorBinance)
        s.symbol = "ETHUSDT"
        s.current_side = "LONG"
        s._resolve_live_qty = MagicMock(return_value=0.4)
        s._orders_book_readable = MagicMock(return_value=False)
        s._cancel_all_tp_limit_orders = MagicMock()
        self.assertEqual(s._rebuild_defenses(0.4, 1900.0), 0)
        s._cancel_all_tp_limit_orders.assert_not_called()

    def test_cancel_tp_aborts_without_cancel_all_on_unreadable(self):
        from position_supervisor_binance import PositionSupervisorBinance
        import binance_client as bc

        s = PositionSupervisorBinance.__new__(PositionSupervisorBinance)
        s.symbol = "ETHUSDT"
        s.current_side = "LONG"
        bc.binance_client.get_open_orders = MagicMock(
            return_value=ORDERS_QUERY_FAILED,
        )
        bc.binance_client.cancel_all_open_orders = MagicMock()
        total = s._cancel_all_tp_limit_orders(max_rounds=2)
        self.assertEqual(total, 0)
        bc.binance_client.cancel_all_open_orders.assert_not_called()

    def test_prune_keeps_one_per_price(self):
        from position_supervisor_binance import PositionSupervisorBinance
        import binance_client as bc

        s = PositionSupervisorBinance.__new__(PositionSupervisorBinance)
        s.symbol = "ETHUSDT"
        s.current_side = "LONG"
        s._collect_tp_limit_orders = MagicMock(
            return_value=[
                {"orderId": 1, "price": 1950.0, "qty": 0.1},
                {"orderId": 2, "price": 1950.0, "qty": 0.1},
                {"orderId": 3, "price": 2000.0, "qty": 0.1},
            ]
        )
        bc.binance_client.cancel_order = MagicMock(return_value=True)
        n = s._prune_duplicate_tp_limits()
        self.assertEqual(n, 1)
        bc.binance_client.cancel_order.assert_called_once_with("ETHUSDT", 2)


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
        s._verify_flat = lambda: True
        s._count_open_limits_and_stops = lambda: None
        self.assertFalse(s._verify_sterile_flat())

    def test_verify_sterile_rejects_ghost_limit(self):
        s = self._make_sup()
        s._verify_flat = lambda: True
        ghost = [{"type": "LIMIT", "price": "4162", "orderId": 1, "side": "SELL"}]
        s._count_open_limits_and_stops = lambda: (1, 0, ghost)
        self.assertFalse(s._verify_sterile_flat())

    def test_verify_sterile_ok_when_empty(self):
        s = self._make_sup()
        s._verify_flat = lambda: True
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
