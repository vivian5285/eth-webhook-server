#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
2026-09-08新增：_latch_radar_activation_sticky()跨账户互通"己方进度"
门槛回归测试。

背景——宝贝反馈SKHYNIXUSDT实盘复现"保本止损后立即被套"：B账户
entry=1380.63，C账户entry=1380.27，两者相差0.36点，远在2026-09-06
那次修复(test_sibling_sync_entry_proximity.py)的entry接近度容差内，
entry校验正确放行。但C账户自己的价格真的冲到了1404.51、摸到了它自己
的激活线(≈1405.92)；B账户这边的价格却全程还在entry附近(~1381.44)，
离自己的激活线(≈1399.10)一步都没挪动——B/C明明是同一个交易所同一个
symbol，行情应该完全一致，大概率是B自己那段时间的行情/best_price
追踪出现了滞后或缺口。结果B被直接顶到保本止损附近，一次很普通的回调
就把它打出去，随后智能重入又被套。

修复：_latch_radar_activation_sticky在采信_radar_sync_touch_check()
之前，新增一道"己方进度"校验——自己的best/curr相对entry→act这段距离，
至少要走完RADAR_SYNC_MIN_OWN_PROGRESS_FRAC(0.5，即一半)，才允许借
姊妹账户的摸线状态；走得太少一律不借，退回各account各自摸各自的线。

只测这一个函数的分支逻辑，不碰任何真实账户/持仓/网络。
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


def _mk_supervisor(side, entry, act, best=0.0):
    with patch.object(psb.PositionSupervisorBinance, "__init__", lambda self, *a, **k: None):
        s = psb.PositionSupervisorBinance()
    s.symbol = "SKHYNIXUSDT"
    s.current_side = side
    s.watched_entry = entry
    s.best_price = best
    s.radar_activation_sticky = False
    s.radar_activated = False
    s._radar_activation_price = MagicMock(return_value=act)
    return s


class TestRadarSyncOwnProgressGate(unittest.TestCase):
    def test_skhynix_real_incident_own_price_stalled_rejects_sync(self):
        """实盘复现：B entry=1380.63，自己的激活线≈1399.10，但自己的
        best全程只到~1381.44(几乎没挪动，进度0.81/18.47≈4%，远低于
        50%门槛)——即使姊妹账户C摸线成功，也不应该被借用/闩锁。"""
        b = _mk_supervisor("LONG", entry=1380.63, act=1399.10, best=1381.44)
        b._radar_sync_touch_check = MagicMock(
            return_value={"acct": "binanceC", "mark": 1404.51}
        )
        b._radar_sync_touch_write = MagicMock()
        with patch.object(psb, "logger"):
            result = b._latch_radar_activation_sticky(curr_px=1381.44)
        self.assertFalse(result, "己方进度不足时不应借姊妹账户摸线状态被顶到保本")
        self.assertFalse(bool(b.radar_activation_sticky))
        b._radar_sync_touch_check.assert_not_called()

    def test_own_progress_sufficient_still_allows_legitimate_sync(self):
        """回归：己方价格确实也走完了大半段路(≥50%)时，姊妹账户互通
        机制应该照常生效——不能因为这次修复把原本合理的互通场景也堵死。"""
        b = _mk_supervisor("LONG", entry=1380.63, act=1399.10, best=1391.0)
        # 进度 = |1391.0-1380.63| / |1399.10-1380.63| = 10.37/18.47 ≈ 56%
        b._radar_sync_touch_check = MagicMock(
            return_value={"acct": "binanceC", "mark": 1404.51}
        )
        with patch.object(psb, "logger"):
            result = b._latch_radar_activation_sticky(curr_px=1391.0)
        self.assertTrue(result, "己方进度达标时应正常继承姊妹账户摸线状态")
        self.assertTrue(bool(b.radar_activation_sticky))
        b._radar_sync_touch_check.assert_called_once()

    def test_progress_insufficient_falls_back_to_own_touch_check(self):
        """己方进度不够、婉拒了sync借用之后，函数应该继续走原有的
        "自己的价格是否已经摸到激活线"判定，而不是直接短路返回False——
        如果自己价格真的也摸线了(哪怕sync没帮上忙)，仍然应该正常武装。"""
        b = _mk_supervisor("LONG", entry=1380.63, act=1381.0, best=1381.0)
        # entry→act距离很短(0.37)，best已经到act本身——自己真的摸线了
        b._radar_sync_touch_check = MagicMock(return_value=None)
        b._radar_sync_touch_write = MagicMock()
        with patch.object(psb, "logger"):
            result = b._latch_radar_activation_sticky(curr_px=1381.0)
        self.assertTrue(result, "己方本来就摸线的情况不应被这次新增校验误伤")
        self.assertTrue(bool(b.radar_activation_sticky))

    def test_sync_none_and_own_not_touched_returns_false(self):
        """回归：姊妹账户没有摸线记录、自己也没摸线时，照常返回False，
        不受这次改动影响。"""
        b = _mk_supervisor("LONG", entry=1380.63, act=1399.10, best=1381.44)
        b._radar_sync_touch_check = MagicMock(return_value=None)
        result = b._latch_radar_activation_sticky(curr_px=1381.44)
        self.assertFalse(result)
        self.assertFalse(bool(b.radar_activation_sticky))

    def test_zero_entry_ref_bypasses_progress_gate_safely(self):
        """边界：watched_entry缺失/为0时(理论上不应发生，但要防御)，
        gate_span算不出来，不应该因为除零或误判而直接拒绝所有互通——
        退化为跳过进度校验，只要sync本身有效就放行。"""
        b = _mk_supervisor("LONG", entry=0.0, act=1399.10, best=1381.44)
        b._radar_sync_touch_check = MagicMock(
            return_value={"acct": "binanceC", "mark": 1404.51}
        )
        with patch.object(psb, "logger"):
            result = b._latch_radar_activation_sticky(curr_px=1381.44)
        self.assertTrue(result, "entry缺失时不应让进度门槛意外拒绝所有互通")

    def test_short_side_progress_measured_symmetrically(self):
        """空单方向对称验证：entry=1400，act(激活线，空头价格更低)=1380，
        己方best只反弹式下探到1398(几乎没挪动，进度2/20=10%)——同样应该
        拒绝借用姊妹账户的摸线状态。"""
        s = _mk_supervisor("SHORT", entry=1400.0, act=1380.0, best=1398.0)
        s._radar_sync_touch_check = MagicMock(
            return_value={"acct": "binanceC", "mark": 1379.0}
        )
        with patch.object(psb, "logger"):
            result = s._latch_radar_activation_sticky(curr_px=1398.0)
        self.assertFalse(result, "空单方向己方进度不足时同样不应借用互通状态")


if __name__ == "__main__":
    unittest.main(verbosity=2)
