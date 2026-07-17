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
    a.check("1.5b TV时序模块", os.path.exists(os.path.join(ROOT, "tv_seq.py")))
    a.check(
        "1.5c bar_index/seq 解析",
        "bar_index" in _read(os.path.join(ROOT, "webhook_parser.py"))
        and "TVSeqBuffer" in _read(os.path.join(ROOT, "position_supervisor_binance.py")),
    )
    from tv_seq import sort_webhooks_by_seq, make_seq_key
    ordered = sort_webhooks_by_seq([
        {"action": "OPEN", "bar_index": 200, "seq": 2},
        {"action": "CLOSE_PROTECT", "bar_index": 200, "seq": 1},
        {"action": "OPEN", "bar_index": 301, "seq": 1},
    ])
    a.check(
        "1.5d 时序排序 bar→seq",
        ordered[0].get("seq") == 1 and ordered[0].get("action") == "CLOSE_PROTECT"
        and ordered[1].get("seq") == 2 and ordered[2].get("bar_index") == 301,
    )
    a.check("1.5e 幂等键格式", make_seq_key("ETHUSDT", 100, 1) == "ETHUSDT_100_1")

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

    expected_margin = {1: 0.08, 2: 0.14, 3: 0.20, 4: 0.26}
    for r, pct in expected_margin.items():
        a.check(f"2.2 R{r} 保证金 {pct*100:.0f}%", VPS_MARGIN_PCT_BY_REGIME.get(r) == pct)

    a.check("2.4 杠杆 25x", EXCHANGE_LEVERAGE == 25)
    a.check("2.7 13x 硬顶", MAX_TOTAL_NOTIONAL_MULT == 13.0)

    qty, meta = compute_vps_open_qty(1000, 1800, 1700, regime=3, leverage=25)
    exp_margin = 1000 * 0.20
    exp_notional = exp_margin * 25
    a.check(
        "2.5~2.6 R3@1000U/1800",
        abs(meta["margin"] - exp_margin) < 1 and abs(meta["position_value"] - exp_notional) < 1,
        f"margin={meta.get('margin')} notional={meta.get('position_value')} qty={qty}",
    )
    a.check("2.5c R3 ETH qty≈2.78", abs(qty - 2.777) < 0.01, f"qty={qty}")

    qty4, meta4 = compute_vps_open_qty(1000, 1800, 1650, regime=4, leverage=25)
    a.check(
        "2.5 R4 名义 6.5x",
        abs(meta4["position_value"] - 6500) < 1,
        f"notional={meta4.get('position_value')} qty={qty4}",
    )
    a.check("2.5d R4 ETH qty≈3.61", abs(qty4 - 3.611) < 0.01, f"qty={qty4}")

    qty_xau, meta_xau = compute_vps_open_qty(1000, 2500, 2430, regime=1, leverage=25)
    a.check(
        "2.5e XAU R1@2500 qty≈0.80",
        abs(meta_xau["position_value"] - 2000) < 1 and abs(qty_xau - 0.80) < 0.01,
        f"notional={meta_xau.get('position_value')} qty={qty_xau}",
    )

    ok, cap_meta = check_total_notional_cap(1000, 6500, 6500, mult=13)
    a.check("双品种 R4 踩线 13x", ok, f"total={cap_meta['total_notional']} cap={cap_meta['cap']}")
    ok2, _ = check_total_notional_cap(1000, 7000, 6500, mult=13)
    a.check("超标拒绝", not ok2)

    bc = _read(os.path.join(ROOT, "binance_client.py"))
    a.check("2.1 get_total_equity", "def get_total_equity" in bc)


