#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
2026-09-05新增：_process_radar_trailing()"够近就不用真的改单"分支的
_last_applied_exchange_sl回归测试。

背景——宝贝反映LITE/MU几个实盘账户平仓时间差异巨大(LITE：C比B晚13
小时；MU：C比B晚4小时+)。排查journalctl完整轨迹确认根因不是行情/
sizing差异，是这道"够近，不用真的改单"分支自己制造的假参照点：

_has_stop_sl_near()只确认"盘口某张单在候选价±2U容差内"，不代表那张单
真的挂在候选价上——它可能还停在几个刻度以前的旧价。候选价随行情缓慢
爬升时，每一步都被判定"够近，不用真的改单"(这个分支本身完全没有调用
任何下单/改单接口，只是纯粹的账本记录)，但修复前的代码会把
_last_applied_exchange_sl直接改写成候选价，而不是"盘口那张单真实的
价格"。后续moved_enough判断专门拿_last_applied_exchange_sl当"交易所
现在挂的是多少"的参照点，参照点跟着候选价一起虚高之后，moved_enough
迟迟判定"没挪够"，真正的改单被无限期拖延——实盘复现：C账户LITEUSDT
2026-09-04 16:33挂的止损单一直到22:30才第一次真的被替换，中间6小时
账本自认为已经涨了2美元以上；MUUSDT同一天更夸张，21:24到次日04:56
中间隔了7.5小时。这6-7.5小时里如果价格真的反手向下打穿"账本自认为"
的止损位置、却打不穿盘口那张滞后的真实止损单，仓位就会带着一个自己
都不知道的、比预期宽得多的实际止损空间继续裸奔。

修复：这个分支本来就没有真的改单，_last_applied_exchange_sl不该在
这里跟着候选价走，必须继续保留"上一次真的成功改单"时的价格。

不碰任何真实账户/持仓，通过重度mock驱动_process_radar_trailing()
走到这个具体分支来验证。
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


def _mk_supervisor(**overrides):
    with patch.object(psb.PositionSupervisorBinance, "__init__", lambda self, *a, **k: None):
        s = psb.PositionSupervisorBinance()
    s.symbol = "LITEUSDT"
    s.current_side = "LONG"
    s.watched_qty = 0.08
    s.initial_qty = 0.0
    s.breath_profile = None
    s._breath_tick_paused = False
    s._radar_placement_blocked = MagicMock(return_value=False)
    s._resolve_live_qty = MagicMock(return_value=0.08)
    s._pipeline_radar_update = MagicMock()
    s._radar_is_dormant = MagicMock(return_value=False)
    s._should_radar_trail = MagicMock(return_value=True)
    s._reconcile_tp_consumed_from_live_qty = MagicMock()
    s._get_active_position = MagicMock(return_value=None)
    s._can_safely_place_radar_sl = MagicMock(return_value=True)
    s._in_radar_runner_zone = MagicMock(return_value=False)
    s._stop_buffer_usd = MagicMock(return_value=0.3)
    s._radar_activation_notified = True
    s._report_breath_phase2 = MagicMock()
    for k, v in overrides.items():
        setattr(s, k, v)
    return s


class TestNearSkipDoesNotPhantomSyncLastApplied(unittest.TestCase):
    def test_real_incident_lite_near_skip_keeps_last_applied_at_true_exchange_price(self):
        """精确复现实盘LITEUSDT数值：真实盘口挂单价872.28，候选目标874.33
        (差2.05，仍在_has_stop_sl_near判定的"够近"范围内，因为mock直接
        控制其返回True来模拟这个场景)。ledger(current_sl)可以照常前进到
        874.33，但_last_applied_exchange_sl必须继续停在872.28这个真实
        挂单价，不能被候选价874.33污染。"""
        s = _mk_supervisor(
            current_sl=872.58,
            _last_applied_exchange_sl=872.28,
        )
        s._apply_breath_stop_tick = MagicMock(return_value={
            "stop": 874.33, "phase_entered": False, "breakeven_phase": True,
        })
        s._clamp_radar_sl_for_market = MagicMock(return_value=874.33)
        s._has_stop_sl_near = MagicMock(return_value=True)
        with patch.object(psb, "order_stop_price", side_effect=lambda side, px, **kw: px):
            result = s._process_radar_trailing(0.08, 880.0)
        self.assertFalse(result)
        # 核心断言：真实挂单价参照点必须原封不动，不能被候选价874.33污染
        self.assertAlmostEqual(s._last_applied_exchange_sl, 872.28, places=2)
        # 账本自己的"该挂在哪"记忆可以照常前进(这部分逻辑本来就是对的)
        self.assertAlmostEqual(s.current_sl, 874.33, places=2)

    def test_far_from_real_order_still_advances_last_applied_via_real_replace_path(self):
        """候选价真的比盘口现有单远(超过容差)时，走的是下面真正改单的
        分支(_has_stop_sl_near=False)，_last_applied_exchange_sl当然
        应该更新——这条不受本次修复影响，用来对照确认修复没有误伤正常
        的真实改单路径。"""
        s = _mk_supervisor(
            current_sl=872.58,
            _last_applied_exchange_sl=872.28,
        )
        s._apply_breath_stop_tick = MagicMock(return_value={
            "stop": 878.0, "phase_entered": False, "breakeven_phase": True,
        })
        s._clamp_radar_sl_for_market = MagicMock(return_value=878.0)
        s._has_stop_sl_near = MagicMock(return_value=False)
        s._realign_radar_defenses = MagicMock(return_value=True)
        s._save_state = MagicMock()
        s._ladder_label_last = ""
        s.watched_entry = 866.75
        s._log_radar_update = MagicMock()
        s._cancel_stale_tp_beyond_radar = MagicMock()
        s._report_radar_trail_update = MagicMock()
        s._report_radar_intervention = MagicMock()
        with patch.object(psb, "order_stop_price", side_effect=lambda side, px, **kw: px):
            s._process_radar_trailing(0.08, 880.0)
        # 真实改单路径被调用，说明修复没有误伤"候选价真的偏离够远"的场景
        s._realign_radar_defenses.assert_called_once()

    def test_regressed_target_within_tolerance_still_pins_last_applied(self):
        """候选价比账本还低(棘轮拒绝倒退)、但仍在盘口现有单容差内的场景：
        current_sl保留原值(既有行为)，_last_applied_exchange_sl同样必须
        保留真实挂单价，不能被(哪怕是被拒绝的)候选价污染。"""
        s = _mk_supervisor(
            current_sl=874.33,
            _last_applied_exchange_sl=872.28,
        )
        s._apply_breath_stop_tick = MagicMock(return_value={
            "stop": 873.59, "phase_entered": False, "breakeven_phase": True,
        })
        s._clamp_radar_sl_for_market = MagicMock(return_value=873.59)
        s._has_stop_sl_near = MagicMock(return_value=True)
        with patch.object(psb, "order_stop_price", side_effect=lambda side, px, **kw: px):
            s._process_radar_trailing(0.08, 880.0)
        self.assertAlmostEqual(s.current_sl, 874.33, places=2)  # 棘轮：保留原值
        self.assertAlmostEqual(s._last_applied_exchange_sl, 872.28, places=2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
