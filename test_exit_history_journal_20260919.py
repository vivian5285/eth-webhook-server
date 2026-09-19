#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
2026-09-19新增：平仓原因(exit_source)落journal + 控制面板聚合API的
回归测试(本周问题总结item6"控制面板显示每笔平仓原因")。

背景：exit_source/exit_source_label此前只进了钉钉通知文案和
journalctl的TIER_LOG行(纯日志，不可结构化查询)，宝贝反馈想在控制
面板里看到每笔平仓的原因——新增_journal_close()把每次平仓落一条
结构化journal记录(复用既有的_append_journal/_journal_path TV/open/
exchange journal同一套机制)，console_api.py新增_recent_exit_history()
跨全部品种supervisor聚合读出来，接进/api/console/overview的
recent_exits字段。

不碰任何真实账户/持仓，binance_client/dingtalk全部mock，_append_journal
本身mock掉(不写真实文件)。
"""
from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

os.environ["BINANCE_SKIP_BOOTSTRAP"] = "1"
_fake_bc = sys.modules.setdefault("binance_client", MagicMock())
_fake_bc.binance_client = MagicMock()
_fake_bc.is_position_query_failed = lambda x: False
_fake_bc.is_orders_query_failed = lambda x: False
sys.modules.setdefault("dingtalk", MagicMock())

import position_supervisor_binance as psb  # noqa: E402


def _mk_supervisor(symbol="OPENAIUSDT"):
    with patch.object(psb.PositionSupervisorBinance, "__init__", lambda self, *a, **k: None):
        s = psb.PositionSupervisorBinance()
    s.symbol = symbol
    s.tv_open_tier = 1
    return s


class TestJournalClose(unittest.TestCase):
    def test_writes_expected_record_to_close_journal_path(self):
        s = _mk_supervisor("OPENAIUSDT")
        meta = {
            "side": "LONG", "entry_px": 1491.75, "live_exit_px": 1475.75,
            "closed_qty": 0.06, "pnl_pct": -1.07,
            "exit_source": "vps_hard_sl", "exit_source_label": "VPS综合硬止损",
        }
        with patch.object(s, "_append_journal") as mock_append, \
             patch.object(s, "_journal_path", return_value="logs/binance_close_journal_OPENAIUSDT.jsonl") as mock_path:
            s._journal_close(meta)
        mock_path.assert_called_once_with("close")
        mock_append.assert_called_once()
        args, kwargs = mock_append.call_args
        path, record = args
        self.assertEqual(path, "logs/binance_close_journal_OPENAIUSDT.jsonl")
        self.assertEqual(record["side"], "LONG")
        self.assertAlmostEqual(record["entry_px"], 1491.75, places=2)
        self.assertAlmostEqual(record["exit_px"], 1475.75, places=2)
        self.assertAlmostEqual(record["qty"], 0.06, places=4)
        self.assertEqual(record["pnl_pct"], -1.07)
        self.assertEqual(record["exit_source"], "vps_hard_sl")
        self.assertEqual(record["exit_source_label"], "VPS综合硬止损")
        self.assertEqual(record["tier"], 1)

    def test_missing_exit_source_writes_empty_string_not_crash(self):
        s = _mk_supervisor()
        with patch.object(s, "_append_journal") as mock_append:
            s._journal_close({"side": "SHORT"})
        args, kwargs = mock_append.call_args
        _, record = args
        self.assertEqual(record["exit_source"], "")
        self.assertEqual(record["exit_source_label"], "")

    def test_append_journal_exception_is_safe_noop(self):
        """journal写失败(磁盘满/权限问题等) 不该让平仓流程本身崩溃——
        跟_log_tier_close_stats同款try/except纪律。"""
        s = _mk_supervisor()
        with patch.object(s, "_append_journal", side_effect=RuntimeError("disk full")):
            s._journal_close({"side": "LONG"})  # 不该抛异常

    def test_report_flat_close_calls_journal_close(self):
        """核心集成点：_report_flat_close必须真正调用_journal_close，
        不是只加了个孤立方法没接线。"""
        s = _mk_supervisor()
        s._enrich_close_meta_live = MagicMock(return_value={"side": "LONG", "exit_source": "tv_close"})
        s._log_tier_close_stats = MagicMock()
        s._journal_close = MagicMock()
        s._wait_verify = MagicMock(return_value=True)
        s._verify_flat = MagicMock(return_value=True)
        s._dingtalk = MagicMock()
        s.symbol = "OPENAIUSDT"
        try:
            s._report_flat_close("测试", curr_px=100.0)
        except Exception:
            pass  # 这个方法后半段还有很多依赖，只关心_journal_close是否被调用
        s._journal_close.assert_called_once()


class TestRecentExitHistoryAggregation(unittest.TestCase):
    def test_aggregates_across_symbols_sorted_desc(self):
        import console_api

        sup_a = MagicMock()
        sup_a._iter_journal_entries = MagicMock(return_value=[
            {"ts": "2026-09-19 10:00:00", "side": "LONG", "exit_source": "tv_close",
             "entry_px": 100.0, "exit_px": 105.0, "qty": 1.0, "pnl_pct": 5.0, "tier": 1},
        ])
        sup_b = MagicMock()
        sup_b._iter_journal_entries = MagicMock(return_value=[
            {"ts": "2026-09-19 12:00:00", "side": "SHORT", "exit_source": "vps_hard_sl",
             "entry_px": 200.0, "exit_px": 210.0, "qty": 0.5, "pnl_pct": -5.0, "tier": 2},
        ])
        fake_supervisors = {"AUSDT": sup_a, "BUSDT": sup_b}
        with patch.object(psb, "SUPERVISORS", fake_supervisors):
            out = console_api._recent_exit_history(limit=40)
        self.assertEqual(len(out), 2)
        self.assertEqual(out[0]["symbol"], "BUSDT")  # 12:00 比 10:00 新，排前面
        self.assertEqual(out[0]["exit_source"], "vps_hard_sl")
        self.assertEqual(out[1]["symbol"], "AUSDT")

    def test_one_symbol_journal_read_failure_does_not_break_others(self):
        import console_api

        sup_good = MagicMock()
        sup_good._iter_journal_entries = MagicMock(return_value=[
            {"ts": "2026-09-19 09:00:00", "side": "LONG", "exit_source": "tv_close"},
        ])
        sup_bad = MagicMock()
        sup_bad._iter_journal_entries = MagicMock(side_effect=RuntimeError("boom"))
        fake_supervisors = {"GOODUSDT": sup_good, "BADUSDT": sup_bad}
        with patch.object(psb, "SUPERVISORS", fake_supervisors):
            out = console_api._recent_exit_history(limit=40)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["symbol"], "GOODUSDT")

    def test_limit_respected(self):
        import console_api

        sup = MagicMock()
        sup._iter_journal_entries = MagicMock(return_value=[
            {"ts": f"2026-09-19 10:00:{i:02d}", "side": "LONG"} for i in range(10)
        ])
        with patch.object(psb, "SUPERVISORS", {"XUSDT": sup}):
            out = console_api._recent_exit_history(limit=3)
        self.assertEqual(len(out), 3)


if __name__ == "__main__":
    unittest.main(verbosity=2)
