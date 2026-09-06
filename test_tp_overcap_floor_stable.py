#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
2026-09-06新增：_normalize_tp_qty_map()"硬帽压回"分支新增的
"降无可降就不重复告警"回归测试。

背景——E账户实盘复现：SNDKUSDT/GSUSDT这类min_qty=0.01的小仓位，TP1/TP2
两档各自都已经被最小下单量下限顶到min_leg_qty(合计2×0.01=0.02)，而
30%硬帽(hard_raw)本身就小于这个下限合计——SNDKUSDT实测tot=0.0200
hard=0.0150 excess=0.0050，GSUSDT实测tot=0.0200 hard=0.0180
excess=0.0020。这种情况下"压回"无论怎么按比例扣减，扣完都会被
min_leg_qty地板重新顶回原值，是"交易所最小下单量下限 vs 30%软帽"
结构性打架、根本降不下去的稳定态。原实现不管有没有真的降下去都无
条件打一条WARNING，导致两小时内被同一组数字刷了929次一模一样的
"TP限价超帽...压回"日志，控制面板异常告警刷屏。

修复：只有真正压下去了(new_vals跟压回前不同)才落地新值+告警；降无
可降时静默保留原值(仍是有效的最小下单量组合，不影响挂单正确性)。

不碰任何真实账户/持仓，纯粹验证这一个纯函数。
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


def _mk_supervisor(symbol, min_qty, initial_qty, place_n=2):
    with patch.object(psb.PositionSupervisorBinance, "__init__", lambda self, *a, **k: None):
        s = psb.PositionSupervisorBinance()
    s.symbol = symbol
    s.min_qty = min_qty
    s.initial_qty = initial_qty
    s._leg_ratios = [0.10, 0.20, 0.70]
    s._effective_place_tp_levels = lambda: place_n
    s._tp_baseline_qty = lambda live_qty: initial_qty
    return s


class TestTpOvercapFloorStable(unittest.TestCase):
    def test_sndk_real_incident_stops_reduce_repeated_warning(self):
        """SNDK实盘复现：tot=0.0200 hard=0.0150，两档都卡在min_qty地板，
        降无可降——反复调用同一个稳定状态，只应该在真正压下去的那次
        (如果压得下去)告警，压不动的稳定态不应该每次都重复打同一条。"""
        s = _mk_supervisor("SNDKUSDT", min_qty=0.01, initial_qty=0.05)
        qty_map = {1: 0.01, 2: 0.01}
        with patch.object(psb, "logger") as mock_logger:
            out1 = s._normalize_tp_qty_map(dict(qty_map), live_qty=0.04)
            out2 = s._normalize_tp_qty_map(dict(qty_map), live_qty=0.04)
            out3 = s._normalize_tp_qty_map(dict(qty_map), live_qty=0.04)
        # 数值本身仍然正确：两档都还是有效的最小下单量0.01，没有被错误清零
        for out in (out1, out2, out3):
            self.assertAlmostEqual(out[1], 0.01, places=3)
            self.assertAlmostEqual(out[2], 0.01, places=3)
        overcap_calls = [
            c for c in mock_logger.warning.call_args_list
            if "TP限价超帽" in str(c)
        ]
        self.assertEqual(
            len(overcap_calls), 0,
            "降无可降(地板挡住)的稳定态不应该打印'TP限价超帽...压回'告警",
        )

    def test_gs_real_incident_stops_reduce_repeated_warning(self):
        """GS实盘复现：tot=0.0200 hard=0.0180，同样降无可降。"""
        s = _mk_supervisor("GSUSDT", min_qty=0.01, initial_qty=0.06)
        qty_map = {1: 0.01, 2: 0.01}
        with patch.object(psb, "logger") as mock_logger:
            for _ in range(5):
                out = s._normalize_tp_qty_map(dict(qty_map), live_qty=0.05)
                self.assertAlmostEqual(out[1], 0.01, places=3)
                self.assertAlmostEqual(out[2], 0.01, places=3)
        overcap_calls = [
            c for c in mock_logger.warning.call_args_list
            if "TP限价超帽" in str(c)
        ]
        self.assertEqual(len(overcap_calls), 0)

    def test_genuine_overcap_still_reduces_and_warns_once(self):
        """回归：仓位够大、真的能压下去的正常场景不受影响——两档都明显
        高于min_qty地板，超帽时应该照常按比例压回并告警一次。"""
        s = _mk_supervisor("SNDKUSDT", min_qty=0.01, initial_qty=1.0)
        # TP1/TP2远高于min_qty，30%硬帽=0.3，live_qty*0.4=0.4，hard=0.3
        qty_map = {1: 0.15, 2: 0.25}  # tot=0.40 > hard=0.30
        with patch.object(psb, "logger") as mock_logger:
            out = s._normalize_tp_qty_map(dict(qty_map), live_qty=1.0)
        overcap_calls = [
            c for c in mock_logger.warning.call_args_list
            if "TP限价超帽" in str(c)
        ]
        self.assertEqual(len(overcap_calls), 1, "真正能压下去的场景应该照常告警一次")
        self.assertLess(out[1] + out[2], 0.40, "压回后合计应该真的降到硬帽以内附近")
        self.assertGreaterEqual(out[1], 0.01)
        self.assertGreaterEqual(out[2], 0.01)


if __name__ == "__main__":
    unittest.main(verbosity=2)
