#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
2026-09-04新增：webhook_log.py的TV信号"结果"回填finalizer两轮观察窗口
纯逻辑测试。背景——宝贝在"熊猫量化"TV信号面板发现XMRUSDT明明实盘早已
成交，面板"结果"列却一直卡在"处理中(限价待成交)"：第一轮
_FINALIZE_TIMEOUT_SEC(90秒)观察窗口太短，撞上"先平后开"+IP冷却重试
(单次_close_all~145秒预算)或网络抽风，真实成交经常要更久才确认，之前
第一轮超时后直接放弃记"timeout"，从此再也没有机制回头核实真结果。

修复：第一轮超时后追加更长更稀疏的第二轮(_FINALIZE_EXTENDED_TIMEOUT_SEC/
_POLL_SEC)，追到真终态就用finalize_signal(按id的UPDATE)回头更正记录。

不碰真实数据库/真实supervisor：用最小fake对象 + monkeypatch超时常量为
极短值，让测试在毫秒级跑完，同时验证两轮窗口机制本身的正确性。
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# webhook_log 顶层 import 时会建 DB 文件；重定向到临时目录，不碰真实
# data/tv_signals.db
_TMP_DB_DIR = tempfile.mkdtemp(prefix="wh_log_test_")
os.environ.setdefault("XDG_RUNTIME_DIR", _TMP_DB_DIR)

# patch("position_supervisor_binance.get_supervisor", ...) 需要真的 import
# 这个模块来挂属性——跟test_patience_mode.py同款纪律：绝不真连VPS/交易所，
# 靠BINANCE_SKIP_BOOTSTRAP=1 + mock掉binance_client/dingtalk让它安全导入。
os.environ["BINANCE_SKIP_BOOTSTRAP"] = "1"
from unittest.mock import MagicMock
_fake_bc = MagicMock()
sys.modules.setdefault("binance_client", _fake_bc)
_fake_bc.binance_client = MagicMock()
_fake_bc.is_position_query_failed = lambda x: False
_fake_bc.is_orders_query_failed = lambda x: False
sys.modules.setdefault("dingtalk", MagicMock())
import position_supervisor_binance  # noqa: E402  (先导入，供下面patch用)

import webhook_log as WL  # noqa: E402

WL.DB_PATH = WL.Path(_TMP_DB_DIR) / "test_tv_signals.db"
WL._init_db()


class FakeSup:
    """极简假supervisor：phase/phase_ts 可以在测试运行中途手动改，
    模拟"过一会儿才等到真终态"这个真实场景。"""

    def __init__(self):
        self.phase = "OPEN_PENDING"
        self.phase_ts = 0.0
        self.current_side = "SHORT"
        self.watched_qty = 0.0
        self.monitoring = False
        self.trading_paused = False
        self.trading_pause_reason = ""
        self.radar_activated = False
        self.current_sl = 0.0
        self.frozen_hard_sl_px = 0.0
        self.tp_levels_consumed = []

    def _pipeline_state_blob(self):
        return {"phase": self.phase, "phase_ts": self.phase_ts}


