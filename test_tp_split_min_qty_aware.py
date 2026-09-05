#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
2026-09-05新增：_split_tp_quantities()新增的"min_qty感知TP1保底"回归测试。

背景——宝贝复现：SNDKUSDT同一笔TV信号，B/C两个账户仓位较小(0.06)，按
固定10%比例算出的TP1(0.006)低于交易所最小下单量(0.01)，被
_normalize_tp_qty_map合并进TP2挂成一档；价格只冲到TP1附近就回落，
合并后的那一档要等到TP2那么远才成交，始终没等到，B/C全程没能提前锁
一点利润，最后整仓吃了一口回撤止损。同一笔信号，仓位更大的E账户
(0.10)TP1能单独挂出、提前锁到了利润，处境好得多。

宝贝问"有没有更好的处理办法"——比起恢复仓位权重(会连带放大早就想
控制住的磨损风险)或者直接改全局TP1/2/3比例(会不必要地牺牲TP3"让
利润奔跑"的份额)，更精准的办法是：在按比例算TP1数量这一步就检查
"TP1单独是否够格挂单"，不够格但TP1+TP2合起来的总量本身足够时，优先
从TP2挪一点点填平TP1，不动TP3；仓位小到连合并都不够的情况(比如
ANTHROPIC那种更极端的例子)原样跳过，交给已经验证过的
_normalize_tp_qty_map合并/放弃兜底处理。

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


def _mk_supervisor(min_qty):
    with patch.object(psb.PositionSupervisorBinance, "__init__", lambda self, *a, **k: None):
        s = psb.PositionSupervisorBinance()
    s.symbol = "SNDKUSDT"
    s.min_qty = min_qty
    return s


class TestSplitTpQuantitiesMinQtyAware(unittest.TestCase):
    RATIOS = [0.10, 0.20, 0.70]

    def test_bc_real_incident_rebalances_tp1_to_viable(self):
        """B/C真实复现：qty=0.06, min_qty=0.01——TP1单独(0.006)不够格，但
        TP1+TP2合计(0.018)够，应该从TP2挪0.002把TP1补到0.01整，TP2剩
        0.01(仍然够格独立挂单)，TP3吸收剩余0.04。"""
        s = _mk_supervisor(min_qty=0.01)
        q1, q2, q3 = s._split_tp_quantities(0.06, self.RATIOS)
        self.assertAlmostEqual(q1, 0.01, places=3)
        self.assertAlmostEqual(q2, 0.01, places=3)
        self.assertAlmostEqual(q3, 0.04, places=3)
        self.assertAlmostEqual(q1 + q2 + q3, 0.06, places=3)
        # 修复前的行为(仅供对照，不是断言)：q1=0.006 < min_qty，会被
        # _normalize_tp_qty_map合并——这里验证的是新行为已经让TP1本身
        # 就够格，不需要再依赖那条合并兜底。
        self.assertGreaterEqual(q1, 0.01)

    def test_e_real_position_unaffected(self):
        """E真实仓位：qty=0.10——TP1(0.01)本来就够格，不该触发任何调整，
        跟修复前完全一样的结果(0.01/0.02/0.07)。"""
        s = _mk_supervisor(min_qty=0.01)
        q1, q2, q3 = s._split_tp_quantities(0.10, self.RATIOS)
        self.assertAlmostEqual(q1, 0.01, places=3)
        self.assertAlmostEqual(q2, 0.02, places=3)
        self.assertAlmostEqual(q3, 0.07, places=3)

    def test_genuinely_too_small_left_untouched(self):
        """ANTHROPIC类真实复现：qty=0.03, min_qty=0.01——TP1(0.003)不够
        格，且TP1+TP2合计(0.009)本身也低于min_qty，不该被这里强行凑数，
        原样按比例返回(0.003/0.006/0.021)，交给下游_normalize_tp_qty_map
        既有的"合并/放弃"兜底处理。"""
        s = _mk_supervisor(min_qty=0.01)
        q1, q2, q3 = s._split_tp_quantities(0.03, self.RATIOS)
        self.assertAlmostEqual(q1, 0.003, places=3)
        self.assertAlmostEqual(q2, 0.006, places=3)
        self.assertAlmostEqual(q3, 0.021, places=3)

    def test_large_position_unaffected(self):
        """常规大仓位(比如ETH几十U保证金对应的qty)TP1本来就远超min_qty，
        完全不受影响。"""
        s = _mk_supervisor(min_qty=0.001)
        q1, q2, q3 = s._split_tp_quantities(1.0, self.RATIOS)
        self.assertAlmostEqual(q1, 0.10, places=3)
        self.assertAlmostEqual(q2, 0.20, places=3)
        self.assertAlmostEqual(q3, 0.70, places=3)

    def test_tp2_alone_still_short_borrows_from_tp3(self):
        """构造一个TP2挪到自己也只剩min_qty还补不平TP1的场景，验证会
        继续从TP3借一点点，且TP3自己不会被借到min_qty以下。"""
        s = _mk_supervisor(min_qty=0.02)
        # qty=0.10, ratios=[0.10,0.15,0.75] → 原始qty1=0.010(缺0.010)，
        # qty2=0.015(比min_qty还小，挪不出0.010-min_qty这么多，只能
        # 挪到min_qty为止，也就是不挪，因为qty2本身已经<min_qty)
        q1, q2, q3 = s._split_tp_quantities(0.10, [0.10, 0.15, 0.75])
        # qty2本身(0.015)小于min_qty(0.02)，take_from_2按公式
        # max(qty2-min_qty,0)=0，挪不出来，只能从TP3借
        self.assertGreaterEqual(q1, 0.02 - 1e-6)
        self.assertGreaterEqual(q3, 0.02 - 1e-6)  # TP3借出后自己仍不低于min_qty
        self.assertAlmostEqual(q1 + q2 + q3, 0.10, places=3)

    def test_missing_min_qty_falls_back_to_module_constant(self):
        """min_qty属性缺失/0时，退回MIN_TP_LEG_QTY(0.001)这个极小的默认
        值，不该因为min_qty读取失败而误伤正常仓位。"""
        s = _mk_supervisor(min_qty=0)
        q1, q2, q3 = s._split_tp_quantities(0.06, self.RATIOS)
        # 0.001门槛下，0.006本来就够格，不触发任何调整
        self.assertAlmostEqual(q1, 0.006, places=3)
        self.assertAlmostEqual(q2, 0.012, places=3)


if __name__ == "__main__":
    unittest.main(verbosity=2)
