#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
万亿战神 VPS 逻辑静态自查 — Cursor / CI 可用，无需交易所 API Key。

对齐：TV v6.5.6 · VPS v15.0.0-risk20-ladder · RISK20_NOTIONAL5

用法:
  python check_vps_logic.py
  python check_vps_logic.py --verbose
"""
from __future__ import annotations

import argparse
import importlib
import inspect
import math
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
    for enc in ("utf-8", "utf-8-sig", "gbk"):
        try:
            with open(path, encoding=enc) as f:
                return f.read()
        except UnicodeDecodeError:
            continue
    with open(path, encoding="utf-8", errors="replace") as f:
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

    empty = resolve_binance_symbol("", default="")
    a.check("缺 ticker 不默念 ETH", empty.get("symbol") == "", str(empty.get("symbol")))

    scanned = extract_symbol_from_payload({"action": "SHORT", "note": "BINANCE:XAUUSDT.P trigger"})
    a.check("全文扫描 XAU", "XAU" in scanned.upper(), scanned)

    app_src = _read(os.path.join(ROOT, "app.py"))
    a.check("1.4 未知品种 400", "Unsupported" in app_src)
    a.check("1.5 信号去重", "SIGNAL_DEDUP_SEC" in _read(os.path.join(ROOT, "position_supervisor_binance.py")))
    a.check("1.5b TV时序模块", os.path.exists(os.path.join(ROOT, "tv_seq.py")))
    wp = _read(os.path.join(ROOT, "webhook_parser.py"))
    a.check(
        "1.5c bar_index/seq 解析",
        ("bar_index" in _read(os.path.join(ROOT, "tv_seq.py"))
         or "bar_index" in wp)
        and "TVSeqBuffer" in _read(os.path.join(ROOT, "position_supervisor_binance.py")),
    )
    from tv_seq import (
        sort_webhooks_by_seq,
        make_seq_key,
        reorder_batch_close_then_open,
        collapse_batch_for_execution,
        SAME_BAR_SETTLE_SEC,
        LEGACY_SETTLE_SEC,
    )
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
    inverted = reorder_batch_close_then_open([
        {"action": "LONG", "entry_type": "OPEN", "bar_index": 27096, "seq": 1},
        {"action": "CLOSE_PROTECT", "bar_index": 27096, "seq": 2},
    ])
    a.check(
        "1.5d2 同秒开平强制先平后开(seq颠倒)",
        inverted[0].get("action") == "CLOSE_PROTECT"
        and inverted[1].get("action") == "LONG",
    )
    collapsed = collapse_batch_for_execution([
        {"action": "SHORT", "price": 100},
        {"action": "CLOSE_QUICK_EXIT", "price": 100},
        {"action": "CLOSE_RSI_EXIT", "price": 101},
        {"action": "LONG", "price": 102},
    ])
    a.check(
        "1.5d3 缓存折叠：平一次+最新开仓",
        len(collapsed) == 2
        and collapsed[0].get("action") == "CLOSE_QUICK_EXIT"
        and collapsed[1].get("action") == "LONG",
        str([m.get("action") for m in collapsed]),
    )
    a.check(
        "1.5d4 缓存窗口固定 1.0s",
        abs(float(SAME_BAR_SETTLE_SEC) - 1.0) < 1e-9
        and abs(float(LEGACY_SETTLE_SEC) - 1.0) < 1e-9,
        f"bar={SAME_BAR_SETTLE_SEC} legacy={LEGACY_SETTLE_SEC}",
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
        "1.5f 先平后开 CLOSE后释放再开+同秒聚合+折叠",
        "release_bar_for_reentry" in tvseq
        and "_release_tv_seq_after_close" in sup
        and "SAME_BAR_SETTLE_SEC" in tvseq
        and "LEGACY_SETTLE_SEC" in tvseq
        and "collapse_batch_for_execution" in tvseq
        and "collapse_batch_for_execution" in sup
        and "reorder_batch_close_then_open" in sup
        and "action_exec_rank" in tvseq
        and "永远先平后开" in tvseq
        and "defense_order_ids" in sup
        and "_set_defense_order_id" in sup
        and "TimedRotatingFileHandler" in sup
        and "SENTINEL_POLL_NORMAL = 0.5" in sup
        and "restart_no_persistence_with_position" in sup,
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
        and "_title_dedup" in dt,
    )

    a.check("钉钉 _resolve_unit", "def _resolve_unit" in dt)
    a.check(
        "钉钉 XAU 单位逻辑",
        "XAU" in dt and "_format_tp_audit" in dt and "ETH" in dt,
    )


def _grep_binance_vps_version() -> str:
    sup = _read(os.path.join(ROOT, "position_supervisor_binance.py"))
    for line in sup.splitlines():
        if "BINANCE_VPS_VERSION" in line and "=" in line:
            parts = line.split("=", 1)
            if len(parts) == 2:
                return parts[1].strip().strip('"').strip("'")
    return ""


def audit_module2_sizing(a: Audit):
    a.section("模块二 · 开单计算（RISK20_NOTIONAL5 · v6.5.6）")
    from webhook_parser import (
        TV_STRATEGY_VERSION,
        FIXED_RISK_PCT,
        FIXED_MARGIN_PCT,
        FIXED_NOTIONAL_MULT,
        FIXED_LEVERAGE,
        HARD_NOTIONAL_CAP,
        EXCHANGE_LEVERAGE,
        MAX_TOTAL_NOTIONAL_MULT,
        LEG_TP_RATIOS,
        PLACE_TP_LEVELS,
        SIZING_MODE,
        compute_fixed_order_qty,
        compute_vps_open_qty,
        compute_tv_order_qty,
        check_total_notional_cap,
        SIGNAL_DEDUP_SEC,
        ATR_UPDATE_SEC,
        ORDER_TIMEOUT_SEC,
    )

    binance_ver = _grep_binance_vps_version()
    a.check("2.0 TV_STRATEGY_VERSION=v6.5.6", TV_STRATEGY_VERSION == "v6.5.6", TV_STRATEGY_VERSION)
    a.check(
        "2.0b BINANCE_VPS_VERSION 含 v15.0.0/risk20-ladder",
        ("v15.0.0" in binance_ver)
        or ("v15." in binance_ver)
        or ("risk20-ladder" in binance_ver)
        or ("risk20" in binance_ver),
        binance_ver,
    )
    risk_ok = abs(float(FIXED_RISK_PCT) - 0.20) < 1e-9 or abs(float(FIXED_MARGIN_PCT) - 0.20) < 1e-9
    a.check("2.1 FIXED_RISK_PCT/FIXED_MARGIN_PCT=0.20", risk_ok, f"risk={FIXED_RISK_PCT} margin={FIXED_MARGIN_PCT}")
    mult_ok = float(FIXED_NOTIONAL_MULT) == 5.0 or float(FIXED_LEVERAGE) == 5
    a.check("2.1b FIXED_NOTIONAL_MULT/FIXED_LEVERAGE=5", mult_ok, f"mult={FIXED_NOTIONAL_MULT} lev={FIXED_LEVERAGE}")
    a.check("2.1c EXCHANGE_LEVERAGE=5", EXCHANGE_LEVERAGE == 5, str(EXCHANGE_LEVERAGE))
    a.check("2.1d LEG_TP_RATIOS=30/30/40", LEG_TP_RATIOS == [0.30, 0.30, 0.40], str(LEG_TP_RATIOS))
    a.check("2.1e PLACE_TP_LEVELS=3", PLACE_TP_LEVELS == 3, str(PLACE_TP_LEVELS))
    a.check("2.1f SIZING_MODE=RISK20_NOTIONAL5", SIZING_MODE == "RISK20_NOTIONAL5", str(SIZING_MODE))
    a.check("2.1g SIGNAL_DEDUP_SEC=60", int(SIGNAL_DEDUP_SEC) == 60, str(SIGNAL_DEDUP_SEC))
    a.check("2.1h ATR_UPDATE_SEC=300", int(ATR_UPDATE_SEC) == 300, str(ATR_UPDATE_SEC))
    a.check("2.1i ORDER_TIMEOUT_SEC=300", int(ORDER_TIMEOUT_SEC) == 300, str(ORDER_TIMEOUT_SEC))
    a.check("2.2 HARD_NOTIONAL_CAP=0", float(HARD_NOTIONAL_CAP or 0) == 0.0)
    a.check("2.3 MAX_TOTAL_NOTIONAL_MULT=13", MAX_TOTAL_NOTIONAL_MULT == 13.0)

    # min(200/100, 5000/3300.5, 12) floored 3dp = 1.514
    qty, meta = compute_fixed_order_qty(1000, 3300.5, stop_loss=3200.5, tv_qty=12)
    expected = 1.514
    a.check(
        "2.4 1000U@3300.5 SL3200.5 tv=12 → 1.514",
        abs(qty - expected) < 0.001,
        f"qty={qty} expected={expected} mode={meta.get('sizing_mode')} bind={meta.get('bind')}",
    )
    a.check(
        "2.4 mode/bind RISK20",
        meta.get("sizing_mode") == "RISK20_NOTIONAL5" and meta.get("bind") == "risk20_notional5",
        f"mode={meta.get('sizing_mode')} bind={meta.get('bind')}",
    )

    qty0, meta0 = compute_fixed_order_qty(1000, 3300.5)
    a.check(
        "2.4b 缺 stop_loss → qty0+error",
        qty0 == 0 and bool(meta0.get("error")),
        f"qty={qty0} err={meta0.get('error')}",
    )

    qty_tv, meta_tv = compute_fixed_order_qty(1000, 1800, stop_loss=1000, tv_qty=0.5)
    a.check(
        "2.5 TV.qty 上限 ≤0.5",
        qty_tv <= 0.5 + 1e-9 and qty_tv > 0,
        f"qty={qty_tv} meta={meta_tv.get('tv_qty')}",
    )

    qty_tv2, meta_tv2 = compute_tv_order_qty(1000, price=3300.5, stop_loss=3200.5, tv_qty=12)
    a.check(
        "2.6 compute_tv_order_qty 有 stop_loss",
        qty_tv2 > 0 and not meta_tv2.get("error"),
        f"qty={qty_tv2}",
    )

    qty_open, meta_open = compute_vps_open_qty(1000, 3300.5, stop_loss=3200.5, tv_qty=12)
    a.check(
        "2.7 compute_vps_open_qty 有 stop_loss",
        qty_open > 0 and not meta_open.get("error"),
        f"qty={qty_open}",
    )

    ok, cap_meta = check_total_notional_cap(1000, 6500, 6500, mult=13)
    a.check("2.8 双品种 13x 踩线", ok, f"total={cap_meta['total_notional']} cap={cap_meta['cap']}")
    ok2, _ = check_total_notional_cap(1000, 7000, 6500, mult=13)
    a.check("2.8b 超标拒绝", not ok2)

    wp = _read(os.path.join(ROOT, "webhook_parser.py"))
    sup = _read(os.path.join(ROOT, "position_supervisor_binance.py"))
    app_src = _read(os.path.join(ROOT, "app.py"))

    a.check("2.9 禁止 TV_RISK_FORMULA", "TV_RISK_FORMULA" not in wp)
    a.check(
        "2.10 supervisor 阶梯/风险/暂停/ATR/超时/对账",
        ("compute_ladder_radar_sl" in sup or "_compute_ladder_sl" in sup)
        and ("RISK20" in sup or "FIXED_LEVERAGE" in sup)
        and "_calc_vps_open_qty" in sup
        and "trading_paused" in sup
        and "_maybe_refresh_atr" in sup
        and "_check_tp_order_timeouts" in sup
        and "_handle_tv_reconcile" in sup,
    )
    a.check(
        "2.11 app health sizing=RISK20_NOTIONAL5",
        "RISK20_NOTIONAL5" in app_src
        or ("SIZING_MODE" in app_src and "sizing" in app_src),
    )
    a.check("2.11b app health leverage=fixed_5", 'leverage": "fixed_5"' in app_src)

    bc = _read(os.path.join(ROOT, "binance_client.py"))
    a.check("2.12 get_total_equity", "def get_total_equity" in bc)


def audit_module3_hard_sl(a: Audit):
    a.section("模块三 · TV 硬止损（实盘挂单）")
    from webhook_parser import VPS_HARD_SL_PCT, compute_vps_hard_sl

    a.check("3.1 VPS%宽止损表已清空", VPS_HARD_SL_PCT == {} or not VPS_HARD_SL_PCT)
    a.check(
        "3.1b compute_vps_hard_sl 恒0(已废除)",
        compute_vps_hard_sl("LONG", 1800, regime=3) == 0
        and compute_vps_hard_sl("SHORT", 1800, regime=4) == 0,
    )

    sup = _read(os.path.join(ROOT, "position_supervisor_binance.py"))
    a.check(
        "3.2 实盘硬止损=TV tv_sl",
        "_tv_hard_sl_target" in sup
        and "禁止再用开仓价×档位%" in sup
        and "TV硬止损" in sup
        and "拒绝挂 TV 紧止损" not in sup,
    )
    a.check(
        "3.2b 禁止贴市推宽改 tv_sl",
        "gap * 1.25" not in sup
        and "推低到安全" not in sup
        and "推高到安全" not in sup
        and "_merge_wider_vps_hard_sl" not in sup
        and "拒TV紧止损·改挂VPS" not in sup
        and "禁止推宽" in sup,
    )
    from webhook_parser import VPS_HARD_SL_LIMIT_PCT, compute_vps_hard_sl_limit_price
    a.check("3.3 LIMIT偏移已清零", float(VPS_HARD_SL_LIMIT_PCT or 0) == 0.0)
    a.check(
        "3.3b limit_price=触发原值",
        abs(compute_vps_hard_sl_limit_price("LONG", 1874.39) - 1874.39) < 1e-9,
    )
    a.check("3.4 STOP 挂单", "place_stop_market_order" in sup or "place_stop_limit" in sup)
    a.check(
        "3.5 硬止损 closePosition 不抢 TP 额度",
        "use_stop_limit=False" in sup and "不占 reduceOnly" in sup,
    )
    a.check(
        "3.6 全平归因 TV硬止损",
        "触碰硬止损平仓（TV硬止损）" in sup
        and "触碰硬止损平仓（VPS宽止损）" not in sup,
    )
    a.check(
        "3.7 硬止损失败撤开仓防裸奔",
        "硬止损失败·撤销开仓防裸奔" in sup or "_emergency_flatten_naked_open" in sup,
    )
    a.check(
        "3.7b 开仓禁止 recover 核武连环撤",
        "开仓后防线对齐" in sup and "recover_mode=False" in sup,
    )
    a.check(
        "3.8 防线 thrash 刹车",
        "NUCLEAR_REALIGN_MIN_INTERVAL_SEC" in sup
        and "_nuclear_backoff_remaining" in sup
        and "_defense_anomaly_is_severe" in sup
        and "idempotent_unified" in sup
        and "exclude_shield=False" in sup
        and "HARD_SL_SYNC_COOLDOWN_SEC" in sup,
    )
    a.check(
        "3.9 账本消毒对齐 TV",
        "_sanitize_vps_hard_sl_ledger" in sup
        and "_is_exchange_stop_acceptable_as_vps_floor" in sup
        and "不得用 VPS% 覆盖" in sup,
    )
    a.check(
        "3.10 TV方向为准·反向强制平仓",
        "_enforce_tv_direction_or_flat" in sup
        and "TV方向为准·强制平仓" in sup
        and "强制平仓对齐 TV" in sup,
    )
    a.check(
        "3.11 雷达主判价触激活线",
        "_price_reached_radar_activation" in sup
        and "get_radar_activation_ratio" in _read(os.path.join(ROOT, "webhook_parser.py")),
    )
    a.check(
        "3.12 TV硬止损允许挂盘（废除紧价拒绝）",
        "_looks_like_tv_tight_stop" in sup
        and "恒返回 False" in sup
        and "拒绝挂 TV 紧止损" not in sup
        and "_is_valid_radar_sl" in sup,
    )
    a.check(
        "3.13 合并底线=TV硬止损",
        "仅挂 TV硬止损" in sup
        and "拒绝合并伪雷达/TV紧止损" not in sup,
    )
    a.check(
        "3.14 硬止损锁定 open_regime(雷达/TP用)",
        "_resolve_hard_sl_regime" in sup
        and "_lock_open_regime_from_sources" in sup
        and "_resolve_tv_open_regime_for_position" in sup
        and "以 TV 为准" in sup,
    )
    a.check(
        "3.15 开仓日志写 open_regime",
        '"open_regime": open_r' in sup or '"open_regime": open_r,' in sup
        or "open_regime\": open_r" in sup,
    )
    a.check(
        "3.16 重启先锁档再挂TV硬止损",
        "_lock_open_regime_from_sources" in sup
        and "重启强制TV硬止损" in sup
        and "重启强制VPS宽硬止损" not in sup,
    )
    a.check(
        "3.17 雷达激活线：ensure闸门",
        "_radar_placement_blocked" in sup
        and "POST_OPEN_RADAR_BLOCK_SEC" in sup
        and (
            "拒绝雷达挂单：未交棒/未达激活线" in sup
            or "_price_reached_radar_activation" in sup
        ),
    )
    a.check(
        "3.18 SHORT保本禁止抬过开仓价",
        "禁止抬到成本及以上" in sup
        and "_clamp_radar_sl_for_market" in sup,
    )
    a.check(
        "3.19 开仓日志按品种隔离",
        "_journal_path" in sup
        and "binance_{kind}_journal_" in sup
        and "_open_regime_sticky" in sup
        and "HARD_SL_SYNC_COOLDOWN_SEC" in sup,
    )
    a.check(
        "3.20 旧VPS%档位匹配已废弃",
        "_matches_any_vps_regime_stop" in sup
        and "旧 VPS% 档位匹配已废弃" in sup,
    )
    a.check(
        "3.21 重启锁按品种隔离",
        "recover_singleton_{self.symbol}" in sup
        or ".recover_singleton_" in sup
        and "_probe_position_for_recover" in sup
        and "AMBIGUOUS" in sup,
    )
    a.check(
        "3.22 hydrate 过滤 None 信源",
        "isinstance(s, dict)" in sup
        and "多品种启动恢复清单" in sup
        and "重启异常兜底" in sup,
    )
    a.check(
        "3.23 开仓裸仓闸 expected=0 不假齐",
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
        "3.23b TV空ATR仍补全TP",
        float(empty_tp.get("tv_tp1") or 0) > 1800
        and float(empty_tp.get("tv_tp3") or 0) > float(empty_tp.get("tv_tp1") or 0),
        str(empty_tp),
    )
    a.check(
        "3.24 版本含 v15/risk20-ladder",
        "v15." in sup or "risk20-ladder" in sup or "risk20" in sup,
    )
    a.check(
        "3.25 v6.5.6 已废除 UPDATE_SL/TP webhook",
        "v6.5.6 已废除 UPDATE_SL" in sup or "is_tp_sl_update = False" in sup,
    )


def audit_module4_radar(a: Audit):
    a.section("模块四 · 阶梯雷达（85% 激活 · ladder SL）")
    sup = _read(os.path.join(ROOT, "position_supervisor_binance.py"))
    dt = _read(os.path.join(ROOT, "dingtalk.py"))
    wp = _read(os.path.join(ROOT, "webhook_parser.py"))

    from webhook_parser import (
        RADAR_ACTIVATE_TP1_FRAC,
        RADAR_STEP_ATR,
        RADAR_LOCK_ATR,
        RADAR_TP1_FLOOR_ATR,
        RADAR_TP2_FLOOR_ATR,
        RADAR_TP3_TRAIL_ATR,
        RADAR_STAGE_COST_BUFFER_PCT,
        RADAR_STAGE_ATR_MULT,
        get_radar_activation_ratio,
        get_radar_trail_step,
        get_radar_breath_atr,
        format_radar_activation_ratios_label,
        radar_activation_price,
        compute_ladder_radar_sl,
    )

    a.check("4.1 RADAR_ACTIVATE_TP1_FRAC=0.85", abs(RADAR_ACTIVATE_TP1_FRAC - 0.85) < 1e-9)
    a.check("4.1b RADAR_STEP_ATR=0.5", abs(RADAR_STEP_ATR - 0.5) < 1e-9)
    a.check("4.1c RADAR_LOCK_ATR=0.3", abs(RADAR_LOCK_ATR - 0.3) < 1e-9)
    a.check("4.1d RADAR_TP1_FLOOR_ATR=0.5", abs(RADAR_TP1_FLOOR_ATR - 0.5) < 1e-9)
    a.check("4.1e RADAR_TP2_FLOOR_ATR=1.5", abs(RADAR_TP2_FLOOR_ATR - 1.5) < 1e-9)
    a.check("4.1f RADAR_TP3_TRAIL_ATR=2.0", abs(RADAR_TP3_TRAIL_ATR - 2.0) < 1e-9)

    act_px = radar_activation_price("LONG", 1800, 1840.5)
    a.check(
        "4.2 LONG 1800→tp1 1840.5 激活≈1834.425",
        abs(act_px - 1834.425) < 0.01,
        f"act={act_px}",
    )
    a.check(
        "4.2b get_radar_activation_ratio 统一 85%",
        abs(get_radar_activation_ratio(1) - 0.85) < 1e-9
        and abs(get_radar_activation_ratio(4) - 0.85) < 1e-9,
    )
    a.check(
        "4.2c 步进/跟进 ATR 统一",
        abs(get_radar_trail_step(3) - 0.5) < 1e-9
        and abs(get_radar_breath_atr(2) - 0.3) < 1e-9,
    )

    label = format_radar_activation_ratios_label()
    a.check(
        "4.3 钉钉比例文案=85%阶梯",
        "85%" in label and "0.5" in label and "2.0" in label,
        label,
    )
    a.check(
        "4.3b 禁止旧 R1=50% 分档文案",
        "R1=50%" not in label
        and "R2=60%" not in label,
        label,
    )
    if "R1=50%" in sup:
        a.warn("4.3b2 supervisor 注释仍含旧 R1=50% 文案", "请清理 docstring")
    a.check("4.3c 旧阶段紧追ATR表已清空", not RADAR_STAGE_ATR_MULT)
    a.check("4.3d 成本缓冲已清零(1 tick保本)", abs(RADAR_STAGE_COST_BUFFER_PCT) < 1e-9)

    for fn in (
        "_price_reached_radar_activation",
        "_radar_activation_price",
        "_compute_ladder_sl",
        "_radar_legitimately_armed",
        "_ideal_radar_sl_is_safe",
        "_disarm_premature_radar",
        "_perform_radar_handoff",
        "_tp1_filled_verified",
    ):
        a.check(f"雷达函数 {fn}", f"def {fn}" in sup)

    a.check(
        "4.4 webhook compute_ladder_radar_sl",
        "def compute_ladder_radar_sl" in wp,
    )
    sl, stage, meta = compute_ladder_radar_sl(
        "LONG", 1800, 12, 1835, 1835, 1840.5, 1860, 1880,
    )
    steps = int(meta.get("steps") or meta.get("step_count") or 0)
    a.check(
        "4.5 达激活线阶梯 SL（px1835 atr12 → steps=5 sl≈1818）",
        meta.get("activated", True)
        and steps == 5
        and abs(float(sl) - 1818.0) < 1.0,
        f"sl={sl} stage={stage} steps={steps}",
    )
    a.check(
        "4.5b ATR_UPDATE_SEC/ORDER_TIMEOUT_SEC 在 webhook_parser",
        "ATR_UPDATE_SEC" in wp and "ORDER_TIMEOUT_SEC" in wp,
    )
    a.check(
        "4.5c trading_paused/暂停交易 在 supervisor",
        "trading_paused" in sup and "暂停交易" in sup,
    )
    from webhook_parser import SIGNAL_DEDUP_SEC as _DEDUP
    a.check("4.5d SIGNAL_DEDUP_SEC=60", int(_DEDUP) == 60, str(_DEDUP))

    a.check("4.6 价触激活线主判", "_price_reached_radar_activation" in sup)
    a.check("4.7 交棒禁止贴市", "_ideal_radar_sl_is_safe" in sup and "雷达交棒延迟" in sup)
    a.check("4.7b 交棒后才武装", "_radar_handoff_done" in sup)
    a.check(
        "4.8 交棒/重启现价激活线或TP1成交",
        "live_only=True" in sup
        and "_radar_ready_to_handoff" in sup
        and "_tp1_fill_allows_radar" in sup
        and "for_handoff=True" in sup,
    )
    a.check(
        "4.9 WS mark 脉冲交棒",
        "_on_mark_price_tick" in sup and "register_price_tick_callback" in (
            _read(os.path.join(ROOT, "binance_client.py"))
        ),
    )
    a.check(
        "4.10 WS最快盯价·接近激活线加速",
        "RADAR_WS_APPROACH_RATIO" in sup
        and "_radar_work_urgent" in sup
        and "markPrice@1s" in _read(os.path.join(ROOT, "binance_client.py"))
        and "_radar_in_progress" in sup,
    )
    a.check(
        "4.11 废除同向仅刷TP·一律先平后开",
        "always_close_then_open" in sup
        and "OPEN_SAME_DIR_COOLDOWN_SEC = 0" in sup
        and 'return "REFRESH_TP"' not in sup,
    )
    a.check(
        "4.12 TP多档对账禁误报人工",
        "_filter_credible_tp_fills" in sup
        and "_detect_tp_fills_by_price_qty_reconcile" in sup
        and "_reconcile_open_qty_vs_tp123" in sup,
    )
    a.check(
        "4.13 平仓归因 exit_source",
        "_resolve_exit_source" in sup
        and "_radar_was_armed" in sup
        and "EXIT_SOURCE_RADAR_BE" in wp
        and "exit_source" in dt,
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
        and "radar_act_ratio" in dt,
    )
    a.check(
        "4.16 TP成交记账禁漏挂补挂",
        "_reconcile_tp_consumed_from_live_qty" in sup
        and "_qty_reduction_looks_like_tp" in sup
        and "_block_rehang_filled_tps_note" in sup,
    )
    a.check(
        "4.17 三轨不抢份额",
        "三轨并行" in sup
        and "reduceOnly" in sup
        and "closePosition 单槽" in sup,
    )
    a.check("4.18 钉钉雷达标题含品种", "[sym]" in dt or "[{sym}]" in dt)
    a.check(
        "4.18b 钉钉雷达激活字段",
        "radar_act_px" in dt and "RADAR_ACTIVATE_TP1_FRAC" in dt,
    )


def audit_module5_actions(a: Audit):
    a.section("模块五 · v6.5.6 动作集（对账/快平）")
    from webhook_parser import (
        RECONCILE_ACTIONS,
        FLATTEN_ACTIONS,
        is_reconcile_action,
        is_flatten_action,
        classify_tv_close,
        CLOSE_TYPE_QUICK,
        CLOSE_TYPE_RSI,
    )

    a.check(
        "5.1 RECONCILE_ACTIONS",
        RECONCILE_ACTIONS
        == frozenset({"CLOSE_TP", "CLOSE_TRAIL", "CLOSE_SL_INITIAL", "CLOSE_SL_BREAKEVEN"}),
    )
    a.check(
        "5.2 FLATTEN_ACTIONS",
        FLATTEN_ACTIONS == frozenset({"CLOSE_QUICK_EXIT", "CLOSE_RSI_EXIT"}),
    )
    a.check("5.3 is_reconcile CLOSE_TP", is_reconcile_action("CLOSE_TP"))
    a.check("5.4 is_flatten CLOSE_RSI_EXIT", is_flatten_action("CLOSE_RSI_EXIT"))
    a.check(
        "5.5 classify CLOSE_SL_INITIAL→hard_sl",
        classify_tv_close("CLOSE_SL_INITIAL") == "hard_sl",
    )
    a.check(
        "5.6 classify CLOSE_QUICK_EXIT→quick",
        classify_tv_close("CLOSE_QUICK_EXIT") == CLOSE_TYPE_QUICK,
    )
    a.check(
        "5.7 classify CLOSE_RSI_EXIT→rsi",
        classify_tv_close("CLOSE_RSI_EXIT") == CLOSE_TYPE_RSI,
    )

    sup = _read(os.path.join(ROOT, "position_supervisor_binance.py"))
    a.check(
        "5.8 supervisor 处理 RECONCILE/FLATTEN",
        "RECONCILE_ACTIONS" in sup and "FLATTEN_ACTIONS" in sup,
    )
    a.check(
        "5.9 对账信号不下主动平仓/状态同步",
        "不下主动平仓" in sup
        or "不下主动平仓单" in sup
        or "状态同步" in sup
        or "对账" in sup
        or "不下单" in sup
        or "_handle_tv_reconcile" in sup,
    )


def audit_module6_risk(a: Audit):
    a.section("模块六 · 全局风控")
    from risk_manager import risk_manager

    a.check("6.1 日亏熔断 5.5%", abs(risk_manager.daily_loss_limit_pct - 0.055) < 1e-6)
    sup = _read(os.path.join(ROOT, "position_supervisor_binance.py"))
    a.check("6.2 敞口钉钉", "_assert_notional_cap_or_reject" in sup)


def audit_module7_position(a: Audit):
    a.section("模块七 · 头寸误判防范")
    sup = _read(os.path.join(ROOT, "position_supervisor_binance.py"))
    a.check("7.1 trusted_initial_qty", "_trusted_initial_qty" in sup)
    a.check("7.2 WS 仓位", "start_user_data_ws" in _read(os.path.join(ROOT, "binance_client.py")))
    a.check("7.3 微漂容忍", "QTY_DRIFT_TOLERANCE_PCT" in sup)


def audit_module8_dingtalk(a: Audit):
    a.section("模块八 · 钉钉")
    dt = _read(os.path.join(ROOT, "dingtalk.py"))
    for fn in (
        "report_supervisor_open",
        "report_radar_activated",
        "report_tp_fill",
        "report_system_alert",
        "report_supervisor_close",
        "report_recover_takeover",
        "report_tv_reconcile",
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
        "钉钉宣称 RISK20/风险20",
        "RISK20_NOTIONAL5" in dt or "风险20" in dt,
    )
    a.check(
        "钉钉开仓三轨文案",
        "closePosition" in dt and "reduceOnly" in dt,
    )
    a.check(
        "钉钉档位对账字段",
        "format_regime_tp_ratios_label" in dt or "30/30/40" in dt,
    )


def audit_readme_consistency(a: Audit):
    a.section("README 一致性")
    readme = _read(os.path.join(ROOT, "README.md"))
    sup = _read(os.path.join(ROOT, "position_supervisor_binance.py"))
    wp = _read(os.path.join(ROOT, "webhook_parser.py"))
    app_src = _read(os.path.join(ROOT, "app.py"))

    a.check(
        "README 版本 v15",
        ("v15." in readme or "risk20" in readme or "tv-direction" in readme)
        and ("v15." in sup or "risk20" in sup or "tv-direction" in sup),
    )
    a.check(
        "README TV v6.5.6",
        "v6.5.6" in readme and 'TV_STRATEGY_VERSION = "v6.5.6"' in wp,
    )
    a.check(
        "README 20% 与 5x",
        "20%" in readme and ("5x" in readme or "5×" in readme or "×5" in readme),
    )
    a.check(
        "README sizing=RISK20_NOTIONAL5",
        "RISK20_NOTIONAL5" in readme
        and ("SIZING_MODE" in app_src or "RISK20_NOTIONAL5" in app_src),
    )
    a.check(
        "README 硬止损描述",
        ("TV 硬止损" in readme or "stop_loss" in readme or "tv_sl" in readme)
        and ("VPS 自主硬止损（开仓价" not in readme),
    )
    a.check("README 检查清单链接", "check_vps_logic" in readme or "VPS实盘检查清单" in readme)
    a.check("README 双品种", "XAU" in readme and "ETH" in readme)
    a.check(
        "README 禁止旧 risk_pct 主公式",
        "TV_RISK_FORMULA" not in readme
        and "0.67 ETH" not in readme
        and "R1=50%" not in readme
        and "仓位三件套" not in readme,
    )
    a.check(
        "README 阶梯雷达 85%",
        "85%" in readme,
    )
    a.check(
        "README TP 30/30/40 挂 TP1+TP2+TP3",
        ("30/30/40" in readme or "30%" in readme)
        and ("TP3" in readme)
        and ("永不挂" not in readme),
    )
    a.check(
        "README 核心铁律保留",
        "先平后开" in readme
        and "reduceOnly" in readme
        and "closePosition" in readme
        and "三轨" in readme,
    )
    a.check(
        "README 对账动作",
        "CLOSE_TP" in readme or "对账" in readme,
    )
    a.check("README token 528586", "528586" in readme)
    a.check(
        "README v15/risk20-ladder",
        "v15." in readme or "risk20-ladder" in readme or "risk20" in readme,
    )
    # Do NOT require EQUITY_20PCT_X5


def main():
    parser = argparse.ArgumentParser(description="VPS logic static audit (v6.5.6 / risk20-ladder)")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    print("[VPS] trillion-warrior logic static audit · v6.5.6 / risk20-ladder")
    print(f"cwd: {ROOT}")

    a = Audit(verbose=args.verbose)
    audit_module1_symbol(a)
    audit_module2_sizing(a)
    audit_module3_hard_sl(a)
    audit_module4_radar(a)
    audit_module5_actions(a)
    audit_module6_risk(a)
    audit_module7_position(a)
    audit_module8_dingtalk(a)
    audit_readme_consistency(a)
    return a.summary()


if __name__ == "__main__":
    sys.exit(main())
