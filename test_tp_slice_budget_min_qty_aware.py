#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
2026-09-05新增：check_tp_slice_budget()新增的expected_override回归测试。

背景——实盘复现：C账户BNBUSDT心跳追回市价成交qty=0.06，_split_tp_quantities
的min_qty感知重分配把TP1从0.006借到0.01凑够最小下单量(其中0.002借自TP2、
0.002借自TP3)，TP1+TP2合计从朴素比例的0.018变成0.02。_assert_place_tp_budget
如果还拿朴素比例当expected喂给check_tp_slice_budget，drift=0.002会超过
tol=max(0.001,init*3%,expected*3%)=0.0018，被误判"超帽"连续拒挂——实盘上
这导致C的这笔仓位从06:43开仓起，往后20多分钟每个巡检周期都被这道闸拦下来，
仓位全程只有硬止损、完全没有止盈单。

expected_override让调用方(_assert_place_tp_budget)传入_split_tp_quantities
算出的真实期望份额，闸门不再用朴素比例线下自己重算一遍。
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

from chief_auditor import check_tp_slice_budget  # noqa: E402
import position_supervisor_binance as psb  # noqa: E402


def _mk_supervisor(min_qty):
    with patch.object(psb.PositionSupervisorBinance, "__init__", lambda self, *a, **k: None):
        s = psb.PositionSupervisorBinance()
    s.symbol = "BNBUSDT"
    s.min_qty = min_qty
    return s


class TestCheckTpSliceBudgetExpectedOverride(unittest.TestCase):
    def test_naive_expected_rejects_legit_min_qty_rebalance(self):
        """不传expected_override(旧行为)：min_qty重分配后的合法结果(0.01/0.01)
        对照朴素比例期望(0.018)会被判超帽——这是修复前实盘复现的确切数值。"""
        item = check_tp_slice_budget(0.06, 0.01, 0.01, place_levels=2,
                                      ratios=[0.10, 0.20, 0.70])
        self.assertFalse(item.ok)
        self.assertIn("drift=0.0020", item.detail)

    def test_expected_override_accepts_legit_min_qty_rebalance(self):
        """传入_split_tp_quantities算出的真实期望(0.02)后，同样的0.01/0.01
        应该放行，不再被朴素比例误伤。"""
        item = check_tp_slice_budget(0.06, 0.01, 0.01, place_levels=2,
                                      ratios=[0.10, 0.20, 0.70],
                                      expected_override=0.02)
        self.assertTrue(item.ok, item.detail)

    def test_readme_regression_still_rejects_real_overshoot(self):
        """README既有断言不受影响：真正超帽(tp1+tp2吞掉几乎整仓)必须仍被拒。"""
        item = check_tp_slice_budget(1, 0.5, 0.5)
        self.assertFalse(item.ok)


class TestAssertPlaceTpBudgetIntegration(unittest.TestCase):
    """端到端复现：BNB心跳追回场景(qty=0.06, min_qty=0.01)走完整个
    _assert_place_tp_budget，确认新代码路径真的放行，不再拒挂。"""

    def test_bnb_catchup_scenario_passes_budget_gate(self):
        s = _mk_supervisor(min_qty=0.01)
        s._leg_ratios = [0.10, 0.20, 0.70]
        s.tp_levels_consumed = []
        s.initial_qty = 0.06
        s._tp_baseline_qty = lambda live_qty: 0.06
        s._effective_place_tp_levels = lambda: 2
        # _expected_tp_levels 应该反映_split_tp_quantities的min_qty感知结果
        s._expected_tp_levels = lambda live_qty: [
            {"level": 1, "qty": 0.01, "price": 776.28},
            {"level": 2, "qty": 0.01, "price": 819.04},
        ]
        ok, detail = s._assert_place_tp_budget(0.06)
        self.assertTrue(ok, detail)


if __name__ == "__main__":
    unittest.main(verbosity=2)
