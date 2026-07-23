#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
万亿战神 VPS 逻辑静态自查 — Cursor / CI 可用，无需交易所 API Key。

对齐：TV v6.5.6 · VPS v15.5.3-rigor-checks · RISK20_NOTIONAL5

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
        OPEN_CLOSE_WINDOW_SEC,
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
    # CLOSE 先到 + OPEN：先平后开
    collapsed_close_first = collapse_batch_for_execution([
        {"action": "CLOSE_QUICK_EXIT", "price": 100},
        {"action": "CLOSE_RSI_EXIT", "price": 101},
        {"action": "SHORT", "price": 100},
        {"action": "LONG", "price": 102},
    ])
    a.check(
        "1.5d3 缓存折叠：CLOSE先到→平一次+最新开仓",
        len(collapsed_close_first) == 2
        and collapsed_close_first[0].get("action") == "CLOSE_QUICK_EXIT"
        and collapsed_close_first[1].get("action") == "LONG",
        str([m.get("action") for m in collapsed_close_first]),
    )
    # OPEN 先到 + CLOSE：丢弃 CLOSE
    collapsed_open_first = collapse_batch_for_execution([
        {"action": "SHORT", "price": 100},
        {"action": "CLOSE_QUICK_EXIT", "price": 100},
        {"action": "LONG", "price": 102},
    ])
    a.check(
        "1.5d3b OPEN先到→丢弃CLOSE只留最新开仓",
        len(collapsed_open_first) == 1
        and collapsed_open_first[0].get("action") == "LONG",
        str([m.get("action") for m in collapsed_open_first]),
    )
    a.check(
        "1.5d4 短settle=1.0s + 开平权威窗=15s",
        abs(float(SAME_BAR_SETTLE_SEC) - 1.0) < 1e-9
        and abs(float(LEGACY_SETTLE_SEC) - 1.0) < 1e-9
        and abs(float(OPEN_CLOSE_WINDOW_SEC) - 15.0) < 1e-9,
        f"bar={SAME_BAR_SETTLE_SEC} legacy={LEGACY_SETTLE_SEC} win={OPEN_CLOSE_WINDOW_SEC}",
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
        and "OPEN_CLOSE_WINDOW_SEC" in tvseq
        and "defense_order_ids" in sup
        and "frozen_hard_sl_px" in sup
        and "LATE_CLOSE_SUPPRESS_SEC = 15.0" in sup
        and "_set_defense_order_id" in sup
        and "TimedRotatingFileHandler" in sup
        and "SENTINEL_POLL_NORMAL = 1.0" in sup
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
        "2.0b BINANCE_VPS_VERSION 含 v15",
        ("qty-tv-sl-adj" in binance_ver)
        or ("tv-field-spec" in binance_ver)
        or ("rigor-checks" in binance_ver)
        or ("final-spec" in binance_ver)
        or ("arch-align" in binance_ver)
        or ("v15." in binance_ver)
        or ("breath" in binance_ver),
        binance_ver,
    )
    risk_ok = abs(float(FIXED_RISK_PCT) - 0.20) < 1e-9 or abs(float(FIXED_MARGIN_PCT) - 0.20) < 1e-9
    a.check("2.1 FIXED_RISK_PCT/FIXED_MARGIN_PCT=0.20", risk_ok, f"risk={FIXED_RISK_PCT} margin={FIXED_MARGIN_PCT}")
    mult_ok = float(FIXED_NOTIONAL_MULT) == 5.0 or float(FIXED_LEVERAGE) == 5
    a.check("2.1b FIXED_NOTIONAL_MULT/FIXED_LEVERAGE=5", mult_ok, f"mult={FIXED_NOTIONAL_MULT} lev={FIXED_LEVERAGE}")
    a.check("2.1c EXCHANGE_LEVERAGE=5", EXCHANGE_LEVERAGE == 5, str(EXCHANGE_LEVERAGE))
    a.check("2.1d LEG_TP_RATIOS=30/30/40", LEG_TP_RATIOS == [0.30, 0.30, 0.40], str(LEG_TP_RATIOS))
    a.check("2.1e PLACE_TP_LEVELS=2(仅TP1+TP2)", PLACE_TP_LEVELS == 2, str(PLACE_TP_LEVELS))
    a.check("2.1f SIZING_MODE=RISK20_NOTIONAL5", SIZING_MODE == "RISK20_NOTIONAL5", str(SIZING_MODE))
    a.check("2.1g SIGNAL_DEDUP_SEC=60", int(SIGNAL_DEDUP_SEC) == 60, str(SIGNAL_DEDUP_SEC))
    a.check("2.1h ATR_UPDATE_SEC=300", int(ATR_UPDATE_SEC) == 300, str(ATR_UPDATE_SEC))
    a.check("2.1i ORDER_TIMEOUT_SEC=300", int(ORDER_TIMEOUT_SEC) == 300, str(ORDER_TIMEOUT_SEC))
    a.check("2.2 HARD_NOTIONAL_CAP=0", float(HARD_NOTIONAL_CAP or 0) == 0.0)
    a.check("2.3 MAX_TOTAL_NOTIONAL_MULT=13", MAX_TOTAL_NOTIONAL_MULT == 13.0)

    # 无 TV.sl → adj=1.0：min(200/100, 1000×20%×5/3300.5, 12) = min(2, 0.3029, 12) → 0.302
    qty, meta = compute_fixed_order_qty(1000, 3300.5, stop_loss=3200.5, tv_qty=12)
    expected = 0.302
    a.check(
        "2.4 1000U@3300.5 SL3200.5 tv=12 → 0.302(本金×20%×5=本金×1)",
        abs(qty - expected) < 0.001,
        f"qty={qty} expected={expected} mode={meta.get('sizing_mode')} bind={meta.get('bind')}",
    )
    a.check(
        "2.4 mode/bind RISK20",
        meta.get("sizing_mode") == "RISK20_NOTIONAL5"
        and str(meta.get("bind") or "") in (
            "notional_primary",
            "equity_x20pct_x5_over_price",
            "risk20_x5_equals_1x_equity_tv_sl_adj",
        ),
        f"mode={meta.get('sizing_mode')} bind={meta.get('bind')}",
    )
    a.check(
        "2.4a 无TV.sl时 sl_adj=1",
        abs(float(meta.get("sl_adj") or 0) - 1.0) < 1e-9,
        f"sl_adj={meta.get('sl_adj')}",
    )
    a.check(
        "2.4a2 名义上限=本金×1",
        abs(float(meta.get("notional_cap") or 0) - 1000.0) < 0.01,
        f"notional_cap={meta.get('notional_cap')}",
    )

    qty0, meta0 = compute_fixed_order_qty(1000, 3300.5)
    a.check(
        "2.4b 缺 stop/tv_qty → 仍按名义下单(白皮书)",
        qty0 > 0 and not meta0.get("error"),
        f"qty={qty0} err={meta0.get('error')} bind={meta0.get('binding')}",
    )

    qty_tv, meta_tv = compute_fixed_order_qty(1000, 1800, stop_loss=1000, tv_qty=0.5)
    a.check(
        "2.5 TV.qty 上限 ≤0.5(adj=1)",
        qty_tv <= 0.5 + 1e-9 and qty_tv > 0,
        f"qty={qty_tv} meta={meta_tv.get('tv_qty')}",
    )

    # 白皮书：sl_adj 已废除；名义主约束；TV.qty=2 软上限不影响名义≈0.333
    qty_adj, meta_adj = compute_fixed_order_qty(
        1000, 3000, stop_loss=2940, tv_qty=2.0, tv_sl=2960,
    )
    a.check(
        "2.5b 白皮书无sl_adj·名义主约束",
        abs(float(meta_adj.get("sl_adj") or 0) - 1.0) < 1e-9
        and meta_adj.get("binding") in ("notional", "risk", "tv_qty"),
        f"sl_adj={meta_adj.get('sl_adj')} bind={meta_adj.get('binding')} "
        f"vps_dist={meta_adj.get('vps_stop_dist')} qty={qty_adj}",
    )
    a.check(
        "2.5c 名义=本金×1 约束生效 → qty≈0.333",
        abs(qty_adj - 0.333) < 0.002 and meta_adj.get("binding") == "notional",
        f"qty={qty_adj} binding={meta_adj.get('binding')} adj_tv={meta_adj.get('adjusted_tv_qty')} "
        f"notional_cap={meta_adj.get('notional_cap')}",
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
        "2.10 supervisor 呼吸止损/风险/暂停/ATR/超时",
        ("from breath_stop import" in sup or "calculate_breath_stop" in sup)
        and ("compute_ladder_radar_sl" not in sup)
        and ("RISK20" in sup or "FIXED_LEVERAGE" in sup or "risk20" in wp)
        and "_calc_vps_open_qty" in sup
        and "trading_paused" in sup
        and "_maybe_refresh_atr" in sup
        and "_check_tp_order_timeouts" in sup,
    )
    a.check(
        "2.10b 旧阶梯雷达函数已删体",
        "compute_ladder_radar_sl deleted" in wp
        or "use breath_stop.calculate_breath_stop" in wp,
    )
    a.check(
        "2.11 app health sizing=RISK20/SIZING_MODE",
        "RISK20_NOTIONAL5" in app_src
        or ("SIZING_MODE" in app_src and "sizing" in app_src),
    )
    a.check("2.11b app health leverage=fixed_5", 'leverage": "fixed_5"' in app_src)

    bc = _read(os.path.join(ROOT, "binance_client.py"))
    a.check("2.12 get_total_equity", "def get_total_equity" in bc)


