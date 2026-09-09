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


class TestConfirmFlatFreshSmallGuard(unittest.TestCase):
    """2026-09-09新增：ANTHROPICUSDT实盘复现——TV心跳追回(_finalize_tv_
    catchup_fill)刚成交、刚挂好硬止损的真实仓位(0.02-0.03，追回市价兜底
    按0.7倍逐轮打折出来的正常仓位大小)被_confirm_position_flat误判"确认
    平仓"(因为0.02 <= ANTHROPIC的dust_qty=0.05)，随即被下一轮空闲巡检
    当蚂蚁仓扫平——刚追回成交几分钟内又变回"TV要LONG、VPS空仓"的漏单
    缺口。跟_should_finalize_tp_victory里2026-08-18那次"_fresh_small"
    同一类坑，这里补上同款防线的回归测试。"""

    def _mk(self, live_qty, initial_qty, watched_qty=None, consumed=None):
        with patch.object(psb.PositionSupervisorBinance, "__init__", lambda self, *a, **k: None):
            s = psb.PositionSupervisorBinance()
        s.symbol = "ANTHROPICUSDT"
        s.dust_qty = 0.05  # ANTHROPIC真实配置值
        s.initial_qty = initial_qty
        s.watched_qty = watched_qty if watched_qty is not None else initial_qty
        s.current_side = "LONG" if initial_qty > 0 else None
        s.tp_levels_consumed = list(consumed or [])
        s._live_position_qty = MagicMock(return_value=live_qty)
        s._build_adverse_extreme_hint = MagicMock(return_value=None)
        s._reset_breath_ledger_on_flat = MagicMock()
        s._purge_all_defense_orders_on_flat = MagicMock()
        s._save_state = MagicMock()
        return s

    def test_real_incident_fresh_catchup_fill_not_treated_as_flat(self):
        """实盘复现精确数值：追回成交0.02，账本记的开仓基线也是0.02，
        一档TP都没吃过——0.02 <= dust_qty(0.05)，但这是刚开的正常仓位，
        不该被判"确认平仓"，不该清账本/撤防御单。"""
        s = self._mk(live_qty=0.02, initial_qty=0.02)
        confirmed = s._confirm_position_flat(retries=1, delay=0)
        self.assertFalse(confirmed, "刚追回成交的真实仓位不应被误判为已确认平仓")
        s._reset_breath_ledger_on_flat.assert_not_called()
        s._purge_all_defense_orders_on_flat.assert_not_called()

    def test_genuine_dust_residual_after_tp_consumed_still_confirmed_flat(self):
        """回归：TP1/TP2已经吃掉、只剩真实零头残留(远小于开仓基线)时，
        既有"确认平仓→清账本"行为不能被这次改动误伤。"""
        s = self._mk(live_qty=0.001, initial_qty=0.13, consumed=[1, 2])
        confirmed = s._confirm_position_flat(retries=1, delay=0)
        self.assertTrue(confirmed, "TP吃完后的真实零头残留仍应正常确认平仓")
        s._reset_breath_ledger_on_flat.assert_called_once()

    def test_genuine_full_close_no_ledger_reference_still_confirmed_flat(self):
        """回归：账本本来就没有参考基线(ref<=0，比如已经清过一次)时，
        新增的fresh-small防线不应该拦住原有"交易所真空仓"的确认。"""
        s = self._mk(live_qty=0.0, initial_qty=0.0, watched_qty=0.0)
        confirmed = s._confirm_position_flat(retries=1, delay=0)
        self.assertTrue(confirmed)

    def test_small_but_shrunk_position_not_protected_by_stale_ref(self):
        """边界：真实仓位已经明显小于账本记的开仓基线(比如部分成交/
        滑点导致远低于98%)，且一档TP都没吃——不该被fresh-small豁免，
        因为它不像"账本记的量本身就等于现在这笔仓位"，交由既有dust_qty
        兜底逻辑正常处理（保持这次改动的判定尽量保守，只保护"现值≈
        基线"这一种明确场景）。"""
        s = self._mk(live_qty=0.01, initial_qty=0.05)
        confirmed = s._confirm_position_flat(retries=1, delay=0)
        self.assertTrue(confirmed, "现值明显小于账本基线时应沿用既有dust_qty判定，不强行保护")


if __name__ == "__main__":
    unittest.main(verbosity=2)
