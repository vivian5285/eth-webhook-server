#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
验证_recover_tv_tps_from_journal的匹配逻辑：纯算法复刻(不import
position_supervisor_binance模块本身——那个模块顶层可能有真实client
初始化副作用，这条规矩今晚一直在守)，核对入场价/方向容差匹配、
tps数量门槛、SL回填条件是否符合预期。
"""
import sys


def recover_tv_tps_from_journal(load_journal_fn, entry, side):
    """跟position_supervisor_binance.py::_recover_tv_tps_from_journal
    逐行对应的纯函数复刻，用于离线核对匹配逻辑，不碰真实状态。"""
    try:
        tv = load_journal_fn() or {}
    except Exception:
        return 0.0, None
    tv_side = str(tv.get("side") or tv.get("action") or "").upper()
    tv_px = float(tv.get("price") or 0)
    if tv_side not in ("LONG", "SHORT") or tv_side != str(side or "").upper():
        return 0.0, None
    if tv_px <= 0 or entry <= 0 or abs(tv_px - entry) > max(2.0, entry * 0.003):
        return 0.0, None
    tps = [
        float(tv.get("tv_tp1") or 0),
        float(tv.get("tv_tp2") or 0),
        float(tv.get("tv_tp3") or 0),
    ]
    if sum(1 for t in tps if t > 0) < 2:
        return 0.0, None
    tv_sl = float(tv.get("tv_sl") or 0)
    return tv_sl, tps


results = []

# 真实案例：C账户XMR，控制台手动发单，entry=489.99，tp1=505/tp2=510/tp3=526.8/sl=487
XMR_JOURNAL = {
    "action": "LONG", "side": "LONG", "price": 489.99,
    "tv_tp1": 505.0, "tv_tp2": 510.0, "tv_tp3": 526.8, "tv_sl": 487.0,
}


def check(name, journal, entry, side, expect_match, expect_tps=None, expect_sl=None):
    sl, tps = recover_tv_tps_from_journal(lambda: journal, entry, side)
    matched = tps is not None
    ok = matched == expect_match
    if ok and expect_match:
        ok = tps == expect_tps and abs(sl - expect_sl) < 1e-9
    status = "PASS" if ok else "FAIL"
    print(f"[{status}] {name}: matched={matched} tps={tps} sl={sl}")
    return ok


# 1. 真实案例：实际接管发生时的入场价499.55跟journal记录price=489.99
#    差了9.56，远超容差max(2.0, 489.99*0.003=1.47)——不该匹配上
#    (这笔实际是markPrice，接管用的watched_entry另算，这里先测精确一致场景)
results.append(check(
    "精确入场价匹配(应命中)", XMR_JOURNAL, entry=489.99, side="LONG",
    expect_match=True, expect_tps=[505.0, 510.0, 526.8], expect_sl=487.0,
))

# 2. 入场价在容差内(489.99+1.0=490.99, 容差max(2.0,1.47)=2.0)
results.append(check(
    "容差内入场价(应命中)", XMR_JOURNAL, entry=490.99, side="LONG",
    expect_match=True, expect_tps=[505.0, 510.0, 526.8], expect_sl=487.0,
))

# 3. 入场价超出容差(489.99+5=494.99)
results.append(check(
    "超出容差入场价(不应命中)", XMR_JOURNAL, entry=494.99, side="LONG",
    expect_match=False,
))

# 4. 方向不一致
results.append(check(
    "方向不一致(不应命中)", XMR_JOURNAL, entry=489.99, side="SHORT",
    expect_match=False,
))

# 5. journal里tps不足两个有效值
INSUFFICIENT_JOURNAL = {"action": "LONG", "side": "LONG", "price": 489.99, "tv_tp1": 505.0, "tv_tp2": 0, "tv_tp3": 0}
results.append(check(
    "journal里tps不足2个(不应命中)", INSUFFICIENT_JOURNAL, entry=489.99, side="LONG",
    expect_match=False,
))

# 6. journal没有tv_sl，只有tps——应该仍然匹配上tps，但sl回填为0(调用方据此决定是否清零tv_sl_ref)
NO_SL_JOURNAL = {"action": "LONG", "side": "LONG", "price": 489.99, "tv_tp1": 505.0, "tv_tp2": 510.0, "tv_tp3": 0, "tv_sl": 0}
results.append(check(
    "journal无tv_sl但tps够(应命中，sl=0)", NO_SL_JOURNAL, entry=489.99, side="LONG",
    expect_match=True, expect_tps=[505.0, 510.0, 0.0], expect_sl=0.0,
))

# 7. SHORT方向也验证一遍
SHORT_JOURNAL = {"action": "SHORT", "side": "SHORT", "price": 1000.0, "tv_tp1": 900.0, "tv_tp2": 850.0, "tv_tp3": 800.0, "tv_sl": 1030.0}
results.append(check(
    "SHORT方向匹配(应命中)", SHORT_JOURNAL, entry=1000.0, side="SHORT",
    expect_match=True, expect_tps=[900.0, 850.0, 800.0], expect_sl=1030.0,
))

print()
if all(results):
    print(f"全部{len(results)}项通过。")
    sys.exit(0)
else:
    print(f"{results.count(False)}/{len(results)}项失败！")
    sys.exit(1)
