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
    from tv_seq import sort_webhooks_by_seq, make_seq_key, reorder_batch_close_then_open
    ordered = sort_webhooks_by_seq([
        {"action": "OPEN", "bar_index": 200, "seq": 2},
        {"action": "CLOSE_PROTECT", "bar_index": 200, "seq": 1},
        {"action": "OPEN", "bar_index": 301, "seq": 1},
    ])
    a.check(
        "1.5d 时序排序 bar→动作优先→seq",
        ordered[0].get("seq") == 1 and ordered[0].get("action") == "CLOSE_PROTECT"
        and ordered[1].get("seq") == 2 and ordered[2].get("bar_index") == 301,
    )
    # 实盘事故：OPEN seq=1 + CLOSE seq=2 同秒 → 必须先平后开（终态开仓）
    inverted = reorder_batch_close_then_open([
        {"action": "LONG", "entry_type": "OPEN", "bar_index": 27096, "seq": 1},
        {"action": "CLOSE_PROTECT", "bar_index": 27096, "seq": 2},
    ])
    a.check(
        "1.5d2 同秒开平强制先平后开(seq颠倒)",
        inverted[0].get("action") == "CLOSE_PROTECT"
        and inverted[1].get("action") == "LONG",
    )
    a.check(
        "1.5e 幂等键含 action",
        make_seq_key("ETHUSDT", 100, 1, "LONG") == "ETHUSDT_100_1_LONG"
        and make_seq_key("ETHUSDT", 100, 1) == "ETHUSDT_100_1_NA",
    )
    sup = _read(os.path.join(ROOT, "position_supervisor_binance.py"))
    tvseq = _read(os.path.join(ROOT, "tv_seq.py"))
    dt = _read(os.path.join(ROOT, "dingtalk.py"))
    a.check(
        "1.5f 先平后开 CLOSE后释放再开+同秒聚合",
        "release_bar_for_reentry" in tvseq
        and "_release_tv_seq_after_close" in sup
        and "SAME_BAR_SETTLE_SEC" in tvseq
        and "reorder_batch_close_then_open" in sup
        and "action_exec_rank" in tvseq
        and "永远先平后开" in tvseq,
    )
    a.check(
        "1.5g 无菌空仓闸（仓+单皆零）",
        "_sterile_flat_gate" in sup
        and "_verify_sterile_flat" in sup
        and "report_close_then_open_chain" in dt
        and "_annotate_close_open_chain" in sup,
    )
    a.check(
        "1.5h 开仓铁律一律先平后开+禁穿价TP",
        "TV开仓·一律先平后开刷新仓位" in sup
        and "_full_reentry" in sup
        and "_tp_is_marketable" in sup
        and "_sanitize_open_tps_vs_mark" in sup
        and "_sterile_flat_gate" in sup
        and "钉钉去重跳过" in sup
        and "_force_hang_open_defenses" in sup
        and "_may_mark_tp_filled_missing_limit" in sup
        and "_clear_spurious_tp_consumed_if_full_size" in sup
        and "_force_tps_unmarketable" in sup
        and "开仓强制挂防线" in sup
        and "拒认 TP" in sup
        and "假成交" in sup
        and "_apply_takeover_price_progress" in sup
        and "开仓价/现价对账" in sup
        and "接管跳过补挂" in sup
        and "takeover_mode" in sup
        and "_bind_tv_open_defenses" in sup
        and "_snapshot_tv_open_defenses" in sup
        and "开仓前防线快照" in sup
        and "绑定开仓防线" in sup,
    )
    a.check(
        "1.5i TP成交必须现价/best触及该档",
        "_live_mark_for_tp_detect" in sup
        and "每一档都必须价到" in sup
        and "拒认仅凭减仓记" in sup
        and "_resync_tp_baseline" in sup
        and "ABNORMAL_REDUCE_ALERT_COOLDOWN_SEC" in sup
        and "DINGTALK_TITLE_DEDUP_SEC" in dt
        and "DINGTALK_ALERT_DEDUP_SEC" in dt
        and "钉钉标题去重" in dt,
    )

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
    a.section("模块二 · 开单计算（TV 唯一公式·无硬上限）")
    from webhook_parser import (
        HARD_NOTIONAL_CAP,
        EXCHANGE_LEVERAGE,
        MAX_TOTAL_NOTIONAL_MULT,
        compute_tv_order_qty,
        compute_vps_open_qty,
        check_total_notional_cap,
    )

    a.check("2.1 单笔硬上限已删除", float(HARD_NOTIONAL_CAP or 0) == 0.0)
    a.check("2.4 API 杠杆 25x", EXCHANGE_LEVERAGE == 25)
    a.check("2.7 13x 组合顶保留", MAX_TOTAL_NOTIONAL_MULT == 13.0)

    # 表：本金 1000 / ETH 1892.43 · Regime1 risk 0.81% / SL距 12.08 → 0.67
    qty1, meta1 = compute_tv_order_qty(
        1000, risk_pct=0.81, leverage=25, qty_ratio=1.0,
        price=1892.43, tv_sl=1892.43 - 12.08, regime=1,
    )
    a.check(
        "2.5 R1@1000U → 0.67 ETH",
        abs(qty1 - 0.67) < 0.005 and abs(meta1["stop_dist"] - 12.08) < 0.01,
        f"qty={qty1} stop={meta1.get('stop_dist')} bind={meta1.get('bind')}",
    )

    # Regime2: risk 1.35% / SL 14.09 → 0.96
    qty2, meta2 = compute_tv_order_qty(
        1000, risk_pct=1.35, leverage=25, qty_ratio=1.0,
        price=1892.43, tv_sl=1892.43 - 14.09, regime=2,
    )
    a.check(
        "2.5 R2@1000U → 0.96 ETH",
        abs(qty2 - 0.96) < 0.01,
        f"qty={qty2} theoretical={meta2.get('theoretical_qty')}",
    )

    # Regime3: risk 2.03% / SL 14.02 → 1.45
    qty3, meta3 = compute_tv_order_qty(
        1000, risk_pct=2.03, leverage=25, qty_ratio=1.0,
        price=1892.43, tv_sl=1892.43 - 14.02, regime=3,
    )
    a.check(
        "2.5 R3@1000U → 1.45 ETH",
        abs(qty3 - 1.45) < 0.01,
        f"qty={qty3} notional={meta3.get('order_amount')}",
    )

    # Regime4 下沿: risk 2.70% / SL 15.94 → 1.69
    qty4lo, _ = compute_tv_order_qty(
        1000, risk_pct=2.70, leverage=25, qty_ratio=1.0,
        price=1892.43, tv_sl=1892.43 - 15.94, regime=4,
    )
    qty4hi, _ = compute_tv_order_qty(
        1000, risk_pct=3.38, leverage=25, qty_ratio=1.0,
        price=1892.43, tv_sl=1892.43 - 15.94, regime=4,
    )
    a.check("2.5 R4下沿 → 1.69", abs(qty4lo - 1.69) < 0.01, f"qty={qty4lo}")
    a.check("2.5 R4上沿 → 2.12", abs(qty4hi - 2.12) < 0.01, f"qty={qty4hi}")

    # 本金线性换算：5000U R3 → 7.25
    qty5k, _ = compute_tv_order_qty(
        5000, risk_pct=2.03, leverage=25, qty_ratio=1.0,
        price=1892.43, tv_sl=1892.43 - 14.02, regime=3,
    )
    a.check("2.6 5000U R3 → 7.25", abs(qty5k - 7.25) < 0.02, f"qty={qty5k}")

    # 加仓 ratio 0.5 → 首仓一半
    qty_add, meta_add = compute_tv_order_qty(
        1000, risk_pct=2.03, leverage=25, qty_ratio=0.5,
        price=1892.43, tv_sl=1892.43 - 14.02, regime=3,
    )
    a.check(
        "2.6b 加仓 ratio0.5 → ~0.72",
        abs(qty_add - 0.72) < 0.02,
        f"qty={qty_add} ratio={meta_add.get('qty_ratio')}",
    )

    # 缺 risk_pct → 0（禁止旧逻辑回退）
    qty0, meta0 = compute_vps_open_qty(1000, 1892.43, 1880, regime=3, leverage=25)
    a.check("2.8 无 risk_pct 拒绝下单", qty0 == 0 and meta0.get("error"), f"meta={meta0}")

    # 无硬上限：极大 risk 不再被 50000/price 卡住，应绑理论或杠杆
    qty_big, meta_big = compute_tv_order_qty(
        1000, risk_pct=40.0, leverage=100, qty_ratio=1.0,
        price=2000, tv_sl=1990, regime=4,
    )
    a.check(
        "2.9 无硬上限·大风险走理论/杠杆",
        meta_big.get("bind") in ("theoretical", "leverage")
        and meta_big.get("bind") != "hard_cap"
        and float(meta_big.get("hard_cap_qty") or 0) == 0
        and qty_big > 25.0,
        f"qty={qty_big} bind={meta_big.get('bind')}",
    )
    a.check(
        "2.9b sizing_mode 无硬上限",
        "NO_HARD_CAP" in str(meta_big.get("sizing_mode") or ""),
    )

    ok, cap_meta = check_total_notional_cap(1000, 6500, 6500, mult=13)
    a.check("双品种 13x 踩线", ok, f"total={cap_meta['total_notional']} cap={cap_meta['cap']}")
    ok2, _ = check_total_notional_cap(1000, 7000, 6500, mult=13)
    a.check("超标拒绝", not ok2)

    bc = _read(os.path.join(ROOT, "binance_client.py"))
    a.check("2.1 get_total_equity", "def get_total_equity" in bc)
    wp = _read(os.path.join(ROOT, "webhook_parser.py"))
    a.check("禁止旧保证金%表参与计算", "TV_RISK_FORMULA" in wp)
    a.check("旧 VPS_MARGIN 已清空", "VPS_MARGIN_PCT_BY_REGIME = {}" in wp)
    a.check("HARD_NOTIONAL_CAP 恒0", "HARD_NOTIONAL_CAP = 0.0" in wp)


