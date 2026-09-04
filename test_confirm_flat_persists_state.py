#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
2026-09-04新增：_confirm_position_flat()的"确认平仓·清stale本地状态"分支
漏了_save_state()的回归测试。

背景——宝贝复现：BNBUSDT在C/E两个真实账户，止损/雷达先后触发平仓，日志
明明打出"🧹 [BNBUSDT] 雷达/防线账本已清零 | confirm_flat_stale_clean"，
但state json文件的mtime停在清零之前那一刻——内存状态是对的(后续追回/
巡检逻辑照常按真实空仓走)，落盘的却还是旧的LONG快照。查实：全仓库其它
每一处调用_reset_breath_ledger_on_flat()的地方(蚂蚁仓扫尾/重启对账补发
收网/感知空仓等)都在紧接着调用_save_state()，唯独_confirm_position_flat
这条"REST复核确认交易所已空仓、账本却还是有仓"的清理路径漏了。

不碰任何真实账户/持仓，纯粹验证"确认空仓后必须落盘"这一步，mock掉
_live_position_qty/_reset_breath_ledger_on_flat/_purge_all_defense_
orders_on_flat/_build_adverse_extreme_hint/_save_state这几个有副作用
的点。
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


def _mk_supervisor(live_qty):
    with patch.object(psb.PositionSupervisorBinance, "__init__", lambda self, *a, **k: None):
        s = psb.PositionSupervisorBinance()
    s.symbol = "BNBUSDT"
    s.dust_qty = 0.0001
    s.watched_qty = 0.13
    s.current_side = "LONG"
    s._live_position_qty = MagicMock(return_value=live_qty)
    s._build_adverse_extreme_hint = MagicMock(return_value=None)
    s._reset_breath_ledger_on_flat = MagicMock()
    s._purge_all_defense_orders_on_flat = MagicMock()
    s._save_state = MagicMock()
    return s


class TestConfirmFlatPersistsState(unittest.TestCase):
    def test_confirmed_flat_with_stale_book_saves_state(self):
        """BNBUSDT真实复现场景：交易所已空仓(live_qty=0)，账本还记着
        LONG——清账本之后必须调用_save_state()，不能只清内存。"""
        s = _mk_supervisor(live_qty=0.0)
        confirmed = s._confirm_position_flat(retries=1, delay=0)
        self.assertTrue(confirmed)
        s._reset_breath_ledger_on_flat.assert_called_once_with(
            source="confirm_flat_stale_clean"
        )
        s._save_state.assert_called_once()

    def test_confirmed_flat_with_clean_book_does_not_over_save(self):
        """账本本来就是空仓状态(current_side=None)时，_book_thinks_active
        为False，不该走清理分支，也就不该调_save_state（避免每次巡检
        空仓品种都空转写盘）。"""
        s = _mk_supervisor(live_qty=0.0)
        s.watched_qty = 0.0
        s.current_side = None
        confirmed = s._confirm_position_flat(retries=1, delay=0)
        self.assertTrue(confirmed)
        s._reset_breath_ledger_on_flat.assert_not_called()
        s._save_state.assert_not_called()

    def test_not_confirmed_flat_does_not_save(self):
        """交易所仍有真实持仓(live_qty>dust)时，不能确认平仓，不该清
        账本也不该落盘。"""
        s = _mk_supervisor(live_qty=0.13)
        confirmed = s._confirm_position_flat(retries=1, delay=0)
        self.assertFalse(confirmed)
        s._reset_breath_ledger_on_flat.assert_not_called()
        s._save_state.assert_not_called()

    def test_query_failed_fails_closed_no_save(self):
        """REST查询失败(None)必须fail-closed：不确认空仓、不清账本、
        不落盘，避免误清一个其实还在的仓位。"""
        s = _mk_supervisor(live_qty=None)
        confirmed = s._confirm_position_flat(retries=1, delay=0)
        self.assertFalse(confirmed)
        s._reset_breath_ledger_on_flat.assert_not_called()
        s._save_state.assert_not_called()

    def test_save_state_exception_does_not_break_confirm(self):
        """_save_state()本身抛异常也不该影响confirmed的返回值——落盘
        失败不该让"交易所已经确认空仓"这个判断本身变得不可信。"""
        s = _mk_supervisor(live_qty=0.0)
        s._save_state = MagicMock(side_effect=RuntimeError("disk full"))
        confirmed = s._confirm_position_flat(retries=1, delay=0)
        self.assertTrue(confirmed)


if __name__ == "__main__":
    unittest.main(verbosity=2)
