#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
止损幂等 + PLACE_TP_LEVELS=2 一致性单测。
优先在 VPS 实盘代码树运行（真实依赖）；本地无密钥时可跳过导入失败。
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
    binance_client = _fake_bc.binance_client
except Exception as exc:  # pragma: no cover
    IMPORT_OK = False
    IMPORT_ERR = str(exc)
    PLACE_TP_LEVELS = 2
    PositionSupervisorBinance = object  # type: ignore
    binance_client = None  # type: ignore
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
        s.regime_settings = {3: {"ratios": [0.30, 0.30, 0.40]}}
        s._leg_ratios = [0.30, 0.30, 0.40]
        s._open_in_progress = False
        s._dingtalk_recent = {}
        s.state_file = os.path.join(ROOT, "_tmp_test_state.json")
        return s

    def test_expected_levels_exclude_tp3(self):
        s = self._make_sup()
        import position_supervisor_binance as psb
        with patch.object(s, "_save_state", lambda: None), \
             patch.object(psb.binance_client, "get_current_price", return_value=1925.0):
            levels = s._expected_tp_levels(0.033)
        self.assertEqual(int(PLACE_TP_LEVELS), 2)
        self.assertEqual([lv["level"] for lv in levels], [1, 2])
        self.assertEqual(s._expected_tp_count(), 2)
        self.assertAlmostEqual(levels[0]["qty"], 0.01, places=3)
        self.assertAlmostEqual(levels[1]["qty"], 0.01, places=3)

    def test_tp_audit_ok_with_tp1_tp2_only(self):
        s = self._make_sup()
        orders = [
            {"orderId": 1, "price": 1950.53, "qty": 0.01},
            {"orderId": 2, "price": 1967.60, "qty": 0.01},
        ]
        import position_supervisor_binance as psb
        with patch.object(s, "_collect_tp_limit_orders", return_value=orders), \
             patch.object(s, "_save_state", lambda: None), \
             patch.object(psb.binance_client, "get_current_price", return_value=1925.0):
            audit = s._audit_tp_levels(0.033)
        self.assertEqual(audit["expected"], 2)
        self.assertEqual(audit["matched_full"], 2)
        self.assertFalse(audit["orphans"])
        self.assertTrue(s._tp_audit_ok(audit))
        self.assertFalse(s._defense_anomaly_is_severe(audit))

    def test_tp3_is_orphan_not_expected(self):
        s = self._make_sup()
        orders = [
            {"orderId": 1, "price": 1950.53, "qty": 0.01},
            {"orderId": 2, "price": 1967.60, "qty": 0.01},
            {"orderId": 3, "price": 1983.92, "qty": 0.013},
        ]
        import position_supervisor_binance as psb
        with patch.object(s, "_collect_tp_limit_orders", return_value=orders), \
             patch.object(s, "_save_state", lambda: None), \
             patch.object(psb.binance_client, "get_current_price", return_value=1925.0):
            audit = s._audit_tp_levels(0.033)
        self.assertEqual(audit["expected"], 2)
        self.assertEqual(audit["matched_full"], 2)
        self.assertTrue(audit["orphans"])
        self.assertFalse(s._tp_audit_ok(audit))
        self.assertTrue(s._defense_anomaly_is_severe(audit))

    def test_sync_exchange_stop_idempotent_no_purge(self):
        """无新价格触发时，已挂同价止损 → 幂等跳过，不撤不挂。"""
        s = self._make_sup()
        s.current_sl = 1910.18
        s.initial_stop = 1910.18
        s.tv_sl = 1910.18
        s._last_applied_exchange_sl = 1910.18
        s._last_hard_sl_sync_ts = 0
        calls = {"place": 0, "purge": 0}

        with patch.object(s, "_resolve_live_qty", return_value=0.033), \
             patch.object(s, "_lock_open_regime_from_sources", lambda **k: None), \
             patch.object(s, "_sanitize_vps_hard_sl_ledger", return_value=False), \
             patch.object(s, "_effective_exchange_stop", return_value=1910.18), \
             patch.object(s, "_count_protective_stops", return_value=[1910.18]), \
             patch.object(
                 s, "_place_vps_hard_sl_order",
                 side_effect=lambda *a, **k: calls.__setitem__("place", calls["place"] + 1),
             ), \
             patch.object(
                 s, "_purge_all_protective_stops",
                 side_effect=lambda *a, **k: calls.__setitem__("purge", calls["purge"] + 1) or 0,
             ), \
             patch.object(s, "_save_state", lambda: None):
            r1 = s._sync_exchange_stop(0.033, reason="心跳确认", force=False)
            r2 = s._sync_exchange_stop(0.033, reason="心跳确认", force=False)
        self.assertTrue(r1.get("ok"))
        self.assertTrue(r1.get("skipped"))
        self.assertEqual(r1.get("reason"), "idempotent_unified")
        self.assertTrue(r2.get("skipped"))
        self.assertEqual(calls["place"], 0)
        self.assertEqual(calls["purge"], 0)

    def test_flat_resets_breath_ledger_immediately(self):
        s = self._make_sup()
        s.symbol = "TESTUSDT"  # 禁止污染实盘 ETH 账本/日志语义
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
        self.assertIsNone(s.current_side)
        self.assertEqual(s.watched_entry, 0.0)
        self.assertEqual(s.initial_stop, 0.0)
        self.assertEqual(s.current_sl, 0.0)
        self.assertEqual(s.open_atr, 0.0)
        self.assertFalse(s.breakeven_phase)
        self.assertFalse(s.radar_activated)
        self.assertEqual(s.best_price, 0.0)
        self.assertEqual(s.tp_levels_consumed, [])
        self.assertFalse(s.monitoring)

    def test_call_dingtalk_accepts_positional_title_detail(self):
        s = self._make_sup()
        seen = {}

        def fake_dingtalk(fn, **kwargs):
            seen["fn"] = fn
            seen["kwargs"] = kwargs
            return "ok"

        s._dingtalk = fake_dingtalk
        out = s._call_dingtalk(
            dingtalk.report_system_alert,
            "雷达守护：止盈仍未对齐",
            "LONG 0.033 ETH | demo",
        )
        self.assertEqual(out, "ok")
        self.assertEqual(seen["kwargs"]["title"], "雷达守护：止盈仍未对齐")
        self.assertEqual(seen["kwargs"]["detail"], "LONG 0.033 ETH | demo")

    def test_tp_slices_short_tv_tps_no_index_error(self):
        s = self._make_sup()
        s.tv_tps = [1950.53]
        slices = s._tp_slices_for_initial(0.033)
        self.assertEqual(len(slices), 1)
        self.assertEqual(slices[0]["level"], 1)


if __name__ == "__main__":
    if not IMPORT_OK:
        print(f"SKIP all: import failed: {IMPORT_ERR}")
        sys.exit(0)
    unittest.main(verbosity=2)