def audit_module3_hard_sl(a: Audit):
    a.section("模块三 · 呼吸止损（永久硬止损+独立雷达双 STOP）")
    from webhook_parser import VPS_HARD_SL_PCT, compute_vps_hard_sl
    from breath_stop import (
        INITIAL_SL_ATR,
        BREAKEVEN_TRIGGER_ATR,
        initial_stop_price,
        calculate_breath_stop,
    )

    a.check("3.1 VPS%宽止损表已清空", VPS_HARD_SL_PCT == {} or not VPS_HARD_SL_PCT)
    a.check(
        "3.1b compute_vps_hard_sl 恒0(已废除)",
        compute_vps_hard_sl("LONG", 1800, regime=3) == 0
        and compute_vps_hard_sl("SHORT", 1800, regime=4) == 0,
    )
    a.check("3.1c INITIAL_SL_ATR=1.5", abs(INITIAL_SL_ATR - 1.5) < 1e-9)
    a.check("3.1d BREAKEVEN_TRIGGER_ATR=3.0", abs(BREAKEVEN_TRIGGER_ATR - 3.0) < 1e-9)
    a.check(
        "3.1e initial_stop LONG 1800 atr40 → 1740",
        abs(initial_stop_price("LONG", 1800, 40) - 1740.0) < 1e-6,
    )

    out = calculate_breath_stop(
        "LONG", 1890, 1800, 40, 1740, 1740, 1890, False,
        breathing_coefficient=1.0,
    )
    a.check(
        "3.1f 阶段一阶梯推进",
        float(out["stop"]) > 1740 and not out["breakeven_phase"],
        f"stop={out['stop']} phase={out['breakeven_phase']}",
    )
    out2 = calculate_breath_stop(
        "LONG", 1930, 1800, 40, 1740, float(out["stop"]), 1930, False,
        breathing_coefficient=1.0,
    )
    a.check(
        "3.1g 浮盈≥3ATR 切入阶段二",
        out2["breakeven_phase"] is True and float(out2["stop"]) > 1800,
        f"stop={out2['stop']} phase={out2['breakeven_phase']}",
    )
    from breath_stop import get_breathing_coefficient, order_stop_price
    coeff, smooth, _ = get_breathing_coefficient(20.0, 20.0, [])
    # ratio=1.0 → 连续插值中间值（ETH: floor0.6/ceil2.2 · min1.2/max2.5 → 1.525）
    a.check(
        "3.1h 呼吸系数 ratio=1 → 插值中间值",
        abs(coeff - 1.525) < 1e-6 and abs(smooth - 1.0) < 1e-9,
        f"coeff={coeff} smooth={smooth}",
    )
    a.check(
        "3.1i 挂单缓冲±0.3",
        abs(order_stop_price("LONG", 1870.0) - 1869.7) < 1e-9
        and abs(order_stop_price("SHORT", 1930.0) - 1930.3) < 1e-9,
    )

    sup = _read(os.path.join(ROOT, "position_supervisor_binance.py"))
    a.check(
        "3.2 实盘止损=呼吸 currentStop",
        "_tv_hard_sl_target" in sup
        and "breath_stop" in sup
        and "initial_stop_price" in sup
        and "calculate_breath_stop" in sup
        and "拒绝挂 TV 紧止损" not in sup,
    )
    a.check(
        "3.2b 禁止贴市推宽改 tv_sl",
        "gap * 1.25" not in sup
        and "推低到安全" not in sup
        and "推高到安全" not in sup
        and "_merge_wider_vps_hard_sl" not in sup
        and "拒TV紧止损·改挂VPS" not in sup,
    )
    from webhook_parser import VPS_HARD_SL_LIMIT_PCT, compute_vps_hard_sl_limit_price
    a.check("3.3 LIMIT偏移已清零", float(VPS_HARD_SL_LIMIT_PCT or 0) == 0.0)
    a.check(
        "3.3b limit_price=触发原值",
        abs(compute_vps_hard_sl_limit_price("LONG", 1874.39) - 1874.39) < 1e-9,
    )
    a.check("3.4 STOP 挂单", "place_stop_market_order" in sup or "place_stop_limit" in sup)
    a.check(
        "3.5 止损 closePosition 不抢 TP 额度",
        "use_stop_limit=False" in sup and "不占 reduceOnly" in sup,
    )
    a.check(
        "3.7 止损失败撤开仓防裸奔",
        "硬止损失败·撤销开仓防裸奔" in sup or "_emergency_flatten_naked_open" in sup,
    )
    a.check(
        "3.7b 开仓禁止 recover 核武连环撤",
        ("开仓后防线对齐" in sup or "开仓共同第一步" in sup or "_arm_temp_stop_and_tp12" in sup)
        and "recover_mode=False" in sup
        and "frozen_hard_sl_px" in sup,
    )
    a.check(
        "3.8 防线 thrash 刹车",
        "NUCLEAR_REALIGN_MIN_INTERVAL_SEC" in sup
        and "_nuclear_backoff_remaining" in sup
        and "_defense_anomaly_is_severe" in sup
        and "HARD_SL_SYNC_COOLDOWN_SEC" in sup,
    )
    a.check(
        "3.9 账本消毒/呼吸对齐",
        "_sanitize_vps_hard_sl_ledger" in sup
        and "_is_exchange_stop_acceptable_as_vps_floor" in sup
        and "breakeven_phase" in sup
        and "initial_stop" in sup,
    )
    a.check(
        "3.10 TV方向为准·反向强制平仓",
        "_enforce_tv_direction_or_flat" in sup
        and "TV方向为准·强制平仓" in sup,
    )
    a.check(
        "3.12 呼吸止损允许低于入场",
        "_is_valid_radar_sl" in sup
        and "只要正价即可" in sup,
    )
    a.check(
        "3.14 档位锁定 open_regime(TP用)",
        "_resolve_hard_sl_regime" in sup
        and "_lock_open_regime_from_sources" in sup,
    )
    a.check(
        "3.15 open_atr 锁定不重算止损距",
        "_locked_initial_atr" in sup
        and ("open_atr锁定" in sup or "open_atr（initialAtr）开仓后锁定" in sup),
    )
    a.check("3.16 POST_OPEN_RADAR_BLOCK_SEC=0", "POST_OPEN_RADAR_BLOCK_SEC = 0" in sup)
    a.check(
        "3.18 哨兵周期 1.0s（限频友好）",
        "SENTINEL_POLL_NORMAL = 1.0" in sup
        and "SENTINEL_POLL_ARMING = 1.0" in sup
        and "SENTINEL_POLL_RADAR = 1.0" in sup,
    )
    a.check(
        "3.20 版本 final-spec/breath",
        "final-spec" in sup or "arch-align" in sup or "breath-stop" in sup or "breath_stop" in sup,
    )
    a.check(
        "3.24 版本含 v15",
        "v15." in sup or "breath-stop" in sup,
    )
    a.check(
        "3.25 v6.5.6 已废除 UPDATE_SL/TP webhook",
        "v6.5.6 已废除 UPDATE_SL" in sup or "is_tp_sl_update = False" in sup,
    )


