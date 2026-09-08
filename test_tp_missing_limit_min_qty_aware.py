#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
2026-09-08新增：_may_mark_tp_filled_missing_limit()新增的min_qty降级
感知回归测试。

背景——实盘复现：LITEUSDT/PAXGUSDT/ANTHROPICUSDT一夜之间被打了
1806/852/596条一模一样的"🧩拒认TPN假成交：价到+限价无，但头寸无减仓
证据...→视为漏挂，允许补挂/推离"，同时全程零次真实补挂动作（30分钟
窗口内0次真实下单）。实盘核实：LITEUSDT live=base=0.08，TP1按10%比例
只有0.008，本身就低于min_qty=0.01——_normalize_tp_qty_map(min_qty
感知借调/放弃流水线)早就正确决定"TP1这一档不挂"，交易所上确实只有
TP2一张单，这是设计内的正常降级状态，不是漏挂。但_may_mark_tp_filled_
missing_limit(4/5调用点在用的共用函数)完全不知道这一层，只看
self.tv_tps这个原始TV价位表，价到+限价无+无减仓证据就无条件判定
"视为漏挂"——跟_infer_tp_consumed_by_price_and_gone(它有merged_qty_map
防护)是同一类问题的两个独立实现，一个修过一个没修。

修复：在判定"视为漏挂"之前，用跟_infer_tp_consumed_by_price_and_gone
同款的_normalize_tp_qty_map检查——这一档如果权威流水线已经正确降到0，
直接判定"不适用"，静默返回False，不打误导性日志。

不碰任何真实账户/持仓，纯粹验证这一个函数。
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


def _mk_supervisor(tv_tps, merged_qty_map, qty_evidence=False):
    with patch.object(psb.PositionSupervisorBinance, "__init__", lambda self, *a, **k: None):
        s = psb.PositionSupervisorBinance()
    s.symbol = "LITEUSDT"
    s.tv_tps = tv_tps
    s.min_qty = 0.01
    s._in_self_tp_purge_window = MagicMock(return_value=False)
    s._has_tp_limit_at_price = MagicMock(return_value=False)  # 限价确实不存在
    s._price_reached_tp_zone = MagicMock(return_value=True)   # 价格已到该档区域
    s._qty_evidence_tp_consumed = MagicMock(return_value=qty_evidence)
    s._tp_baseline_qty = MagicMock(return_value=0.08)
    s._split_remaining_tp_quantities = MagicMock(return_value=dict(merged_qty_map))
    s._normalize_tp_qty_map = MagicMock(return_value=dict(merged_qty_map))
    return s


class TestTpMissingLimitMinQtyAware(unittest.TestCase):
    def test_lite_real_incident_min_qty_dropped_level_silently_skipped(self):
        """LITE实盘复现：TP1按比例0.008<min_qty=0.01，已被min_qty流水线
        正确降到0(merged_qty_map[1]=0.0)——不应该打"拒认假成交"日志，
        应该静默返回False。"""
        s = _mk_supervisor(
            tv_tps=[906.0, 915.24, 930.0],
            merged_qty_map={1: 0.0, 2: 0.01},
            qty_evidence=False,
        )
        with patch.object(psb, "logger") as mock_logger:
            result = s._may_mark_tp_filled_missing_limit(1, live_qty=0.08, curr_px=910.61)
        self.assertFalse(result)
        fake_fill_calls = [
            c for c in mock_logger.warning.call_args_list
            if "拒认" in str(c) and "假成交" in str(c)
        ]
        self.assertEqual(
            len(fake_fill_calls), 0,
            "min_qty正确降级的档位不应该打印'拒认假成交'误导性日志",
        )

    def test_genuine_missing_tp_still_warns_and_rejects(self):
        """回归：这一档在min_qty流水线里本来就有正常的非零期望量
        (真的是漏挂，不是降级)——必须照常打印警告、返回False，不能因为
        这次修复反而把真正的漏挂也放过。"""
        s = _mk_supervisor(
            tv_tps=[906.0, 915.24, 930.0],
            merged_qty_map={1: 0.02, 2: 0.05},  # TP1本来就该有0.02，没降级
            qty_evidence=False,
        )
        with patch.object(psb, "logger") as mock_logger:
            result = s._may_mark_tp_filled_missing_limit(1, live_qty=0.08, curr_px=910.61)
        self.assertFalse(result)
        fake_fill_calls = [
            c for c in mock_logger.warning.call_args_list
            if "拒认" in str(c) and "假成交" in str(c)
        ]
        self.assertEqual(len(fake_fill_calls), 1, "真正的漏挂必须照常告警，不能被误伤放过")

    def test_qty_evidence_present_still_returns_true_unaffected(self):
        """回归：有减仓证据的正常成交判定路径完全不受这次改动影响。"""
        s = _mk_supervisor(
            tv_tps=[906.0, 915.24, 930.0],
            merged_qty_map={1: 0.0, 2: 0.01},
            qty_evidence=True,
        )
        result = s._may_mark_tp_filled_missing_limit(1, live_qty=0.072, curr_px=910.61)
        self.assertTrue(result)

    def test_normalize_exception_falls_back_to_original_warning_behavior(self):
        """新增的merged_qty_map查询本身若异常(比如_split_remaining_tp_
        quantities抛错)，要能安全降级回原有行为(照常告警)，不能让整个
        判定函数崩溃或被异常静默吞掉真正的漏挂。"""
        s = _mk_supervisor(
            tv_tps=[906.0, 915.24, 930.0],
            merged_qty_map={1: 0.02, 2: 0.05},
            qty_evidence=False,
        )
        s._split_remaining_tp_quantities = MagicMock(side_effect=RuntimeError("boom"))
        with patch.object(psb, "logger") as mock_logger:
            result = s._may_mark_tp_filled_missing_limit(1, live_qty=0.08, curr_px=910.61)
        self.assertFalse(result)
        fake_fill_calls = [
            c for c in mock_logger.warning.call_args_list
            if "拒认" in str(c) and "假成交" in str(c)
        ]
        self.assertEqual(len(fake_fill_calls), 1, "查询异常时应安全降级回原有告警行为")


if __name__ == "__main__":
    unittest.main(verbosity=2)
