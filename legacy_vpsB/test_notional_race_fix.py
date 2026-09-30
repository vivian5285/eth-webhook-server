#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
隔离测试_assert_notional_cap_or_reject的并发预占修复——
mock掉binance_client/dingtalk，纯逻辑验证，不碰真实账户。
"""
import os
import sys
import time
from unittest.mock import MagicMock, patch

os.environ["BINANCE_SKIP_BOOTSTRAP"] = "1"

_fake_bc = MagicMock()
sys.modules.setdefault("binance_client", _fake_bc)
_fake_bc.binance_client = MagicMock()
_fake_bc.is_position_query_failed = lambda x: False
_fake_bc.is_orders_query_failed = lambda x: False

_fake_dt = MagicMock()
sys.modules.setdefault("dingtalk", _fake_dt)

import position_supervisor_binance as psb  # noqa: E402

# webhook_parser.MAX_TOTAL_NOTIONAL_MULT 当前是13；equity=1000U → cap=13000U
psb.MAX_TOTAL_NOTIONAL_MULT = 13.0


def make_stub(symbol):
    with patch.object(psb.PositionSupervisorBinance, "__init__", lambda self, *a, **k: None):
        s = psb.PositionSupervisorBinance()
    s.symbol = symbol
    s._other_symbols_notional = MagicMock(return_value=(0.0, {}, 0.0))  # 假设REST查到的其它品种敞口=0
    s._resolve_cap_sizing_base = MagicMock(return_value=1000.0)  # equity=1000U
    return s


def test_race_closed():
    psb._PENDING_OPEN_NOTIONAL.clear()
    a = make_stub("AUSDT")
    b = make_stub("BUSDT")

    # A: 7000U notional，equity=1000U，cap=13000U，单独看没问题
    ok_a, meta_a = a._assert_notional_cap_or_reject(qty=1, price=7000.0)
    assert ok_a, f"A应该通过: {meta_a}"
    assert psb._PENDING_OPEN_NOTIONAL.get("AUSDT") is not None, "A应该已登记预占"

    # B: 7000U notional，如果B看不到A的预占，会误判"其它品种=0"，单独也通过；
    # 但A(7000)+B(7000)=14000U > cap(13000U)，B应该被拦截
    ok_b, meta_b = b._assert_notional_cap_or_reject(qty=1, price=7000.0)
    assert not ok_b, f"B应该被拦截(A的预占7000U应该被算进去)，但结果是通过: {meta_b}"
    assert meta_b.get("pending_other") == 7000.0, f"B应该看到A的7000U预占，实际={meta_b.get('pending_other')}"
    print("✅ test_race_closed 通过：并发场景下B正确被拦截，pending_other正确反映A的预占")


def test_ttl_expiry():
    psb._PENDING_OPEN_NOTIONAL.clear()
    a = make_stub("AUSDT")
    b = make_stub("BUSDT")

    ok_a, _ = a._assert_notional_cap_or_reject(qty=1, price=7000.0)
    assert ok_a
    # 手动把A的预占时间戳往回拨，模拟超过TTL(90s)
    notional, _ts = psb._PENDING_OPEN_NOTIONAL["AUSDT"]
    psb._PENDING_OPEN_NOTIONAL["AUSDT"] = (notional, time.time() - psb._PENDING_OPEN_NOTIONAL_TTL_SEC - 5)

    ok_b, meta_b = b._assert_notional_cap_or_reject(qty=1, price=7000.0)
    assert ok_b, f"A的预占已过期，B应该正常通过: {meta_b}"
    assert meta_b.get("pending_other") == 0.0, f"过期预占不应计入，实际={meta_b.get('pending_other')}"
    assert "AUSDT" not in psb._PENDING_OPEN_NOTIONAL, "过期预占应该被自动清理"
    print("✅ test_ttl_expiry 通过：过期预占自动清理，不误伤后续开仓")


def test_same_symbol_not_double_counted():
    """同品种自己的旧预占不该被当成"其它品种"算进去（避免自己卡自己）。"""
    psb._PENDING_OPEN_NOTIONAL.clear()
    a = make_stub("AUSDT")

    ok1, _ = a._assert_notional_cap_or_reject(qty=1, price=7000.0)
    assert ok1
    # 同一个品种再算一次（比如重试），不该把自己刚才的预占算成"其它品种"敞口
    ok2, meta2 = a._assert_notional_cap_or_reject(qty=1, price=7000.0)
    assert ok2, f"同品种自己的预占不该卡自己: {meta2}"
    assert meta2.get("pending_other") == 0.0
    print("✅ test_same_symbol_not_double_counted 通过：不会自己卡自己")


if __name__ == "__main__":
    test_race_closed()
    test_ttl_expiry()
    test_same_symbol_not_double_counted()
    print("\n全部通过")
