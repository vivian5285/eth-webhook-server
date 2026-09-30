#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
12品种全链路脚本内测——纯计算，不碰REST、不下单、不改任何账户状态。
覆盖：TV payload解析 -> 品种识别 -> 仓位计算 -> TP1/2/3 -> 硬止损校验 ->
保本激活门槛 -> 呼吸/雷达系数，逐品种跑一遍，任何一步炸了就当场报错。
"""
import sys

from webhook_parser import normalize_tv_payload, compute_fixed_order_qty, get_tier_notional_mult
from symbol_config import resolve_binance_symbol, active_binance_symbols
from breath_profiles import get_breath_profile
from reentry_profiles import get_reentry_profile, radar_gate_price_from_tps, reentry_window_sec
from defense_profiles import get_defense_profile, buffer_multiplier, tp_leg_ratios, min_stop_distance, validate_tv_stop_loss

# 每品种一组贴近真实量级的合成payload（entry/atr取自今天校准时拉到的最新价/ATR附近）
SAMPLES = {
    "ETHUSDT":       dict(price=1890.00, atr=11.30, tp1=1905.30, tp2=1918.25, tp3=1935.00, sl=1873.00),
    "XAUUSDT":       dict(price=4390.00, atr=14.50, tp1=4409.58, tp2=4426.25, tp3=4450.00, sl=4373.30),
    "BNBUSDT":       dict(price=610.00,  atr=2.70,  tp1=613.65,  tp2=616.75,  tp3=622.00,  sl=606.00),
    "ZECUSDT":       dict(price=58.00,   atr=1.80,  tp1=58.90,   tp2=59.65,   tp3=61.00,   sl=56.60),
    "BCHUSDT":       dict(price=560.00,  atr=13.50, tp1=568.20,  tp2=575.10,  tp3=588.00,  sl=548.00),
    "XMRUSDT":       dict(price=330.00,  atr=6.60,  tp1=333.90,  tp2=337.20,  tp3=343.00,  sl=323.00),
    "SNDKUSDT":      dict(price=1620.00, atr=38.10, tp1=1651.50, tp2=1678.00, tp3=1720.00, sl=1580.00),
    "PAXGUSDT":      dict(price=4386.00, atr=23.90, tp1=4418.30, tp2=4446.00, tp3=4490.00, sl=4356.00),
    "SKHYNIXUSDT":   dict(price=1163.00, atr=26.40, tp1=1198.60, tp2=1229.00, tp3=1278.00, sl=1123.00),
    "XPDUSDT":       dict(price=1323.00, atr=10.05, tp1=1336.60, tp2=1348.30, tp3=1366.00, sl=1310.00),
    "OPENAIUSDT":    dict(price=1275.00, atr=21.10, tp1=1298.10, tp2=1317.90, tp3=1349.00, sl=1249.00),
    "ANTHROPICUSDT": dict(price=1680.00, atr=16.50, tp1=1698.20, tp2=1713.90, tp3=1738.00, sl=1658.00),
    "ASMLUSDT":      dict(price=1846.00, atr=4.98,  tp1=1852.72, tp2=1858.45, tp3=1868.50, sl=1838.55),
}

WEBHOOK_SECRET = "528586"


def check_symbol(sym: str) -> list:
    problems = []
    s = SAMPLES[sym]

    # 1) TV payload 解析
    raw = {
        "action": "LONG", "symbol": sym, "price": s["price"], "atr": s["atr"],
        "tp1": s["tp1"], "tp2": s["tp2"], "tp3": s["tp3"], "stop_loss": s["sl"],
        "tier": 2, "secret": WEBHOOK_SECRET,
    }
    norm = normalize_tv_payload(raw)
    if not norm.get("_parse_ok"):
        problems.append("normalize_tv_payload解析失败(_parse_ok=False)")
    if norm.get("ticker") != sym and norm.get("symbol") != sym:
        problems.append(f"品种识别错位: ticker={norm.get('ticker')} symbol={norm.get('symbol')}")
    if abs(float(norm.get("atr") or 0) - s["atr"]) > 1e-6:
        problems.append("ATR透传丢失/被改写")

    # 2) 品种元数据 + 呼吸档案挂载
    meta = resolve_binance_symbol(sym)
    if meta.get("symbol") != sym:
        problems.append(f"resolve_binance_symbol返回错误品种: {meta.get('symbol')}")
    bp_attached = meta.get("breath_profile")
    bp_direct = get_breath_profile(sym)
    if not bp_attached or bp_attached.get("name") != bp_direct.get("name"):
        problems.append("resolve_binance_symbol挂载的breath_profile跟get_breath_profile直查不一致")
    if bp_direct.get("name") == "ETH" and sym != "ETHUSDT":
        problems.append("breath_profile静默落到了ETH默认值！")

    # 3) 仓位计算（RISK20_NOTIONAL5，principal=1000U模拟本金）
    principal = 1000.0
    qty, sizing_meta = compute_fixed_order_qty(
        principal=principal, price=s["price"], qty_step=meta["qty_step"],
        min_qty=meta["min_qty"], stop_loss=s["sl"], tv_price=s["price"],
    )
    if qty <= 0:
        problems.append(f"仓位计算结果qty<=0: qty={qty} meta={sizing_meta}")
    base_notional = qty * s["price"]
    if not (principal * 0.5 <= base_notional <= principal * 1.5):
        problems.append(f"基础名义偏离预期区间(≈1×本金): notional={base_notional:.1f} principal={principal}")

    # 4) 弱/中/强三档倍数——2026-08-15起全部12个品种统一0.7/0.8/1.0倍
    # （最高档=现货1倍，不放大杠杆），细水长流。
    expected = [0.7, 0.8, 1.0]
    mult = [get_tier_notional_mult(sym, t) for t in (0, 1, 2)]
    if mult != expected:
        problems.append(f"档位倍数跟预期不符: got={mult} expected={expected}")

    # 5) 硬止损：距离校验 + 呼吸垫倍数
    ok, reason, dist = validate_tv_stop_loss(sym, s["price"], s["sl"])
    if not ok:
        problems.append(f"硬止损距离校验未通过: {reason} dist={dist}")
    buf = buffer_multiplier(sym)
    if abs(buf - 1.15) > 1e-9:
        problems.append(f"硬止损呼吸垫不是统一1.15: {buf}")
    legs = tp_leg_ratios(sym)
    if abs(sum(legs) - 1.0) > 1e-6 or legs[:2] != [0.10, 0.20]:
        problems.append(f"TP分腿比例不是10/20/70: {legs}")

    # 6) 保本激活门槛（首次开仓，reentry_attempt=0）
    rp = get_reentry_profile(sym)
    return_pct = float(rp.get("radar_gate_return_pct") or 0)
    gate = radar_gate_price_from_tps(
        s["tp1"], s["tp2"], 0, entry=s["price"], atr=s["atr"], return_pct=return_pct,
    )
    if gate <= s["price"] or gate >= s["tp1"]:
        problems.append(f"保本激活门槛不在entry~TP1之间: gate={gate} entry={s['price']} tp1={s['tp1']}")
    if rp.get("name") != bp_direct.get("name"):
        problems.append(f"reentry_profile名字跟breath_profile名字不一致: {rp.get('name')} vs {bp_direct.get('name')}")
    if sym != "ZECUSDT" and return_pct > 0:
        problems.append(f"非ZEC品种意外配了radar_gate_return_pct: {return_pct}")

    # 7) 重入窗口时长应为正数、量级合理（<=24小时）
    win_sec = reentry_window_sec(sym)
    if not (0 < win_sec <= 24 * 3600):
        problems.append(f"重入窗口时长异常: {win_sec}s")

    return problems


def main():
    syms = active_binance_symbols()
    print(f"活跃品种数: {len(syms)}\n")
    total_problems = 0
    for sym in syms:
        if sym not in SAMPLES:
            print(f"[{sym}] ⚠️ 测试脚本没有为这个品种准备合成样本，跳过")
            total_problems += 1
            continue
        problems = check_symbol(sym)
        if problems:
            total_problems += len(problems)
            print(f"[{sym}] ❌ {len(problems)}个问题:")
            for p in problems:
                print(f"    - {p}")
        else:
            print(f"[{sym}] ✅ 全链路通过（解析/品种识别/仓位/档位/硬止损/保本门槛/重入窗口）")
    print(f"\n合计问题数: {total_problems}")
    sys.exit(1 if total_problems else 0)


if __name__ == "__main__":
    main()