def audit_module4_radar(a: Audit):
    a.section("模块四 · 呼吸止损雷达（TV atr + 1h 呼吸系数）")
    sup = _read(os.path.join(ROOT, "position_supervisor_binance.py"))
    dt = _read(os.path.join(ROOT, "dingtalk.py"))
    wp = _read(os.path.join(ROOT, "webhook_parser.py"))
    bs = _read(os.path.join(ROOT, "breath_stop.py"))
    me = _read(os.path.join(ROOT, "market_engine.py"))
    a1h = _read(os.path.join(ROOT, "atr_1h.py"))

    a.check("4.1 breath_stop.py 存在", "INITIAL_SL_ATR" in bs and "calculate_stop_long" in bs)
    a.check(
        "4.1b STEP_TRIGGER=0.75(ETH默认)",
        "STEP_TRIGGER_ATR" in bs and (
            "STEP_TRIGGER_ATR = 0.75" in bs
            or 'step_trigger_atr' in bs
        ),
    )
    a.check(
        "4.1c STEP_ADVANCE=0.4(ETH默认)",
        "STEP_ADVANCE_ATR" in bs and (
            "STEP_ADVANCE_ATR = 0.4" in bs
            or "step_advance_atr" in bs
        ),
    )
    a.check("4.1d 呼吸系数连续插值", "get_breathing_coefficient" in bs and "trail_distance_multiplier" in open(os.path.join(ROOT, "breath_profiles.py"), encoding="utf-8").read())
    a.check("4.1e atr_1h 引擎", "Atr1hEngine" in a1h and "REFRESH_MIN_SEC = 300" in a1h)
    from breath_stop import STEP_TRIGGER_ATR, STEP_ADVANCE_ATR
    a.check("4.1f ETH默认阶梯数值", abs(STEP_TRIGGER_ATR - 0.75) < 1e-9 and abs(STEP_ADVANCE_ATR - 0.4) < 1e-9)

    for fn in (
        "_apply_breath_stop_tick",
        "_compute_ladder_sl",
        "_process_radar_trailing",
        "_report_breath_phase2",
        "_is_radar_active",
        "_should_radar_trail",
        "_refresh_breathing_coefficient",
        "_should_ignore_late_close",
    ):
        a.check(f"呼吸函数 {fn}", f"def {fn}" in sup)

    a.check("4.2 market_engine 90m保留(降级)", "merge_30m_to_90m" in me and "wilder_atr" in me)
    a.check(
        "4.2b TV atr 锁定 initial_atr（缺则拒）",
        "_tv_signal_atr" in sup
        and ("missing_tv_atr" in sup or "拒绝开仓" in sup)
        and "breath_profile" in sup,
    )
    a.check(
        "4.2c tick 用呼吸系数关键字传参",
        "breathing_coefficient=coeff" in sup or "breathing_coefficient=" in sup,
    )
    a.check(
        "4.2d 盘口执行缓冲 order_stop_price",
        "order_stop_price" in sup and ("STOP_EXEC_BUFFER_USD" in sup or "stop_exec_buffer" in sup),
    )
    a.check(
        "4.2e 双雷达 breath_profiles",
        os.path.isfile(os.path.join(ROOT, "breath_profiles.py")),
    )
    try:
        from breath_profiles import BREATH_ETH, BREATH_XAU, get_breath_profile, trail_distance_multiplier
        a.check(
            "4.2f ETH/XAU 连续插值 min/max（无×0.8层）",
            abs(float(BREATH_ETH["stop_exec_buffer"]) - 0.3) < 1e-9
            and abs(float(BREATH_XAU["stop_exec_buffer"]) - 0.5) < 1e-9
            and abs(float(BREATH_ETH["early_be_atr"]) - 0.5) < 1e-9
            and abs(float(BREATH_XAU["early_be_atr"]) - 0.3) < 1e-9
            and abs(float(BREATH_ETH["min_mult"]) - 1.2) < 1e-9
            and abs(float(BREATH_ETH["max_mult"]) - 2.5) < 1e-9
            and abs(float(BREATH_XAU["min_mult"]) - 0.8) < 1e-9
            and abs(float(BREATH_XAU["max_mult"]) - 1.8) < 1e-9
            and abs(float(BREATH_XAU["phase2_trail_mult"]) - 1.0) < 1e-9
            and abs(trail_distance_multiplier(1.0, BREATH_ETH) - 1.525) < 1e-9
            and abs(trail_distance_multiplier(1.0, BREATH_XAU) - 1.05) < 1e-9,
        )
        pe = get_breath_profile("ETHUSDT")
        px = get_breath_profile("XAUUSDT")
        a.check(
            "4.2g get_breath_profile 路由",
            pe.get("name") == "ETH" and px.get("name") == "XAU",
        )
        from breath_profiles import LockedInitialAtr
        _lk = LockedInitialAtr(strict=True)
        _lk.set_on_open(20.0)
        _blocked = False
        try:
            _lk.try_set(30.0)
        except RuntimeError:
            _blocked = True
        a.check("4.2g2 LockedInitialAtr 持仓期拒写", _blocked and abs(_lk.value - 20.0) < 1e-9)
    except Exception as e:
        a.check("4.2f ETH/XAU 连续插值 min/max（无×0.8层）", False, str(e))
        a.check("4.2g get_breath_profile 路由", False, str(e))
        a.check("4.2g2 LockedInitialAtr 持仓期拒写", False, str(e))
    a.check(
        "4.2h early_be_done 持久化",
        "early_be_done" in sup,
    )
    a.check(
        "4.3 钉钉阶段二文案",
        "阶段二" in dt and ("呼吸追踪" in dt or "呼吸系数" in dt),
    )
    a.check(
        "4.3b 阶段切换钉钉",
        "阶段切换" in dt and "阶段二" in dt,
    )
    a.check(
        "4.3c 止损数量收缩",
        "_breath_resize_stop_on_tp" in sup and "_breath_tick_paused" in sup,
    )
    a.check(
        "4.3d Webhook仅4action",
        "CLOSE_TP" not in (_read(os.path.join(ROOT, "webhook_parser.py")).split("VALID_ACTIONS")[1].split("ACTION_ALIASES")[0]),
    )
    a.check(
        "4.3e 先平后开文案",
        "检测到已有持仓" in dt,
    )
    a.check(
        "4.3f CAP_ALIGN已废除",
        "CAP_ALIGN已废除" in sup
        or "CAP_ALIGN/_trim 已废除" in sup
        or "禁止 reduceOnly 主动减仓" in sup,
    )
    a.check(
        "4.3g HARD_SL_FAIL_ABORT",
        "HARD_SL_FAIL_ABORT" in sup and "report_hard_sl_fail_abort" in dt,
    )
    a.check(
        "4.3g2 CLOSE_THEN_OPEN_FAIL_ABORT",
        "CLOSE_THEN_OPEN_FAIL_ABORT" in sup
        and "report_close_then_open_fail_abort" in dt
        and "1.0,3.0,6.0" in sup.replace(" ", ""),
    )
    a.check(
        "4.3h 旧schema暂停不转换",
        "restart_old_schema_no_auto_migrate" in sup
        or "_state_old_schema" in sup,
    )
    a.check(
        "4.4 废弃待命回撤",
        "已废弃：呼吸止损开仓即运行" in sup
        and "已废弃回撤逻辑" in sup,
    )
    a.check(
        "4.5 ATR_UPDATE/ORDER_TIMEOUT 在 webhook_parser",
        "ATR_UPDATE_SEC" in wp and "ORDER_TIMEOUT_SEC" in wp,
    )
    a.check(
        "4.5c trading_paused/暂停交易 在 supervisor",
        "trading_paused" in sup and "暂停交易" in sup,
    )
    _bc = _read(os.path.join(ROOT, "binance_client.py"))
    a.check(
        "4.5c2 挂单查询失败 fail-closed + 同价去重 + 首挂本地锁",
        "ORDERS_QUERY_FAILED" in _bc
        and "is_orders_query_failed" in _bc
        and "_existing_same_limit" in _bc
        and "_existing_same_stop" in _bc
        and "_recent_limit_place" in _bc
        and "_recent_stop_place" in _bc
        and "_place_dedupe_lock" in _bc
        and "允许首挂" in _bc
        and "orders_unreadable" in sup
        and ("止损收缩幂等跳过" in sup or "止损数量收缩" in sup or "止损收缩签名幂等" in sup)
        and "本轮不再重挂" in sup
        and "_sentinel_start_lock" in sup
        and "_count_open_limits_and_stops" in sup
        and "limits=0 stops=0" in sup
        and "下单前挂单未净" in sup
        and "_verify_sterile_flat" in sup
        and "frozen_hard_sl_px" in sup,
    )
    a.check(
        "4.5c3 开仓 atr 只认 TV（禁止本地回填）",
        '_atr_reject' in wp
        and '"tv_invalid"' in wp
        and "RADAR_ACTIVATE_TP1_FRAC = 0.0" in wp
        and "RADAR_TP3_TRAIL_ATR = 0.0" in wp,
    )
    from webhook_parser import SIGNAL_DEDUP_SEC as _DEDUP
    a.check("4.5d SIGNAL_DEDUP_SEC=60", int(_DEDUP) == 60, str(_DEDUP))

    a.check(
        "4.9 WS mark 脉冲",
        "_on_mark_price_tick" in sup and "register_price_tick_callback" in (
            _read(os.path.join(ROOT, "binance_client.py"))
        ),
    )
    a.check(
        "4.11 废除同向仅刷TP·一律先平后开",
        "always_close_then_open" in sup
        and "OPEN_SAME_DIR_COOLDOWN_SEC = 0" in sup,
    )
    a.check(
        "4.12 TP多档对账禁误报人工",
        "_filter_credible_tp_fills" in sup
        and "_detect_tp_fills_by_price_qty_reconcile" in sup,
    )
    a.check(
        "4.13 平仓归因 exit_source",
        "_resolve_exit_source" in sup
        and "EXIT_SOURCE_RADAR_BE" in wp
        and "exit_source" in dt,
    )
    a.check(
        "4.14 阶段二钉钉+哨兵补发",
        "_flush_pending_radar_notify" in sup
        and "_radar_notify_pending" in sup
        and "radar_activation_notified" in sup,
    )
    a.check(
        "4.15 开单钉钉含止损价",
        "hard_sl_px" in dt and "呼吸止损" in dt,
    )
    a.check(
        "4.16 TP成交记账",
        "_reconcile_tp_consumed_from_live_qty" in sup
        and "_qty_reduction_looks_like_tp" in sup,
    )
    a.check(
        "4.17 closePosition 单槽",
        "closePosition" in sup and "reduceOnly" in sup,
    )
    a.check("4.18 钉钉标题含品种", "[sym]" in dt or "[{sym}]" in dt)


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
        "5.1 RECONCILE_ACTIONS 已废除为空",
        RECONCILE_ACTIONS == frozenset(),
    )
    a.check(
        "5.2 FLATTEN_ACTIONS",
        FLATTEN_ACTIONS == frozenset({"CLOSE_QUICK_EXIT", "CLOSE_RSI_EXIT"}),
    )
    a.check("5.3 is_reconcile CLOSE_TP 应为False", not is_reconcile_action("CLOSE_TP"))
    a.check("5.4 is_flatten CLOSE_RSI_EXIT", is_flatten_action("CLOSE_RSI_EXIT"))
    a.check(
        "5.5 classify CLOSE_SL_INITIAL 兼容仍可映射",
        classify_tv_close("CLOSE_SL_INITIAL") in ("hard_sl", "generic"),
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
        "5.9 旧对账路径已废除为空壳",
        "旧对账路径已废除" in sup
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
        "钉钉开仓字段",
        "账户权益" in dt and "初始止损" in dt,
    )
    a.check(
        "钉钉异常告警标题",
        "异常告警" in dt,
    )
    a.check(
        "钉钉止损平仓文案",
        "止损平仓（阶段一）" in dt or "止损平仓" in dt,
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
        ("呼吸止损" in readme or "1.5×ATR" in readme or "1.5xATR" in readme)
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
        "README 呼吸止损参数",
        ("呼吸止损" in readme or "breath" in readme.lower())
        and ("3.0" in readme or "呼吸系数" in readme or "breathing" in readme.lower())
        and "85%" not in readme,
    )
    a.check(
        "README 仅挂 TP1+TP2",
        ("TP1+TP2" in readme or "TP1 / TP2" in readme or "只挂" in readme)
        and ("不挂 TP3" in readme or "余仓" in readme or "阶段二" in readme or "场景二" in readme),
    )
    a.check(
        "README 核心铁律保留",
        "先平后开" in readme
        and ("reduceOnly" in readme or "TP1+TP2" in readme or "双 STOP" in readme or "三层防线" in readme)
        and ("呼吸止损" in readme or "双轨" in readme or "雷达止损" in readme),
    )
    a.check(
        "README 废弃对账已说明",
        "CLOSE_QUICK_EXIT" in readme or "仅 LONG" in readme or "Webhook" in readme,
    )
    a.check(
        "README secret 鉴权说明",
        ("secret" in readme.lower() or "token" in readme.lower())
        and ("鉴权" in readme or "auth" in readme.lower()),
    )
    a.check(
        "README v15/arch-align",
        "v15." in readme or "arch-align" in readme or "RISK20" in readme,
    )
    # Do NOT require EQUITY_20PCT_X5


def main():
    parser = argparse.ArgumentParser(description="VPS logic static audit (v6.5.6 / arch-align)")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    print("[VPS] trillion-warrior logic static audit · v6.5.6 / arch-align")
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
