#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
2026-09-15：_maybe_start_tv_heartbeat_catchup()新增的45分钟双均线+3根
同向+放量"快速通道"回归测试——跟原有5m/15m/30m EMA+动量确认(
_multi_tf_trend_confirmed)并行、任一满足即放行。

背景——今天OPENAI在B账户被误判止损(保本激活加宽第三版修正之前的旧
bug)后TV其实还在持有，5m/15m/30m多周期确认反复未通过，从04:04一路
卡到重启前(04:36)账户始终空仓。新增的这条快速通道用更强的技术信号
(45分钟双均线+最近3根同向实体+放量)作为平行判据，任一满足即可启动
追回，不改动原有确认逻辑本身，也不影响episode去重/并发上限/利润空间
这几道既有闸门。

不碰任何真实账户/持仓，纯粹验证这一步的开关行为，mock接管
_place_tv_catchup_limit/_save_state/binance_client等所有副作用点。
"""
from __future__ import annotations

import os
import sys
import time
import unittest
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

os.environ["BINANCE_SKIP_BOOTSTRAP"] = "1"
_fake_bc = sys.modules.setdefault("binance_client", MagicMock())
_fake_bc.binance_client = MagicMock()
_fake_bc.is_position_query_failed = lambda x: False
_fake_bc.is_orders_query_failed = lambda x: False
sys.modules.setdefault("dingtalk", MagicMock())

import position_supervisor_binance as psb  # noqa: E402
from radar_reentry_mixin import TREND_REENTRY_MAX_PER_DAY  # noqa: E402


def _mk_supervisor(**overrides):
    from unittest.mock import patch
    with patch.object(psb.PositionSupervisorBinance, "__init__", lambda self, *a, **k: None):
        s = psb.PositionSupervisorBinance()
    s.symbol = "OPENAIUSDT"
    now = time.time()
    s.tv_heartbeat_side = "LONG"
    s.tv_heartbeat_ts = now
    s.tv_heartbeat_entry = 1402.64
    s.tv_heartbeat_stop = 1379.70
    s.tv_heartbeat_tp1 = 1425.47
    s.trading_paused = False
    s.reentry_active = False
    s._chase_watch_active = False
    s._tv_gap_first_seen_ts = now - 300
    s.last_tv_signal = {"action": "LONG"}
    s.last_hard_sl_exit_ts = 0.0
    s._catchup_episode_resolved = False
    s._catchup_episode_side = None
    s._catchup_episode_entry = 0.0
    s._catchup_reward_blocked_alerted = False
    s._catchup_capacity_blocked_alerted = False
    s._count_active_catchup_siblings = MagicMock(return_value=0)
    s._tv_heartbeat_stale_sec = MagicMock(return_value=600)
    s._maybe_notify_catchup_watch_expired = MagicMock()
    s._save_state = MagicMock()
    s._place_tv_catchup_limit = MagicMock(return_value=True)
    s._dingtalk = MagicMock()
    s._trend_reentry_daily = {"date": "", "count": 0}
    _fake_bc.binance_client.get_current_price = MagicMock(return_value=1403.0)
    for k, v in overrides.items():
        setattr(s, k, v)
    return s


class TestTrendVolumeFastPath(unittest.TestCase):
    def test_multi_tf_fails_but_volume_gate_confirms_starts_catchup(self):
        """核心场景：5m/15m/30m未确认，但45分钟双均线+3根同向+放量确认
        通过——应该照样启动追回。"""
        s = _mk_supervisor()
        s._multi_tf_trend_confirmed = MagicMock(return_value=False)
        s._trend_confirmed_with_volume_gate = MagicMock(
            return_value=(True, {"reason": "confirmed"})
        )

        s._maybe_start_tv_heartbeat_catchup()

        s._trend_confirmed_with_volume_gate.assert_called_once_with("LONG")
        s._place_tv_catchup_limit.assert_called_once()
        self.assertTrue(s._save_state.called)

    def test_multi_tf_confirmed_does_not_need_volume_gate(self):
        """5m/15m/30m本来就确认通过时，不该多此一举去跑新的放量快速
        通道(短路求值，也不占用它的每日上限)。"""
        s = _mk_supervisor()
        s._multi_tf_trend_confirmed = MagicMock(return_value=True)
        s._trend_confirmed_with_volume_gate = MagicMock()

        s._maybe_start_tv_heartbeat_catchup()

        s._trend_confirmed_with_volume_gate.assert_not_called()
        s._place_tv_catchup_limit.assert_called_once()

    def test_both_gates_fail_no_catchup(self):
        s = _mk_supervisor()
        s._multi_tf_trend_confirmed = MagicMock(return_value=False)
        s._trend_confirmed_with_volume_gate = MagicMock(
            return_value=(False, {"reason": "volume_not_confirmed"})
        )

        s._maybe_start_tv_heartbeat_catchup()

        s._place_tv_catchup_limit.assert_not_called()

    def test_volume_fastpath_daily_cap_blocks_fourth(self):
        """快速通道套用跟_maybe_start_trend_reentry同一个每日上限——
        第4次应该被拒绝，不启动追回。5m/15m/30m确认通过时不受这个上限
        影响(不同分支)。"""
        s = _mk_supervisor()
        s._multi_tf_trend_confirmed = MagicMock(return_value=False)
        s._trend_confirmed_with_volume_gate = MagicMock(
            return_value=(True, {"reason": "confirmed"})
        )

        for i in range(TREND_REENTRY_MAX_PER_DAY):
            s._catchup_episode_resolved = False  # 每次都是"新事件"，绕开episode去重
            s._maybe_start_tv_heartbeat_catchup()
        self.assertEqual(s._place_tv_catchup_limit.call_count, TREND_REENTRY_MAX_PER_DAY)

        s._catchup_episode_resolved = False
        s._maybe_start_tv_heartbeat_catchup()
        self.assertEqual(
            s._place_tv_catchup_limit.call_count, TREND_REENTRY_MAX_PER_DAY,
            "第4次应该被每日上限拦下",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
