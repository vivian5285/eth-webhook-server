#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
2026-09-04修复(E账户MARIO XMRUSDT实盘复现)：reverse_open_after_flat
("超卖变反向"，_sweep_orphan_reverse_after_flat触发，代表"发生过一次未
预期的方向翻转，需要人工核对是不是真故障")这个trading_paused原因，之前
在两个地方会被无差别静默清除——①重启确认空仓后的批量清除(v16.9.x那条，
本来只该对CLOSE_THEN_OPEN_FAIL_ABORT这类"卡在半途、空仓即安全"的暂停
原因生效)；②下一笔TV新开仓信号到达时的"成功进入开仓路径→解除非人工
中止类暂停"逻辑(sticky名单里本来没有reverse_open_after_flat)。两条路径
都会让"暂停自动化，需人工核对盘口"这句承诺从未真正兑现——2026-09-03
深夜XMRUSDT在E账户上，这条暂停先被无关的服务重启意外清掉，之后没人
真的复核过，仓位空仓挂了5.5小时都没人知道。

用源码切片断言两处guard都在(比起mock整个_close_all_impl/
recover_state_on_startup这种巨型函数、涉及大量交易所调用依赖，切片断言
更轻量、跟test_patience_mode.py里TestRealCloseNotShielded同款纪律)。
"""
from __future__ import annotations

import os
import unittest

ROOT = os.path.dirname(os.path.abspath(__file__))


class TestReverseAfterFlatStaysSticky(unittest.TestCase):
    def setUp(self):
        with open(os.path.join(ROOT, "position_supervisor_binance.py"), encoding="utf-8") as f:
            self.src = f.read()

    def test_restart_path_does_not_blanket_clear_reverse_open_after_flat(self):
        anchor = self.src.index("系统重启点火：REST确认无持仓，账本复位为空仓待命")
        # 往后切到下一个函数定义之前，覆盖v16.9.x那段清除逻辑
        window = self.src[anchor:anchor + 2000]
        self.assertIn("reverse_open_after_flat", window,
                      "重启确认空仓的清除逻辑必须对reverse_open_after_flat单独判断，不能无差别清")
        self.assertIn("保持暂停", window)

    def test_new_open_path_treats_reverse_open_after_flat_as_sticky(self):
        anchor = self.src.index("成功进入开仓路径")
        window = self.src[anchor:anchor + 1200]
        self.assertIn('pause_r.startswith("reverse_open_after_flat")', window,
                      "新开仓解除暂停的sticky名单必须包含reverse_open_after_flat")

    def test_close_all_wrapped_by_non_blocking_lock(self):
        # _close_all本身现在是_close_all_impl的加锁外壳(2026-09-04修复，
        # 见__init__里_close_all_lock字段注释)——防主线程"先平后开"跟哨兵
        # "续追未兑现的强平意图"并发调用同一个_close_all，后者把刚开出来
        # 的合法新仓当成残留意图市价平掉。
        anchor = self.src.index("def _close_all(self, reason=")
        window = self.src[anchor:anchor + 600]
        self.assertIn("_close_all_lock.acquire(blocking=False)", window)
        self.assertIn("_close_all_impl", window)


if __name__ == "__main__":
    unittest.main(verbosity=2)