class TestFinalizerTwoPassWindow(unittest.TestCase):
    def setUp(self):
        # 超时常量压到毫秒级，测试不真的等90秒/600秒
        self._orig = (
            WL._FINALIZE_TIMEOUT_SEC, WL._FINALIZE_POLL_SEC,
            WL._FINALIZE_EXTENDED_TIMEOUT_SEC, WL._FINALIZE_EXTENDED_POLL_SEC,
        )
        WL._FINALIZE_TIMEOUT_SEC = 0.06
        WL._FINALIZE_POLL_SEC = 0.01
        WL._FINALIZE_EXTENDED_TIMEOUT_SEC = 0.15
        WL._FINALIZE_EXTENDED_POLL_SEC = 0.02

    def tearDown(self):
        (WL._FINALIZE_TIMEOUT_SEC, WL._FINALIZE_POLL_SEC,
         WL._FINALIZE_EXTENDED_TIMEOUT_SEC, WL._FINALIZE_EXTENDED_POLL_SEC) = self._orig

    def _insert_signal(self):
        sid = WL.record_signal({"action": "SHORT", "symbol": "XMRUSDT"}, source="test")
        self.assertIsNotNone(sid)
        return sid

    def test_fast_terminal_within_first_pass_no_extended_pass_needed(self):
        """常规情况：第一轮观察窗口内就等到真终态，不需要进第二轮。"""
        sid = self._insert_signal()
        sup = FakeSup()

        def flip_soon():
            time.sleep(0.02)
            sup.phase = "MONITORING"
            sup.phase_ts = time.time()

        import threading
        threading.Thread(target=flip_soon, daemon=True).start()

        with patch("position_supervisor_binance.get_supervisor", return_value=sup, create=True):
            WL._finalizer_loop(sid, "XMRUSDT")

        row = WL.get_signal(sid)
        self.assertEqual(row["final_phase"], "MONITORING")
        print("[PASS] 第一轮窗口内等到真终态，直接记为MONITORING")

    def test_slow_terminal_after_first_timeout_gets_corrected_by_extended_pass(self):
        """2026-09-03深夜XMRUSDT那种场景：第一轮超时先记'timeout'，
        真终态在第二轮窗口内才出现——记录必须被回头更正，不能永远卡在
        'timeout'。"""
        sid = self._insert_signal()
        sup = FakeSup()

        def flip_late():
            # 比第一轮窗口(0.06s)晚，但在第二轮窗口(0.06+0.15s)内
            time.sleep(0.10)
            sup.phase = "MONITORING"
            sup.phase_ts = time.time()

        import threading
        threading.Thread(target=flip_late, daemon=True).start()

        with patch("position_supervisor_binance.get_supervisor", return_value=sup, create=True):
            WL._finalizer_loop(sid, "XMRUSDT")

        row = WL.get_signal(sid)
        self.assertEqual(
            row["final_phase"], "MONITORING",
            "第一轮超时记的'timeout'必须被第二轮追到的真终态回头更正",
        )
        print("[PASS] 第一轮超时后，第二轮追到真终态并回头更正了记录(不再永远卡在timeout)")

    def test_never_terminal_gives_up_after_both_passes(self):
        """两轮都等不到终态(真正长期卡死/异常)：保留'timeout'，不假报成功，
        交给人工核对——不能编造一个假的终态。"""
        sid = self._insert_signal()
        sup = FakeSup()  # phase 永远停在 OPEN_PENDING，不是终态

        with patch("position_supervisor_binance.get_supervisor", return_value=sup, create=True):
            WL._finalizer_loop(sid, "XMRUSDT")

        row = WL.get_signal(sid)
        self.assertEqual(row["final_phase"], "timeout")
        print("[PASS] 两轮都等不到终态时保留'timeout'，不假报，交给人工核对")

    def test_old_signal_stale_phase_not_mistaken_for_new_result(self):
        """2026-08-18那条既有修复的回归防护：phase_ts早于本信号start_ts的
        旧终态，不能被误当成本信号自己的结果——本次改动不能破坏这条。"""
        sid = self._insert_signal()
        sup = FakeSup()
        sup.phase = "FAILED"
        sup.phase_ts = time.time() - 999  # 明显是更早那笔信号遗留的旧终态

        with patch("position_supervisor_binance.get_supervisor", return_value=sup, create=True):
            WL._finalizer_loop(sid, "XMRUSDT")

        row = WL.get_signal(sid)
        self.assertEqual(
            row["final_phase"], "timeout",
            "旧终态(phase_ts早于本信号开始观察的时间)不该被误采信为本信号的结果",
        )
        print("[PASS] 旧终态没有被误当成本信号自己的结果，2026-08-18修复的时间戳校验仍然生效")


if __name__ == "__main__":
    unittest.main(verbosity=2)
