#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
2026-09-07新增：_sentinel_loop()"哨兵续追未兑现的强平意图"分支新增的
退避sleep回归测试。

背景——宝贝"老规矩"标准检查实盘复现：_close_all内部有非阻塞的
per-instance锁(2026-09-04 _close_all race/XMR事故那次修复引入)，如果
主流程(比如_ensure_flat_before_open的"先平后开"净场)正持有这把锁，
哨兵这里调用_close_all会立刻"本次退让"原样返回，pending_forced_close
却还没被清掉——原实现调用完_close_all后紧跟着continue，中间完全没有
sleep，导致本轮循环立刻回到顶部重新判断，还是True，再退让，再
continue……零延迟死循环。实盘复现：ZEC/BCH/XAU/GEV/ETH/MU/SKHYNIX/
LITE等多个品种"先平后开"期间，单个symbol几分钟内被打了近1.5万次
一模一样的"哨兵续追...本次退让"，一夜下来8个品种合计超8.6万条——纯
粹空转刷屏(有锁保护，没有造成任何下错单，但白白消耗CPU)。

修复：跟紧接着下面"_lock.acquire超时"分支同款处理——退让后睡0.5秒
再重试，给主流程留出完成净场的时间窗口，不再零延迟空转。

测试策略：真实调用_sentinel_loop()，但把_close_all mock成"调用几次
后才清掉pending_forced_close"(模拟锁被占用、退让几轮后主流程终于
释放)，用一个计数器在到达预定次数后把self.monitoring设False让循环
自然结束(有限次数，不会真的死循环)。同时patch time.sleep验证每次
"退让"分支都会调用0.5秒退避。

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


def _mk_supervisor():
    with patch.object(psb.PositionSupervisorBinance, "__init__", lambda self, *a, **k: None):
        s = psb.PositionSupervisorBinance()
    s.symbol = "ZECUSDT"
    s.monitoring = True
    s.trading_paused = False
    s.pending_forced_close = True
    s.pending_forced_close_reason = "TV开仓·一律先平后开刷新仓位·尝试1/6 · 强制清场"
    s._ensure_price_ws = MagicMock()
    return s


class TestSentinelPendingCloseBackoff(unittest.TestCase):
    def test_pending_close_retry_sleeps_between_retreat_iterations(self):
        """真实复现"本次退让"场景：_close_all每次调用都因为锁被主流程
        占用而原样返回、不清掉pending_forced_close，循环3轮后主流程终于
        释放锁，第4轮才真正清掉——验证退让的3轮里每轮都调用了0.5秒
        退避，不是零延迟空转。"""
        s = _mk_supervisor()
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
