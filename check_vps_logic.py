#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
万亿战神 VPS 逻辑静态自查 — Cursor / CI 可用，无需交易所 API Key。

用法:
  python check_vps_logic.py
  python check_vps_logic.py --verbose
"""
from __future__ import annotations

import argparse
import ast
import importlib
import inspect
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

PASS = "✅"
FAIL = "❌"
WARN = "⚠️"


class Audit:
    def __init__(self, verbose: bool = False):
        self.verbose = verbose
        self.ok = 0
        self.bad = 0
        self.warnings = 0
        self.lines: list[str] = []

    def check(self, name: str, cond: bool, detail: str = ""):
        mark = PASS if cond else FAIL
        if cond:
            self.ok += 1
        else:
            self.bad += 1
        msg = f"{mark} {name}"
        if detail:
            msg += f" — {detail}"
        self.lines.append(msg)
        if self.verbose or not cond:
            print(msg)

    def warn(self, name: str, detail: str = ""):
        self.warnings += 1
        msg = f"{WARN} {name}"
        if detail:
            msg += f" — {detail}"
        self.lines.append(msg)
        if self.verbose:
            print(msg)

    def section(self, title: str):
        print(f"\n{'=' * 60}\n{title}\n{'=' * 60}")

    def summary(self) -> int:
        print(f"\n{'=' * 60}")
        print(f"通过 {self.ok} · 失败 {self.bad} · 警告 {self.warnings}")
        if self.bad:
            print(f"{FAIL} 存在 {self.bad} 项未通过，请修复后再实盘")
            return 1
        print(f"{PASS} 静态逻辑自查全部通过")
        return 0


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as f:
        return f.read()


def _has_def(module_path: str, name: str) -> bool:
    try:
        mod = importlib.import_module(module_path.replace("/", ".").replace(".py", ""))
        return hasattr(mod, name) or any(
            hasattr(obj, name)
            for obj in (getattr(mod, c) for c in dir(mod))
            if inspect.isclass(obj)
        )
    except Exception:
        return False


def audit_module1_symbol(a: Audit):
    a.section("模块一 · Webhook 解析与币种路由")
    from symbol_config import (
        extract_symbol_from_payload,
        resolve_binance_symbol,
        active_binance_symbols,
        BINANCE_SYMBOL_META,
    )

    eth = resolve_binance_symbol("ETHUSDT.P")
    xau = resolve_binance_symbol("XAUUSDT.P")
    a.check("1.2 ETHUSDT.P → ETHUSDT", eth["symbol"] == "ETHUSDT", eth["symbol"])
    a.check("1.3 XAUUSDT.P → XAUUSDT", xau["symbol"] == "XAUUSDT", xau["symbol"])
    a.check("1.4 双品种元数据", "ETHUSDT" in BINANCE_SYMBOL_META and "XAUUSDT" in BINANCE_SYMBOL_META)
    a.check("1.4 active symbols", len(active_binance_symbols()) >= 2)

    payload = {"symbol": "BINANCE:XAUUSDT.P", "action": "LONG"}
    raw = extract_symbol_from_payload(payload)
    routed = resolve_binance_symbol(raw)
    a.check("网关 XAU 路由", routed["symbol"] == "XAUUSDT", routed["symbol"])

    # 缺 symbol 不得默念 ETH
    empty = resolve_binance_symbol("", default="")
    a.check("缺 ticker 不默念 ETH", empty.get("symbol") == "", str(empty.get("symbol")))

    # 全文扫描兜底优先 XAU
    scanned = extract_symbol_from_payload({"action": "SHORT", "note": "BINANCE:XAUUSDT.P trigger"})
    a.check("全文扫描 XAU", "XAU" in scanned.upper(), scanned)

    app_src = _read(os.path.join(ROOT, "app.py"))
    a.check("1.4 未知品种 400", "Unsupported" in app_src)
    a.check("1.5 信号去重", "SIGNAL_DEDUP_SEC" in _read(os.path.join(ROOT, "position_supervisor_binance.py")))

    # 钉钉单位不得硬编码黄金为 ETH
    from dingtalk import _resolve_unit, _format_tp_audit, bind_dingtalk_symbol, reset_dingtalk_symbol
    a.check("钉钉 XAU 单位", _resolve_unit(None, "XAUUSDT") == "XAU")
    tokens = bind_dingtalk_symbol(symbol="XAUUSDT", unit_label="XAU")
    try:
        txt = _format_tp_audit({
            "levels": [{"level": 1, "price": 4022.85, "qty": 0.073, "actual_qty": 0.073, "status": "ok"}]
        })
        a.check("TP 审计写 XAU 不写 ETH", "XAU" in txt and "ETH" not in txt, txt[:80])
    finally:
        reset_dingtalk_symbol(tokens)


def audit_module2_sizing(a: Audit):
    a.section("模块二 · 开单计算")
    from webhook_parser import (
        VPS_MARGIN_PCT_BY_REGIME,
        EXCHANGE_LEVERAGE,
        MAX_TOTAL_NOTIONAL_MULT,
        compute_vps_open_qty,
        check_total_notional_cap,
    )

    expected_margin = {1: 0.06, 2: 0.12, 3: 0.18, 4: 0.22}
    for r, pct in expected_margin.items():
        a.check(f"2.2 R{r} 保证金 {pct*100:.0f}%", VPS_MARGIN_PCT_BY_REGIME.get(r) == pct)

    a.check("2.4 杠杆 25x", EXCHANGE_LEVERAGE == 25)
    a.check("2.7 11x 硬顶", MAX_TOTAL_NOTIONAL_MULT == 11.0)

    qty, meta = compute_vps_open_qty(1000, 1800, 1700, regime=3, leverage=25)
    exp_margin = 1000 * 0.18
    exp_notional = exp_margin * 25
    a.check(
        "2.5~2.6 R3@1000U/1800",
        abs(meta["margin"] - exp_margin) < 1 and abs(meta["position_value"] - exp_notional) < 1,
        f"margin={meta.get('margin')} notional={meta.get('position_value')} qty={qty}",
    )

    qty4, meta4 = compute_vps_open_qty(1000, 1800, 1650, regime=4, leverage=25)
    a.check(
        "2.5 R4 名义 5.5x",
        abs(meta4["position_value"] - 5500) < 1,
        f"notional={meta4.get('position_value')} qty={qty4}",
    )

    ok, cap_meta = check_total_notional_cap(1000, 5500, 5500, mult=11)
    a.check("双品种 R4 踩线 11x", ok, f"total={cap_meta['total_notional']} cap={cap_meta['cap']}")
    ok2, _ = check_total_notional_cap(1000, 6000, 5500, mult=11)
    a.check("超标拒绝", not ok2)

    bc = _read(os.path.join(ROOT, "binance_client.py"))
    a.check("2.1 get_total_equity", "def get_total_equity" in bc)


def audit_module3_hard_sl(a: Audit):
    a.section("模块三 · VPS 硬止损")
    from webhook_parser import VPS_HARD_SL_PCT, compute_vps_hard_sl

    expected = {1: 0.0278, 2: 0.0389, 3: 0.0556, 4: 0.0833}
    for r, pct in expected.items():
        a.check(f"3.2 R{r} 硬止损 {pct*100:.2f}%", abs(VPS_HARD_SL_PCT[r] - pct) < 0.0001)

    sl_long = compute_vps_hard_sl("LONG", 1800, regime=3)
    a.check("3.3 做多 R3@1800", abs(sl_long - 1800 * (1 - 0.0556)) < 1, f"sl={sl_long}")

    sup = _read(os.path.join(ROOT, "position_supervisor_binance.py"))
    a.check("3.7 tv_sl_ref 参考", "tv_sl_ref" in sup and "仅作参考" in sup)
    a.check("3.5 STOP 挂单", "place_stop_market_order" in sup or "place_stop_limit" in sup)
    a.check(
        "3.8 硬止损 closePosition 不抢 TP 额度",
        "use_stop_limit=False" in sup and "不占 reduceOnly" in sup,
    )
    a.check(
        "3.9 全平勿误标 TV tv_sl",
        "触碰硬止损平仓（TV tv_sl）" not in sup
        and "触碰硬止损平仓（VPS宽止损）" in sup,
    )
    a.check(
        "3.10 开仓禁止 recover 核武连环撤",
        "开仓后防线对齐" in sup and "recover_mode=False" in sup,
    )
    a.check(
        "3.11 账本消毒拒 TV 紧止损",
        "_sanitize_vps_hard_sl_ledger" in sup
        and "_is_exchange_stop_acceptable_as_vps_floor" in sup,
    )
    a.check(
        "3.12 重启禁止方向背离自动强平",
        "重启方向背离·保留持仓" in sup,
    )
    a.check(
        "3.13 雷达必须价格达 TP1",
        "WS hint 不能单独替代" in sup or "必须达标" in sup,
    )


def audit_module4_radar(a: Audit):
    a.section("模块四 · 雷达三重验证")
    sup = _read(os.path.join(ROOT, "position_supervisor_binance.py"))

    for fn in (
        "_tp1_filled_verified",
        "_tp1_triad_ok",
        "_tp_filled_verified",
        "_price_reached_tp1_zone",
        "_tp1_qty_matches_baseline",
        "_tp_fill_ok_to_arm_radar",
        "_radar_legitimately_armed",
        "_ideal_radar_sl_is_safe",
        "_disarm_premature_radar",
    ):
        a.check(f"雷达函数 {fn}", f"def {fn}" in sup)

    a.check("4.5 噪声阈值", "TP_FILL_NOISE_VS_OPEN_PCT" in sup)
    a.check("4.1 三角对账日志", "三角对账" in sup or "三重" in sup)
    a.check("交棒禁止贴市", "_ideal_radar_sl_is_safe" in sup and "雷达交棒延迟" in sup)
    a.check("交棒后才武装", "_radar_handoff_done" in sup)

    from webhook_parser import RADAR_STAGE_COST_BUFFER_PCT
    a.check("4.6 成本缓冲 0.1%", abs(RADAR_STAGE_COST_BUFFER_PCT - 0.001) < 1e-6)

    # 钉钉雷达标题须含品种
    dt = _read(os.path.join(ROOT, "dingtalk.py"))
    a.check("钉钉雷达标题含品种", "[sym]" in dt or "[{sym}]" in dt)


def audit_module5_risk(a: Audit):
    a.section("模块五 · 全局风控")
    from risk_manager import risk_manager

    a.check("5.3 日亏熔断 5.5%", abs(risk_manager.daily_loss_limit_pct - 0.055) < 1e-6)
    sup = _read(os.path.join(ROOT, "position_supervisor_binance.py"))
    a.check("5.2 敞口钉钉", "_assert_notional_cap_or_reject" in sup)


def audit_module6_position(a: Audit):
    a.section("模块六 · 头寸误判防范")
    sup = _read(os.path.join(ROOT, "position_supervisor_binance.py"))
    a.check("6.3 trusted_initial_qty", "_trusted_initial_qty" in sup)
    a.check("6.4 WS 仓位", "start_user_data_ws" in _read(os.path.join(ROOT, "binance_client.py")))
    a.check("6.5 微漂容忍", "QTY_DRIFT_TOLERANCE_PCT" in sup)


def audit_module7_dingtalk(a: Audit):
    a.section("模块七 · 钉钉")
    dt = _read(os.path.join(ROOT, "dingtalk.py"))
    for fn in (
        "report_supervisor_open",
        "report_radar_activated",
        "report_tp_fill",
        "report_system_alert",
        "report_supervisor_close",
    ):
        a.check(f"钉钉 {fn}", f"def {fn}" in dt)


def audit_readme_consistency(a: Audit):
    a.section("README 一致性")
    readme = _read(os.path.join(ROOT, "README.md"))
    if "exclusively 来自 TV `tv_sl`" in readme:
        a.check("README 硬止损描述", False, "仍写 tv_sl 为唯一来源，应改为 VPS 自主")
    else:
        a.check("README 硬止损描述", "VPS 自主" in readme or "开仓价百分比" in readme)
    a.check("README 检查清单链接", "check_vps_logic" in readme or "VPS实盘检查清单" in readme)
    a.check("README 双品种", "XAU" in readme and "ETH" in readme)


def main():
    parser = argparse.ArgumentParser(description="VPS logic static audit")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    print("[VPS] trillion-warrior logic static audit")
    print(f"cwd: {ROOT}")

    a = Audit(verbose=args.verbose)
    audit_module1_symbol(a)
    audit_module2_sizing(a)
    audit_module3_hard_sl(a)
    audit_module4_radar(a)
    audit_module5_risk(a)
    audit_module6_position(a)
    audit_module7_dingtalk(a)
    audit_readme_consistency(a)
    return a.summary()


if __name__ == "__main__":
    sys.exit(main())
