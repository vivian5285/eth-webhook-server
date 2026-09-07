#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
2026-09-07新增：_sentinel_loop()"哨兵续追未兑现的强平意图"分支新增的
退避sleep + 新鲜度门槛回归测试。

背景一——宝贝"老规矩"标准检查实盘复现：_close_all内部有非阻塞的
per-instance锁(2026-09-04 _close_all race/XMR事故那次修复引入)，如果
主流程(比如_ensure_flat_before_open的"先平后开"净场)正持有这把锁，
哨兵这里调用_close_all会立刻"本次退让"原样返回，pending_forced_close
却还没被清掉——原实现调用完_close_all后紧跟着continue，中间完全没有
sleep，导致本轮循环立刻回到顶部重新判断，还是True，再退让，再
continue……零延迟死循环。实盘复现：ZEC/BCH/XAU/GEV/ETH/MU/SKHYNIX/
LITE等多个品种"先平后开"期间，单个symbol几分钟内被打了近1.5万次
一模一样的"哨兵续追...本次退让"，一夜下来8个品种合计超8.6万条——纯
粹空转刷屏(有锁保护，没有造成任何下错单，但白白消耗CPU)。第一次修复
(2026-09-07早)：跟紧接着下面"_lock.acquire超时"分支同款处理——退让
后睡0.5秒再重试，给主流程留出完成净场的时间窗口，不再零延迟空转。

