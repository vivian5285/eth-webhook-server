#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
2026-09-06新增：_normalize_tp_qty_map()"仓位过小放弃挂限价TP"分支新增的
5分钟日志限频回归测试。

背景——宝贝反馈面板刷屏第二例：GEV/SNDK/GS/SKHYNIX这类min_qty=0.01的
小仓位，只要仓位不变，"两档TP合计仍低于最小下单量，放弃挂限价TP"这
个结论每次调用都成立，是同一个legit结论的重复确认，不是新事件——跟
同批修的"硬帽压回死循环"不是一回事(那个原本就不该重复)，但一样吵：
实盘复现单个symbol两小时内被打了123~222次一模一样的日志。

修复：跟dingtalk.py既有的"title dedup(300s)"同一个思路，按supervisor
实例(=一个symbol一个账户)加5分钟冷却，只在冷却期外才真正落地这条
日志；冷却期内静默但清零动作(out[last]=0.0)照常执行，不影响任何
实际下单行为——两个测试都验证这一点，测的是"日志频率"，不是"业务
结果"。

不碰任何真实账户/持仓，纯粹验证这一个纯函数。
"""
from __future__ import annotations

import os
import sys
import time
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


def _mk_supervisor(symbol, min_qty):
    with patch.object(psb.PositionSupervisorBinance, "__init__", lambda self, *a, **k: None):
        s = psb.PositionSupervisorBinance()
    s.symbol = symbol
    s.min_qty = min_qty
    s._effective_place_tp_levels = lambda: 2
    s._tp_baseline_qty = lambda live_qty: live_qty
    return s


def _too_small_calls(mock_logger):
    return [
        c for c in mock_logger.warning.call_args_list
        if "仓位过小放弃挂限价TP" in str(c)
    ]


class TestTpTooSmallLogThrottle(unittest.TestCase):
    def test_gev_real_incident_throttles_repeated_identical_warning(self):
        """GEV实盘复现：qty=0.04，TP1+TP2合计低于min_qty——短时间内反复
        调用(模拟每个监控tick都会重新算一次)只应该打第一条，冷却期内
        后续调用静默；但清零动作(out[last]=0.0)每次都要照常执行。"""
        s = _mk_supervisor("GEVUSDT", min_qty=0.01)
        qty_map = {1: 0.01, 2: 0.008}  # 合计低于min_qty的档在TP2
        with patch.object(psb, "logger") as mock_logger:
            outs = [s._normalize_tp_qty_map(dict(qty_map), live_qty=0.04) for _ in range(5)]
        for out in outs:
            self.assertEqual(out[2], 0.0, "无论是否打日志，清零动作都必须照常执行")
        self.assertEqual(
            len(_too_small_calls(mock_logger)), 1,
            "5分钟冷却期内连续5次调用只应该打印一次'仓位过小'告警",
        )

    def test_warning_resumes_after_cooldown_expires(self):
        """冷却期过后(≥300s)应该恢复打印，确认这不是被永久静默、只是
        限频——万一状态真的变化了，运维依然能看到最新一条。"""
        s = _mk_supervisor("SNDKUSDT", min_qty=0.01)
        qty_map = {1: 0.01, 2: 0.005}
        with patch.object(psb, "logger") as mock_logger:
            s._normalize_tp_qty_map(dict(qty_map), live_qty=0.04)
            # 手动把上次打印时间拨回300秒以前，模拟冷却期已过
            s._tp_too_small_log_ts = time.time() - 301.0
            s._normalize_tp_qty_map(dict(qty_map), live_qty=0.04)
        self.assertEqual(
            len(_too_small_calls(mock_logger)), 2,
            "冷却期过后再次调用应该恢复打印，不是永久只打一次",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
