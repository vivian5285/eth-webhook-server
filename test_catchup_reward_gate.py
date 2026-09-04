#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
2026-09-04新增：追回前"利润空间还剩多少"闸门(CATCHUP_MIN_REWARD_FRAC)的
回归测试。

背景——宝贝复现：XMRUSDT一根大阳线把价格从TV心跳entry(494.39)拉到527
附近才让多周期EMA确认通过，追回限价刷新6轮后在523.15才成交(比TV原始
entry高28.76，接近TV止损空间33.07那么大)，成交后价格很快回落，雷达
止损焊在保本附近被回撤打掉——TV的利润空间在我们真正进场之前已经被这根
阳线吃掉大半，追了个"入场即接近打平"的仓位，白白磨损。宝贝原话："价格
差很大，就不要心跳追了，因为利润空间少了"。

不碰任何真实账户/持仓，纯粹验证"追回启动前置检查"这一步的开关行为，
用mock接管_place_tv_catchup_limit/_save_state/binance_client等所有
副作用点。
"""
from __future__ import annotations

import os
import sys
import time
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

os.environ["BINANCE_SKIP_BOOTSTRAP"] = "1"
# 2026-09-04：跟其它测试文件一起用`py -m unittest a b c`同进程跑时，
# sys.modules["binance_client"]是所有测试文件共享的同一个对象——谁先
# import谁的mock就留下来，后面文件如果只在自己的局部变量里塞一个新
# MagicMock而不回读sys.modules里已经有的那个，配置的get_current_price
# 永远生效不到radar_reentry_mixin.py内部的`from binance_client import
# binance_client`拿到的对象上(那个是先跑的文件留下的旧mock)。用
# setdefault的返回值(不是自己新建的那个)才是后续代码真正会用到的对象。
_fake_bc = sys.modules.setdefault("binance_client", MagicMock())
_fake_bc.binance_client = MagicMock()
_fake_bc.is_position_query_failed = lambda x: False
_fake_bc.is_orders_query_failed = lambda x: False
sys.modules.setdefault("dingtalk", MagicMock())

import position_supervisor_binance as psb  # noqa: E402
from radar_reentry_mixin import CATCHUP_MIN_REWARD_FRAC  # noqa: E402


def _mk_supervisor(**overrides):
    with patch.object(psb.PositionSupervisorBinance, "__init__", lambda self, *a, **k: None):
        s = psb.PositionSupervisorBinance()
    s.symbol = "XMRUSDT"
    now = time.time()
    # 前面几道闸门(过期/暂停/重入中/漏单宽限/同一事件/永久硬止损/多周期
    # 确认/并发上限)全部设成"放行"状态，只测试新加的这一道。
    s.tv_heartbeat_side = "LONG"
    s.tv_heartbeat_ts = now
    s.trading_paused = False
    s.reentry_active = False
    s._chase_watch_active = False
    s._tv_gap_first_seen_ts = now - 300  # 早于180秒宽限期
    s.last_tv_signal = {"action": "LONG"}  # 不是CLOSE前缀
    s.last_hard_sl_exit_ts = 0.0
    s._catchup_episode_resolved = False
    s._catchup_episode_side = None
    s._catchup_episode_entry = 0.0
    s._catchup_reward_blocked_alerted = False
    s._catchup_capacity_blocked_alerted = False
    s._multi_tf_trend_confirmed = MagicMock(return_value=True)
    s._count_active_catchup_siblings = MagicMock(return_value=0)
    s._tv_heartbeat_stale_sec = MagicMock(return_value=600)
    s._maybe_notify_catchup_watch_expired = MagicMock()
    s._save_state = MagicMock()
    s._place_tv_catchup_limit = MagicMock(return_value=True)
    s._dingtalk = MagicMock()
    for k, v in overrides.items():
        setattr(s, k, v)
    return s


class TestCatchupRewardGate(unittest.TestCase):
    def test_xmrusdt_real_incident_blocks_catchup(self):
        """XMRUSDT真实复现数值：TV.entry=494.39, TV.tp1按平常涨幅估算给
        一个合理值(比如530, 距entry 35.61)，当前价已经涨到523.15——
        remaining=|530-523.15|=6.85, original=35.61, frac≈0.19 < 0.4门槛
        → 必须拒绝启动追回。"""
        s = _mk_supervisor(
            tv_heartbeat_entry=494.39,
            tv_heartbeat_stop=461.32,
            tv_heartbeat_tp1=530.0,
        )
        _fake_bc.binance_client.get_current_price = MagicMock(return_value=523.15)
        s._maybe_start_tv_heartbeat_catchup()
        s._place_tv_catchup_limit.assert_not_called()
        s._save_state.assert_not_called()
        self.assertTrue(s._catchup_reward_blocked_alerted)

    def test_fresh_gap_allows_catchup(self):
        """价差还很小(刚形成漏单，价格没怎么跑)时，利润空间接近100%，
        必须正常放行到挂单这一步。"""
        s = _mk_supervisor(
            tv_heartbeat_entry=494.39,
            tv_heartbeat_stop=461.32,
            tv_heartbeat_tp1=530.0,
        )
        _fake_bc.binance_client.get_current_price = MagicMock(return_value=496.0)
        s._maybe_start_tv_heartbeat_catchup()
        s._place_tv_catchup_limit.assert_called_once()

    def test_missing_tp1_does_not_block(self):
        """tv_heartbeat_tp1缺失(<=0)时不设限，不能因为数据缺失误伤，
        必须正常放行。"""
        s = _mk_supervisor(
            tv_heartbeat_entry=494.39,
            tv_heartbeat_stop=461.32,
            tv_heartbeat_tp1=0.0,
        )
        _fake_bc.binance_client.get_current_price = MagicMock(return_value=523.15)
        s._maybe_start_tv_heartbeat_catchup()
        s._place_tv_catchup_limit.assert_called_once()

    def test_recovering_reward_frac_clears_alert_flag(self):
        """价格回落、利润空间恢复到门槛以上时，去重标记必须清掉，不能
        被上一次的拦截标记永久压住导致以后拦截了也不再提醒。"""
        s = _mk_supervisor(
            tv_heartbeat_entry=494.39,
            tv_heartbeat_stop=461.32,
            tv_heartbeat_tp1=530.0,
            _catchup_reward_blocked_alerted=True,
        )
        _fake_bc.binance_client.get_current_price = MagicMock(return_value=500.0)
        s._maybe_start_tv_heartbeat_catchup()
        self.assertFalse(s._catchup_reward_blocked_alerted)
        s._place_tv_catchup_limit.assert_called_once()

    def test_exactly_at_threshold_frac_matches_constant(self):
        """健全性检查：门槛常量本身是0.4，构造一个恰好卡在边界两侧的
        场景，确认阈值方向没有反过来(< 拒绝，>= 放行)。"""
        self.assertAlmostEqual(CATCHUP_MIN_REWARD_FRAC, 0.4)
        entry, tp1 = 100.0, 200.0  # original_reward=100
        # frac恰好=0.4 → remaining=40 → curr_px = tp1-40 = 160
        s = _mk_supervisor(tv_heartbeat_entry=entry, tv_heartbeat_stop=90.0, tv_heartbeat_tp1=tp1)
        _fake_bc.binance_client.get_current_price = MagicMock(return_value=160.0)
        s._maybe_start_tv_heartbeat_catchup()
        s._place_tv_catchup_limit.assert_called_once()  # >= 门槛，放行

        s2 = _mk_supervisor(tv_heartbeat_entry=entry, tv_heartbeat_stop=90.0, tv_heartbeat_tp1=tp1)
        _fake_bc.binance_client.get_current_price = MagicMock(return_value=161.0)  # remaining=39<40
        s2._maybe_start_tv_heartbeat_catchup()
        s2._place_tv_catchup_limit.assert_not_called()  # < 门槛，拒绝


if __name__ == "__main__":
    unittest.main(verbosity=2)
