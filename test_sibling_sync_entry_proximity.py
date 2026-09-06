#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
2026-09-06新增：雷达激活线跨账户互通(_radar_sync_touch_write/_radar_sync_
touch_check)新增的入场价接近度校验回归测试。

背景——B账户ETHUSDT实盘复现"秒平"：09-05晚TV发新LONG信号，全体账户
"先平后开"。E(MARIO)账户手上是entry=2465.45、早在约23分钟前就已经摸过
自己激活线的老仓位；B账户这次是entry=2504.67的全新仓位，08秒后就被
"📌 激活线跟随姊妹账户(binanceE)联动闩锁"直接继承了E的摸线状态——原
因是_radar_sync_touch_check原来只按写入时间戳"新旧"判断(ts < open_ts-5
才算过期)，完全没检查两笔仓位的入场价是不是真的接近(即真的是同一条
信号广播出的近乎同时开仓)。B的新仓因此跳过正常的呼吸/推进过程直接被
顶到保本止损附近(entry=2504.67, current_sl=2506.68)，价格一次很普通的
小回落(2508→2506)就把新仓在92秒内打了个盈亏几乎为零的"秒平"。

修复：写入时额外记录写入方自己的入场价(entry)；读取时新增校验——只有
两笔仓位的入场价足够接近(≤2×锁定ATR 或 ≤0.5%价格，取较大者)才继续
互通，否则视为两笔互不相关的仓位，各自摸各自的线。容差取值需要同时
满足：(a) 拒绝这次E(2465.45)/B(2504.67)相差39.22点(远超2×ATR=18.76)
的场景；(b) 不影响原来SNDKUSDT那次B/C两账户入场价只差1.35点(约0.08%)
就该继续互通的场景（2026-08-19注释里的历史实例）。
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
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
# _radar_sync_touch_write() 用 pwd.getpwuid(os.getuid()) 取账户名——生产
# 环境是Linux VPS，本地/CI跑在Windows时没有pwd模块也没有os.getuid，测试
# 环境里打个桩，不影响实际生产代码。
if "pwd" not in sys.modules:
    _fake_pwd = MagicMock()
    _fake_pwd.getpwuid.return_value = MagicMock(pw_name="test_acct")
    sys.modules["pwd"] = _fake_pwd
if not hasattr(os, "getuid"):
    os.getuid = lambda: 0  # type: ignore[attr-defined]

import position_supervisor_binance as psb  # noqa: E402


def _mk_supervisor(symbol, side, entry, atr=9.3776, open_ts=None):
    with patch.object(psb.PositionSupervisorBinance, "__init__", lambda self, *a, **k: None):
        s = psb.PositionSupervisorBinance()
    s.symbol = symbol
    s.current_side = side
    s.watched_entry = entry
    s.open_atr = atr
    s.current_atr = atr
    s._locked_initial_atr = None
    s._radar_sync_open_ts = float(open_ts if open_ts is not None else time.time())
    return s


class TestRadarSyncEntryProximity(unittest.TestCase):
    def setUp(self):
        self._tmp_dir = tempfile.mkdtemp(prefix="radar_sync_test_")
        self._orig_dir = psb.PositionSupervisorBinance._RADAR_SYNC_DIR
        psb.PositionSupervisorBinance._RADAR_SYNC_DIR = self._tmp_dir

    def tearDown(self):
        psb.PositionSupervisorBinance._RADAR_SYNC_DIR = self._orig_dir
        shutil.rmtree(self._tmp_dir, ignore_errors=True)

    def test_real_incident_far_entry_gap_rejected_not_inherited(self):
        """实盘复现：E老仓entry=2465.45摸线在先，B新仓entry=2504.67刚
        开仓——入场价差39.22点，远超2×ATR(18.76)容差，必须拒绝继承。"""
        e_old = _mk_supervisor("ETHUSDT", "LONG", entry=2465.45)
        e_old._radar_sync_touch_write(2480.0)

        b_new = _mk_supervisor("ETHUSDT", "LONG", entry=2504.67)
        result = b_new._radar_sync_touch_check()
        self.assertIsNone(result, "入场价差39.22点(远超容差)不应被继承")

    def test_sndk_style_close_entries_still_synced(self):
        """历史正常场景不受影响：SNDKUSDT B/C两账户几乎同一时刻开仓，
        入场价只差1.35点(约0.08%)，远在容差内，应继续正常互通。"""
        acct_c = _mk_supervisor("SNDKUSDT", "LONG", entry=1568.02)
        acct_c._radar_sync_touch_write(1568.02)

        acct_b = _mk_supervisor("SNDKUSDT", "LONG", entry=1569.23, open_ts=time.time())
        # 确保开仓时间在写入之后的容许窗口内
        acct_b._radar_sync_open_ts = acct_c._radar_sync_open_ts if False else time.time() - 1
        result = acct_b._radar_sync_touch_check()
        self.assertIsNotNone(result, "入场价接近(0.08%)不应被新校验误伤")

    def test_missing_entry_field_backward_compatible(self):
        """滚动部署过渡期：旧版本写入的记录没有entry字段，读取方新代码
        应向后兼容——跳过接近度校验，退回原有仅按时间戳判断的行为。"""
        b = _mk_supervisor("ETHUSDT", "LONG", entry=2504.67)
        path = b._radar_sync_file()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            json.dump({"ts": time.time(), "mark": 2504.67, "acct": "binanceE"}, f)
        result = b._radar_sync_touch_check()
        self.assertIsNotNone(result, "缺entry字段的旧记录应向后兼容，不因新校验被拒")

    def test_stale_timestamp_still_rejected(self):
        """既有行为不受影响：写入时间早于本仓位开仓时间(过期记录)一律
        拒绝，跟入场价是否接近无关。"""
        old = _mk_supervisor("ETHUSDT", "LONG", entry=2504.00)
        old._radar_sync_touch_write(2504.00)

        new = _mk_supervisor("ETHUSDT", "LONG", entry=2504.67)
        new._radar_sync_open_ts = time.time() + 100  # 本仓位"开仓"晚于写入
        result = new._radar_sync_touch_check()
        self.assertIsNone(result, "写入时间早于本仓位开仓时间的过期记录必须拒绝")

    def test_entry_gap_within_two_atr_accepted(self):
        """边界正向：入场价差正好在2×ATR容差以内，应继续互通。"""
        a = _mk_supervisor("ETHUSDT", "LONG", entry=2500.0, atr=9.3776)
        a._radar_sync_touch_write(2500.0)

        b = _mk_supervisor("ETHUSDT", "LONG", entry=2500.0 + 18.0, atr=9.3776)
        result = b._radar_sync_touch_check()
        self.assertIsNotNone(result, "入场价差18点 < 2×ATR(18.76)应接受")

    def test_entry_gap_beyond_two_atr_rejected(self):
        """边界反向：入场价差刚超过2×ATR容差，应拒绝。"""
        a = _mk_supervisor("ETHUSDT", "LONG", entry=2500.0, atr=9.3776)
        a._radar_sync_touch_write(2500.0)

        b = _mk_supervisor("ETHUSDT", "LONG", entry=2500.0 + 20.0, atr=9.3776)
        result = b._radar_sync_touch_check()
        self.assertIsNone(result, "入场价差20点 > 2×ATR(18.76)应拒绝")


if __name__ == "__main__":
    unittest.main(verbosity=2)
