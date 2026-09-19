#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
2026-09-12新增：_symbols_with_orphaned_live_positions() 的回归测试。

背景——宝贝实盘复现：2026-09-12把TV白名单从20个收窄到几个之后，
bootstrap_supervisors() 只给白名单品种建"军师"对象——原本在白名单里、
暂停时手上还挂着仓位的品种(BCHUSDT，B账户0.62/E账户1.02空单)完全没有
军师了，哨兵循环/雷达追踪/硬止损维护全部停摆，只剩交易所上早先挂好的
静态止损单裸奔兜底。

2026-09-19推翻(宝贝明确叫停，实盘复现：ZEC今天被强平两次、ETH手动多单
也被平仓)："自动补建军师继续照看"这个设计本身就是问题根源——白名单外
的仓位(不管是历史TV仓位还是宝贝自己手工开的)一律不该被VPS的硬止损/
雷达/TV方向强平碰到，宝贝自己管理止盈止损。_symbols_with_orphaned_
live_positions()这个检测函数本身保留(纯只读，帮宝贝发现+告警)，但
bootstrap_supervisors()不再拿它的结果去建supervisor——detection≠adoption。

不碰任何真实账户/持仓，mock binance_client._refresh_all_positions，
用BINANCE_SYMBOL_META真实内容验证"认识的品种才补建、不认识的跳过"。
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


class TestOrphanedPositionBootstrap(unittest.TestCase):
    def test_bch_style_incident_gets_supervisor_slot(self):
        """核心回归：BCHUSDT有真实非零仓位、但不在白名单里 → 必须被
        识别为需要补建军师的孤儿仓位。"""
        _fake_bc.binance_client._refresh_all_positions = MagicMock(return_value={
            "BCHUSDT": {"positionAmt": "-0.620"},
            "OPENAIUSDT": {"positionAmt": "0.5"},   # 白名单内，不算孤儿
            "ETHUSDT": {"positionAmt": "0.0"},       # 已空仓，不算孤儿
        })
        out = psb._symbols_with_orphaned_live_positions({"OPENAIUSDT", "XPDUSDT", "SNDKUSDT"})
        self.assertEqual(out, ["BCHUSDT"])

    def test_unknown_ticker_skipped(self):
        """交易所返回一个本地symbol_config不认识的ticker(比如已下架/
        重命名) → 不该被拉去建军师，交由人工核查，不能因为不认识就崩。"""
        _fake_bc.binance_client._refresh_all_positions = MagicMock(return_value={
            "TOTALLYUNKNOWNUSDT": {"positionAmt": "1.0"},
        })
        out = psb._symbols_with_orphaned_live_positions(set())
        self.assertEqual(out, [])

    def test_all_flat_or_whitelisted_returns_empty(self):
        """回归：正常情况(白名单品种有仓，非白名单全部空仓)不应该产生
        任何孤儿列表，避免每次启动都误建一堆不需要的军师。"""
        _fake_bc.binance_client._refresh_all_positions = MagicMock(return_value={
            "OPENAIUSDT": {"positionAmt": "0.5"},
            "XPDUSDT": {"positionAmt": "0.0"},
            "BCHUSDT": {"positionAmt": "0"},
        })
        out = psb._symbols_with_orphaned_live_positions({"OPENAIUSDT", "XPDUSDT", "SNDKUSDT"})
        self.assertEqual(out, [])

    def test_rest_query_failure_fails_safe_empty(self):
        """账户级持仓核对失败(REST异常/None) → 不能让bootstrap_
        supervisors()本身崩掉，宁可这轮跳过孤儿仓位补建，下次重启再试。"""
        _fake_bc.binance_client._refresh_all_positions = MagicMock(return_value=None)
        out = psb._symbols_with_orphaned_live_positions(set())
        self.assertEqual(out, [])

        _fake_bc.binance_client._refresh_all_positions = MagicMock(side_effect=RuntimeError("boom"))
        out = psb._symbols_with_orphaned_live_positions(set())
        self.assertEqual(out, [])

    def test_bootstrap_symbols_list_only_whitelist_orphans_alert_only(self):
        """2026-09-19宝贝明确叫停(实盘复现：ZEC今天被强平两次、ETH手动
        多单也被平仓)：孤儿仓位品种不再自动补建军师接管——只保留检测+
        告警，bootstrap清单只有白名单品种，orphaned品种不会被get_
        supervisor()创建（也就不会有任何线程碰它们的下单/平仓）。"""
        with patch.object(psb, "_symbols_with_orphaned_live_positions", return_value=["BCHUSDT", "ETHUSDT"]), \
             patch("symbol_config.active_binance_symbols", return_value=["OPENAIUSDT", "XPDUSDT", "SNDKUSDT"]), \
             patch.object(psb, "get_supervisor") as mock_get_sup:
            psb.SUPERVISORS.clear()
            psb.bootstrap_supervisors()
            called_syms = [c.args[0] for c in mock_get_sup.call_args_list]
            self.assertEqual(called_syms, ["OPENAIUSDT", "XPDUSDT", "SNDKUSDT"])
            self.assertNotIn("BCHUSDT", called_syms)
            self.assertNotIn("ETHUSDT", called_syms)

    def test_orphans_found_sends_alert_not_silent(self):
        """检测到白名单外仓位时必须告警——不接管不等于假装没看见，宝贝
        需要知道自己有个仓位VPS不再管了，得自己去挂止损。bootstrap本身
        还有其它跟孤儿仓位无关的既有告警(比如"品种档位不齐提醒")，这里
        只关心"白名单外发现仓位"这一条有没有真的发出去，不对整个mock
        的调用次数做强假设。"""
        with patch.object(psb, "_symbols_with_orphaned_live_positions", return_value=["ZECUSDT"]), \
             patch("symbol_config.active_binance_symbols", return_value=["OPENAIUSDT"]), \
             patch.object(psb, "get_supervisor"):
            psb.SUPERVISORS.clear()
            import dingtalk
            dingtalk.report_system_alert = MagicMock()
            psb.bootstrap_supervisors()
            orphan_calls = [
                c for c in dingtalk.report_system_alert.call_args_list
                if "白名单外发现仓位" in str(c.kwargs.get("title", ""))
            ]
            self.assertEqual(len(orphan_calls), 1)
            self.assertIn("ZECUSDT", orphan_calls[0].kwargs.get("detail", ""))

    def test_no_orphans_no_orphan_alert(self):
        with patch.object(psb, "_symbols_with_orphaned_live_positions", return_value=[]), \
             patch("symbol_config.active_binance_symbols", return_value=["OPENAIUSDT"]), \
             patch.object(psb, "get_supervisor"):
            psb.SUPERVISORS.clear()
            import dingtalk
            dingtalk.report_system_alert = MagicMock()
            psb.bootstrap_supervisors()
            orphan_calls = [
                c for c in dingtalk.report_system_alert.call_args_list
                if "白名单外发现仓位" in str(c.kwargs.get("title", ""))
            ]
            self.assertEqual(orphan_calls, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
