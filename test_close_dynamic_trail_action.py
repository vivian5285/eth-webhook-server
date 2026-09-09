#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
2026-09-09新增：TV新增CLOSE_DYNAMIC_TRAIL(动态移动止盈)平仓action的
回归测试。

背景——宝贝反馈+实盘复现(SKHYNIXUSDT.P，ETH优化版VPS策略"动态移动止盈"
alert，B/C/E三账户)：TV这个新action不在webhook_parser.py的VALID_ACTIONS/
FLATTEN_ACTIONS白名单里，三账户当天15:09全部被app.py的webhook路由用
_parse_ok=False直接400拒绝、完全不下单——TV已经把position在自己脚本
里标记为"已平"，VPS却对这条平仓指令一无所知，实盘仓位一直挂到~15分钟后
才靠人工在交易所补平(成交1406.63，比TV想要的止盈价1413.83更差)。

覆盖两层：
1. webhook_parser.py：白名单本身 + normalize_tv_payload对真实payload的
   _parse_ok/_is_flatten判定。
2. position_supervisor_binance._process_signal：新action走到"主动全平"
   分支后，tag/exit_source不能被误判成CLOSE_RSI_EXIT那个写死的else分支
   （日志/钉钉展示错误来源，虽然平仓动作本身不受影响）。

不碰任何真实账户/持仓，webhook_parser部分是纯函数测试；supervisor部分
mock掉_get_active_position/_should_ignore_late_close/_release_tv_seq_
after_close/_record_tv_signal/_handle_manual_flat_detected等副作用点，
走"信号到达时盘口已空"这条最短路径验证tag映射，不需要真实下单。
"""
from __future__ import annotations

import os
import sys
import threading
import unittest
from queue import Queue
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

os.environ["BINANCE_SKIP_BOOTSTRAP"] = "1"
_fake_bc = sys.modules.setdefault("binance_client", MagicMock())
_fake_bc.binance_client = MagicMock()
_fake_bc.is_position_query_failed = lambda x: False
_fake_bc.is_orders_query_failed = lambda x: False
sys.modules.setdefault("dingtalk", MagicMock())

from webhook_parser import (  # noqa: E402
    VALID_ACTIONS,
    FLATTEN_ACTIONS,
    is_flatten_action,
    normalize_tv_payload,
)
import position_supervisor_binance as psb  # noqa: E402
from position_supervisor_binance import EXIT_SOURCE_RSI, EXIT_SOURCE_TV_CLOSE  # noqa: E402


class TestCloseDynamicTrailWhitelist(unittest.TestCase):
    def test_action_in_valid_and_flatten_whitelist(self):
        self.assertIn("CLOSE_DYNAMIC_TRAIL", VALID_ACTIONS)
        self.assertIn("CLOSE_DYNAMIC_TRAIL", FLATTEN_ACTIONS)
        self.assertTrue(is_flatten_action("CLOSE_DYNAMIC_TRAIL"))

    def test_real_skhynix_payload_parses_ok_and_flattens(self):
        """实盘复现的原始payload结构(截取关键字段)：TV发出时_parse_ok
        必须为True(否则app.py会400拒绝)，_is_flatten必须为True(否则不会
        走到平仓分支)。"""
        payload = {
            "secret": "528586",
            "action": "CLOSE_DYNAMIC_TRAIL",
            "symbol": "SKHYNIXUSDT.P",
            "side": "LONG",
            "price": 1413.83,
            "reason": "动态移动止盈",
            "bot_id": "Trillion_God_v6.5_Pro_Light",
        }
        out = normalize_tv_payload(payload)
        self.assertTrue(out["_parse_ok"], "CLOSE_DYNAMIC_TRAIL必须被识别为合法action")
        self.assertTrue(out["_is_flatten"])
        self.assertEqual(out["action"], "CLOSE_DYNAMIC_TRAIL")


def _mk_supervisor():
    with patch.object(psb.PositionSupervisorBinance, "__init__", lambda self, *a, **k: None):
        s = psb.PositionSupervisorBinance()
    s.symbol = "SKHYNIXUSDT"
    s._lock = threading.Lock()
    s._signal_queue = Queue()
    s.trading_paused = False
    s.api_monitor_only = False
    s.min_qty = 0.001
    s.last_tv_signal = {}
    s.last_tv_side = None
    s.tv_price = 0.0
    s.tv_tps = [0.0, 0.0, 0.0]
    s.regime = 3
    s.current_atr = 0.0
    s.current_side = "LONG"
    s.watched_entry = 1375.96
    s.watched_qty = 0.05
    s.initial_qty = 0.05
    s.tp_levels_consumed = []
    s._last_tv_field_sources = {}
    s.monitoring = True
    # 副作用点全部mock掉，只验证tag/exit_source映射本身
    s._get_active_position = MagicMock(return_value=None)  # 盘口已空 → already_flat分支
    s._should_ignore_late_close = MagicMock(return_value=False)
    s._release_tv_seq_after_close = MagicMock()
    s._record_tv_signal = MagicMock()
    s._on_position_query_failed = MagicMock()
    s._handle_manual_flat_detected = MagicMock()
    s._close_all = MagicMock()
    return s


class TestCloseDynamicTrailSupervisorTag(unittest.TestCase):
    def test_dynamic_trail_gets_own_tag_not_misclassified_as_rsi(self):
        """核心回归：新action必须有自己的tag/exit_source映射，不能静默
        落进原来写死的CLOSE_RSI_EXIT那个else分支。"""
        s = _mk_supervisor()
        payload = {
            "action": "CLOSE_DYNAMIC_TRAIL",
            "symbol": "SKHYNIXUSDT.P",
            "side": "LONG",
            "price": 1413.83,
            "reason": "动态移动止盈",
        }
        s._process_signal(payload)
        s._handle_manual_flat_detected.assert_called_once()
        _, kwargs = s._handle_manual_flat_detected.call_args
        flat_meta = kwargs.get("close_meta") or s._handle_manual_flat_detected.call_args[0][1]
        self.assertEqual(flat_meta["exit_source"], EXIT_SOURCE_TV_CLOSE)
        self.assertNotEqual(
            flat_meta["exit_source"], EXIT_SOURCE_RSI,
            "CLOSE_DYNAMIC_TRAIL不能被误标成CLOSE_RSI_EXIT的反转保护(RSI)",
        )
        self.assertIn("动态移动止盈", flat_meta["tv_reason"])

    def test_quick_exit_and_rsi_exit_unaffected(self):
        """回归：既有的两个action的tag/exit_source映射保持不变，新增
        分支不能改变老行为。"""
        for action, expect_source in (
            ("CLOSE_QUICK_EXIT", psb.EXIT_SOURCE_QUICK),
            ("CLOSE_RSI_EXIT", EXIT_SOURCE_RSI),
        ):
            s = _mk_supervisor()
            s._process_signal({
                "action": action, "symbol": "SKHYNIXUSDT.P",
                "side": "LONG", "price": 1400.0, "reason": "",
            })
            _, kwargs = s._handle_manual_flat_detected.call_args
            flat_meta = kwargs.get("close_meta") or s._handle_manual_flat_detected.call_args[0][1]
            self.assertEqual(flat_meta["exit_source"], expect_source, action)


if __name__ == "__main__":
    unittest.main(verbosity=2)