def audit_module3_hard_sl(a: Audit):
    a.section("模块三 · VPS 硬止损")
    from webhook_parser import VPS_HARD_SL_PCT, compute_vps_hard_sl

    expected = {1: 0.0278, 2: 0.0389, 3: 0.0556, 4: 0.0833}
    for r, pct in expected.items():
        a.check(f"3.2 R{r} 硬止损 {pct*100:.2f}%", abs(VPS_HARD_SL_PCT[r] - pct) < 0.0001)

    # ETH@1800 绝对距离对照表（用户规格：50/70/100/150U）
    eth_abs = {1: 50.0, 2: 70.0, 3: 100.0, 4: 150.0}
    for r, dist in eth_abs.items():
        sl = compute_vps_hard_sl("SHORT", 1800, regime=r)
        a.check(
            f"3.2b ETH@1800 R{r} ≈ +{dist:.0f}U",
            abs(sl - (1800 + dist)) < 0.2,
            f"sl={sl}",
        )

    # XAU 与 ETH 同比例：R3@4003.94 → 4226.56（非 R4 4337）
    xau_r3 = compute_vps_hard_sl("SHORT", 4003.94, regime=3)
    xau_r4 = compute_vps_hard_sl("SHORT", 4003.94, regime=4)
    a.check("3.2c XAU R3@4003.94≈4226.56", abs(xau_r3 - 4226.56) < 0.05, f"sl={xau_r3}")
    a.check("3.2d XAU R4@4003.94≈4337.47", abs(xau_r4 - 4337.47) < 0.05, f"sl={xau_r4}")
    a.check("3.2e ETH/XAU 共用同一 PCT 表", "ETH / XAU 同一套" in _read(os.path.join(ROOT, "webhook_parser.py")))

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
        "3.13 雷达主判价触激活线",
        "_price_reached_radar_activation" in sup
        and "get_radar_activation_ratio" in _read(os.path.join(ROOT, "webhook_parser.py")),
    )
    a.check(
        "3.14 硬止损实时算 VPS 不读污染账本",
        "_looks_like_tv_tight_stop" in sup
        and "拒绝挂 TV 紧止损" in sup
        and "_is_valid_radar_sl" in sup,
    )
    a.check(
        "3.15 SHORT 禁止 min 挂 TV 紧价",
        "拒绝合并伪雷达/TV紧止损" in sup,
    )
    a.check(
        "3.16 硬止损锁定 open_regime",
        "_resolve_hard_sl_regime" in sup
        and "_lock_open_regime_from_sources" in sup
        and "禁止被后续 TV UPDATE" in sup,
    )
    a.check(
        "3.17 开仓日志写 open_regime",
        '"open_regime": open_r' in sup or '"open_regime": open_r,' in sup
        or "open_regime\": open_r" in sup,
    )
    a.check(
        "3.18 重启先锁档再挂硬止损",
        "_lock_open_regime_from_sources" in sup
        and "重启强制VPS宽硬止损" in sup,
    )
    a.check(
        "3.19 雷达激活线：ensure闸门",
        "_radar_placement_blocked" in sup
        and "POST_OPEN_RADAR_BLOCK_SEC" in sup
        and (
            "拒绝雷达挂单：未交棒/未达激活线" in sup
            or "_price_reached_radar_activation" in sup
        ),
    )
    a.check(
        "3.20 SHORT保本禁止抬过开仓价",
        "禁止抬到成本及以上" in sup
        and "_clamp_radar_sl_for_market" in sup,
    )
    a.check(
        "3.21 开仓日志按品种隔离",
        "_journal_path" in sup
        and "binance_{kind}_journal_" in sup
        and "_open_regime_sticky" in sup
        and "HARD_SL_SYNC_COOLDOWN_SEC" in sup,
    )
    a.check(
        "3.22 别档VPS宽价不作TV紧价",
        "_matches_any_vps_regime_stop" in sup
        and "不是 TV 紧价" in sup,
    )
    a.check(
        "3.23 重启锁按品种隔离",
        "recover_singleton_{self.symbol}" in sup
        or ".recover_singleton_" in sup
        and "_probe_position_for_recover" in sup
        and "AMBIGUOUS" in sup,
    )
    a.check(
        "3.24 hydrate 过滤 None 信源",
        "isinstance(s, dict)" in sup
        and "多品种启动恢复清单" in sup
        and "重启异常兜底" in sup,
    )


def audit_module4_radar(a: Audit):
    a.section("模块四 · 雷达价触激活线")
    sup = _read(os.path.join(ROOT, "position_supervisor_binance.py"))

    for fn in (
        "_price_reached_radar_activation",
        "_radar_activation_price",
        "_radar_activation_ratio",
        "_radar_legitimately_armed",
        "_ideal_radar_sl_is_safe",
        "_disarm_premature_radar",
        "_perform_radar_handoff",
        "_tp1_filled_verified",
    ):
        a.check(f"雷达函数 {fn}", f"def {fn}" in sup)

    a.check("4.1 价触激活线主判", "_price_reached_radar_activation" in sup)
    a.check("交棒禁止贴市", "_ideal_radar_sl_is_safe" in sup and "雷达交棒延迟" in sup)
    a.check("交棒后才武装", "_radar_handoff_done" in sup)
    a.check("废除三重门槛文案", "废除三重" in sup or "不再要求限价成交+减仓三重" in sup)

    from webhook_parser import (
        RADAR_STAGE_COST_BUFFER_PCT,
        RADAR_ACTIVATION_RATIO_BY_REGIME,
        get_radar_activation_ratio,
    )
    a.check("4.6 成本缓冲 0.1%", abs(RADAR_STAGE_COST_BUFFER_PCT - 0.001) < 1e-6)
    expected_act = {1: 0.70, 2: 0.70, 3: 0.75, 4: 0.80}
    for r, pct in expected_act.items():
        a.check(
            f"4.2 R{r} 激活线 {pct*100:.0f}%",
            abs(RADAR_ACTIVATION_RATIO_BY_REGIME.get(r) - pct) < 1e-9
            and abs(get_radar_activation_ratio(r) - pct) < 1e-9,
        )

    # 钉钉雷达标题须含品种 + 新文案
    dt = _read(os.path.join(ROOT, "dingtalk.py"))
    a.check("钉钉雷达标题含品种", "[sym]" in dt or "[{sym}]" in dt)
    a.check("钉钉价触激活线文案", "价触激活线" in dt)


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
        "report_recover_takeover",
        "report_tv_sl_updated",
    ):
        a.check(f"钉钉 {fn}", f"def {fn}" in dt)
    a.check(
        "钉钉攒批+重试",
        "DINGTALK_BATCH" in dt and "_post_with_retry" in dt and "WECHAT_WEBHOOK" in dt,
    )
    a.check(
        "钉钉不宣称挂 TV硬止损",
        "send_alert(\"🛡️ TV硬止损" not in dt
        and "TV硬止损 · UPDATE_SL" not in dt
        and "VPS宽硬止损" in dt,
    )
    a.check(
        "UPDATE_SL 仅记录参考",
        "永不挂 TV 紧止损" in dt or "未改盘口硬止损" in dt,
    )


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