背景二——同一天下午宝贝进一步反馈GSUSDT实盘复现：TV信号到最终开仓
完成耗时72秒，其中21秒(42次退让×0.5秒)被这里的续追空转占用——第一次
修复只解决了"零延迟"，没解决"压根不该在这个时间点去抢"：
pending_forced_close是_close_all_impl一进函数就无条件打上的("必须
平掉"意图落盘防崩溃丢单)，完全不区分"刚打上、主流程正常在忙"还是
"真的卡死很久没人管"。第二次修复：加一道新鲜度门槛
(PENDING_FORCED_CLOSE_SENTINEL_GRACE_SEC=150秒，略高于
_ensure_flat_before_open自己文档记载的~145秒worst case重试预算)——
意图挂起时间没到这个门槛，哨兵完全让位(不打日志、不调用_close_all、
只睡2秒再回来复查)；超过门槛才认定"大概率真的没人管了"，出手续追
(仍然是修复一里的0.5秒退避版本)。

测试策略：真实调用_sentinel_loop()，用pending_forced_close_started_ts
控制"新鲜"还是"过期"；把_close_all mock成"调用几次后才清掉
pending_forced_close"，用计数器/monitoring=False让循环在有限次数内
自然结束，不会真的死循环。

不碰任何真实账户/持仓，纯粹验证这一段状态机逻辑。
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

# 2026-09-07修复：不依赖模块级共享mock的当前状态——组合跑多个测试文件
# 时，其它文件可能在导入期或用例执行期改过同一个共享binance_client
# mock对象的ip_rate_limit_remaining，如果这里读到的是非0值，
# _sentinel_loop会走进"IP限流冷却"分支真睡15~600秒，把测试拖成假死。
# 每个测试方法内用patch.object局部锁定成0，不依赖导入时的全局状态。


def _mk_supervisor(pending_age_sec=200.0):
    """pending_age_sec: pending_forced_close_started_ts距现在多少秒——
    默认200秒(超过150秒门槛)，模拟"真的卡了很久没人管"的场景；测试
    "新鲜、该让位"场景时传一个小于150的值。"""
    with patch.object(psb.PositionSupervisorBinance, "__init__", lambda self, *a, **k: None):
        s = psb.PositionSupervisorBinance()
    s.symbol = "ZECUSDT"
    s.monitoring = True
    s.trading_paused = False
    s.pending_forced_close = True
    s.pending_forced_close_reason = "TV开仓·一律先平后开刷新仓位·尝试1/6 · 强制清场"
    s.pending_forced_close_started_ts = psb.time.time() - pending_age_sec
    s._ensure_price_ws = MagicMock()
    return s


class TestSentinelPendingCloseBackoff(unittest.TestCase):
    def test_pending_close_retry_sleeps_between_retreat_iterations(self):
        """真实复现"本次退让"场景(意图已经挂起200秒，超过150秒门槛，
        哨兵判定"大概率没人管了"出手)：_close_all每次调用都因为锁被
        主流程占用而原样返回、不清掉pending_forced_close，循环3轮后
        主流程终于释放锁，第4轮才真正清掉——验证退让的3轮里每轮都
        调用了0.5秒退避，不是零延迟空转。"""
        s = _mk_supervisor(pending_age_sec=200.0)
        call_count = {"n": 0}

        def fake_close_all(reason, reset_state):
            call_count["n"] += 1
            if call_count["n"] >= 4:
                # 第4次：模拟主流程终于释放锁，_close_all真正执行完成，
                # 清掉pending_forced_close并停止循环(用monitoring=False
                # 让while循环自然结束，避免测试真的死循环)。
                s.pending_forced_close = False
                s.monitoring = False
            return True

        s._close_all = MagicMock(side_effect=fake_close_all)

        with patch.object(psb.time, "sleep") as mock_sleep, \
                patch.object(psb.binance_client, "ip_rate_limit_remaining", return_value=0):
            s._sentinel_loop()

        self.assertEqual(call_count["n"], 4, "应该退让3轮+第4轮成功清掉，共调用4次_close_all")
        # 前3轮"退让"都应该sleep(0.5)；第4轮清掉后monitoring=False，
        # while循环在sleep之后仍会执行到continue但下一轮判断为False退出，
        # 所以sleep总共至少调用了3次(退让轮)，不应该是0次(零延迟死循环)。
        sleep_calls = [c for c in mock_sleep.call_args_list if c.args == (0.5,)]
        self.assertGreaterEqual(
            len(sleep_calls), 3,
            "每一轮'本次退让'之后都必须sleep(0.5)，不能零延迟立刻continue空转",
        )

    def test_fresh_pending_close_does_not_retry_at_all(self):
        """GSUSDT实盘复现：意图刚打上没几秒(主流程正常在"先平后开"净场
        中)——哨兵完全不应该调用_close_all，静默睡2秒后继续等，不产生
        任何"续追/退让"日志。用call_count在几轮之后停掉monitoring，
        避免测试真的死循环。"""
        s = _mk_supervisor(pending_age_sec=5.0)  # 远低于150秒门槛
        tick_count = {"n": 0}
        real_sleep = psb.time.sleep

        def fake_sleep(sec):
            tick_count["n"] += 1
            if tick_count["n"] >= 3:
                s.monitoring = False

        s._close_all = MagicMock()

        with patch.object(psb.time, "sleep", side_effect=fake_sleep) as mock_sleep, \
                patch.object(psb.binance_client, "ip_rate_limit_remaining", return_value=0):
            s._sentinel_loop()

        s._close_all.assert_not_called()
        sleep_2s_calls = [c for c in mock_sleep.call_args_list if c.args == (2.0,)]
        self.assertGreaterEqual(
            len(sleep_2s_calls), 1,
            "意图新鲜时应该静默sleep(2.0)让位，不去抢锁",
        )

    def test_pending_close_becomes_stale_and_finally_retried(self):
        """新鲜度门槛的边界行为：意图打上时还新鲜，随时间推移"变旧"
        (模拟真实世界里主流程迟迟没有完成)，一旦超过150秒门槛，哨兵
        才开始真正续追——用一个会递增的fake now模拟时间流逝。"""
        s = _mk_supervisor(pending_age_sec=0.0)  # 刚打上，此刻还新鲜
        fake_now = {"t": s.pending_forced_close_started_ts}
        tick_count = {"n": 0}

        def fake_time():
            return fake_now["t"]

        def fake_sleep(sec):
            tick_count["n"] += 1
            # 每次sleep后快进时间——新鲜阶段快进较小步长，超过门槛后
            # 下一轮就该被真实续追命中。
            fake_now["t"] += 80.0
            if tick_count["n"] >= 5:
                s.monitoring = False  # 保险丝，防止意外死循环

        s._close_all = MagicMock(side_effect=lambda reason, reset_state: (
            setattr(s, "pending_forced_close", False),
            setattr(s, "monitoring", False),
        ))

        with patch.object(psb.time, "time", side_effect=fake_time), \
                patch.object(psb.time, "sleep", side_effect=fake_sleep), \
                patch.object(psb.binance_client, "ip_rate_limit_remaining", return_value=0):
            s._sentinel_loop()

        # 前面至少有一轮是"新鲜、让位"(sleep 2.0)，之后时间快进超过150秒
        # 门槛，_close_all才被真正调用一次并清掉意图结束循环。
        s._close_all.assert_called_once()


if __name__ == "__main__":
    unittest.main(verbosity=2)
