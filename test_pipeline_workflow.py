#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""v16.6.0 pipeline ledger / auditor / throttle unit checks."""
from __future__ import annotations

import os
import sys
import time
import unittest

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from pipeline_ledger import Phase, Role, PipelineLedger, soft_gates_enabled
from chief_auditor import audit_open_bundle, check_tp_slice_budget, should_hard_pause
from api_throttle import AccountThrottle, get_throttle


class TestPipelineLedger(unittest.TestCase):
    def test_happy_path_phases(self):
        pl = PipelineLedger("ETHUSDT", "binance")
        self.assertEqual(pl.phase, Phase.IDLE)
        ok, _ = pl.advance(Phase.SIGNAL_RECEIVED, Role.SIGNAL, side="LONG")
        self.assertTrue(ok)
        ok, _ = pl.advance(Phase.PENDING_CLEAR, Role.AUDITOR_POS)
        self.assertTrue(ok)
        ok, _ = pl.advance(Phase.CLEARED, Role.AUDITOR_POS)
        self.assertTrue(ok)
        ok, _ = pl.advance(Phase.ENTRY_SUBMITTED, Role.EXECUTION)
        self.assertTrue(ok)
        ok, _ = pl.advance(
            Phase.ENTRY_CONFIRMED, Role.EXECUTION, initial_qty=1.0, qty=1.0, entry=1900,
        )
        self.assertTrue(ok)
        self.assertEqual(float(pl.to_dict()["initial_qty"]), 1.0)
        # initial_qty 不可被执行官覆写
        pl.advance(Phase.ORDERS_PLACED, Role.EXECUTION, initial_qty=9.9)
        self.assertEqual(float(pl.to_dict()["initial_qty"]), 1.0)

    def test_soft_gate_illegal_skip(self):
        os.environ["PIPELINE_SOFT_GATES"] = "1"
        pl = PipelineLedger("XAUUSDT", "binance")
        pl.advance(Phase.SIGNAL_RECEIVED, Role.SIGNAL)
        ok, msg = pl.advance(Phase.ENTRY_SUBMITTED, Role.EXECUTION)
        self.assertFalse(ok)
        self.assertEqual(pl.phase, Phase.SIGNAL_RECEIVED)

    def test_stale_entry_submitted(self):
        pl = PipelineLedger("ETHUSDT", "binance")
        pl.advance(Phase.SIGNAL_RECEIVED, Role.SIGNAL)
        pl.advance(Phase.PENDING_CLEAR, Role.AUDITOR_POS)
        pl.advance(Phase.CLEARED, Role.AUDITOR_POS)
        pl.advance(Phase.ENTRY_SUBMITTED, Role.EXECUTION)
        pl.data["phase_ts"] = time.time() - 120
        self.assertIsNotNone(pl.stale())


class TestChiefAuditor(unittest.TestCase):
    def test_tp_slice_30pct(self):
        item = check_tp_slice_budget(1.0, 0.10, 0.20)
        self.assertTrue(item.ok)
        bad = check_tp_slice_budget(1.0, 0.50, 0.50)
        self.assertFalse(bad.ok)

    def test_audit_hard_fail_direction(self):
        r = audit_open_bundle({
            "symbol": "ETHUSDT",
            "ledger_symbol": "ETHUSDT",
            "signal_side": "LONG",
            "live_side": "SHORT",
            "initial_qty": 1.0,
            "tp1_qty": 0.10,
            "tp2_qty": 0.20,
            "hard_sl_px": 1800,
            "hard_sl_expected": 1800,
            "hard_sl_live": True,
            "leverage": 5,
            "leverage_cap": 5,
            "risk_pct": 0.2,
            "risk_pct_cfg": 0.2,
            "api_recent": 3,
            "api_budget": 48,
        })
        self.assertFalse(r.ok)
        self.assertTrue(any("direction" in x for x in r.hard_fails))
        os.environ["PIPELINE_AUDITOR_HARD_PAUSE"] = "1"
        self.assertTrue(should_hard_pause(r))

    def test_audit_pass_open_bundle(self):
        r = audit_open_bundle({
            "symbol": "ETHUSDT",
            "ledger_symbol": "ETHUSDT",
            "signal_side": "LONG",
            "live_side": "LONG",
            "initial_qty": 0.932,
            "tp1_qty": 0.093,
            "tp2_qty": 0.186,
            "hard_sl_px": 1850,
            "hard_sl_expected": 1850,
            "hard_sl_live": True,
            "leverage": 5,
            "leverage_cap": 5,
            "risk_pct": 0.2,
            "risk_pct_cfg": 0.2,
            "api_recent": 10,
            "api_budget": 48,
        })
        self.assertTrue(r.ok)


class TestThrottle(unittest.TestCase):
    def test_silence_blocks(self):
        th = AccountThrottle("test_acct")
        th.budget_per_min = 100
        th.enter_silence(30)
        ok, detail = th.acquire("rest")
        self.assertFalse(ok)
        self.assertIn("silence", detail)

    def test_budget_probe_soft(self):
        th = AccountThrottle("test_budget")
        th.budget_per_min = 5
        th.soft_ratio = 0.6
        th._silence_until = 0.0
        for _ in range(4):
            th.acquire("rest_trade", force=True)
        ok, detail = th.acquire("rest_probe")
        self.assertFalse(ok)


class TestVersion(unittest.TestCase):
    def test_version_tags_in_source(self):
        # 避免本机导入 binance_client 时走 SOCKS ping
        with open(os.path.join(ROOT, "binance_client.py"), "r", encoding="utf-8") as f:
            bc = f.read()
        with open(
            os.path.join(ROOT, "position_supervisor_binance.py"), "r", encoding="utf-8"
        ) as f:
            sup = f.read()
        self.assertIn('BINANCE_CLIENT_VERSION = "v16.6.0-pipeline"', bc)
        self.assertIn('BINANCE_VPS_VERSION = "v16.6.0-pipeline"', sup)
        self.assertTrue(soft_gates_enabled())


if __name__ == "__main__":
    unittest.main()