def audit_module3_hard_sl(a: Audit):
    a.section("模块三 · TV 硬止损（实盘挂单）")
    from webhook_parser import VPS_HARD_SL_PCT, compute_vps_hard_sl

    # VPS% 宽止损表必须清空；compute 恒 0
    a.check("3.2 VPS%宽止损表已清空", VPS_HARD_SL_PCT == {} or not VPS_HARD_SL_PCT)
    a.check(
        "3.2b compute_vps_hard_sl 恒0(已废除)",
        compute_vps_hard_sl("LONG", 1800, regime=3) == 0
        and compute_vps_hard_sl("SHORT", 1800, regime=4) == 0,
    )

    sup = _read(os.path.join(ROOT, "position_supervisor_binance.py"))
    a.check(
        "3.7 实盘硬止损=TV tv_sl",
        "_tv_hard_sl_target" in sup
        and "禁止再用开仓价×档位%" in sup
        and "TV硬止损" in sup
        and "拒绝挂 TV 紧止损" not in sup,
    )
    a.check("3.5 STOP 挂单", "place_stop_market_order" in sup or "place_stop_limit" in sup)
    a.check(
        "3.8 硬止损 closePosition 不抢 TP 额度",
        "use_stop_limit=False" in sup and "不占 reduceOnly" in sup,
    )
    a.check(
        "3.9 全平归因 TV硬止损",
        "触碰硬止损平仓（TV硬止损）" in sup
        and "触碰硬止损平仓（VPS宽止损）" not in sup,
    )
    a.check(
        "3.10 硬止损失败撤开仓防裸奔",
        "硬止损失败·撤销开仓防裸奔" in sup or "_emergency_flatten_naked_open" in sup,
    )
    a.check(
        "3.10 开仓禁止 recover 核武连环撤",
        "开仓后防线对齐" in sup and "recover_mode=False" in sup,
    )
    a.check(
        "3.10b 防线 thrash 刹车",
        "NUCLEAR_REALIGN_MIN_INTERVAL_SEC" in sup
        and "_nuclear_backoff_remaining" in sup
        and "_defense_anomaly_is_severe" in sup
        and "idempotent_unified" in sup
        and "exclude_shield=False" in sup
        and "HARD_SL_SYNC_COOLDOWN_SEC" in sup,
    )
    a.check(
        "3.11 账本消毒对齐 TV",
        "_sanitize_vps_hard_sl_ledger" in sup
        and "_is_exchange_stop_acceptable_as_vps_floor" in sup
        and "不得用 VPS% 覆盖" in sup,
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
        "3.14 TV硬止损允许挂盘（废除紧价拒绝）",
        "_looks_like_tv_tight_stop" in sup
        and "恒返回 False" in sup
        and "拒绝挂 TV 紧止损" not in sup
        and "_is_valid_radar_sl" in sup,
    )
    a.check(
        "3.15 合并底线=TV硬止损",
        "仅挂 TV硬止损" in sup
        and "拒绝合并伪雷达/TV紧止损" not in sup,
    )
    a.check(
        "3.16 硬止损锁定 open_regime(雷达/TP用)",
        "_resolve_hard_sl_regime" in sup
        and "_lock_open_regime_from_sources" in sup
        and "_resolve_tv_open_regime_for_position" in sup
        and "以 TV 为准" in sup,
    )
    a.check(
        "3.17 开仓日志写 open_regime",
        '"open_regime": open_r' in sup or '"open_regime": open_r,' in sup
        or "open_regime\": open_r" in sup,
    )
    a.check(
        "3.18 重启先锁档再挂TV硬止损",
        "_lock_open_regime_from_sources" in sup
        and "重启强制TV硬止损" in sup
        and "重启强制VPS宽硬止损" not in sup,
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
        "3.22 旧VPS%档位匹配已废弃",
        "_matches_any_vps_regime_stop" in sup
        and "旧 VPS% 档位匹配已废弃" in sup,
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
    a.check(
        "3.25 开仓裸仓闸 expected=0 不假齐",
        "开仓 TP123 补全失败" in sup
        and "开仓终检裸仓补挂" in sup
        and "不标 align_ok" in sup
        and "expected <= 0" in sup
        and "_ensure_tp123_prices_from_tv" in sup
        and "盘口无保护 STOP" in sup,
    )
    from webhook_parser import enrich_entry_tp_prices
    empty_tp = enrich_entry_tp_prices("LONG", 1800.0, 0, 1, {})
    a.check(
        "3.25b TV空ATR仍补全TP",
        float(empty_tp.get("tv_tp1") or 0) > 1800
        and float(empty_tp.get("tv_tp3") or 0) > float(empty_tp.get("tv_tp1") or 0),
        str(empty_tp),
    )
    a.check(
        "3.26 UPDATE_SL 必须同步盘口",
        "UPDATE_SL·按TV硬止损重挂" in sup
        and "UPDATE_SL 已按 TV 硬止损执行" in sup
        and "UPDATE_SL 已忽略盘口动作" not in sup,
    )
    a.check(
        "3.27 版本含 TV 仓位公式",
        "v13.85.0-no-hard-cap" in sup
        or "v13.84.0-tv-strategy-sync" in sup
        or "v13.82.0-tv-risk-sizing" in sup,
    )


def audit_module4_radar(a: Audit):
    a.section("模块四 · 雷达价触激活线")
    sup = _read(os.path.join(ROOT, "position_supervisor_binance.py"))
    dt = _read(os.path.join(ROOT, "dingtalk.py"))
    wp = _read(os.path.join(ROOT, "webhook_parser.py"))

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
    a.check(
        "交棒门槛文案",
        "档位激活线" in sup or "_radar_ready_to_handoff" in sup,
    )
    a.check(
        "4.8 交棒/重启现价激活线或TP1成交",
        "live_only=True" in sup
        and "_radar_ready_to_handoff" in sup
        and "_tp1_fill_allows_radar" in sup
        and "for_handoff=True" in sup,
    )
    a.check(
        "4.9 硬止损撤后重试挂单",
        "硬止损挂单未核实" in sup and "重试" in sup,
    )
    a.check(
        "4.10 开仓滞后核实补挂",
        "开仓滞后核实" in sup and "开仓滞后核实·强制TV硬止损" in sup,
    )
    a.check(
        "4.11 WS mark 脉冲交棒",
        "_on_mark_price_tick" in sup and "register_price_tick_callback" in (
            _read(os.path.join(ROOT, "binance_client.py"))
        ),
    )
    a.check(
        "4.11b WS最快盯价·接近激活线加速",
        "RADAR_WS_APPROACH_RATIO" in sup
        and "_radar_work_urgent" in sup
        and "markPrice@1s" in _read(os.path.join(ROOT, "binance_client.py"))
        and "_radar_in_progress" in sup
        and "TV开仓·一律先平后开刷新仓位" in sup
        and "禁止先 scope=radar 撤再挂" in sup,
    )
    a.check(
        "4.11c 废除同向仅刷TP·一律先平后开",
        "always_close_then_open" in sup
        and "OPEN_SAME_DIR_COOLDOWN_SEC = 0" in sup
        and "废弃同向仅刷TP" in sup
        and 'return "REFRESH_TP"' not in sup,
    )
    a.check(
        "4.12 TP多档对账禁误报人工",
        "_filter_credible_tp_fills" in sup
        and "_detect_tp_fills_by_price_qty_reconcile" in sup
        and "_reconcile_open_qty_vs_tp123" in sup
        and "限价止盈待核实对账" in sup,
    )
    a.check(
        "4.13 平仓归因 exit_source",
        "_resolve_exit_source" in sup
        and "_radar_was_armed" in sup
        and "EXIT_SOURCE_RADAR_BE" in wp
        and "exit_source" in dt
        and "平仓归因" in dt,
    )
    a.check(
        "4.14 雷达钉钉必达+哨兵补发",
        "_flush_pending_radar_notify" in sup
        and "_radar_notify_pending" in sup
        and "radar_activation_notified" in sup
        and "trigger_gate" in dt
        and "补发雷达激活钉钉" in sup,
    )
    a.check(
        "4.15 开单钉钉头寸对账字段",
        "hard_sl_px" in dt
        and "radar_act_px" in dt
        and "头寸对账" in dt
        and ("雷达激活线" in dt or "雷达候命" in dt),
    )
    a.check(
        "4.16 TP成交记账禁漏挂补挂",
        "_reconcile_tp_consumed_from_live_qty" in sup
        and "_qty_reduction_looks_like_tp" in sup
        and "_block_rehang_filled_tps_note" in sup
        and "_tp_level_price_and_order_gone" in sup
        and "_infer_tp_consumed_by_price_and_gone" in sup
        and "价到+限价消失" in sup
        and "耐心等" in sup
        and "soft_infer" in sup,
    )
    a.check(
        "4.17 WS多档TP成交提示",
        "_ws_tp_fill_levels" in sup
        and "UD-WS TP" in sup
        and "禁当漏挂补挂" in sup,
    )
    a.check(
        "4.18 三轨不抢份额",
        "三轨并行" in sup
        and "reduceOnly" in sup
        and "closePosition 单槽" in sup
        and "互不抢份额" in dt,
    )

    from webhook_parser import (
        RADAR_STAGE_COST_BUFFER_PCT,
        RADAR_ACTIVATION_RATIO_BY_REGIME,
        get_radar_activation_ratio,
    )
    a.check("4.6 成本缓冲 0.1%", abs(RADAR_STAGE_COST_BUFFER_PCT - 0.001) < 1e-6)
    from webhook_parser import (
        get_radar_trail_step,
        get_radar_breath_atr,
        RADAR_TRAIL_STEP_BY_REGIME,
        RADAR_BREATH_ATR_BY_REGIME,
        RADAR_STAGE_ATR_MULT,
    )
    expected_act = {1: 0.50, 2: 0.60, 3: 0.70, 4: 0.80}
    expected_step = {1: 0.35, 2: 0.30, 3: 0.25, 4: 0.20}
    expected_breath = {1: 1.0, 2: 0.8, 3: 0.65, 4: 0.5}
    for r, pct in expected_act.items():
        a.check(
            f"4.2 R{r} 激活线 {pct*100:.0f}%",
            abs(RADAR_ACTIVATION_RATIO_BY_REGIME.get(r) - pct) < 1e-9
            and abs(get_radar_activation_ratio(r) - pct) < 1e-9,
        )
        a.check(
            f"4.2s R{r} 步进 {expected_step[r]*100:.0f}%",
            abs(RADAR_TRAIL_STEP_BY_REGIME.get(r) - expected_step[r]) < 1e-9
            and abs(get_radar_trail_step(r) - expected_step[r]) < 1e-9,
        )
        a.check(
            f"4.2b R{r} 呼吸 {expected_breath[r]}ATR",
            abs(RADAR_BREATH_ATR_BY_REGIME.get(r) - expected_breath[r]) < 1e-9
            and abs(get_radar_breath_atr(r) - expected_breath[r]) < 1e-9,
        )
    a.check(
        "4.2c 旧阶段紧追ATR表已清空",
        not RADAR_STAGE_ATR_MULT,
    )
    a.check(
        "4.2d 生产雷达用适度追随",
        "_radar_breath_atr" in sup
        and "_radar_trail_step" in sup
        and "RADAR_TRAIL_MIN_INTERVAL_SEC" in sup
        and "适度追随" in sup,
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
        "钉钉攒批+重试+标题去重",
        "DINGTALK_BATCH" in dt and "_post_with_retry" in dt and "WECHAT_WEBHOOK" in dt
        and "DINGTALK_TITLE_DEDUP_SEC" in dt
        and "DINGTALK_ALERT_DEDUP_SEC" in dt
        and "report_position_qty_reconcile" in dt,
    )
    a.check(
        "钉钉宣称挂 TV硬止损",
        "TV硬止损 · UPDATE_SL 已同步盘口" in dt
        and "VPS宽硬止损" not in dt,
    )
    a.check(
        "UPDATE_SL 同步盘口",
        "已按 TV tv_sl 改挂盘口硬止损" in dt
        and "永不挂 TV 紧止损" not in dt,
    )
    a.check(
        "钉钉开仓三轨文案",
        "TV硬止损已挂(closePosition)" in dt
        and "三轨不抢份额" in dt
        and "雷达待命" in dt
        and "VPS硬止损已挂" not in dt,
    )
    a.check(
        "钉钉档位对账字段",
        "开仓档位(硬止损/TP)" in dt and "TV信号档位" in dt,
    )


def audit_readme_consistency(a: Audit):
    a.section("README 一致性")
    readme = _read(os.path.join(ROOT, "README.md"))
    a.check(
        "README 硬止损描述",
        "TV 硬止损" in readme
        and "tv_sl" in readme
        and ("VPS 自主硬止损（开仓价" not in readme),
    )
    a.check("README 检查清单链接", "check_vps_logic" in readme or "VPS实盘检查清单" in readme)
    a.check("README 双品种", "XAU" in readme and "ETH" in readme)
    a.check(
        "README 当前版本对齐代码",
        "v13.85.0-no-hard-cap" in readme
        and "开仓裸仓闸" in readme
        and "closePosition" in readme
        and "TV_RISK_FORMULA" in _read(os.path.join(ROOT, "webhook_parser.py"))
        and "risk_pct" in readme
        and "HARD_NOTIONAL_CAP = 0.0" in _read(os.path.join(ROOT, "webhook_parser.py"))
        and "无硬上限" in readme
        and "50%" in readme
        and "80%" in readme
        and "exit_source" in readme
        and "价到" in readme
        and "reduceOnly" in readme
        and "先平后开" in readme
        and "穿价" in readme
        and "mark@1s" in readme
        and "一律先平后开" in readme
        and "三轨不抢份额" in readme
        and "place_failed_keep_old" in _read(os.path.join(ROOT, "position_supervisor_binance.py"))
        and "实盘事故与优化备忘" in readme
        and "_force_hang_open_defenses" in _read(os.path.join(ROOT, "position_supervisor_binance.py"))
        and "_bind_tv_open_defenses" in _read(os.path.join(ROOT, "position_supervisor_binance.py"))
        and "v13.85.0-no-hard-cap" in _read(os.path.join(ROOT, "position_supervisor_binance.py"))
        and "硬止损失败·撤销开仓防裸奔" in _read(os.path.join(ROOT, "position_supervisor_binance.py")),
    )
    a.check(
        "README 雷达分档激活",
        "50%" in readme and "60%" in readme and "70%" in readme and "80%" in readme
        and "1.0 ATR" in readme,
    )
    a.check("README UPDATE_SL 同步盘口", "UPDATE_SL" in readme and "按 TV" in readme)


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
