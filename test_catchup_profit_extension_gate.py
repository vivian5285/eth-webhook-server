#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
2026-09-09新增：追回"深度浮盈/明显超涨超跌不追"闸门
(CATCHUP_MAX_PROFIT_EXTENSION_MULT)的回归测试。

背景——宝贝ZECUSDT实盘复现：晚上看ZEC涨了很多，怕突然回调利润回吐，
在交易所把盈利的多单手动平仓了；TV自己的心跳流还没跟上(还在报老的
entry/LONG)，VPS这边的"TV心跳追回"机制误把这判成"技术性漏单"，拿已经
在高位的现价重新追了回去——之前也出现过同类情况。既有的
CATCHUP_MIN_REWARD_FRAC闸门(见test_catchup_reward_gate.py)只看"到
TV.tp1还剩多少空间"，ZEC这类品种tp1本来就设得比较远，深度浮盈之后到
tp1可能仍有余量，那道闸门拦不住。宝贝原话："一个是趋势坏了、硬止损
不重入；一个是盈利太多出局的，那时候虽然指标看起来还没走坏趋势，但
随时有反转可能性，也不适合重入，应该耐心等待下次TV开仓说话，而不是
自己在不利于我们方向的价格再次自己做主开单"。

不碰任何真实账户/持仓，纯粹验证"追回启动前置检查"这一步的开关行为，
用mock接管_place_tv_catchup_limit/_save_state/binance_client等所有
副作用点，跟test_catchup_reward_gate.py同一套mock helper。
"""
from __future__ import annotations

import os
import sys
import time
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
from radar_reentry_mixin import CATCHUP_MAX_PROFIT_EXTENSION_MULT  # noqa: E402


def _mk_supervisor(**overrides):
    with patch.object(psb.PositionSupervisorBinance, "__init__", lambda self, *a, **k: None):
        s = psb.PositionSupervisorBinance()
    s.symbol = "ZECUSDT"
    now = time.time()
    # 前面几道闸门(过期/暂停/重入中/漏单宽限/同一事件/永久硬止损/多周期
    # 确认/并发上限)全部设成"放行"状态，只测试新加的这一道。
    s.tv_heartbeat_side = "LONG"
    s.tv_heartbeat_ts = now
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
    s._catchup_extension_blocked_alerted = False
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


class TestCatchupProfitExtensionGate(unittest.TestCase):
    def test_zec_style_deep_profit_blocks_catchup_even_with_ample_reward_space(self):
        """ZEC实盘复现的核心场景：entry=100 stop=90(止损空间10)，
        tp1=300设得很远——现价涨到125(顺向跑出25，超过止损空间10的
        1.5倍=15)。先确认既有的reward闸门本身会放行(到tp1空间还剩
        175/200=87.5%，远高于40%门槛)，证明如果没有这道新闸门，追回会
        照旧启动；加上新闸门后，必须被拦住，不启动追回。"""
        s = _mk_supervisor(
            tv_heartbeat_entry=100.0,
            tv_heartbeat_stop=90.0,
            tv_heartbeat_tp1=300.0,
        )
        _fake_bc.binance_client.get_current_price = MagicMock(return_value=125.0)
        s._maybe_start_tv_heartbeat_catchup()
        s._place_tv_catchup_limit.assert_not_called()
        s._save_state.assert_not_called()
        self.assertTrue(s._catchup_extension_blocked_alerted)

    def test_short_side_symmetric_deep_profit_blocks_catchup(self):
        """空单方向对称验证：entry=1000 stop=1010(止损空间10)，
        tp1=700(远)，现价跌到975(顺向跑出25>15阈值) → 同样应该拦住。"""
        s = _mk_supervisor(
            tv_heartbeat_side="SHORT",
            tv_heartbeat_entry=1000.0,
            tv_heartbeat_stop=1010.0,
            tv_heartbeat_tp1=700.0,
        )
        _fake_bc.binance_client.get_current_price = MagicMock(return_value=975.0)
        s._maybe_start_tv_heartbeat_catchup()
        s._place_tv_catchup_limit.assert_not_called()
        self.assertTrue(s._catchup_extension_blocked_alerted)

    def test_fresh_small_gap_still_allowed(self):
        """回归——这个函数最初要解决的场景：信号刚发出几分钟内的技术性
        漏单，现价离entry通常还很近，不该被这道新闸门误伤。"""
        s = _mk_supervisor(
            tv_heartbeat_entry=100.0,
            tv_heartbeat_stop=90.0,
            tv_heartbeat_tp1=300.0,
        )
        _fake_bc.binance_client.get_current_price = MagicMock(return_value=102.0)
        s._maybe_start_tv_heartbeat_catchup()
        s._place_tv_catchup_limit.assert_called_once()

    def test_exactly_at_threshold_boundary(self):
        """边界：止损空间10，阈值1.5倍=15。顺向跑出恰好15(不超过) →
        放行；跑出15.01(超过) → 拦住。跟测试常量本身的1.5倍对上，不是
        写死数字。"""
        self.assertAlmostEqual(CATCHUP_MAX_PROFIT_EXTENSION_MULT, 1.5)
        s = _mk_supervisor(
            tv_heartbeat_entry=100.0, tv_heartbeat_stop=90.0, tv_heartbeat_tp1=300.0,
        )
        _fake_bc.binance_client.get_current_price = MagicMock(return_value=115.0)  # 跑出15
        s._maybe_start_tv_heartbeat_catchup()
        s._place_tv_catchup_limit.assert_called_once()  # 恰好等于阈值，不算"超过"，放行

        s2 = _mk_supervisor(
            tv_heartbeat_entry=100.0, tv_heartbeat_stop=90.0, tv_heartbeat_tp1=300.0,
        )
        _fake_bc.binance_client.get_current_price = MagicMock(return_value=115.01)  # 跑出15.01
        s2._maybe_start_tv_heartbeat_catchup()
        s2._place_tv_catchup_limit.assert_not_called()

    def test_recovering_price_clears_alert_flag(self):
        """价格回落到阈值以内时，去重标记必须清掉，不能被上一次的拦截
        标记永久压住导致以后拦截了也不再提醒。"""
        s = _mk_supervisor(
            tv_heartbeat_entry=100.0,
            tv_heartbeat_stop=90.0,
            tv_heartbeat_tp1=300.0,
            _catchup_extension_blocked_alerted=True,
        )
        _fake_bc.binance_client.get_current_price = MagicMock(return_value=105.0)
        s._maybe_start_tv_heartbeat_catchup()
        self.assertFalse(s._catchup_extension_blocked_alerted)
        s._place_tv_catchup_limit.assert_called_once()

    def test_missing_stop_or_current_price_does_not_block(self):
        """tv_heartbeat_stop缺失(<=0)时函数在更早的位置就直接返回(见
        hb_entry<=0 or hb_stop<=0那道既有检查)，不会走到这道新闸门；
        current_price查询失败(0)时新闸门的hb_dist>0 and curr_px>0前提
        不成立，不设限，不能因为数据缺失误伤，必须正常放行。"""
        s = _mk_supervisor(
            tv_heartbeat_entry=100.0,
            tv_heartbeat_stop=90.0,
            tv_heartbeat_tp1=0.0,  # 顺带验证tp1缺失的既有行为不受影响
        )
        _fake_bc.binance_client.get_current_price = MagicMock(return_value=0.0)
        s._maybe_start_tv_heartbeat_catchup()
        s._place_tv_catchup_limit.assert_called_once()


if __name__ == "__main__":
    unittest.main(verbosity=2)
