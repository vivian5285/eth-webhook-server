#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
2026-09-05新增：全局杠杆假设从5降到3的回归测试。

背景——宝贝原话："由于有的品种周期调的更低了（可能噪音也多，当然机会
也更多），把所有的仓位权重调整成3倍杠杆吧，仓位金额不变，杠杆下降，
换算下降"。仓位公式与交易所真实杠杆本来就彻底解耦(系统从不调用
set_leverage改交易所)，这次只改VPS下单公式里的杠杆假设：risk_pct
(本金×20%，"仓位金额"这部分)不变，leverage从5降到3，名义/qty按比例
(3/5=0.6倍)一起降下来。

这次改动牵出一个预先存在的显示bug：FIXED_RISK_PCT*FIXED_NOTIONAL_MULT
用0.20×5=1.0这种"整数"侥幸，日志里用:.0f格式化从没露过馅；换成0.20×3=
0.6后再用:.0f会被四舍五入误显示成"1"，两处(webhook_parser.py::
format_vps_sizing_note、position_supervisor_binance.py同款日志)都
改成:.2f。

不碰任何真实账户/持仓，纯粹验证常量值和compute_fixed_order_qty()
纯函数的输出。
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

os.environ["BINANCE_SKIP_BOOTSTRAP"] = "1"

import webhook_parser as wp  # noqa: E402


class TestLeverageConstants(unittest.TestCase):
    def test_fixed_leverage_is_3(self):
        self.assertEqual(wp.FIXED_LEVERAGE, 3)
        self.assertEqual(wp.FIXED_NOTIONAL_MULT, 3.0)
        self.assertEqual(wp.EXCHANGE_LEVERAGE, 3)
        self.assertEqual(wp.VPS_MARGIN_LEVERAGE, 3)

    def test_risk_pct_unchanged_at_20pct(self):
        """"仓位金额不变"——risk_pct(本金×20%)这部分必须保持不变，只有
        leverage变了。"""
        self.assertAlmostEqual(wp.FIXED_RISK_PCT, 0.20, places=6)
        self.assertAlmostEqual(wp.FIXED_MARGIN_PCT, 0.20, places=6)

    def test_sizing_mode_label_matches_new_leverage(self):
        self.assertEqual(wp.SIZING_MODE, "RISK20_NOTIONAL3")


class TestComputeFixedOrderQtyAt3x(unittest.TestCase):
    """精确复现check_vps_logic.py同款用例，验证3倍杠杆下的实际输出
    （手工核对过的真实数值，不是脚本自己再算一遍来自我验证）。"""

    def test_notional_primary_case(self):
        """1000本金，无TV.sl，risk/dist=2.0 vs 名义3300.5价位下的qty_by_
        notional=600/3300.5≈0.1818，名义约束生效：1000×20%×3=600
        （旧5倍杠杆版本是1000，新版本按比例(3/5=0.6倍)降到600）。"""
        qty, meta = wp.compute_fixed_order_qty(
            1000, 3300.5, stop_loss=3200.5, tv_qty=12,
        )
        self.assertAlmostEqual(qty, 0.181, places=3)
        self.assertAlmostEqual(meta["notional_cap"], 600.0, places=1)
        self.assertEqual(meta["sizing_mode"], "RISK20_NOTIONAL3")
        self.assertEqual(meta["binding"], "notional")

    def test_notional_cap_scales_down_proportionally_to_leverage(self):
        """1000本金@3000价，名义约束案例：旧版本(5x)qty≈0.333，
        新版本(3x)应该精确按3/5=0.6倍降到≈0.2——不是巧合凑数，是
        risk_pct(不变)×leverage(5→3)直接线性关系。"""
        qty, meta = wp.compute_fixed_order_qty(
            1000, 3000, stop_loss=2940, tv_qty=2.0, tv_sl=2960,
        )
        self.assertAlmostEqual(qty, 0.2, places=3)
        self.assertEqual(meta["binding"], "notional")
        self.assertAlmostEqual(meta["notional_cap"], 600.0, places=1)

    def test_sizing_note_display_precision_not_misleading(self):
        """0.20×3=0.6不是整数——format_vps_sizing_note里如果还用旧的
        :.0f格式化会把0.6四舍五入误显示成"1"，误导后续核对仓位公式的人。
        这里验证展示文本里出现的是精确的0.60，不是被四舍五入的1。"""
        qty, meta = wp.compute_fixed_order_qty(
            1000, 3300.5, stop_loss=3200.5, tv_qty=12,
        )
        note = wp.format_vps_sizing_note(meta, qty=qty)
        self.assertIn("0.60", note)
        self.assertNotIn("×1)", note)


if __name__ == "__main__":
    unittest.main(verbosity=2)
