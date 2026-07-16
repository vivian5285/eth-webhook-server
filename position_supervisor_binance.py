#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# position_supervisor_binance.py — 与深币 VPS 逻辑对齐（币安 ETH 数量/25x 适配）
import logging
import time
import threading
import os
import json
import queue
from datetime import datetime
from logging.handlers import RotatingFileHandler
from binance_client import binance_client
from position_manager import position_manager
import dingtalk
from webhook_parser import (
    enrich_signal_fields,
    format_tv_field_sources,
    classify_tv_close,
    compute_vps_open_qty,
    compute_vps_add_qty,
    check_total_notional_cap,
    MAX_TOTAL_NOTIONAL_MULT,
    compute_vps_hard_sl,
    compute_vps_hard_sl_distance,
    compute_vps_hard_sl_limit_price,
    format_vps_hard_sl_note,
    format_tv_vps_sl_compare,
    get_vps_hard_sl_params,
    format_vps_sizing_note,
    enrich_entry_tp_prices,
    VPS_RISK_PCT,
    get_regime_max_add_times,
    resolve_tv_add_qty_ratio,
    get_regime_tp_ratios,
    format_regime_tp_ratios_label,
    EXCHANGE_LEVERAGE,
    validate_tp_prices_for_side,
    normalize_entry_type,
    ENTRY_TYPE_OPEN,
    ENTRY_TYPE_PYRAMID,
    ENTRY_TYPE_PROFIT_ADD,
    CLOSE_TYPE_TP3,
    CLOSE_TYPE_BREAKEVEN,
    CLOSE_TYPE_VPS_SHIELD,
    RADAR_STAGE_COST_BUFFER_PCT,
    RADAR_STAGE_ATR_MULT,
    RADAR_STAGE_LABELS,
)

if not os.path.exists('logs'):
    os.makedirs('logs')
_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_LOG_DIR = os.path.join(_BASE_DIR, 'logs')
os.makedirs(_LOG_DIR, exist_ok=True)
_BRAIN_LOG = os.path.join(_LOG_DIR, 'binance_brain.log')
handler = RotatingFileHandler(_BRAIN_LOG, maxBytes=5 * 1024 * 1024, backupCount=3)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] Brain: %(message)s',
    handlers=[handler, logging.StreamHandler()],
)
logger = logging.getLogger(__name__)

BINANCE_VPS_VERSION = "v13.49.0-radar-triad-xau-safe-handoff"
SENTINEL_POLL_NORMAL = 8
SENTINEL_POLL_ARMING = 5
SENTINEL_POLL_RADAR = 5
IDLE_PATROL_INTERVAL_SEC = 12
IDLE_TAKEOVER_COOLDOWN_SEC = 30
DUST_QTY_ETH = 0.004
TP_COMPLETE_RESIDUAL_RATIO = 0.12
OPEN_OVERSIZE_RATIO = 1.10  # 与 QTY_ALIGN_MIN_PCT 一致：偏离 ≥10% 才裁减
SIGNAL_DEDUP_SEC = 45
DEFENSE_ALIGN_COOLDOWN_SEC = 60
SENTINEL_GRACE_AFTER_RECOVER_SEC = 45
FLAT_CONFIRM_RETRIES = 6
FLAT_CONFIRM_DELAY_SEC = 0.85
STARTUP_FLAT_CONFIRM_RETRIES = 10
STARTUP_FLAT_CONFIRM_DELAY_SEC = 1.0
RECOVER_LOCK_FILE = "logs/.recover_singleton.lock"
RECOVER_LOCK_TTL_SEC = 180
REGIME_CAP_COOLDOWN_SEC = 90
REGIME_CAP_TOLERANCE_ETH = 0.001
CAP_MIN_RETAIN_RATIO = 0.25
CAP_TRIM_MAX_ROUNDS = 4
QTY_DRIFT_TOLERANCE_PCT = 0.015  # 微漂 ≤1.5%：仅同步账本，不对齐
QTY_ALIGN_MIN_PCT = 0.10         # 偏离 ≥10% 才视为离谱，触发对齐/档位裁减
# TP 成交对账（R4 TP1=5%：严禁用开仓微差/微漂触发）
TP_SLICE_MATCH_TOL_PCT = 0.05     # 相对该档切片的数量容差（原 8%~18% 过松）
TP_FILL_NOISE_VS_OPEN_PCT = 0.02  # 相对开仓基线 <2% 的减仓一律视为噪声
# 价格达 TP1 区容差：相对价 0.10%（禁止过大容差在未到价时误判）
TP1_PRICE_ZONE_PCT = 0.001
SHIELD_HARD_STOP_PCT = 0.10  # 历史常量（仅哨兵成交分类标签）；硬止损由 VPS 自主计算
SHIELD_TIER_PCTS = (SHIELD_HARD_STOP_PCT,)
SHIELD_TIER_RATIOS = (1.0,)
SHIELD_STOP_TOLERANCE = 2.0
SHIELD_MAINTAIN_COOLDOWN_SEC = 60
SHIELD_FAIL_BACKOFF_BASE_SEC = 45
SHIELD_FAIL_BACKOFF_MAX_SEC = 300
SHIELD_QTY_TOLERANCE_PCT = 0.04
SHIELD_MAX_TIER_ORDERS = 1
RADAR_DINGTALK_COOLDOWN_SEC = 120
RADAR_STOP_MIN_GAP_USD = 2.5
RADAR_STOP_MIN_GAP_PCT = 0.0012
# 交棒额外安全：理想保本线相对现价至少再留 0.15% 利润缓冲，禁止夹成贴市毛刺止损
RADAR_HANDOFF_EXTRA_GAP_PCT = 0.0015
MIN_TP_LEG_QTY = 0.001
# 同向 TV 智能筛选：① ATR 变化 → 先平后开；② 价差低于该百分比 → 不重复开仓，仅刷新 TP123
SAME_DIR_MIN_SPREAD_PCT = 0.15
SAME_DIR_DEDUP_SEC = 300
OPEN_SAME_DIR_COOLDOWN_SEC = 180  # 同向重复 OPEN：开仓后冷却期内禁止先平后开
ATR_SIMILAR_RATIO = 0.03  # 持仓 ATR 与 TV ATR 偏差 ≤3% 视为未变
TV_JOURNAL = "logs/binance_tv_journal.jsonl"
OPEN_JOURNAL = "logs/binance_open_journal.jsonl"
EXCHANGE_JOURNAL = "logs/binance_exchange_journal.jsonl"


class PositionSupervisorBinance:
    def __init__(self, symbol="ETHUSDT"):
        from symbol_config import resolve_binance_symbol
        meta = resolve_binance_symbol(symbol)
        self.symbol = meta["symbol"]
        self.unit_label = meta.get("unit") or "ETH"
        self.qty_step = float(meta.get("qty_step") or 0.001)
        self.min_qty = float(meta.get("min_qty") or 0.001)
        self.dust_qty = float(meta.get("dust_qty") or DUST_QTY_ETH)
        self.atr_fallback_symbol = meta.get("atr_fallback_symbol") or self.symbol
        self.monitoring = False
        self._lock = threading.Lock()

        # 钉钉展示用档位保证金%（与双品种文档一致）
        self.regime_settings = {
            1: {"margin": 0.05, "ratios": get_regime_tp_ratios(1)},
            2: {"margin": 0.10, "ratios": get_regime_tp_ratios(2)},
            3: {"margin": 0.15, "ratios": get_regime_tp_ratios(3)},
            4: {"margin": 0.18, "ratios": get_regime_tp_ratios(4)},
        }
        self.leverage = EXCHANGE_LEVERAGE
        self.tv_sizing_leverage = EXCHANGE_LEVERAGE

        self.regime = 3
        self.current_atr = 30.0
        self.best_price = 0.0
        self.current_sl = 0.0
        self.tv_price = 0.0
        self.tv_tps = [0.0, 0.0, 0.0]

        self.initial_qty = 0.0
        self.watched_qty = 0.0
        self.watched_entry = 0.0
        self.current_side = None
        self.last_tv_side = None
        self.last_tv_signal = None
        self._scan_ticks = 0
        self._signal_queue = queue.Queue()
        self._signal_worker_started = False
        self._sentinel_active = False
        self.open_regime = 3
        self.open_atr = 30.0
        self._last_entry_signal = None
        self._recover_in_progress = False
        self._recover_tp_unconfirmed = False
        self._post_recover_radar_pulse = False
        self._open_in_progress = False
        self._open_tp_unconfirmed = False
        self._last_signal_fp = None
        self._last_signal_fp_ts = 0.0
        self._defense_align_in_progress = False
        self._last_defense_align_ok_ts = 0.0
        self._guardian_bad_streak = 0
        self._sentinel_grace_until = 0.0
        self._last_regime_cap_ts = 0.0
        self.shield_active = False
        self.shield_tiers_consumed = []
        self.tp_levels_consumed = []
        self._last_shield_maintain_ts = 0.0
        self._shield_fail_streak = 0
        self._last_shield_fail_ts = 0.0
        self._shield_arm_notified = False
        self._shield_handoff_notified = False
        self.shield_sized_qty = 0.0
        self._radar_activation_notified = False
        self._radar_armed_after_tp1 = False
        self._radar_handoff_done = False  # 仅保本 STOP 核实后才 True
        self._open_settled_qty = 0.0
        self._last_radar_report_ts = 0.0
        self._last_radar_report_sl = 0.0
        self.sizing_principal = 0.0
        self.tv_sl = 0.0
        self.tv_sl_ref = 0.0
        self._radar_stage_last = 0
        self._last_applied_exchange_sl = 0.0
        self.tv_risk_pct = 0.0
        self.tv_qty_ratio = 1.0
        self.tv_entry_type = ENTRY_TYPE_OPEN
        self.base_qty = 0.0
        self.add_count = 0
        self._last_idle_takeover_ts = 0.0
        self._ws_defense_pulse = False
        self._ws_tp1_fill_hint = False
        self._tv_sl_missing_alerted = False

        self.state_file = os.path.join(
            _BASE_DIR, f'binance_vps_state_{self.symbol}.json'
        )
        legacy = os.path.join(_BASE_DIR, 'binance_vps_state.json')
        if (
            self.symbol == "ETHUSDT"
            and not os.path.exists(self.state_file)
            and os.path.exists(legacy)
        ):
            try:
                import shutil
                shutil.copy2(legacy, self.state_file)
                logger.info(f"📦 已迁移旧状态 → {self.state_file}")
            except Exception as e:
                logger.warning(f"旧状态迁移失败: {e}")
        logger.info(
            f"🧠 币安 VPS [{BINANCE_VPS_VERSION}] {self.symbol} 军师已加载："
            f"双品种·TP1三角对账·雷达5阶段 · {self.leverage}x · {self.unit_label}"
        )
        self._start_signal_worker()
        self._start_idle_flat_patrol()
        # 启动即订阅行情/私有流，避免首仓前钉钉与盘口不同步
        try:
            self._ensure_price_ws()
        except Exception as e:
            logger.warning(f"启动 WS 订阅跳过: {e}")

    def _start_idle_flat_patrol(self):
        """空仓待命时激进实盘巡检：反向强平 / 同向接管 / 人工异动 / 漏报全平 / 蚂蚁扫尾"""
        def loop():
            while True:
                time.sleep(IDLE_PATROL_INTERVAL_SEC)
                if self.monitoring:
                    continue
                if not self._lock.acquire(timeout=2.0):
                    continue
                try:
                    if self.monitoring:
                        continue
                    self._run_idle_live_reconcile()
                except Exception as e:
                    logger.error(f"空闲巡检异常: {e}")
                finally:
                    self._lock.release()

        threading.Thread(target=loop, daemon=True, name="idle-live-watch").start()

    def _book_thinks_active(self):
        return (
            float(self.watched_qty or 0) > 0
            or self.current_side in ("LONG", "SHORT")
        )

    def _live_position_qty(self):
        pos = self._get_active_position()
        if not pos:
            return 0.0
        return float(pos.get("size", 0) or 0)

    def _confirm_position_flat(self, retries=None, delay=None):
        """REST 延迟/重启抖动时多次复核，避免误报空仓触发常规清场"""
        retries = retries if retries is not None else FLAT_CONFIRM_RETRIES
        delay = delay if delay is not None else FLAT_CONFIRM_DELAY_SEC
        for i in range(max(1, int(retries))):
            qty = self._live_position_qty()
            if qty > DUST_QTY_ETH:
                return False
            if i + 1 < retries:
                time.sleep(delay)
        return self._live_position_qty() <= DUST_QTY_ETH

    def _reconcile_stale_tp_consumed(self, initial_qty, live_qty, curr_px=0.0):
        """有 tp_levels_consumed 标记但无减仓证据 → 清空，避免只挂 TP23 漏 TP1"""
        initial_qty = float(initial_qty or 0)
        live_qty = float(live_qty or 0)
        consumed = list(getattr(self, "tp_levels_consumed", []) or [])
        if not consumed:
            return False
        inferred = self._infer_tp_consumed_sequential(initial_qty, live_qty, curr_px)
        if initial_qty <= live_qty + 0.001 and not inferred:
            logger.warning(
                f"⚠️ 清除陈旧 tp_levels_consumed={consumed} "
                f"(开单 {initial_qty}≈现仓 {live_qty}，无减仓证据)"
            )
            self.tp_levels_consumed = []
            self._save_state()
            return True
        if 1 in consumed and self.tv_tps and self.tv_tps[0] > 0:
            if 1 not in inferred and not self._has_tp_limit_at_price(self.tv_tps[0]):
                logger.warning(
                    f"⚠️ TP1 已标记成交但无减仓/无 TP1 挂单 → 重置 {consumed}"
                )
                self.tp_levels_consumed = []
                self._save_state()
                return True
        return False

    def _live_defenses_need_repair(self, live_qty):
        audit = self._audit_tp_levels(live_qty)
        expected = audit.get("expected", 0)
        matched = audit.get("matched_full", 0)
        if expected > 0 and matched < expected:
            return True, audit
        sl = self._radar_sl_to_pass() or float(getattr(self, "tv_sl", 0) or 0)
        if sl > 0 and not self._has_stop_sl_near(sl):
            return True, audit
        return False, audit

    def _resume_live_monitoring(self, pos, source="空闲巡检"):
        """账本与实盘一致但 monitoring=False → 恢复哨兵与雷达跟踪"""
        curr_px = binance_client.get_current_price(self.symbol) or 0
        entry = float(pos.get("entry_price", 0) or self.watched_entry or 0)
        self._refresh_radar_state_on_recover(curr_px, entry)
        self.monitoring = True
        self._save_state()
        self._ensure_price_ws()
        self._ensure_sentinel_running()
        self._sentinel_grace_until = time.time() + SENTINEL_GRACE_AFTER_RECOVER_SEC
        logger.info(
            f"📡 [{source}] 恢复实盘监督 {pos['side']} {pos['size']} ETH "
            f"| 雷达={'已激活' if self._is_radar_active() else '待命'}"
        )

    def _perform_live_takeover(self, pos, source="巡检", manual_open=False, qty_change=None):
        """
        实盘有仓但 VPS 未监控 / 防线缺失 → 补挂 TP123+硬止损，启动雷达哨兵。
        """
        real_amt = float(pos["size"])
        side = pos["side"]
        tv_side = self._resolve_tv_authoritative_side()
        if tv_side and side != tv_side:
            return False

        self.current_side = side
        if not self.last_tv_side:
            self.last_tv_side = tv_side or side

        if manual_open or float(getattr(self, "watched_qty", 0) or 0) <= 0:
            self._reset_fresh_takeover_state()

        self.watched_qty = real_amt
        self.watched_entry = float(pos["entry_price"])
        if manual_open:
            self.initial_qty = real_amt
            self.base_qty = float(real_amt)
            self.tp_levels_consumed = []
            saved_initial = real_amt
        else:
            saved_initial = self._resolve_open_initial_qty(real_amt, self.watched_entry)
            if saved_initial <= 0:
                saved_initial = real_amt
            if self.base_qty <= 0:
                self.base_qty = float(saved_initial or real_amt)
            self.initial_qty = saved_initial
        self.watched_qty = real_amt
        if not getattr(self, "open_regime", None):
            self.open_regime = self.regime
        if not getattr(self, "open_atr", None):
            self.open_atr = self.current_atr

        reconcile_notes = self._hydrate_tv_defense_context(pos)
        curr_px = binance_client.get_current_price(self.symbol)
        stack = self._ensure_full_defense_stack(
            real_amt, self.watched_entry, curr_px,
            source=source, manual_fresh=manual_open,
        )
        audit = stack.get("audit") or {}
        result = stack.get("result") or {}
        health = stack.get("health") or {}
        sl_ok = stack.get("shield_ok", False)
        matched = audit.get("matched_full", 0)
        expected = audit.get("expected", 0)
        radar_active = self._is_radar_active()
        tp_repair = {"repaired": False}
        reconcile_notes.extend(stack.get("notes") or [])

        self.monitoring = True
        self._save_state()
        self._ensure_price_ws()
        log_source = source.split("·")[0].replace(" ", "")
        self._record_open_log(side, real_amt, self.watched_entry, source=log_source)
        self._ensure_sentinel_running()
        self._sentinel_grace_until = time.time() + SENTINEL_GRACE_AFTER_RECOVER_SEC
        self._last_idle_takeover_ts = time.time()

        verified = self._wait_verify(
            lambda: self._verify_position_qty(real_amt, side),
            retries=6,
            delay=0.5,
        )
        entry_px = float((verified or pos)["entry_price"])

        reconcile_txt = (" | " + " ; ".join(reconcile_notes)) if reconcile_notes else ""
        extra_notes = stack.get("notes") or []
        extra_txt = (" | " + " · ".join(extra_notes)) if extra_notes else ""
        verify_note = (
            f"[{source}] 接管 {real_amt} ETH @ {entry_px:.2f} | "
            f"开单 {saved_initial} ETH | TV {self.last_tv_side} | "
            f"止盈 {matched}/{expected} 档 | "
            f"tv_sl={float(getattr(self, 'tv_sl', 0) or 0):.2f} | "
            f"雷达={'已激活' if radar_active else '待命(TP1后)'} | "
            f"{self._format_audit_summary(audit)}{extra_txt}{reconcile_txt}"
        )
        if not verified:
            verify_note += " | REST 同步略延迟"

        if manual_open:
            self._call_dingtalk(
                dingtalk.report_manual_position_change,
                action_type=f"人工开仓 · {source}",
                old_qty=0.0,
                new_qty=real_amt,
                new_entry_price=entry_px,
                verify_note=verify_note,
                tp_audit=audit,
                verified=bool(verified),
            )
        elif qty_change:
            old_q, new_q, action_msg = qty_change
            self._call_dingtalk(
                dingtalk.report_manual_position_change,
                action_type=action_msg,
                old_qty=old_q,
                new_qty=new_q,
                new_entry_price=entry_px,
                verify_note=f"{source} | {verify_note}",
                tp_audit=audit,
                verified=bool(verified),
            )
        else:
            self._call_dingtalk(
                dingtalk.report_recover_takeover,
                side=side,
                qty=real_amt,
                entry=entry_px,
                tv_tps=self.tv_tps,
                regime=self.regime,
                radar_active=radar_active,
                sl_price=self.current_sl,
                verify_note=verify_note,
                tp_matched=matched,
                tp_expected=expected,
                tp_audit=audit,
                last_tv_signal=self.last_tv_signal,
                radar_sl_ok=sl_ok,
                pnl_label=health.get("pnl_label", ""),
                defense_plan=health.get("defense_plan", ""),
                shield_status=health.get("shield_status", ""),
                initial_qty=saved_initial,
                tp_consumed_levels=getattr(self, "tp_levels_consumed", []) or [],
            )

        if expected > 0 and matched < expected:
            dingtalk.report_system_alert(
                f"{source} · 止盈未完全对齐",
                f"{side} {real_amt} ETH @ {entry_px:.2f} | "
                f"仅 {matched}/{expected} 档 | 哨兵将接力纠偏",
            )
        else:
            self._mark_defense_align_ok()

        logger.info(f"✅ [{source}] 实盘接管完成 {side} {real_amt} ETH @ {entry_px:.2f}")
        return True

    def _run_idle_live_reconcile(self):
        """VPS 空仓/待命时周期性对账实盘：全场景生产级应对"""
        if self.monitoring or getattr(self, "_recover_in_progress", False):
            return
        if getattr(self, "_open_in_progress", False):
            return

        pos = self._get_active_position()
        live_qty = float(pos["size"]) if pos else 0.0

        if live_qty <= 0:
            if self._book_thinks_active():
                if not self._confirm_position_flat():
                    logger.warning(
                        "📭 [空闲巡检] 首次无仓但复核仍有持仓 → 跳过误清场"
                    )
                    return
                curr_px = binance_client.get_current_price(self.symbol)
                logger.warning("📭 [空闲巡检] 账本有仓且复核空仓 → 补发收网钉钉")
                self._handle_manual_flat_detected(
                    "仓位归零 (人工强平 / 止盈吃单 / 止损触发)",
                    curr_px=curr_px,
                )
            return

        if self._enforce_tv_direction_or_flat(pos, source="空闲巡检"):
            return

        if self._is_dust_qty(live_qty) or self._should_finalize_tp_victory(live_qty):
            if not self.current_side:
                self.current_side = pos["side"]
            logger.warning(
                f"🐜 [空闲巡检] 发现残量 {pos['side']} {live_qty} ETH → 扫尾"
            )
            self._sweep_dust_and_finalize("空闲巡检：盘口蚂蚁仓自动扫平")
            return

        live_side = pos["side"]
        tv_side = self._resolve_tv_authoritative_side()
        if not tv_side or live_side != tv_side:
            return

        now = time.time()
        watched = float(self.watched_qty or 0)

        if watched <= 0:
            if now - getattr(self, "_last_idle_takeover_ts", 0) < IDLE_TAKEOVER_COOLDOWN_SEC:
                return
            logger.warning(
                f"🔍 [空闲巡检] VPS空仓但实盘同向持仓 {live_side} {live_qty} ETH "
                f"(TV={tv_side}) → 闪电接管+挂TP123"
            )
            self._perform_live_takeover(pos, source="空闲巡检", manual_open=True)
            return

        if self._is_material_qty_change(watched, live_qty):
            logger.warning(
                f"🔍 [空闲巡检] 人工异动 {watched} → {live_qty} ETH → 重算TP123+止损"
            )
            curr_px = binance_client.get_current_price(self.symbol)
            old_qty = watched
            self.watched_qty = live_qty
            self.watched_entry = float(pos["entry_price"])
            self.current_side = live_side
            change, result = self._handle_smart_qty_change(old_qty, live_qty, curr_px)
            if result:
                self._report_qty_change_dingtalk(old_qty, live_qty, result, change=change)
            self.monitoring = True
            self._save_state()
            self._ensure_sentinel_running()
            self._ensure_price_ws()
            self._last_idle_takeover_ts = now
            return

        need_repair, audit = self._live_defenses_need_repair(live_qty)
        if need_repair:
            if now - getattr(self, "_last_idle_takeover_ts", 0) < IDLE_TAKEOVER_COOLDOWN_SEC:
                return
            logger.warning(
                f"🔍 [空闲巡检] 防线不齐 ({audit.get('matched_full', 0)}/"
                f"{audit.get('expected', 0)} 档) → 续挂TP123+止损"
            )
            self._perform_live_takeover(pos, source="空闲巡检·防线续挂")
            return

        if not self.monitoring:
            self._resume_live_monitoring(pos, source="空闲巡检")

    def _dingtalk(self, fn, **kwargs):
        """钉钉播报：强制绑定本军师品种单位（XAU/ETH）。"""
        kwargs.setdefault("symbol", self.symbol)
        kwargs.setdefault("unit_label", self.unit_label)
        tokens = []
        try:
            tokens = dingtalk.bind_dingtalk_symbol(
                symbol=kwargs.get("symbol"),
                unit_label=kwargs.get("unit_label"),
            )
            try:
                return fn(**kwargs)
            except TypeError as exc:
                if "unexpected keyword argument" not in str(exc):
                    raise
                legacy = {
                    k: v for k, v in kwargs.items()
                    if k not in (
                        "verified", "swept_dust", "radar_sl_ok", "action_type",
                        "symbol", "unit_label",
                    )
                }
                logger.warning(
                    f"钉钉旧版降级播报 {getattr(fn, '__name__', 'dingtalk')}: {exc}"
                )
                return fn(**legacy)
        finally:
            dingtalk.reset_dingtalk_symbol(tokens)

    def _call_dingtalk(self, fn, **kwargs):
        """兼容旧调用名 → _dingtalk（自动注入 symbol/unit_label）"""
        return self._dingtalk(fn, **kwargs)

    def _start_signal_worker(self):
        if self._signal_worker_started:
            return
        self._signal_worker_started = True
        threading.Thread(target=self._signal_worker_loop, daemon=True, name="tv-signal-worker").start()

    def _signal_worker_loop(self):
        while True:
            payload = self._signal_queue.get()
            try:
                self._process_signal(payload)
            except Exception as e:
                logger.error(f"❌ 信号处理异常: {e}", exc_info=True)
            finally:
                self._signal_queue.task_done()

    def _signal_fingerprint(self, payload):
        action = str(payload.get("action", "")).strip().upper()
        if action.startswith("CLOSE"):
            return (
                action,
                str(payload.get("reason", ""))[:48],
                round(self._safe_float(payload.get("price"), 0), 2),
                round(self._safe_float(payload.get("pnl_pct"), 0), 2),
            )
        if action == "UPDATE_SL":
            return (
                action,
                str(payload.get("side", "")).upper(),
                round(self._safe_float(payload.get("tv_sl"), 0), 2),
            )
        if action == "UPDATE_TP":
            return (
                action,
                str(payload.get("side", "")).upper(),
                round(self._safe_float(payload.get("tv_tp1"), 0), 2),
                round(self._safe_float(payload.get("tv_tp2"), 0), 2),
                round(self._safe_float(payload.get("tv_tp3"), 0), 2),
            )
        if action in ("LONG", "SHORT"):
            return (
                action,
                normalize_entry_type(payload.get("entry_type")),
                round(self._safe_float(payload.get("tv_sl"), 0), 2),
                round(self._safe_float(payload.get("risk_pct"), 0), 3),
                round(self._safe_float(payload.get("qty_ratio"), 1.0), 3),
                round(self._safe_float(payload.get("price"), 0), 2),
            )
        return (
            action,
            self._safe_int(payload.get("regime"), 3),
            round(self._safe_float(payload.get("price"), 0), 2),
            round(self._safe_float(payload.get("atr"), 0), 2),
        )

    def enqueue_signal(self, payload):
        fp = self._signal_fingerprint(payload)
        action = fp[0] or "?"
        now = time.time()
        if (
            fp == self._last_signal_fp
            and now - self._last_signal_fp_ts < SIGNAL_DEDUP_SEC
        ):
            logger.warning(
                f"📬 TV信号去重忽略: {action} | {SIGNAL_DEDUP_SEC}s 内重复推送"
            )
            return
        if self._open_in_progress and action in ("LONG", "SHORT"):
            logger.warning(f"📬 开仓进行中，忽略重复建仓信号 {action}")
            return
        self._last_signal_fp = fp
        self._last_signal_fp_ts = now
        depth = self._signal_queue.qsize()
        self._signal_queue.put(payload)
        logger.info(f"📬 TV信号入队: {action} | 队列深度 {depth + 1}")

    def signal_queue_depth(self):
        return self._signal_queue.qsize()

    def _append_journal(self, path, record):
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        record = dict(record)
        record["ts"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def _log_exchange_api(self, action, detail="", result=None):
        """记录交易所 API 动作与响应摘要"""
        note = str(detail or "")
        if result is not None:
            if isinstance(result, dict):
                snippet = json.dumps(result, ensure_ascii=False)[:800]
            else:
                snippet = str(result)[:800]
            note = f"{note} | resp={snippet}" if note else f"resp={snippet}"
        logger.info(f"🔌 [交易所] {action}" + (f" | {note}" if note else ""))
        self._append_journal(EXCHANGE_JOURNAL, {
            "action": action,
            "detail": detail,
            "result": result if isinstance(result, (dict, list, str, int, float, bool)) else str(result),
        })

    def _log_radar_update(self, stage, old_sl, new_sl, action, curr_px=0.0, extra=""):
        """雷达阶段/止损变更结构化日志"""
        stage = int(stage or 0)
        old_sl = float(old_sl or 0)
        new_sl = float(new_sl or 0)
        label = self._radar_stage_label(stage)
        compare = ""
        if self.watched_entry and self.current_side:
            compare = format_tv_vps_sl_compare(
                self.current_side, self.watched_entry,
                self.current_atr, self.regime,
                tv_sl_ref=getattr(self, "tv_sl_ref", 0),
            )
        logger.info(
            f"📡 [雷达] 阶段{stage} {label} | SL {old_sl:.2f}→{new_sl:.2f} | "
            f"{action} | 现价 {float(curr_px or 0):.2f}"
            + (f" | {compare}" if compare else "")
            + (f" | {extra}" if extra else "")
        )
        self._append_journal(EXCHANGE_JOURNAL, {
            "kind": "radar_update",
            "stage": stage,
            "stage_label": label,
            "old_sl": old_sl,
            "new_sl": new_sl,
            "action": action,
            "curr_px": float(curr_px or 0),
            "best_price": float(self.best_price or 0),
            "entry": float(self.watched_entry or 0),
            "side": self.current_side,
        })

    def _load_last_journal_entry(self, path):
        if not os.path.exists(path):
            return None
        last = None
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        last = json.loads(line)
                    except json.JSONDecodeError:
                        continue
        return last

    def _record_tv_signal(self, payload, raw_action):
        full_payload = dict(payload or {})
        entry = {
            "action": raw_action,
            "regime": self.regime,
            "atr": self.current_atr,
            "price": self.tv_price,
            "tv_tps": self.tv_tps,
            "reason": payload.get("reason", ""),
            "side": payload.get("side", ""),
            "pnl_pct": payload.get("pnl_pct"),
            "tv_sl": payload.get("tv_sl"),
            "entry_type": payload.get("entry_type"),
            "risk_pct": payload.get("risk_pct"),
            "leverage": payload.get("leverage"),
            "qty_ratio": payload.get("qty_ratio"),
            "payload": full_payload,
            "ts": time.time(),
        }
        self.last_tv_signal = entry
        self._append_journal(TV_JOURNAL, entry)
        try:
            payload_txt = json.dumps(full_payload, ensure_ascii=False)[:1800]
        except (TypeError, ValueError):
            payload_txt = str(full_payload)[:1800]
        logger.info(f"📥 [TV警报全文] {raw_action} | {payload_txt}")
        sizing_note = ""
        et = normalize_entry_type(payload.get("entry_type"))
        open_sizing_meta = None
        if et == ENTRY_TYPE_OPEN and self.tv_price > 0:
            _, open_sizing_meta = self._calc_vps_open_qty(self.tv_price)
            sizing_note = " | " + format_vps_sizing_note(open_sizing_meta, entry_type=ENTRY_TYPE_OPEN)
        elif et in (ENTRY_TYPE_PYRAMID, ENTRY_TYPE_PROFIT_ADD):
            _, sm = self._calc_vps_add_qty()
            sizing_note = " | " + format_vps_sizing_note(sm, entry_type=et)
        logger.info(
            f"📡 TV日志: {raw_action} R{self.regime} @ {self.tv_price:.2f} "
            f"TP={self.tv_tps}"
            + sizing_note
            + (f" | pnl={payload.get('pnl_pct')}%" if payload.get("pnl_pct") is not None else "")
        )
        self._call_dingtalk(
            dingtalk.report_tv_signal_received,
            action=raw_action,
            entry_type=payload.get("entry_type"),
            price=self.tv_price,
            regime=self.regime,
            atr=self.current_atr,
            tv_sl=payload.get("tv_sl"),
            risk_pct=payload.get("risk_pct"),
            leverage=EXCHANGE_LEVERAGE,
            qty_ratio=payload.get("qty_ratio"),
            reason=payload.get("reason", ""),
            vps_sizing_meta=open_sizing_meta,
            vps_hard_sl_note=(
                format_tv_vps_sl_compare(
                    raw_action, self.tv_price, self.current_atr, self.regime,
                    tv_sl_ref=payload.get("tv_sl"),
                )
                if raw_action in ("LONG", "SHORT") and self.tv_price > 0
                else ""
            ),
        )

    def _record_open_log(self, side, qty, entry, source="open"):
        self._append_journal(OPEN_JOURNAL, {
            "source": source,
            "side": side,
            "qty": qty,
            "entry": entry,
            "regime": self.regime,
            "tv_tps": self.tv_tps,
            "tv_price": self.tv_price,
            "last_tv_side": self.last_tv_side,
        })

    def _load_active_tv_direction_from_journal(self):
        """从 TV 日志末尾向前：跳过尾部 CLOSE，取当前活跃周期的 LONG/SHORT"""
        if not os.path.exists(TV_JOURNAL):
            return None
        entries = []
        with open(TV_JOURNAL, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        for entry in reversed(entries):
            action = (entry.get("action") or "").upper()
            if action.startswith("CLOSE"):
                continue
            if action in ("LONG", "SHORT"):
                return action
            side = (entry.get("side") or "").upper()
            if side in ("LONG", "SHORT"):
                return side
        return None

    def _collect_credible_tv_directions(self):
        """可信 TV 方向集合：state 最新信号 > 日志末条 > 活跃周期"""
        sides = []
        seen = set()

        def add(raw):
            s = (raw or "").upper()
            if s in ("LONG", "SHORT") and s not in seen:
                seen.add(s)
                sides.append(s)

        if self.last_tv_signal:
            add(self.last_tv_signal.get("action"))
            add(self.last_tv_signal.get("side"))
        last_tv = self._load_last_journal_entry(TV_JOURNAL)
        if last_tv:
            add(last_tv.get("action"))
            add(last_tv.get("side"))
        add(self._load_active_tv_direction_from_journal())
        add(getattr(self, "last_tv_side", None))
        return sides

    def _live_aligns_with_credible_tv(self, live_side):
        """人工同向开仓：任一可信 TV 信源与实盘一致 → 应接管，禁止误杀"""
        return live_side in self._collect_credible_tv_directions()

    def _strict_tv_opposite_side(self, live_side):
        """仅当「最新 TV 指令」与实盘明确反向时才强平（不用陈旧全量扫描）"""
        for src in (self.last_tv_signal, self._load_last_journal_entry(TV_JOURNAL)):
            if not src:
                continue
            action = (src.get("action") or "").upper()
            if action in ("LONG", "SHORT") and action != live_side:
                return action
            side = (src.get("side") or "").upper()
            if side in ("LONG", "SHORT") and side != live_side:
                return side
        return None

    def _load_last_tv_open_signal(self):
        """TV 日志中最近一条 LONG/SHORT（CLOSE 之后仍可用于方向对账）"""
        if not os.path.exists(TV_JOURNAL):
            return None
        last_open = None
        with open(TV_JOURNAL, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                action = (entry.get("action") or "").upper()
                if action in ("LONG", "SHORT"):
                    last_open = entry
        return last_open

    def _resolve_tv_authoritative_side(self):
        """TV 战略方向：优先最新信源，避免陈旧全量扫描误杀同向人工单"""
        if self.last_tv_signal:
            action = (self.last_tv_signal.get("action") or "").upper()
            if action in ("LONG", "SHORT"):
                return action
            side = (self.last_tv_signal.get("side") or "").upper()
            if side in ("LONG", "SHORT"):
                return side
        last_tv = self._load_last_journal_entry(TV_JOURNAL)
        if last_tv:
            tv_action = (last_tv.get("action") or "").upper()
            if tv_action in ("LONG", "SHORT"):
                return tv_action
            side = (last_tv.get("side") or "").upper()
            if side in ("LONG", "SHORT"):
                return side
            if tv_action.startswith("CLOSE"):
                active = self._load_active_tv_direction_from_journal()
                if active:
                    return active
        active = self._load_active_tv_direction_from_journal()
        if active:
            return active
        side = getattr(self, "last_tv_side", None)
        if side in ("LONG", "SHORT"):
            return side
        last_open_tv = self._load_last_tv_open_signal()
        if last_open_tv:
            tv_open = (last_open_tv.get("action") or "").upper()
            if tv_open in ("LONG", "SHORT"):
                return tv_open
        return None

    def _live_position_side(self, pos):
        if not pos:
            return None
        if pos.get("side") in ("LONG", "SHORT"):
            return pos["side"]
        amt = float(pos.get("positionAmt", 0) or 0)
        if amt > 0:
            return "LONG"
        if amt < 0:
            return "SHORT"
        return None

    def _enforce_tv_direction_or_flat(self, pos, source="sentinel"):
        """实盘与 TV 明确反向 → 核武全平；同向或信源不明 → 交给接管"""
        if not pos or float(pos.get("size", 0) or 0) <= 0:
            return False
        live_side = self._live_position_side(pos)
        if self._live_aligns_with_credible_tv(live_side):
            logger.info(
                f"✅ [{source}] 实盘 {live_side} 与可信 TV 信源同向 → 跳过强平，进入接管"
            )
            return False
        tv_opposite = self._strict_tv_opposite_side(live_side)
        if not tv_opposite or not live_side:
            return False
        reason = (
            f"人工反向手单 vs TV：实盘({live_side}) ≠ 最新TV({tv_opposite}) [{source}]"
        )
        logger.error(f"🚨 {reason} → 核武全平强制对齐 TV")
        verify_note = (
            f"触发源: {source} | 最新TV {tv_opposite} | 实盘反向 {live_side} | "
            "已核武全平，账本归零待命"
        )
        self._close_all(
            reason,
            force_align=(live_side, tv_opposite),
            force_verify_note=verify_note,
        )
        return True

    def _journal_tp_prices(self, entry):
        """从日志条目解析 TP123（支持 tv_tps 列表或 tv_tp1/2/3 字段）"""
        if not entry:
            return [0.0, 0.0, 0.0]
        if entry.get("tv_tps"):
            return self._sanitize_tp_prices(entry.get("tv_tps", []))
        return self._sanitize_tp_prices([
            entry.get("tv_tp1"), entry.get("tv_tp2"), entry.get("tv_tp3"),
        ])

    def _hydrate_tv_defense_context(self, pos):
        """
        人工开仓 / 重启接管：从 TV 日志补全 tp/sl/regime/atr，避免字段缺失导致接管异常。
        """
        notes = []
        side = pos.get("side") or self.current_side
        entry = float(pos.get("entry_price", 0) or self.watched_entry or 0)
        if not side:
            return notes

        self.current_side = side
        if not self.last_tv_side:
            self.last_tv_side = side

        sources = [
            self.last_tv_signal,
            self._load_last_journal_entry(TV_JOURNAL),
            self._load_last_tv_open_signal(),
            self._load_last_journal_entry(OPEN_JOURNAL),
        ]

        for src in sources:
            if not src:
                continue
            if src.get("regime"):
                self.regime = int(src["regime"])
            if src.get("atr"):
                self.current_atr = float(src["atr"])
            if float(self.tv_price or 0) <= 0 and float(src.get("price", 0) or 0) > 0:
                self.tv_price = float(src["price"])

        tp_ok = sum(1 for t in (self.tv_tps or []) if t > 0)
        if not self._tp_prices_valid_for_side(side, entry):
            if tp_ok >= 3:
                logger.warning(
                    f"⚠️ 接管: 账本 TP{self.tv_tps} 与 {side}@{entry:.2f} 方向不符 → 重载"
                )
            self.tv_tps = [0.0, 0.0, 0.0]
            tp_ok = 0
        if tp_ok < 3:
            for src in sources:
                src_side = (src.get("action") or src.get("side") or "").upper()
                if src_side in ("LONG", "SHORT") and side and src_side != side:
                    continue
                tps = self._journal_tp_prices(src)
                if (
                    sum(1 for t in tps if t > 0) >= 3
                    and self._tp_prices_valid_for_side(side, entry, tps)
                ):
                    self.tv_tps = tps
                    notes.append(f"补全TP123 {tps}")
                    break

        if sum(1 for t in (self.tv_tps or []) if t > 0) < 3 and entry > 0 and self.current_atr > 0:
            payload = enrich_entry_tp_prices(
                side, entry, self.current_atr, self.regime, {},
            )
            tps = self._sanitize_tp_prices([
                payload.get("tv_tp1"), payload.get("tv_tp2"), payload.get("tv_tp3"),
            ])
            if self._tp_prices_valid_for_side(side, entry, tps):
                self.tv_tps = tps
                notes.append(f"ATR本地补全TP {tps}")

        if float(getattr(self, "tv_sl", 0) or 0) <= 0:
            for src in sources:
                sl = float(src.get("tv_sl", 0) or 0)
                if sl > 0:
                    self.tv_sl_ref = sl
                    notes.append(f"TV参考tv_sl={sl:.2f}")
                    break

        # v13.38+：硬止损按开仓价×档位%，不依赖 ATR
        if entry > 0 and side in ("LONG", "SHORT"):
            if self._refresh_vps_hard_sl(
                entry=entry, side=side,
                regime=self.regime, atr=self.current_atr,
                tv_sl_ref=getattr(self, "tv_sl_ref", 0) or None,
                source="接管补全",
            ):
                notes.append(format_vps_hard_sl_note(
                    side, entry, self.current_atr, self.regime,
                    tv_sl_ref=getattr(self, "tv_sl_ref", 0),
                ))
            else:
                adopted = self._adopt_exchange_hard_sl(source="接管盘口采纳")
                if adopted:
                    notes.append(f"盘口采纳硬止损@{adopted:.2f}")

            # 重启叠单（例如 1697+1747）→ 强制统一为当前 VPS 计算价
            live_stops = binance_client.find_protective_stop_prices(self.symbol)
            uniq = sorted({round(float(p), 2) for p in live_stops if float(p) > 0})
            target = round(float(getattr(self, "tv_sl", 0) or 0), 2)
            if target > 0 and (
                len(uniq) > 1
                or (uniq and all(abs(p - target) > SHIELD_STOP_TOLERANCE for p in uniq))
            ):
                qty = float(pos.get("size") or pos.get("positionAmt") or self.watched_qty or 0)
                qty = abs(qty)
                if qty <= 0:
                    qty = float(self.watched_qty or 0)
                if qty > 0:
                    sync = self._sync_exchange_stop(
                        qty, radar_sl=None, reason="接管统一硬止损", force=True,
                    )
                    if sync.get("ok"):
                        notes.append(
                            f"统一硬止损@{sync.get('target'):.2f}(撤{sync.get('purged', 0)})"
                        )

        self.monitoring = True
        self._save_state()
        for n in notes:
            logger.info(f"💧 接管上下文补全: {n}")
        return notes

    def _reset_fresh_takeover_state(self):
        """人工/孤儿接管：清空陈旧 TP/雷达状态，避免误判已成交导致只挂 TP12"""
        self.tp_levels_consumed = []
        self.shield_tiers_consumed = []
        self._radar_activation_notified = False
        self._shield_handoff_notified = False
        self.shield_active = False
        self.shield_sized_qty = 0.0
        self.tv_tps = [0.0, 0.0, 0.0]
        self.tv_sl = 0.0
        self.tv_sl_ref = 0.0
        if not getattr(self, "open_regime", None):
            self.open_regime = self.regime
        if not getattr(self, "open_atr", None):
            self.open_atr = self.current_atr

    def _tp_prices_valid_for_side(self, side=None, entry=None, tp_list=None):
        side = side or self.current_side
        entry = float(entry or self.watched_entry or 0)
        tp_list = tp_list if tp_list is not None else (self.tv_tps or [])
        return validate_tp_prices_for_side(side, entry, tp_list)

    def _reload_tv_tp_prices_from_sources(self, side, entry):
        """从 TV 信源重载 TP123；拒绝方向错误或陈旧价位"""
        entry = float(entry or 0)
        side = str(side or "").strip().upper()
        sources = [
            self.last_tv_signal,
            self._load_last_journal_entry(TV_JOURNAL),
            self._load_last_tv_open_signal(),
            self._load_last_journal_entry(OPEN_JOURNAL),
        ]
        for src in sources:
            if not src:
                continue
            src_side = (src.get("action") or src.get("side") or "").upper()
            if src_side in ("LONG", "SHORT") and side and src_side != side:
                continue
            tps = self._journal_tp_prices(src)
            if sum(1 for t in tps if t > 0) >= 3 and self._tp_prices_valid_for_side(side, entry, tps):
                return tps, f"TV日志TP {tps}"
        return None, ""

    def _adopt_tp_prices_from_open_orders(self, entry=None):
        """
        账本 tv_tps 为空/不全时，从盘口仍挂着的限价止盈恢复价位。
        避免「expected=0 → 全盘单判孤儿 → 撤光」灾难路径。
        """
        orders = self._collect_tp_limit_orders()
        if not orders:
            return False
        entry = float(entry or self.watched_entry or 0)
        side = (self.current_side or "").upper()
        prices = sorted({round(float(o["price"]), 2) for o in orders if float(o.get("price") or 0) > 0})
        if side == "LONG" and entry > 0:
            prices = sorted(p for p in prices if p >= entry - 1.0)
        elif side == "SHORT" and entry > 0:
            prices = sorted(
                (p for p in prices if p <= entry + 1.0),
                reverse=True,
            )
        if not prices:
            return False

        consumed = set(getattr(self, "tp_levels_consumed", []) or [])
        tps = [float(x or 0) for x in (self.tv_tps or [0.0, 0.0, 0.0])]
        while len(tps) < 3:
            tps.append(0.0)

        rem_slots = [i for i in range(3) if (i + 1) not in consumed]
        known = [t for t in tps if t > 0]
        if sum(1 for t in tps if t > 0) == 0:
            for slot, px in zip(rem_slots, prices):
                tps[slot] = px
        else:
            unused = [
                p for p in prices
                if not any(abs(p - t) <= 1.5 for t in known)
            ]
            free = [i for i in rem_slots if tps[i] <= 0]
            for slot, px in zip(free, unused):
                tps[slot] = px

        if sum(1 for t in tps if t > 0) < 1:
            return False
        self.tv_tps = tps
        logger.info(f"📐 从盘口限价止盈恢复 TP 价位 → {self.tv_tps}")
        self._save_state()
        return True

    def _ensure_tv_tps_for_fill_detect(self, entry=None):
        """减仓归因前：尽量保证至少有可匹配的 TP 价（日志/盘口）"""
        entry = float(entry or self.watched_entry or 0)
        if sum(1 for t in (self.tv_tps or []) if float(t or 0) > 0) >= 2:
            return True
        if self._ensure_tp123_prices_from_tv(entry):
            return True
        return self._adopt_tp_prices_from_open_orders(entry)

    def _ensure_tp123_prices_from_tv(self, entry):
        """以实盘 entry + open_atr/regime 确保 TP123 三价齐全（人工开仓必跑）"""
        side = self.current_side
        entry = float(entry or self.watched_entry or 0)
        if self._tp_prices_valid_for_side(side, entry):
            return True

        reloaded, note = self._reload_tv_tp_prices_from_sources(side, entry)
        if reloaded:
            self.tv_tps = reloaded
            logger.info(f"📐 接管重载 TP123 @ entry={entry:.2f} → {self.tv_tps} ({note})")
            self._save_state()
            return True

        if self._adopt_tp_prices_from_open_orders(entry):
            if self._tp_prices_valid_for_side(side, entry):
                return True
            # 部分止盈后盘口只剩 1~2 档也算可用
            if sum(1 for t in (self.tv_tps or []) if float(t or 0) > 0) >= 1:
                return True

        if sum(1 for t in (self.tv_tps or []) if t > 0) >= 3:
            logger.warning(
                f"⚠️ 陈旧 TP 价位与 {side} @ {entry:.2f} 方向不符 → 丢弃重算"
            )
            self.tv_tps = [0.0, 0.0, 0.0]

        atr = float(getattr(self, "open_atr", None) or self.current_atr or 30)
        regime = int(getattr(self, "open_regime", None) or self.regime or 3)
        if not side or entry <= 0:
            return False
        payload = enrich_entry_tp_prices(side, entry, atr, regime, {})
        self.tv_tps = self._sanitize_tp_prices([
            payload.get("tv_tp1"), payload.get("tv_tp2"), payload.get("tv_tp3"),
        ])
        ok = self._tp_prices_valid_for_side(side, entry)
        if ok:
            logger.info(f"📐 人工接管 ATR 补全 TP123 @ entry={entry:.2f} → {self.tv_tps}")
        return ok

    def _resolve_defense_stop_for_audit(self, radar_sl=None):
        """审计用止损价：雷达已激活则合并线；否则 tv_sl"""
        if radar_sl and float(radar_sl) > 0:
            return float(radar_sl)
        tracked = self._radar_sl_to_pass()
        if tracked:
            return tracked
        return self._shield_stop_price()

    def _normalize_tp_qty_map(self, qty_map, live_qty):
        """不足最小下单量的小档合并到最后一档，避免 TP3 被静默丢弃"""
        if not qty_map:
            return qty_map
        live_qty = float(live_qty or 0)
        levels = sorted(qty_map.keys())
        if len(levels) <= 1:
            return qty_map
        out = dict(qty_map)
        carry = 0.0
        last = levels[-1]
        for lvl in levels[:-1]:
            q = float(out.get(lvl, 0) or 0)
            if 0 < q < MIN_TP_LEG_QTY:
                carry += q
                out[lvl] = 0.0
        if carry > 0:
            out[last] = round(float(out.get(last, 0) or 0) + carry, 3)
        total = round(sum(float(out.get(l, 0) or 0) for l in levels), 3)
        if total > live_qty + 0.001:
            out[last] = round(max(out.get(last, 0) - (total - live_qty), MIN_TP_LEG_QTY), 3)
        return out

    def _ensure_full_defense_stack(self, live_qty, entry, curr_px, source="接管", manual_fresh=False):
        """
        全链防线：TP123 比例限价 + VPS 自主硬止损；
        雷达按 5 阶段推进（仅 TP1 限价成交后激活；TP1 前仅 VPS 宽硬止损）。
        """
        notes = []
        live_qty = float(self._resolve_live_qty(live_qty) or live_qty)
        entry = float(entry or self.watched_entry or 0)
        curr_px = float(curr_px or binance_client.get_current_price(self.symbol) or 0)

        if manual_fresh:
            self._reset_fresh_takeover_state()

        self._disarm_premature_radar(live_qty, curr_px, source=source)
        self._reconcile_stale_tp_consumed(
            self._trusted_initial_qty(live_qty, entry), live_qty, curr_px,
        )
        trusted_initial = self._trusted_initial_qty(live_qty, entry)
        if float(self.initial_qty or 0) != trusted_initial:
            self.initial_qty = trusted_initial
        self._sanitize_tp_consumed(trusted_initial, live_qty, curr_px)
        if not self._ensure_tp123_prices_from_tv(entry):
            notes.append("TP123补全失败")
        if float(getattr(self, "tv_sl", 0) or 0) <= 0:
            self._hydrate_tv_defense_context({
                "side": self.current_side, "entry_price": entry, "size": live_qty,
            })
        if float(getattr(self, "tv_sl", 0) or 0) <= 0 and entry > 0:
            self._refresh_vps_hard_sl(
                entry=entry, side=self.current_side,
                regime=int(getattr(self, "open_regime", None) or self.regime or 3),
                atr=float(getattr(self, "open_atr", None) or self.current_atr or 30),
                tv_sl_ref=getattr(self, "tv_sl_ref", 0) or None,
                source=f"{source} boot",
            )
            if float(getattr(self, "tv_sl", 0) or 0) > 0:
                notes.append(format_vps_hard_sl_note(
                    self.current_side, entry,
                    float(getattr(self, "open_atr", None) or self.current_atr or 30),
                    int(getattr(self, "open_regime", None) or self.regime or 3),
                    tv_sl_ref=getattr(self, "tv_sl_ref", 0),
                ))

        self._enforce_pre_tp1_radar_standby(live_qty, curr_px, source=source)

        try:
            cap = self._radar_enforce_regime_cap(live_qty, curr_px, force=True)
            if cap:
                live_qty = float(cap["new_qty"])
                self.watched_qty = live_qty
                if float(self.initial_qty or 0) <= live_qty + 0.001:
                    self.initial_qty = live_qty
        except Exception as e:
            logger.warning(f"接管档位限额跳过: {e}")

        tp_repair = {"repaired": False}
        try:
            tp_repair = self._repair_partial_tp_on_recover(
                live_qty, entry, trusted_initial, curr_px,
            )
            if tp_repair.get("repaired"):
                notes.extend(tp_repair.get("actions") or [])
        except Exception as e:
            logger.error(f"接管TP修复跳过: {e}")
            notes.append(f"TP修复跳过:{e}")

        self._refresh_radar_state_on_recover(curr_px, entry)
        radar_sl = self._radar_sl_to_pass()

        if tp_repair.get("repaired") and tp_repair.get("result"):
            result = tp_repair["result"]
        else:
            result = self._enforce_defense_alignment(
                live_qty, entry, dynamic_sl=radar_sl,
                reason=f"{source} TP123+tv_sl", rounds=3, recover_mode=True,
            )

        stop_check = self._resolve_defense_stop_for_audit(radar_sl)
        if not self._radar_legitimately_armed(live_qty, curr_px):
            radar_sl = None
            self._enforce_pre_tp1_radar_standby(live_qty, curr_px, source=source)
            stop_check = self._shield_stop_price()
        shield_ok = self._maintain_hard_shield(live_qty, curr_px, force=True, radar_sl=radar_sl)
        audit = self._wait_defense_settled(live_qty, stop_check)

        if not self._tp_audit_ok(audit) or (
            stop_check and not self._has_stop_sl_near(stop_check, tolerance=2.5)
        ):
            logger.warning(
                f"⚠️ [{source}] TP/止损未齐 ({audit.get('matched_full', 0)}/"
                f"{audit.get('expected', 0)}) → 核武重挂 TP123+tv_sl"
            )
            audit = self._nuclear_realign_tp(live_qty, entry, dynamic_sl=radar_sl, rounds=3)
            shield_ok = self._maintain_hard_shield(
                live_qty, curr_px, force=True, radar_sl=radar_sl,
            )
            stop_check = self._resolve_defense_stop_for_audit(radar_sl)
            audit = self._wait_defense_settled(live_qty, stop_check)

        health = self._build_recover_health_report(
            {"side": self.current_side, "size": live_qty, "entry_price": entry},
            curr_px, audit,
        )

        if self._radar_legitimately_armed(live_qty, curr_px) and (
            health.get("should_radar") or health.get("radar_active")
        ):
            self._process_radar_trailing(live_qty, curr_px)
            sl = self._radar_sl_to_pass()
            if sl and not self._has_stop_sl_near(sl):
                self._maintain_hard_shield(live_qty, curr_px, force=True, radar_sl=sl)
        else:
            progress = self._radar_activation_progress(curr_px) if curr_px > 0 else 0.0
            tp1_prog = self._tp1_direction_progress(curr_px) if curr_px > 0 else 0.0
            logger.info(
                f"📡 [{source}] 雷达待命 激活进度{progress:.0%} "
                f"朝TP1{tp1_prog:.0%} | "
                f"tv_sl={float(getattr(self, 'tv_sl', 0) or 0):.2f} | "
                f"TP {audit.get('matched_full', 0)}/{audit.get('expected', 0)}"
            )

        if self._tp_audit_ok(audit):
            self._mark_defense_align_ok()
        else:
            exp = audit.get("expected", 0)
            if exp and audit.get("matched_full", 0) < exp:
                dingtalk.report_system_alert(
                    f"{source} · 止盈未完全对齐",
                    f"{self.current_side} {live_qty} ETH @ {entry:.2f} | "
                    f"仅 {audit.get('matched_full', 0)}/{exp} 档 | "
                    f"tv_sl={float(getattr(self, 'tv_sl', 0) or 0):.2f} | 哨兵接力",
                )

        self._post_recover_radar_pulse = True
        return {
            "audit": audit,
            "result": result,
            "health": health,
            "shield_ok": shield_ok,
            "notes": notes,
        }

    def _smart_recover_defenses(self, real_amt, entry, dynamic_sl=None):
        """重启智能补挂：审计齐全则跳过，缺档增量补，避免重复挂单"""
        matched, pending, expected, rebuilt = self._ensure_defenses_on_recover(
            real_amt, entry, dynamic_sl=dynamic_sl,
        )
        audit = self._audit_tp_levels(real_amt)
        return {
            "matched": matched,
            "expected": expected,
            "pending_prices": pending,
            "rebuilt": rebuilt,
            "audit": audit,
        }

    def _reconcile_context_on_recover(self, pos):
        """重启对账：实盘头寸 vs 账本 vs 最新 TV / 开仓日志"""
        notes = []
        reconcile = {
            "notes": notes,
            "tv_close": False,
            "direction_mismatch": False,
            "qty_manual_change": None,
        }
        side = pos["side"]
        real_amt = float(pos["size"])
        saved_watched = float(self.watched_qty or 0)
        saved_initial = float(self.initial_qty or 0)

        last_tv = self._load_last_journal_entry(TV_JOURNAL)
        last_open = self._load_last_journal_entry(OPEN_JOURNAL)
        last_open_tv = self._load_last_tv_open_signal()

        if last_tv:
            self.last_tv_signal = last_tv
            tv_action = (last_tv.get("action") or "").upper()
            tv_tps_saved = self._sanitize_tp_prices(last_tv.get("tv_tps", []))
            tv_tp_count = sum(1 for t in tv_tps_saved if t > 0)

            if last_tv.get("regime"):
                self.regime = int(last_tv["regime"])
            if last_tv.get("atr"):
                self.current_atr = float(last_tv["atr"])
            if self.tv_price <= 0 and float(last_tv.get("price", 0) or 0) > 0:
                self.tv_price = float(last_tv["price"])

            if tv_action in ("LONG", "SHORT"):
                self.last_tv_side = tv_action
                if tv_tp_count > 0:
                    self.tv_tps = tv_tps_saved
                    notes.append(f"TV日志同步止盈价 {self.tv_tps}")
                if side != tv_action:
                    reconcile["direction_mismatch"] = True
                    notes.append(
                        f"方向背离: 实盘{side} vs TV最新{tv_action} ({last_tv.get('ts', '')})"
                    )
            elif tv_action.startswith("CLOSE"):
                reconcile["tv_close"] = True
                notes.append(
                    f"TV最新为{tv_action} ({last_tv.get('ts', '')})，实盘仍有仓 → 应清场"
                )
                if last_open_tv:
                    self.last_tv_side = (last_open_tv.get("action") or "").upper()
                    open_tps = self._sanitize_tp_prices(last_open_tv.get("tv_tps", []))
                    if sum(1 for t in open_tps if t > 0) > 0:
                        self.tv_tps = open_tps

        if not self.last_tv_side and last_open_tv:
            self.last_tv_side = (last_open_tv.get("action") or "").upper()

        if last_open:
            open_side = last_open.get("side")
            if open_side and side != open_side:
                notes.append(f"开仓日志方向 {open_side} ≠ 实盘 {side}")
            open_entry = float(last_open.get("entry", 0) or 0)
            if open_entry > 0 and abs(pos["entry_price"] - open_entry) > 3.0:
                notes.append(
                    f"入场偏差: 开仓日志 {open_entry:.2f} vs 实盘 {pos['entry_price']:.2f}"
                )

        if saved_watched <= 0 and real_amt > 0:
            reconcile["manual_open"] = True
            self.initial_qty = real_amt
            self.tp_levels_consumed = []
            if float(getattr(self, "base_qty", 0) or 0) <= 0:
                self.base_qty = real_amt
            notes.append(
                f"人工开仓(重启): 账本空仓 → 实盘 {real_amt} ETH {side}，已接管为基准仓"
            )
        elif saved_watched > 0 and real_amt > 0:
            entry_px = float(pos.get("entry_price", 0) or 0)
            je = float(last_open.get("entry", 0) or 0) if last_open else 0.0
            entry_tol = max(3.0, entry_px * 0.003) if entry_px > 0 else 3.0
            if last_open and je > 0 and entry_px > 0 and abs(entry_px - je) > entry_tol:
                reconcile["manual_open"] = True
                self.initial_qty = real_amt
                self.tp_levels_consumed = []
                self.base_qty = float(real_amt)
                notes.append(
                    f"人工新开(入场偏差): 日志 {je:.2f} vs 实盘 {entry_px:.2f} → 重置 TP123"
                )
            elif saved_initial > real_amt + 0.001:
                trusted = self._trusted_initial_qty(real_amt, entry_px)
                if trusted <= real_amt + 0.001:
                    reconcile["manual_open"] = True
                    self.initial_qty = real_amt
                    self.tp_levels_consumed = []
                    notes.append(
                        f"人工/重置(重启): 陈旧 initial={saved_initial} > 现仓 {real_amt} "
                        f"但无日志锚定 → 全链 TP123"
                    )

        if saved_watched > 0 and self._is_material_qty_change(saved_watched, real_amt):
            action_msg = (
                "手动加仓" if real_amt > saved_watched
                else "部分止盈吃单 / 手动减仓"
            )
            reconcile["qty_manual_change"] = (saved_watched, real_amt, action_msg)
            notes.append(
                f"人工异动(重启): {saved_watched} ETH → {real_amt} ETH ({action_msg})"
            )

        if not self.last_tv_side:
            if not reconcile["direction_mismatch"]:
                self.last_tv_side = side
        elif side != self.last_tv_side and not reconcile["tv_close"]:
            if self._live_aligns_with_credible_tv(side):
                notes.append(
                    f"陈旧TV方向{self.last_tv_side}与实盘{side}不一致，"
                    f"但最新TV信源同向 → 以接管为准"
                )
                self.last_tv_side = side
            else:
                reconcile["direction_mismatch"] = True
                if not any("方向背离" in n for n in notes):
                    notes.append(f"方向背离: 实盘{side} vs TV指令{self.last_tv_side}")

        if saved_initial <= 0 and real_amt > 0:
            self.initial_qty = real_amt

        for n in notes:
            logger.warning(f"🔎 重启对账: {n}")
        return reconcile

    def _trusted_initial_qty(self, live_qty, entry=None):
        """
        可信开单量：同笔持仓只升不降。
        OPEN 日志入场对齐时，journal/saved > live 视为部分止盈或减仓，必须保留开单量；
        仅当入场价显著偏离（换仓）时才回落到现仓。
        """
        live_qty = float(live_qty or 0)
        entry = float(entry or self.watched_entry or 0)
        saved = float(self.initial_qty or 0)
        last_open = self._load_last_journal_entry(OPEN_JOURNAL)
        jq = float((last_open or {}).get("qty", 0) or 0)
        je = float((last_open or {}).get("entry", 0) or 0)
        entry_tol = max(3.0, entry * 0.003) if entry > 0 else 3.0
        same_trade = (
            jq > 0
            and (entry <= 0 or je <= 0 or abs(entry - je) <= entry_tol)
        )
        if same_trade:
            # TP1/TP2 成交后 live < journal —— 这是正常减仓，绝不能压成现仓
            peak = max(jq, saved if saved > 0 else 0.0, live_qty)
            if live_qty > 0 and jq > live_qty + 0.001:
                logger.info(
                    f"📖 OPEN日志开单 {jq} ETH @ {je:.2f} > 现仓 {live_qty} ETH "
                    f"→ 保留开单量（部分止盈/减仓）"
                )
            return peak
        # 入场偏离 → 不同一笔；仍优先保留监控中已记录的开单峰值
        if self.monitoring and self.current_side and saved > live_qty + 0.001 and live_qty > 0:
            return saved
        if 0 < saved <= live_qty + 0.001:
            return max(saved, live_qty)
        return live_qty if live_qty > 0 else saved

    def _resolve_open_initial_qty(self, live_qty, entry=None):
        """
        开单原始头寸：同笔持仓只升不降。
        禁止因减仓把 initial 压到现仓并清空 tp_levels_consumed（会摧毁 TP 成交识别）。
        """
        live_qty = float(live_qty or 0)
        trusted = self._trusted_initial_qty(live_qty, entry)
        saved = float(self.initial_qty or 0)
        peak = max(trusted, saved, live_qty)

        if live_qty <= 0:
            return 0.0

        # 监控中同向持仓：峰值写入 initial，绝不因减仓清零已成交档
        if self.monitoring and self.current_side:
            if peak > saved + 0.0005:
                self.initial_qty = peak
                self._save_state()
            elif float(self.initial_qty or 0) <= 0 and peak > 0:
                self.initial_qty = peak
                self._save_state()
            return peak

        # 非监控接管：仅当入场与 OPEN 日志明显不符时才重置为现仓
        if saved > live_qty + 0.001 and trusted <= live_qty + 0.001:
            logger.warning(
                f"📖 丢弃陈旧 initial_qty={saved} → 锚定 {trusted} ETH "
                f"(现仓 {live_qty}，换仓/无同笔开仓证据)"
            )
            self.initial_qty = trusted
            self.tp_levels_consumed = []
            self._save_state()
            return trusted if trusted > 0 else live_qty

        if peak > saved + 0.0005:
            self.initial_qty = peak
            self._save_state()
        return peak if peak > 0 else live_qty

    def _save_state(self):
        try:
            with open(self.state_file, 'w') as f:
                json.dump({
                    "last_tv_side": self.last_tv_side,
                    "current_side": self.current_side,
                    "watched_qty": self.watched_qty,
                    "watched_entry": self.watched_entry,
                    "current_sl": self.current_sl,
                    "monitoring": self.monitoring,
                    "regime": self.regime,
                    "current_atr": self.current_atr,
                    "tv_tps": self.tv_tps,
                    "tv_price": self.tv_price,
                    "best_price": self.best_price,
                    "initial_qty": self.initial_qty,
                    "last_tv_signal": self.last_tv_signal,
                    "open_regime": self.open_regime,
                    "open_atr": self.open_atr,
                    "shield_active": getattr(self, "shield_active", False),
                    "shield_tiers_consumed": list(getattr(self, "shield_tiers_consumed", []) or []),
                    "tp_levels_consumed": list(getattr(self, "tp_levels_consumed", []) or []),
                    "shield_sized_qty": float(getattr(self, "shield_sized_qty", 0) or 0),
                    "sizing_principal": float(getattr(self, "sizing_principal", 0) or 0),
                    "tv_sl": float(getattr(self, "tv_sl", 0) or 0),
                    "tv_sl_ref": float(getattr(self, "tv_sl_ref", 0) or 0),
                    "radar_stage_last": int(getattr(self, "_radar_stage_last", 0) or 0),
                    "radar_armed_after_tp1": bool(
                        getattr(self, "_radar_armed_after_tp1", False)
                    ),
                    "radar_handoff_done": bool(
                        getattr(self, "_radar_handoff_done", False)
                    ),
                    "open_settled_qty": float(
                        getattr(self, "_open_settled_qty", 0) or 0
                    ),
                    "last_applied_exchange_sl": float(
                        getattr(self, "_last_applied_exchange_sl", 0) or 0
                    ),
                    "tv_risk_pct": float(getattr(self, "tv_risk_pct", 0) or 0),
                    "tv_qty_ratio": float(getattr(self, "tv_qty_ratio", 1.0) or 1.0),
                    "tv_entry_type": getattr(self, "tv_entry_type", ENTRY_TYPE_OPEN),
                    "leverage": EXCHANGE_LEVERAGE,
                    "tv_sizing_leverage": float(
                        getattr(self, "tv_sizing_leverage", EXCHANGE_LEVERAGE) or EXCHANGE_LEVERAGE
                    ),
                    "base_qty": float(getattr(self, "base_qty", 0) or 0),
                    "add_count": int(getattr(self, "add_count", 0) or 0),
                }, f)
        except Exception as e:
            logger.error(f"保存状态失败: {e}")

    def _qty_change_ratio(self, old_qty, new_qty):
        old = float(old_qty or 0)
        new = float(new_qty or 0)
        if old <= 0 and new <= 0:
            return 0.0
        return abs(new - old) / max(old, new, 1e-9)

    def _is_material_qty_change(self, old_qty, new_qty):
        """
        离谱级异动：偏离 ≥ QTY_ALIGN_MIN_PCT 才触发对齐/钉钉。
        微漂（1.5%~10%）由哨兵静默同步账本，不打扰。
        """
        old = float(old_qty or 0)
        new = float(new_qty or 0)
        delta = abs(new - old)
        if delta <= REGIME_CAP_TOLERANCE_ETH:
            return False
        ratio = self._qty_change_ratio(old, new)
        return ratio >= QTY_ALIGN_MIN_PCT

    def _sanitize_tp_prices(self, tp_list):
        out = []
        for t in tp_list:
            try:
                out.append(round(float(t), 2) if float(t) > 0 else 0.0)
            except (TypeError, ValueError):
                out.append(0.0)
        return out

    def _get_active_position(self):
        pos = position_manager.get_position(self.symbol)
        if not pos or float(pos.get("positionAmt", 0)) == 0:
            return None
        amt = float(pos["positionAmt"])
        return {
            "size": abs(amt),
            "entry_price": round(float(pos.get("entryPrice", 0)), 2),
            "side": "LONG" if amt > 0 else "SHORT",
        }

    def _verify_flat(self):
        pos = self._get_active_position()
        return pos is None

    def _ensure_flat_before_open(self, reason_tag="开仓前"):
        """开仓闸门：盘口必须归零，否则阶梯强平；仍失败则拒绝叠仓"""
        if self._wait_verify(self._verify_flat, retries=4, delay=0.4):
            return True
        logger.warning(f"⚠️ {reason_tag}：检测到残留持仓，启动强制平仓")
        if self._close_all(f"{reason_tag} · 强制清场", reset_state=True):
            return self._wait_verify(self._verify_flat, retries=6, delay=0.5)
        return False

    def _snapshot_sizing_principal(self, reason=""):
        """全平/开仓前：锁定账户总权益（marginBalance），供本周期开仓与 9x 硬顶共用"""
        principal = binance_client.get_total_equity()
        if principal > 0:
            self.sizing_principal = principal
            self._save_state()
            logger.info(f"📸 本金快照 {principal:.2f} USDT ({reason})")
            if reason and ("全平" in reason or "开仓前" in reason):
                target_qty = None
                eff_risk = None
                if "开仓前" in reason and self.tv_price > 0:
                    t, meta = self._calc_vps_open_qty(self.tv_price)
                    target_qty = t
                    eff_risk = float(meta.get("effective_risk_pct", VPS_RISK_PCT) or VPS_RISK_PCT) / 100.0
                    vps_meta = meta
                else:
                    vps_meta = None
                try:
                    self._call_dingtalk(
                        dingtalk.report_principal_snapshot,
                        reason=reason,
                        principal=principal,
                        regime=self.regime if "开仓前" in reason else None,
                        margin_pct=eff_risk,
                        target_qty=target_qty,
                        leverage=EXCHANGE_LEVERAGE,
                        vps_sizing_meta=vps_meta,
                    )
                except Exception as e:
                    logger.warning(f"本金快照钉钉跳过: {e}")
        return principal

    def _resolve_cap_sizing_base(self, wallet_balance=None):
        """
        档位额度唯一基数：sizing_principal 快照；下单按 VPS 风险系数公式。
        """
        wallet = float(
            wallet_balance if wallet_balance is not None
            else binance_client.get_total_equity()
        )
        principal = float(getattr(self, "sizing_principal", 0) or 0)
        if principal > 0:
            if wallet > 0 and wallet < principal:
                return wallet
            return principal
        return wallet

    def _regime_cap_target_qty(self, curr_px, regime=None):
        """VPS OPEN 公式 → 仓位上限（已废弃 margin% 口径）"""
        regime = int(regime if regime is not None else self.regime)
        qty, meta = self._calc_vps_open_qty(curr_px, regime=regime)
        balance = float(meta.get("principal", 0) or self._resolve_cap_sizing_base())
        order_amount = float(meta.get("order_amount", 0) or 0)
        eff = float(meta.get("effective_risk_pct", VPS_RISK_PCT) or VPS_RISK_PCT) / 100.0
        return float(qty or 0), balance, order_amount, eff, regime

    def _validate_cap_trim_plan(self, live_qty, target_qty, trim_qty):
        """裁减前安全校验：防止 target 被错误算成灰尘导致几乎全平"""
        live = float(live_qty or 0)
        target = float(target_qty or 0)
        trim = float(trim_qty or 0)
        if live <= 0 or target <= 0:
            return "数量无效，无法裁减"
        if trim <= 0:
            return None
        retain = target / live
        if retain < CAP_MIN_RETAIN_RATIO and live > target * 2:
            return (
                f"目标仅相当于实盘的 {retain:.1%}，疑似误用「可用保证金」而非「本金快照」"
                f"（目标 {target:.4f} ETH vs 实盘 {live:.4f} ETH）"
            )
        if trim > live * 0.85 and target < live * 0.15:
            return (
                f"裁减幅度过大：将平掉 {trim:.4f} ETH，仅保留 {target:.4f} ETH，"
                f"疑似额度基数算错"
            )
        expected = round(live - target, 3)
        if abs(trim - expected) > max(live * 0.05, 0.01):
            return f"裁减量不符：计划 {trim:.4f} ETH，应为 {expected:.4f} ETH"
        return None

    def _max_add_times_for_regime(self, regime=None):
        """TV v6.9.93：加仓次数上限跟当前信号档位"""
        return get_regime_max_add_times(int(regime if regime is not None else self.regime or 3))

    def _apply_tv_sizing_params(self, payload):
        """解析 entry_type：OPEN 由 VPS 自主 sizing；加仓用 TV qty_ratio × 首仓 base_qty"""
        self.tv_entry_type = normalize_entry_type(payload.get("entry_type"))
        if self.tv_entry_type in (ENTRY_TYPE_PYRAMID, ENTRY_TYPE_PROFIT_ADD):
            self.tv_qty_ratio = resolve_tv_add_qty_ratio(
                self.regime,
                self._safe_float(payload.get("qty_ratio"), None),
            )
        else:
            self.tv_qty_ratio = 1.0
        self.leverage = EXCHANGE_LEVERAGE
        self._save_state()
        max_add = self._max_add_times_for_regime()
        logger.info(
            f"📐 TV参数: type={self.tv_entry_type} "
            f"| VPS风险={VPS_RISK_PCT}% R{self.regime} "
            f"| 加仓=base×{self.tv_qty_ratio:.2f}(TV) 最多{max_add}次 "
            f"| 交易所={EXCHANGE_LEVERAGE}x"
        )

    def _calc_vps_add_qty(self, qty_ratio=None):
        base = float(getattr(self, "base_qty", 0) or 0)
        if base <= 0:
            base = float(getattr(self, "initial_qty", 0) or getattr(self, "watched_qty", 0) or 0)
        ratio = resolve_tv_add_qty_ratio(
            self.regime,
            qty_ratio if qty_ratio is not None else getattr(self, "tv_qty_ratio", None),
        )
        qty, meta = compute_vps_add_qty(base, ratio, regime=self.regime)
        meta["principal"] = self._resolve_cap_sizing_base()
        meta["add_count"] = int(getattr(self, "add_count", 0) or 0)
        meta["max_add_times"] = self._max_add_times_for_regime()
        return float(qty or 0), meta

    def _calc_vps_open_qty(self, curr_px, regime=None):
        principal = self._resolve_cap_sizing_base()
        px = float(curr_px or self.tv_price or 0)
        sl = float(getattr(self, "tv_sl", 0) or 0)
        qty, meta = compute_vps_open_qty(
            principal, px, sl, int(regime if regime is not None else self.regime),
            leverage=EXCHANGE_LEVERAGE,
            qty_step=float(getattr(self, "qty_step", 0.001) or 0.001),
            min_qty=float(getattr(self, "min_qty", 0.001) or 0.001),
        )
        meta["principal"] = principal
        meta["symbol"] = self.symbol
        return float(qty or 0), meta

    def _other_symbols_notional(self, exclude_symbol=None):
        """账户其它品种名义敞口合计（不含本品种）。"""
        exclude = str(exclude_symbol or self.symbol).upper()
        by_sym, total = binance_client.get_all_usdt_position_notionals()
        other = 0.0
        for sym, notion in (by_sym or {}).items():
            if str(sym).upper() == exclude:
                continue
            other += float(notion or 0)
        return round(other, 2), by_sym, total

    def _assert_notional_cap_or_reject(self, qty, price, sizing_meta=None):
        """双品种硬顶：其它品种名义 + 本笔名义 ≤ equity×9。"""
        equity = float(
            (sizing_meta or {}).get("principal")
            or self._resolve_cap_sizing_base()
            or 0
        )
        new_notional = float(qty or 0) * float(price or 0)
        other, by_sym, all_total = self._other_symbols_notional(self.symbol)
        # 本品种若已有仓，开仓流程本应先平；保守起见从 existing 去掉本品种
        existing = other
        ok, meta = check_total_notional_cap(
            equity, existing, new_notional, mult=MAX_TOTAL_NOTIONAL_MULT,
        )
        meta["by_symbol"] = by_sym
        meta["symbol"] = self.symbol
        if ok:
            logger.info(
                f"📐 敞口校验通过 {self.symbol}: 其它 {existing:.0f}U + 本笔 {new_notional:.0f}U "
                f"= {meta['total_notional']:.0f}U ≤ 本金 {equity:.0f}U×{MAX_TOTAL_NOTIONAL_MULT:.0f}"
            )
            return True, meta
        logger.error(
            f"🚫 敞口硬顶拦截 {self.symbol}: 其它 {existing:.0f}U + 本笔 {new_notional:.0f}U "
            f"= {meta['total_notional']:.0f}U > 上限 {meta['cap']:.0f}U "
            f"(本金 {equity:.0f}U×{MAX_TOTAL_NOTIONAL_MULT:.0f}) | 盘口 {by_sym}"
        )
        dingtalk.report_system_alert(
            f"开仓拦截·名义敞口超限 [{self.symbol}]",
            f"本金 {equity:.0f}U · 上限 {meta['cap']:.0f}U ({MAX_TOTAL_NOTIONAL_MULT:.0f}x)\n"
            f"其它品种名义 {existing:.0f}U + 本笔 {new_notional:.0f}U "
            f"= {meta['total_notional']:.0f}U\n"
            f"盘口名义 {by_sym}",
        )
        return False, meta

    def _tv_sizing_note(self, qty, meta=None, entry_type="OPEN"):
        return format_vps_sizing_note(meta or {}, qty=qty, entry_type=entry_type)

    def _calc_target_open_qty(self, curr_px, payload=None):
        qty, meta = self._calc_vps_open_qty(curr_px)
        principal = float(meta.get("principal", 0) or 0)
        margin_usdt = float(meta.get("order_amount", 0) or 0)
        margin_pct = float(meta.get("effective_risk_pct", VPS_RISK_PCT) or VPS_RISK_PCT) / 100.0
        return qty, principal, margin_usdt, margin_pct, meta

    def _calc_regime_margin_qty(self, curr_px):
        qty, meta = self._calc_vps_open_qty(curr_px)
        principal = float(meta.get("principal", 0) or 0)
        return qty, principal, float(meta.get("order_amount", 0) or 0), float(meta.get("effective_risk_pct", VPS_RISK_PCT) or VPS_RISK_PCT) / 100.0

    def _regime_cap_tolerance(self, target_qty):
        """档位裁减容忍：离谱才管 — 超标 ≤10% 不裁"""
        target = float(target_qty or 0)
        if target <= 0:
            return REGIME_CAP_TOLERANCE_ETH
        return max(REGIME_CAP_TOLERANCE_ETH, target * QTY_ALIGN_MIN_PCT)

    def _is_oversize_for_regime(self, live_qty, curr_px, regime=None):
        target, _, _, margin_pct, reg = self._regime_cap_target_qty(curr_px, regime)
        if target <= 0 or live_qty <= 0:
            return False, target, margin_pct, reg
        tol = self._regime_cap_tolerance(target)
        excess = float(live_qty) - target
        if excess > REGIME_CAP_TOLERANCE_ETH and excess <= tol:
            logger.info(
                f"📎 [档位限额] 微超 {live_qty} > {target} ETH "
                f"(+{excess:.3f}, {excess / target:.2%} ≤ {QTY_ALIGN_MIN_PCT:.0%} 容忍)，跳过裁减"
            )
        return live_qty > target + tol, target, margin_pct, reg

    def _trim_position_to_target(self, target_qty, action, reason_tag="叠仓Remediation"):
        """叠仓Remediation：仅裁减 excess=实盘-目标，带安全校验与多轮核实"""
        target_qty = float(target_qty or 0)
        pos = self._get_active_position()
        if not pos or target_qty <= 0:
            return pos["size"] if pos else 0.0
        live = float(pos["size"])
        cap_tol = self._regime_cap_tolerance(target_qty)
        if live <= target_qty + cap_tol:
            return live
        trim_qty = round(live - target_qty, 3)
        plan_err = self._validate_cap_trim_plan(live, target_qty, trim_qty)
        if plan_err:
            logger.error(f"✂️ {reason_tag} 中止: {plan_err} | live={live} target={target_qty}")
            dingtalk.report_system_alert(
                "档位裁减已中止（安全保护）",
                f"场景：{reason_tag}\n"
                f"实盘：**{live}** ETH → 目标：**{target_qty}** ETH\n"
                f"原因：{plan_err}",
                suggestion="请核对本金快照与 TV 档位是否一致；勿手动干预，待下一 TV 信号或人工核查后重试",
            )
            return live
        close_side = "SELL" if action == "LONG" else "BUY"
        logger.warning(
            f"✂️ {reason_tag}: 裁减 {trim_qty} ETH "
            f"(实盘 {live} → 目标 {target_qty})"
        )
        binance_client.cancel_all_open_orders(self.symbol)
        time.sleep(0.5)
        self._cancel_all_tp_limit_orders(max_rounds=3)
        time.sleep(0.3)
        new_sz = live
        for round_i in range(CAP_TRIM_MAX_ROUNDS):
            pos = self._get_active_position()
            if not pos or pos["size"] <= 0:
                break
            cur = float(pos["size"])
            if cur <= target_qty + cap_tol:
                new_sz = cur
                break
            slice_trim = round(cur - target_qty, 3)
            if slice_trim <= 0:
                new_sz = cur
                break
            binance_client.place_market_order(close_side, slice_trim, symbol=self.symbol, reduce_only=True)
            time.sleep(1.0)
            verified = self._wait_verify(
                lambda: self._get_active_position(),
                retries=6,
                delay=0.5,
            )
            new_sz = float(verified["size"]) if verified else cur
            if new_sz <= target_qty + cap_tol:
                break
        if new_sz < target_qty * 0.5 and live > target_qty * 1.5:
            dingtalk.report_system_alert(
                "档位裁减过度",
                f"目标 {target_qty} ETH，裁减后仅 {new_sz} ETH（原 {live}），请人工核查",
            )
        elif new_sz > target_qty * OPEN_OVERSIZE_RATIO:
            dingtalk.report_system_alert(
                "叠仓裁减未达标",
                f"目标 {target_qty} ETH，裁减后仍 {new_sz} ETH，请人工核查",
            )
        return new_sz

    def _radar_enforce_regime_cap(self, live_qty, curr_px, force=False):
        """
        雷达最高权限：实盘超过 TV 档位保证金上限 → reduceOnly 裁减 → 重挂 TP123。
        雷达移动止损位不变，仅补挂缺失 STOP。
        """
        if live_qty <= 0 or not self.current_side:
            return None
        if not force and (
            getattr(self, "_open_in_progress", False)
            or getattr(self, "_recover_in_progress", False)
        ):
            return None

        oversize, target, margin_pct, regime = self._is_oversize_for_regime(
            live_qty, curr_px, self.regime,
        )
        if not oversize:
            return None

        now = time.time()
        severe = live_qty > target * 1.35
        if (
            not severe
            and now - getattr(self, "_last_regime_cap_ts", 0) < REGIME_CAP_COOLDOWN_SEC
        ):
            logger.info(
                f"📡 [雷达档位限额] 超标 {live_qty}>{target} ETH 但冷却中 "
                f"(R{regime} {margin_pct:.0%})"
            )
            return None

        _, balance, margin_usdt, margin_pct, regime = self._regime_cap_target_qty(curr_px, regime)
        old_qty = live_qty
        logger.warning(
            f"📡 [雷达档位限额] R{regime} VPS上限 {target} ETH "
            f"(本金 {balance:.0f}U×VPS风险{margin_pct:.1%}×{self.leverage}x) | "
            f"实盘 {live_qty} ETH 超标 → 强制裁减"
        )

        new_qty = self._trim_position_to_target(
            target, self.current_side, reason_tag=f"雷达R{regime}档位限额",
        )
        pos = self._get_active_position()
        entry = pos["entry_price"] if pos else self.watched_entry
        self.watched_qty = new_qty
        self.initial_qty = new_qty
        if pos:
            self.watched_entry = entry
        self._save_state()

        sl = self._radar_sl_to_pass()
        result = self._enforce_defense_alignment(
            new_qty, entry, dynamic_sl=sl,
            reason=f"雷达档位限额 R{regime} 裁减后 TP 对齐", rounds=3,
        )
        if sl and not self._has_stop_sl_near(sl):
            self._ensure_radar_sl(sl, new_qty)

        self._last_regime_cap_ts = now
        verify_note = (
            f"VPS {balance:.2f}U × R{regime} 风险{margin_pct:.1%} × {self.leverage}x "
            f"= 下单额 {margin_usdt:.0f}U → 上限 {target} ETH | "
            f"裁减 {old_qty} → {new_qty} ETH | "
            f"TP {result['matched']}/{result['expected']} | "
            f"{self._format_audit_summary(result['audit'])} | "
            f"雷达SL={'已保留/已补' if sl else '待命'}"
        )
        self._call_dingtalk(
            dingtalk.report_radar_regime_cap_trim,
            side=self.current_side,
            old_qty=old_qty,
            new_qty=new_qty,
            target_qty=target,
            regime=regime,
            margin_pct=margin_pct,
            tp_audit=result["audit"],
            verify_note=verify_note,
            principal_balance=balance,
            margin_usdt=margin_usdt,
            leverage=self.leverage,
            trim_qty=round(old_qty - new_qty, 3),
        )
        return {"new_qty": new_qty, "target": target, "result": result}

    def _is_dust_qty(self, qty):
        """币安 ETH 最小步长 0.001；≤0.004 视为蚂蚁仓"""
        try:
            q = float(qty)
        except (TypeError, ValueError):
            return False
        return 0 < q <= DUST_QTY_ETH

    def _should_finalize_tp_victory(self, real_amt):
        """止盈网格已吃完、盘口无 TP 限价单，但可能残留蚂蚁仓 → 扫尾收网"""
        if real_amt <= 0:
            return False
        if self._is_dust_qty(real_amt):
            return True
        if self._collect_limit_tp_prices():
            return False
        if self._expected_tp_count() > 0 and not self._tp1_filled_verified(real_amt):
            return False
        ref = self.initial_qty or self.watched_qty
        if ref > 0 and real_amt <= ref * TP_COMPLETE_RESIDUAL_RATIO:
            return True
        return False

    def _report_flat_close(self, reason, swept_dust=False, close_meta=None, curr_px=0.0):
        """平仓/止盈收网钉钉：REST 核查重试，与 Pine 四标签对齐"""
        meta = self._enrich_close_meta_live(close_meta, curr_px)
        flat = self._wait_verify(self._verify_flat, retries=6, delay=0.5)
        base_note = "盘口无持仓 | 挂单已清空 | 智慧大脑复位待命"
        if swept_dust:
            base_note = f"蚂蚁仓已市价扫尾 | {base_note}"
        if meta.get("pnl_pct") is not None:
            base_note += f" | 盈亏 {self._safe_float(meta.get('pnl_pct')):+.2f}%"
        if meta.get("side"):
            base_note += f" | 方向 {meta.get('side')}"
        if meta.get("entry_px") and float(meta.get("entry_px") or 0) > 0:
            base_note += f" | 开仓 {float(meta['entry_px']):.2f}"
        if meta.get("closed_qty") and float(meta.get("closed_qty") or 0) > 0:
            base_note += f" | 平仓 {float(meta['closed_qty']):.3f} ETH"
        if meta.get("live_exit_px") and float(meta.get("live_exit_px") or 0) > 0:
            base_note += f" | 现价 {float(meta['live_exit_px']):.2f}"
        if meta.get("regime"):
            base_note += f" | TV档位 R{int(meta.get('regime'))}"
        if meta.get("atr") and float(meta.get("atr") or 0) > 0:
            base_note += f" | TV ATR {float(meta['atr']):.2f}"
        src_note = format_tv_field_sources(meta.get("field_sources") or {})
        if src_note and "TV透传" not in src_note:
            base_note += f" | {src_note}"
        if flat:
            verify_note = base_note
        else:
            pos = self._get_active_position()
            residual = pos["size"] if pos else 0.0
            if residual > 0 and not self._is_dust_qty(residual):
                logger.warning(
                    f"平仓钉钉跳过：空仓核查未通过 | 残留 {residual} ETH | reason={reason}"
                )
                return
            verify_note = f"{base_note} | REST 同步略延迟"
            logger.info(f"平仓钉钉：REST 延迟，仍推送收网播报 | reason={reason}")
        display_reason = meta.get("tv_reason") or reason or "仓位归零"
        self._call_dingtalk(
            dingtalk.report_supervisor_close,
            reason=display_reason,
            verify_note=verify_note,
            verified=flat,
            swept_dust=swept_dust,
            tv_pnl_pct=meta.get("pnl_pct"),
            tv_side=meta.get("side"),
            tv_price=meta.get("tv_price"),
            close_action=meta.get("action"),
            tv_regime=meta.get("regime"),
            tv_atr=meta.get("atr"),
            tv_field_sources=meta.get("field_sources"),
            close_type=meta.get("close_type"),
            tv_reason=meta.get("tv_reason") or display_reason,
            entry_px=meta.get("entry_px"),
            closed_qty=meta.get("closed_qty"),
            live_exit_px=meta.get("live_exit_px"),
        )

    def _sweep_dust_and_finalize(self, reason):
        """哨兵检测：止盈后蚂蚁仓/无 TP 残量 → 撤单 + reduceOnly 扫尾 + 完美胜利钉钉"""
        logger.warning(f"🐜 止盈扫尾：检测到残量，启动蚂蚁仓强平 → {reason}")
        self.monitoring = False
        self._purge_all_defense_orders_on_flat(f"蚂蚁仓扫尾·{reason}")
        time.sleep(0.4)
        for round_i in range(4):
            pos = self._get_active_position()
            if not pos or pos["size"] <= 0:
                break
            close_side = "SELL" if pos["side"] == "LONG" else "BUY"
            logger.info(f"🐜 扫尾第 {round_i + 1}/4: {close_side} {pos['size']} ETH reduceOnly")
            binance_client.place_market_order(close_side, pos["size"], symbol=self.symbol, reduce_only=True)
            time.sleep(1.0)
        self.watched_qty = 0.0
        self.initial_qty = 0.0
        self._open_settled_qty = 0.0
        self.base_qty = 0.0
        self.add_count = 0
        self.tp_levels_consumed = []
        self.shield_active = False
        self.current_side = None
        self._save_state()
        self._purge_all_defense_orders_on_flat(f"扫尾完成·{reason}")
        self._report_flat_close(reason, swept_dust=True)

    def _apply_recover_live_alignment(self, side, reconcile):
        """重启对账备注：TV 平仓日志不回放；方向背离由 _enforce_tv_direction_or_flat 核武处理"""
        extra_notes = []
        if reconcile.get("tv_close"):
            action = (self.last_tv_signal or {}).get("action", "CLOSE")
            msg = (
                f"TV日志末条为 {action}，重启不回放平仓 → 以实盘 {side} 继续闪电接管"
            )
            logger.warning(f"🔄 [重启] {msg}")
            extra_notes.append(msg)
            last_open_tv = self._load_last_tv_open_signal()
            if last_open_tv:
                self.last_tv_side = (last_open_tv.get("action") or side).upper()
                open_tps = self._sanitize_tp_prices(last_open_tv.get("tv_tps", []))
                if sum(1 for t in open_tps if t > 0) > 0:
                    self.tv_tps = open_tps
        elif reconcile.get("direction_mismatch"):
            tv_side = self._resolve_tv_authoritative_side()
            extra_notes.append(
                f"方向背离: 实盘{side} vs TV{tv_side} → 已由核武全平强制对齐 TV"
            )
        elif not self.last_tv_side:
            self.last_tv_side = side
        return extra_notes

    def _scan_and_sweep_dust_on_startup(self, was_monitoring=False):
        """重启首检：发现蚂蚁仓/止盈残量 → 扫尾收网，避免误接管为正常持仓"""
        pos = self._get_active_position()
        if not pos or pos["size"] <= 0:
            return False
        if not self.current_side:
            self.current_side = pos["side"]
        real_amt = pos["size"]
        ref = max(float(self.initial_qty or 0), float(self.watched_qty or 0))
        if was_monitoring and not self._is_dust_qty(real_amt):
            if ref <= 0 or real_amt > max(
                DUST_QTY_ETH, ref * TP_COMPLETE_RESIDUAL_RATIO
            ):
                logger.info(
                    f"🔄 [重启扫描] 活跃主仓 {real_amt} ETH (ref={ref})，跳过蚂蚁扫尾"
                )
                return False
        if not self._is_dust_qty(real_amt) and not self._should_finalize_tp_victory(real_amt):
            return False
        if self.initial_qty > 0 or self.watched_qty > 0:
            reason = "仓位归零 (止盈吃单 / 人工全平 / TV 强制平仓)"
        else:
            reason = "重启扫描：盘口蚂蚁仓自动扫平"
        logger.warning(
            f"🐜 [重启扫描] {self.current_side} 残量 {real_amt} ETH "
            f"(initial={self.initial_qty}, watched={self.watched_qty}) → 扫尾强平"
        )
        self._sweep_dust_and_finalize(reason)
        return True

    def _recover_missed_flat_on_startup(self, was_monitoring=False):
        """重启对账：服务宕机/重启期间已全平，但账本仍有仓 → 补发完美胜利钉钉"""
        pos = self._get_active_position()
        if pos and pos["size"] > 0:
            return False

        prev_watched = float(self.watched_qty or 0)
        prev_initial = float(self.initial_qty or 0)
        prev_side = self.current_side

        had_active_book = (
            prev_watched > 0
            or prev_initial > 0
            or prev_side in ("LONG", "SHORT")
            or was_monitoring
        )
        if not had_active_book:
            last_open = self._load_last_journal_entry(OPEN_JOURNAL)
            if last_open and last_open.get("source") in ("open", "recover"):
                had_active_book = True
                prev_watched = prev_watched or float(last_open.get("qty", 0) or 0)
                prev_side = prev_side or last_open.get("side")

        if not had_active_book:
            return False

        if not self._confirm_position_flat(
            retries=STARTUP_FLAT_CONFIRM_RETRIES,
            delay=STARTUP_FLAT_CONFIRM_DELAY_SEC,
        ):
            logger.info(
                "📭 [重启对账] 首次无仓但多次复核仍有持仓 → 跳过误补发收网"
            )
            return False

        logger.warning(
            f"📭 [重启对账] 账本/日志曾有仓 (watched={prev_watched}, side={prev_side}, "
            f"monitoring={was_monitoring}) 但盘口已全平 → 补发收网播报"
        )
        binance_client.cancel_all_open_orders(self.symbol)
        self.monitoring = False
        self.watched_qty = 0.0
        self.base_qty = 0.0
        self.add_count = 0
        self.current_side = None
        self.initial_qty = 0.0
        self._save_state()

        verify_note = (
            f"重启对账补发 | 原账本 {prev_watched} ETH {prev_side or ''} | "
            f"盘口无持仓 | 挂单已清空 | 智慧大脑复位待命"
        )
        recover_meta = self._infer_flat_close_meta(hint_reason="重启对账补发收网")
        self._call_dingtalk(
            dingtalk.report_supervisor_close,
            reason=recover_meta.get("tv_reason", "仓位归零 (重启对账补发)"),
            verify_note=verify_note,
            verified=True,
            swept_dust=False,
            tv_pnl_pct=recover_meta.get("pnl_pct"),
            tv_side=recover_meta.get("side") or prev_side,
            close_action=recover_meta.get("action"),
            tv_regime=recover_meta.get("regime"),
            tv_atr=recover_meta.get("atr"),
            close_type=recover_meta.get("close_type"),
            tv_reason=recover_meta.get("tv_reason"),
            entry_px=recover_meta.get("entry_px"),
            closed_qty=prev_watched,
        )
        return True

    def _verify_position(self, expected_side=None):
        pos = self._get_active_position()
        if not pos or pos["size"] <= 0:
            return None
        if expected_side and pos["side"] != expected_side:
            return None
        return pos

    def _verify_position_qty(self, expected_qty, expected_side=None):
        pos = self._verify_position(expected_side)
        if not pos or abs(pos["size"] - expected_qty) >= 0.001:
            return None
        return pos

    def _is_tp_limit_order(self, o):
        if o.get("type") != "LIMIT":
            return False
        val = o.get("reduceOnly")
        if val is True or str(val).lower() in ("true", "1"):
            return True
        if not self.current_side:
            return False
        close_side = "BUY" if self.current_side == "SHORT" else "SELL"
        return o.get("side") == close_side

    def _collect_limit_tp_prices(self):
        prices = []
        for o in binance_client.get_open_orders(self.symbol):
            if not self._is_tp_limit_order(o):
                continue
            px = float(o.get("price", 0) or 0)
            if px > 0:
                prices.append(round(px, 2))
        return sorted(prices)

    def _expected_tp_count(self, tp_pxs=None):
        tp_pxs = tp_pxs if tp_pxs is not None else self.tv_tps
        consumed = set(getattr(self, "tp_levels_consumed", []) or [])
        return sum(
            1 for i, t in enumerate(tp_pxs)
            if t > 0 and (i + 1) not in consumed
        )

    def _tp_split_regime(self):
        """止盈比例以开仓档位为准（open_regime），避免 TV 档位变化导致比例算错"""
        if self.watched_qty and self.watched_qty > 0:
            return int(getattr(self, "open_regime", self.regime) or self.regime)
        return int(self.regime)

    def _tp_slices_for_initial(self, initial_qty):
        ratios = self.regime_settings[self._tp_split_regime()]["ratios"]
        o1, o2, o3 = self._split_tp_quantities(initial_qty, ratios)
        return [
            {"level": 1, "price": self.tv_tps[0], "qty": o1},
            {"level": 2, "price": self.tv_tps[1], "qty": o2},
            {"level": 3, "price": self.tv_tps[2], "qty": o3},
        ]

    @staticmethod
    def _sequential_tp_prefix(levels):
        """已成交档必须是顺序前缀：不可出现 [1,3] 而无 2"""
        out = []
        for lv in (1, 2, 3):
            if lv in levels:
                out.append(lv)
            else:
                break
        return out

    def _infer_tp_consumed_sequential(self, initial_qty, live_qty, curr_px=0.0):
        """
        按开单→现仓累计减仓，顺序推断已 fully 成交的 TP 档。
        硬约束（防 R4 TP1=5% 误判）：
        1) 该档限价仍在盘口 → 本档及后续一律未成交
        2) 相对开仓基线的减仓若 <2% → 噪声，不推断
        3) 用「接近累计减仓」双侧带，禁止「任意更大减仓」单向盖章
        4) TP1 须价已进入 TP1 区（或已武装过）
        """
        initial_qty = float(initial_qty or 0)
        live_qty = float(live_qty or 0)
        if initial_qty <= live_qty + 0.001:
            return []

        reduced = round(initial_qty - live_qty, 3)
        noise = max(0.003, initial_qty * TP_FILL_NOISE_VS_OPEN_PCT)
        if reduced < noise:
            return []

        consumed = []
        cum = 0.0

        for sl in self._tp_slices_for_initial(initial_qty):
            if sl["qty"] <= 0.0005 or sl["price"] <= 0:
                continue
            # 限价单还在 → 绝不可能已成交该档
            if self._has_tp_limit_at_price(sl["price"]):
                break
            if int(sl["level"]) == 1 and not (
                self._price_reached_tp1_zone(curr_px, sl["price"])
                or getattr(self, "_radar_armed_after_tp1", False)
                or getattr(self, "_ws_tp1_fill_hint", False)
            ):
                break
            cum = round(cum + sl["qty"], 3)
            tol = max(0.003, float(sl["qty"]) * TP_SLICE_MATCH_TOL_PCT)
            # 双侧：累计减仓必须达到本档；过冲可继续吃下一档，但首档至少吃到切片 85%
            if len(consumed) == 0 and reduced + 0.0005 < float(sl["qty"]) - tol:
                break
            if reduced + 0.0005 >= cum - tol:
                consumed.append(sl["level"])
                continue
            break

        return self._sequential_tp_prefix(consumed)

    def _sanitize_tp_consumed(self, initial_qty, live_qty, curr_px=0.0):
        """纠正 tp_levels_consumed：全标已成交但仍有仓 / 非顺序前缀 → 按减仓重算"""
        live_qty = float(live_qty or 0)
        initial_qty = float(initial_qty or 0)
        if live_qty <= DUST_QTY_ETH:
            self.tp_levels_consumed = []
            self._save_state()
            return []

        saved = self._sequential_tp_prefix(getattr(self, "tp_levels_consumed", []) or [])
        inferred = self._infer_tp_consumed_sequential(initial_qty, live_qty, curr_px)

        if initial_qty <= live_qty + 0.001 and saved and not inferred:
            logger.warning(
                f"⚠️ 无减仓但 tp_levels_consumed={saved} → 清空（避免漏挂 TP1）"
            )
            saved = []
        elif initial_qty <= live_qty + 0.001 and saved and inferred and saved != inferred:
            logger.info(
                f"🎯 无减仓以推断为准: TP{saved} → TP{inferred or '无'}"
            )
            saved = inferred

        if len(saved) >= 3 and live_qty > DUST_QTY_ETH:
            logger.warning(
                f"⚠️ tp_levels_consumed={saved} 但仍有 {live_qty} ETH → "
                f"按开单 {initial_qty} 重算为 TP{inferred or '无'}"
            )
            saved = inferred
        elif inferred and (not saved or len(inferred) < len(saved)):
            if saved != inferred:
                logger.info(
                    f"🎯 已成交档修正: TP{saved or '无'} → TP{inferred} "
                    f"(开单 {initial_qty} → 现仓 {live_qty})"
                )
            saved = inferred
        elif saved and inferred and saved != inferred:
            logger.info(
                f"🎯 已成交档以减仓为准: TP{saved} → TP{inferred}"
            )
            saved = inferred

        if saved != list(getattr(self, "tp_levels_consumed", []) or []):
            self.tp_levels_consumed = saved
            self._save_state()
        return saved

    def _mark_tp_levels_consumed(self, levels):
        consumed = set(getattr(self, "tp_levels_consumed", []) or [])
        for lv in levels:
            consumed.add(int(lv))
        self.tp_levels_consumed = self._sequential_tp_prefix(sorted(consumed))
        self._save_state()

    def _split_remaining_tp_quantities(self, live_qty, ratios=None):
        """已成交档跳过；剩余仓位 → 多档按比例，仅余一档则全给该档"""
        ratios = ratios or self.regime_settings[self._tp_split_regime()]["ratios"]
        consumed = set(getattr(self, "tp_levels_consumed", []) or [])
        remaining = [i for i in range(3) if (i + 1) not in consumed]
        if not remaining or live_qty <= 0:
            return {}
        if len(remaining) == 1:
            return {remaining[0] + 1: round(float(live_qty), 3)}
        rem_weights = [ratios[i] for i in remaining]
        wsum = sum(rem_weights) or 1.0
        out = {}
        budget = float(live_qty)
        for j, idx in enumerate(remaining[:-1]):
            level = idx + 1
            q = round(live_qty * rem_weights[j] / wsum, 3)
            out[level] = q
            budget -= q
        out[remaining[-1] + 1] = round(budget, 3)
        return out

    def _expected_tp_levels(self, live_qty):
        consumed = set(getattr(self, "tp_levels_consumed", []) or [])
        qty_map = self._split_remaining_tp_quantities(live_qty)
        qty_map = self._normalize_tp_qty_map(qty_map, live_qty)
        levels = []
        for level in (1, 2, 3):
            if level in consumed:
                continue
            price = self.tv_tps[level - 1]
            qty = qty_map.get(level, 0.0)
            levels.append({"level": level, "qty": qty, "price": price})
        return levels

    def _audit_tp_levels(self, live_qty, tolerance=1.0, qty_tol=0.005):
        """严格审计：每档价位唯一 + 数量符合 regime 比例 + 无孤儿单"""
        live_qty = self._resolve_live_qty(live_qty)
        orders = self._collect_tp_limit_orders()
        levels = []
        matched_full = 0
        issues = []

        for lv in self._expected_tp_levels(live_qty):
            if lv["qty"] <= 0 or lv["price"] <= 0:
                continue
            at_px = [o for o in orders if abs(o["price"] - lv["price"]) <= tolerance]
            status = "ok"
            actual_qty = 0.0
            if len(at_px) == 0:
                status = "missing"
                issues.append(f"TP{lv['level']} @{lv['price']:.2f} 缺失")
            elif len(at_px) > 1:
                status = "duplicate"
                actual_qty = sum(o["qty"] for o in at_px)
                issues.append(f"TP{lv['level']} @{lv['price']:.2f} 重复 {len(at_px)} 张")
            elif abs(at_px[0]["qty"] - lv["qty"]) > qty_tol:
                status = "qty_mismatch"
                actual_qty = at_px[0]["qty"]
                issues.append(
                    f"TP{lv['level']} 数量 {actual_qty} ≠ 期望 {lv['qty']} "
                    f"({self.regime_settings[self._tp_split_regime()]['ratios']})"
                )
            else:
                matched_full += 1
                actual_qty = at_px[0]["qty"]
            levels.append({**lv, "status": status, "actual_qty": actual_qty})

        expected_prices = [lv["price"] for lv in levels]
        orphans = [
            o for o in orders
            if not any(abs(o["price"] - p) <= tolerance for p in expected_prices)
        ]
        for o in orphans:
            issues.append(f"孤儿止盈 @{o['price']:.2f} qty={o['qty']}")

        expected = self._expected_tp_count()
        pending_prices = sorted({o["price"] for o in orders})
        return {
            "matched_full": matched_full,
            "expected": expected,
            "levels": levels,
            "issues": issues,
            "orphans": orphans,
            "pending_prices": pending_prices,
            "live_qty": live_qty,
        }

    def _format_audit_summary(self, audit):
        parts = []
        for lv in audit.get("levels", []):
            if lv["price"] <= 0:
                continue
            icon = "✅" if lv["status"] == "ok" else "❌"
            line = f"{icon}TP{lv['level']} {lv['qty']}@{lv['price']:.2f}"
            if lv["status"] != "ok":
                line += f"({lv['status']})"
            parts.append(line)
        if audit.get("issues"):
            parts.append("问题:" + "; ".join(audit["issues"][:3]))
        return " | ".join(parts) if parts else "无有效 TP"

    def _count_matched_tp_orders(self, tp_pxs, tolerance=1.0, live_qty=None):
        if live_qty is not None and live_qty > 0:
            audit = self._audit_tp_levels(live_qty, tolerance)
            return audit["matched_full"], audit["pending_prices"]
        pending_prices = self._collect_limit_tp_prices()
        matched = 0
        for tp in tp_pxs:
            if tp <= 0:
                continue
            if any(abs(p - tp) <= tolerance for p in pending_prices):
                matched += 1
        return matched, pending_prices

    def _cancel_orphan_tp_orders(self, live_qty, tolerance=1.0):
        # 无期望价时禁止清场：否则 TP 成交后账本空会把仍有效的 TP2/TP3 当孤儿撤掉
        if self._expected_tp_count() <= 0:
            if self._adopt_tp_prices_from_open_orders(self.watched_entry):
                logger.info("📐 孤儿清场前已从盘口恢复 TP 价，重新审计")
            else:
                logger.warning(
                    "⚠️ 跳过孤儿止盈撤单：暂无有效期望 TP 价，保留盘口限价"
                )
                return 0
        audit = self._audit_tp_levels(live_qty, tolerance)
        if audit.get("expected", 0) <= 0:
            logger.warning("⚠️ 跳过孤儿止盈撤单：审计期望档=0")
            return 0
        cancelled = 0
        for o in audit["orphans"]:
            if o.get("orderId"):
                binance_client.cancel_order(self.symbol, order=o)
                cancelled += 1
                time.sleep(0.2)
        if cancelled:
            logger.info(f"🧹 撤销 {cancelled} 张孤儿止盈单")
        return cancelled

    def _pick_best_tp_order(self, orders, target_qty):
        if not orders:
            return None
        return min(orders, key=lambda o: abs(o["qty"] - target_qty))

    def _surgical_repair_tp_defenses(self, live_qty, entry, tolerance=1.0, qty_tol=0.005):
        """
        重启智能修复：先读实盘 → 撤重复留最佳 → 补缺档/纠偏数量。
        不动已正确的单，避免核武撤挂把正确盘口毁掉。
        """
        live_qty = self._resolve_live_qty(live_qty)
        if live_qty <= 0:
            return self._audit_tp_levels(live_qty), 0

        close_side = "SHORT" if self.current_side == "LONG" else "LONG"
        actions = 0
        audit = self._audit_tp_levels(live_qty, tolerance, qty_tol)

        actions += self._cancel_orphan_tp_orders(live_qty, tolerance)
        if actions:
            time.sleep(0.4)
            audit = self._audit_tp_levels(live_qty, tolerance, qty_tol)

        for lv in self._expected_tp_levels(live_qty):
            price = lv["price"]
            target_q = lv["qty"]
            if price <= 0 or target_q <= 0:
                continue

            at_px = [
                o for o in self._collect_tp_limit_orders()
                if abs(o["price"] - price) <= tolerance
            ]

            if len(at_px) > 1:
                keep = self._pick_best_tp_order(at_px, target_q)
                for o in at_px:
                    if o["orderId"] == keep["orderId"]:
                        continue
                    binance_client.cancel_order(self.symbol, order=o)
                    actions += 1
                    time.sleep(0.2)
                logger.info(
                    f"🔧 重启去重 TP{lv['level']} @{price:.2f}："
                    f"撤 {len(at_px) - 1} 留 {keep['qty']} ETH"
                )
                time.sleep(0.35)
                at_px = [keep]

            if len(at_px) == 1:
                if abs(at_px[0]["qty"] - target_q) > qty_tol:
                    oid = at_px[0].get("orderId")
                    if oid:
                        binance_client.cancel_order(self.symbol, oid)
                        actions += 1
                        time.sleep(0.3)
                    res = binance_client.place_limit_order(close_side, target_q, price, symbol=self.symbol, reduce_only=True,
                    )
                    if res:
                        actions += 1
                        logger.info(
                            f"🔧 重启纠偏 TP{lv['level']} @{price:.2f} → {target_q} ETH"
                        )
                    time.sleep(0.35)
                continue

            res = binance_client.place_limit_order(close_side, target_q, price, symbol=self.symbol, reduce_only=True,
            )
            if res:
                actions += 1
                logger.info(f"🔧 重启补挂 TP{lv['level']} @{price:.2f} qty={target_q} ETH")
            time.sleep(0.35)

        final = self._audit_tp_levels(live_qty, tolerance, qty_tol)
        if actions:
            logger.info(
                f"🔧 重启智能修复完成 {actions} 步 | "
                f"{final['matched_full']}/{final['expected']} | "
                f"{self._format_audit_summary(final)}"
            )
        return final, actions

    def _cancel_stop_orders(self, scope="all"):
        """scope: all | radar | shield"""
        cancelled = 0
        for o in binance_client.get_open_orders(self.symbol):
            if o.get("type") not in ("STOP_MARKET", "STOP"):
                continue
            if scope == "radar" and not self._is_radar_stop_order(o):
                continue
            if scope == "shield" and not self._is_shield_stop_order(o):
                continue
            oid = o.get("orderId")
            if oid:
                binance_client.cancel_order(self.symbol, order=o)
                cancelled += 1
                time.sleep(0.2)
        return cancelled

    @staticmethod
    def _order_stop_price(o):
        for key in ("stopPrice", "triggerPrice", "activatePrice"):
            val = o.get(key)
            if val is None or str(val).strip() in ("", "0"):
                continue
            try:
                return round(float(val), 2)
            except (TypeError, ValueError):
                continue
        return None

    def _legacy_shield_stop_price(self, entry=None):
        """已废弃：止损价 exclusively 来自 TV tv_sl"""
        return None

    def _shield_stop_price(self, entry=None):
        """VPS 自主硬止损价（tv_sl 账本字段存 VPS 计算结果）"""
        tv = round(float(getattr(self, "tv_sl", 0) or 0), 2)
        return tv if tv > 0 else None

    def _refresh_vps_hard_sl(self, entry=None, side=None, regime=None, atr=None,
                             tv_sl_ref=None, source=""):
        """
        VPS 自主硬止损：开仓价 × 档位百分比（等比呼吸，与 ETH 价格缩放）。
        TV tv_sl 仅作参考存入 tv_sl_ref，不直接挂单。
        持仓期间不随波动重算；仅开仓/接管等 source 触发时刷新。
        """
        entry = float(entry or self.watched_entry or self.tv_price or 0)
        side = (side or self.current_side or "").strip().upper()
        regime = int(regime if regime is not None else self.regime or 3)

        if tv_sl_ref is not None:
            ref = round(self._safe_float(tv_sl_ref, 0), 2)
            if ref > 0:
                self.tv_sl_ref = ref

        if entry <= 0 or side not in ("LONG", "SHORT"):
            return False

        vps_sl = compute_vps_hard_sl(side, entry, atr, regime)
        if vps_sl <= 0:
            return False

        old = round(float(getattr(self, "tv_sl", 0) or 0), 2)
        self.tv_sl = vps_sl
        if abs(vps_sl - old) > SHIELD_STOP_TOLERANCE:
            self._last_applied_exchange_sl = 0.0
        self._save_state()

        params = get_vps_hard_sl_params(regime)
        dist = compute_vps_hard_sl_distance(entry, regime)
        ref_txt = (
            f" | {format_tv_vps_sl_compare(side, entry, atr, regime, tv_sl_ref=self.tv_sl_ref)}"
            if getattr(self, "tv_sl_ref", 0) > 0 else ""
        )
        logger.info(
            f"🛡️ VPS硬止损 R{regime} 开仓×{params['pct_label']} | "
            f"呼吸 {dist:.2f}U ({params['pct_label']}) → {vps_sl:.2f}"
            + (f" ({source})" if source else "")
            + ref_txt
            + (f" | 原 {old:.2f}" if old > 0 and abs(vps_sl - old) > SHIELD_STOP_TOLERANCE else "")
        )
        return True

    def _apply_tv_sl_from_payload(self, payload, source=""):
        """TV tv_sl 仅参考；挂单价由 VPS 按 开仓价×档位% 重算"""
        tv_ref = payload.get("tv_sl")
        if tv_ref is None or tv_ref == "":
            return self._refresh_vps_hard_sl(source=source or "信号")
        ref_px = round(self._safe_float(tv_ref, 0), 2)
        if ref_px <= 0:
            return False
        entry = float(self.tv_price or self.watched_entry or 0)
        side = str(payload.get("action") or payload.get("side") or self.current_side or "").upper()
        if side not in ("LONG", "SHORT"):
            side = self.current_side
        return self._refresh_vps_hard_sl(
            entry=entry, side=side,
            regime=self.regime, atr=self.current_atr,
            tv_sl_ref=ref_px, source=source or "TV参考",
        )

    def _effective_exchange_stop(self, radar_sl=None):
        """合并止损：LONG 取 max(雷达, tv_sl)；SHORT 取 min"""
        floor = self._shield_stop_price()
        radar = round(float(radar_sl), 2) if radar_sl and float(radar_sl) > 0 else None
        if not floor and not radar:
            return None
        if not floor:
            return radar
        if not radar:
            return floor
        if self.current_side == "LONG":
            return max(radar, floor)
        if self.current_side == "SHORT":
            return min(radar, floor)
        return floor

    def _clamp_radar_to_tv_floor(self, radar_sl):
        """雷达保本线不得低于 TV 硬止损底线"""
        if not radar_sl:
            return radar_sl
        effective = self._effective_exchange_stop(radar_sl)
        return effective if effective else radar_sl

    def _purge_all_close_position_stops(self):
        """撤净所有 closePosition 止损（TV硬止损与雷达共用单槽）"""
        cancelled = 0
        for o in binance_client.get_open_orders(self.symbol):
            order_type = str(o.get("type") or o.get("orderType") or "").upper()
            if order_type not in ("STOP", "STOP_MARKET"):
                continue
            if not binance_client._truthy_close_position(o.get("closePosition")):
                continue
            oid = o.get("orderId") or o.get("algoId")
            if oid:
                if o.get("algoId") is not None:
                    binance_client.cancel_algo_order(self.symbol, oid)
                else:
                    binance_client.cancel_order(self.symbol, oid)
                cancelled += 1
                time.sleep(0.12)
        return cancelled

    def _purge_all_protective_stops(self, keep_near=None, tolerance=None):
        """
        撤净全部保护性 STOP / STOP_MARKET（含 Stop-Limit reduceOnly + Algo closePosition）。
        keep_near: 若给出目标价，保留触发价贴近该价的单仓位；其余一律撤（统一硬止损）。
        """
        keep_near = float(keep_near or 0)
        tol = float(tolerance if tolerance is not None else SHIELD_STOP_TOLERANCE)
        cancelled = 0
        for o in binance_client.get_open_orders(self.symbol, include_algo=True):
            order_type = str(o.get("type") or o.get("orderType") or "").upper()
            if order_type not in ("STOP", "STOP_MARKET"):
                continue
            px = self._order_stop_price(o)
            if keep_near > 0 and px is not None and abs(px - keep_near) <= tol:
                continue
            oid = o.get("orderId") or o.get("algoId")
            if not oid:
                continue
            binance_client.cancel_order(self.symbol, order=o)
            cancelled += 1
            time.sleep(0.12)
        return cancelled

    def _count_protective_stops(self):
        return binance_client.find_protective_stop_prices(self.symbol)

    def _place_vps_hard_sl_order(self, live_qty, trigger_px, use_stop_limit=True):
        """VPS 缓冲硬止损：默认 Stop-Limit（防跳空）；雷达合并档用 Stop-Market closePosition"""
        live_qty = self._resolve_live_qty(live_qty)
        trigger_px = round(float(trigger_px or 0), 2)
        if live_qty <= 0 or trigger_px <= 0 or not self.current_side:
            return None
        close_side = "SHORT" if self.current_side == "LONG" else "LONG"
        if use_stop_limit:
            limit_px = compute_vps_hard_sl_limit_price(self.current_side, trigger_px)
            return binance_client.place_stop_limit_order(close_side, live_qty, trigger_px, symbol=self.symbol, limit_price=limit_px,
            )
        return binance_client.place_stop_market_order(close_side, trigger_px, symbol=self.symbol, quantity=None)

    def _sync_exchange_stop(self, live_qty, radar_sl=None, reason="", force=False):
        """
        统一交易所保护止损为单槽：先撤残余 STOP，再按 effective 挂 1 笔。
        """
        live_qty = self._resolve_live_qty(live_qty)
        if live_qty <= 0 or not self.current_side or not self.watched_entry:
            return {"ok": False, "skipped": True, "reason": "no_position"}

        target = self._effective_exchange_stop(radar_sl)
        if not target or target <= 0:
            return {"ok": False, "skipped": True, "reason": "no_stop_price"}
        target = round(float(target), 2)

        live_stops = self._count_protective_stops()
        near = [p for p in live_stops if abs(p - target) <= SHIELD_STOP_TOLERANCE]
        orphans = [p for p in live_stops if abs(p - target) > SHIELD_STOP_TOLERANCE]

        last = round(float(getattr(self, "_last_applied_exchange_sl", 0) or 0), 2)
        # 已统一为唯一目标价 → 幂等跳过（含 force，避免重启对账反复重挂）
        if not orphans and len(near) == 1:
            self._last_applied_exchange_sl = target
            self.shield_active = True
            self.shield_sized_qty = live_qty
            self._tv_sl_missing_alerted = False
            if abs(last - target) > SHIELD_STOP_TOLERANCE:
                self._save_state()
            return {
                "ok": True, "skipped": True, "target": target,
                "reason": "idempotent_unified",
            }

        # 有孤儿价（如重启残留 1697 + 新 1747）或缺失 → 先清净再挂唯一
        purged = self._purge_all_protective_stops(keep_near=0)
        if purged:
            logger.warning(
                f"🛡️ 统一硬止损：撤净保护STOP {purged} 笔 "
                f"(原盘口{live_stops} → 目标 @{target:.2f})"
                + (f" | 孤儿{orphans}" if orphans else "")
            )
            time.sleep(0.5)

        floor = round(float(self._shield_stop_price() or 0), 2)
        use_limit = (
            floor > 0
            and abs(target - floor) <= SHIELD_STOP_TOLERANCE
        )
        res = self._place_vps_hard_sl_order(
            live_qty, target, use_stop_limit=use_limit,
        )
        time.sleep(0.45)
        ok = res is not None and self._has_stop_sl_near(target, exclude_shield=False)
        # 二次清：若仍有非目标价 STOP，再扫一遍孤儿
        leftovers = [
            p for p in self._count_protective_stops()
            if abs(p - target) > SHIELD_STOP_TOLERANCE
        ]
        if leftovers:
            extra = self._purge_all_protective_stops(keep_near=target)
            purged += extra
            logger.warning(f"🛡️ 二次清孤儿STOP{leftovers} 撤 {extra} 笔")

        if ok:
            self._last_applied_exchange_sl = target
            self.shield_active = True
            self.shield_sized_qty = live_qty
            self._shield_fail_streak = 0
            self._tv_sl_missing_alerted = False
            self._save_state()
            tv_floor = round(float(getattr(self, "tv_sl", 0) or 0), 2)
            order_tag = "Stop-Limit" if use_limit else "Stop-Market closePosition"
            logger.warning(
                f"🛡️ [VPS硬止损/统一] {reason or '同步止损'} | {order_tag} @ {target:.2f} "
                f"| vps_sl={tv_floor or 'fallback'} | 撤 {purged} 笔"
            )
        else:
            self._record_shield_maintain(success=False)
        return {"ok": ok, "skipped": False, "target": target, "purged": purged}

    def _handle_tv_sl_update(self, payload):
        """UPDATE_SL：仅记录 TV 紧止损参考，不挂撤单（VPS 自主硬止损 + 雷达）"""
        ref = round(self._safe_float(payload.get("tv_sl"), 0), 2)
        if ref > 0:
            self.tv_sl_ref = ref
            self._save_state()
        vps_sl = round(float(getattr(self, "tv_sl", 0) or 0), 2)
        logger.info(
            f"UPDATE_SL 已忽略盘口动作 | TV参考 tv_sl={ref or 'N/A'} "
            f"| VPS硬止损 `{vps_sl:.2f}` 由 regime+atr 自主管理"
        )

    def _place_tp_levels_only(self, live_qty, retries=2):
        """只挂未成交 TP 限价档，绝不触碰止损/雷达"""
        close_side = "SHORT" if self.current_side == "LONG" else "LONG"
        live_qty = self._resolve_live_qty(live_qty)
        if live_qty <= 0:
            return 0
        placed = 0
        for lv in self._expected_tp_levels(live_qty):
            q, px = float(lv["qty"] or 0), float(lv["price"] or 0)
            if q <= 0 or px <= 0:
                continue
            ok = False
            for attempt in range(max(1, retries + 1)):
                res = binance_client.place_limit_order(close_side, q, px, symbol=self.symbol, reduce_only=True,
                )
                if res:
                    ok = True
                    break
                time.sleep(0.2)
            if ok:
                placed += 1
                logger.info(f"📈 UPDATE_TP 挂 TP{lv['level']} {q} @ {px:.2f}")
            else:
                logger.error(f"❌ UPDATE_TP 挂 TP{lv['level']} @ {px:.2f} 失败")
            time.sleep(0.25)
        return placed

    def _handle_tv_tp_update(self, payload):
        """
        UPDATE_TP（v6.9.108 动能止盈升级）：
        只撤换限价 TP123，绝不触碰硬止损 / 雷达 STOP。
        """
        side = str(payload.get("side") or "").strip().upper()
        new_tps = self._sanitize_tp_prices([
            self._safe_float(payload.get("tv_tp1"), 0),
            self._safe_float(payload.get("tv_tp2"), 0),
            self._safe_float(payload.get("tv_tp3"), 0),
        ])
        if sum(1 for t in new_tps if t > 0) < 3:
            logger.warning(f"UPDATE_TP 无效：TP 不全 {new_tps}")
            return

        pos = self._get_active_position()
        if not pos or float(pos.get("size", 0) or 0) <= 0:
            logger.info("UPDATE_TP 到达但盘口已空仓 → 忽略")
            return

        pos_side = pos["side"]
        if side and side != pos_side:
            logger.warning(f"UPDATE_TP side={side} 与实盘 {pos_side} 不符，已忽略")
            return

        live_qty = self._resolve_live_qty(pos["size"])
        entry = float(pos.get("entry_price") or self.watched_entry or 0)
        curr_px = float(
            binance_client.get_current_price(self.symbol)
            or self.tv_price
            or 0
        )
        self.current_side = pos_side
        if not self.monitoring:
            self.monitoring = True
            self.watched_qty = live_qty
            self.watched_entry = entry

        if not validate_tp_prices_for_side(pos_side, entry, new_tps):
            logger.warning(
                f"UPDATE_TP 方向校验失败 {pos_side} entry={entry:.2f} tps={new_tps} → 忽略"
            )
            dingtalk.report_system_alert(
                "UPDATE_TP 已拒绝",
                f"{pos_side} entry `{entry:.2f}` | 新TP {new_tps} 与持仓方向不符",
            )
            return

        tp1 = float(new_tps[0])
        if curr_px > 0:
            if pos_side == "LONG" and tp1 <= curr_px:
                logger.warning(
                    f"UPDATE_TP 新TP1={tp1:.2f} ≤ 市价 {curr_px:.2f} → 忽略（防即时成交）"
                )
                return
            if pos_side == "SHORT" and tp1 >= curr_px:
                logger.warning(
                    f"UPDATE_TP 新TP1={tp1:.2f} ≥ 市价 {curr_px:.2f} → 忽略（防即时成交）"
                )
                return

        old_tps = list(getattr(self, "_prev_tv_tps_before_update", None) or self.tv_tps or [])
        self.tv_tps = new_tps
        self._save_state()

        # 幂等：账本同价且盘口已对齐 → 跳过撤挂
        same_ledger = (
            len(old_tps) >= 3
            and all(
                abs(float(old_tps[i] or 0) - float(new_tps[i] or 0)) <= 0.51
                for i in range(3)
            )
        )
        audit_before = self._audit_tp_levels(live_qty)
        if same_ledger and self._tp_audit_ok(audit_before):
            logger.info(f"UPDATE_TP 幂等跳过：TP 已是 {new_tps}")
            return

        cancelled = self._cancel_all_tp_limit_orders(max_rounds=4)
        time.sleep(0.45)
        leftover = self._collect_tp_limit_orders()
        if leftover:
            logger.error(
                f"UPDATE_TP 撤旧 TP 未净（剩 {len(leftover)}）→ 放弃挂新单，等待下次"
            )
            dingtalk.report_system_alert(
                "UPDATE_TP 撤单失败",
                f"旧限价 TP 未清净 {len(leftover)} 张，未挂新价，硬止损/雷达未动",
            )
            # 回滚账本价，避免审计用新价误杀盘口残留
            if old_tps and sum(1 for t in old_tps if float(t or 0) > 0) >= 2:
                self.tv_tps = self._sanitize_tp_prices(old_tps)
                self._save_state()
            return

        placed = self._place_tp_levels_only(live_qty, retries=2)
        time.sleep(0.5)
        audit = self._audit_tp_levels(live_qty)
        verified = self._tp_audit_ok(audit)
        if not verified and placed > 0:
            time.sleep(0.35)
            placed += self._place_tp_levels_only(live_qty, retries=1)
            time.sleep(0.4)
            audit = self._audit_tp_levels(live_qty)
            verified = self._tp_audit_ok(audit)

        verify_note = (
            f"动能UPDATE_TP | {old_tps} → {new_tps} | "
            f"撤旧 {cancelled} | 新挂 {placed} | "
            f"止盈 {audit.get('matched_full', 0)}/{audit.get('expected', 0)} | "
            f"持仓 {live_qty} ETH @ {entry:.2f} | "
            f"市价 {curr_px:.2f} | 硬止损/雷达未触碰"
        )
        logger.info(
            f"🚀 UPDATE_TP 完成 verified={verified} | {verify_note}"
        )
        self._call_dingtalk(
            dingtalk.report_tv_tp_updated,
            side=pos_side,
            live_qty=live_qty,
            entry=entry,
            old_tps=old_tps,
            new_tps=new_tps,
            placed=placed,
            regime=self.regime,
            verify_note=verify_note,
            verified=verified,
            curr_px=curr_px,
        )
        if not verified:
            dingtalk.report_system_alert(
                "UPDATE_TP 对齐未完成",
                f"{self._format_audit_summary(audit)} | 哨兵将继续核对",
            )

    def _shield_tier_prices(self, entry=None):
        px = self._shield_stop_price(entry)
        return [px] if px else []

    def _is_shield_stop_order(self, o, tier_prices=None):
        px = self._order_stop_price(o)
        if px is None:
            return False
        tier_prices = tier_prices or self._shield_tier_prices()
        if not any(abs(px - tp) <= SHIELD_STOP_TOLERANCE for tp in tier_prices if tp):
            return False
        order_type = str(o.get("type") or o.get("orderType") or "").upper()
        if binance_client._truthy_close_position(o.get("closePosition")):
            return True
        if order_type in ("STOP", "STOP_MARKET"):
            return True
        return False

    def _is_radar_stop_order(self, o):
        if o.get("type") not in ("STOP", "STOP_MARKET"):
            return False
        px = self._order_stop_price(o)
        if px is None:
            return False
        if not self._is_radar_active():
            return False
        if abs(px - round(float(self.current_sl), 2)) <= SHIELD_STOP_TOLERANCE:
            return True
        return False

    def _adverse_move_pct(self, curr_px):
        entry = self.watched_entry
        if not entry or curr_px <= 0:
            return 0.0
        if self.current_side == "LONG":
            return max(0.0, (entry - curr_px) / entry)
        if self.current_side == "SHORT":
            return max(0.0, (curr_px - entry) / entry)
        return 0.0

    def _favorable_move_pct(self, curr_px):
        entry = self.watched_entry
        if not entry or curr_px <= 0:
            return 0.0
        if self.current_side == "LONG":
            return max(0.0, (curr_px - entry) / entry)
        if self.current_side == "SHORT":
            return max(0.0, (entry - curr_px) / entry)
        return 0.0

    def _resolve_defense_regime(self, curr_px):
        """FAVORABLE=雷达已/应激活 | SHIELD=维护 TV tv_sl 硬止损"""
        if curr_px <= 0 or not self.watched_entry:
            return "SHIELD"
        if self._is_radar_active() or self._should_radar_trail(curr_px):
            return "FAVORABLE"
        return "SHIELD"

    def _shield_present_on_exchange(self):
        stop_px = self._shield_stop_price()
        if stop_px and self._has_shield_stop_at_price(stop_px):
            return True
        audit = self._audit_shield_orders(self._resolve_live_qty(self.watched_qty or 0))
        return audit.get("status") in ("ok", "duplicate", "qty_mismatch")

    def _wait_shield_cleared(self, entry=None, retries=8, delay=0.4):
        live_qty = self._resolve_live_qty(self.watched_qty or 0)

        def _probe():
            if self._shield_present_on_exchange():
                return None
            return True

        return bool(self._wait_verify(_probe, retries=retries, delay=delay))

    def _radar_min_stop_gap(self, curr_px=0.0):
        """交易所 STOP 与现价的最小安全距离，防「刚挂就触发」"""
        px = float(curr_px or 0)
        if px <= 0:
            try:
                px = float(binance_client.get_current_price(self.symbol) or 0)
            except Exception:
                px = 0.0
        if px <= 0:
            return RADAR_STOP_MIN_GAP_USD
        return max(RADAR_STOP_MIN_GAP_USD, px * RADAR_STOP_MIN_GAP_PCT)

    def _clamp_radar_sl_for_market(self, curr_px, sl):
        """LONG: SL 必须低于 mark-gap；SHORT: 高于 mark+gap；再与 tv_sl 合并"""
        if not sl or curr_px <= 0:
            return sl
        gap = self._radar_min_stop_gap(curr_px)
        sl = round(float(sl), 2)
        if self.current_side == "LONG":
            safe_cap = round(curr_px - gap, 2)
            if sl >= safe_cap:
                sl = safe_cap
            merged = self._clamp_radar_to_tv_floor(sl)
            if merged and merged > safe_cap:
                sl = safe_cap
            else:
                sl = merged or sl
        elif self.current_side == "SHORT":
            safe_cap = round(curr_px + gap, 2)
            if sl <= safe_cap:
                sl = safe_cap
            merged = self._clamp_radar_to_tv_floor(sl)
            if merged and merged < safe_cap:
                sl = safe_cap
            else:
                sl = merged or sl
        return sl

    def _can_safely_place_radar_sl(self, curr_px, sl):
        """False = 止损价贴市价，交易所会立即触发 closePosition 全平"""
        if curr_px <= 0 or not sl:
            return False
        gap = self._radar_min_stop_gap(curr_px)
        sl = float(sl)
        if self.current_side == "LONG":
            return sl <= curr_px - gap
        if self.current_side == "SHORT":
            return sl >= curr_px + gap
        return False

    def _notify_shield_handoff_to_radar(self, real_amt, curr_px, new_sl, reason="",
                                        sl_verified=False, cancelled_hint=0):
        """保本止损已核实后推送交棒钉钉（禁止先撤硬止损再裸奔）"""
        if getattr(self, "_shield_handoff_notified", False):
            return
        real_amt = float(self._resolve_live_qty(real_amt) or 0)
        if real_amt <= 0:
            return
        stop_px = self._shield_stop_price()
        progress = self._radar_activation_progress(curr_px) if curr_px > 0 else 1.0
        verify_note = (
            f"先挂雷达保本 @ {new_sl:.2f} 已核实"
            + (f" | 替换 TV硬止损 @ {stop_px:.2f}" if stop_px else "")
            + f" | 持仓 {real_amt} {self._unit()}"
        )
        if not sl_verified:
            verify_note += f" | {dingtalk.VERIFY_DELAY_MARK}"
        self._call_dingtalk(
            dingtalk.report_shield_disarmed,
            side=self.current_side,
            live_qty=real_amt,
            entry=self.watched_entry,
            cancelled_count=max(cancelled_hint, 1),
            reason=reason or "雷达交棒 · 先挂保本再撤 tv_sl",
            radar_progress=progress,
            verify_note=verify_note,
            verified=sl_verified,
        )
        self._shield_handoff_notified = True
        self._save_state()

    def _qty_noise_floor(self, baseline=0.0):
        """品种感知噪声下限：ETH/XAU 用各自 qty_step，禁止硬编码 0.003 误伤小仓。"""
        step = float(getattr(self, "qty_step", 0) or 0.001)
        dust = float(getattr(self, "dust_qty", 0) or step)
        base = float(baseline or 0)
        return max(step * 2, dust, base * TP_FILL_NOISE_VS_OPEN_PCT)

    def _unit(self):
        return str(getattr(self, "unit_label", None) or "ETH")

    def _perform_radar_handoff(self, real_amt, curr_px, reason=""):
        """
        原子雷达交棒：三重验证通过后，挂「理想保本线」并核实 → 再钉钉。
        禁止把止损夹到现价旁（毛刺易打掉）；空间不足则延迟，保留宽硬止损。
        """
        real_amt = float(self._resolve_live_qty(real_amt) or 0)
        if real_amt <= 0:
            return False
        if getattr(self, "_open_in_progress", False) or getattr(
            self, "_defense_align_in_progress", False
        ):
            logger.info(
                f"📡 [{self.symbol}] 雷达交棒拒绝：开仓/防线重建中 | {reason or ''}"
            )
            return False
        # 交棒前必须实时三重验证（禁止仅凭旧 latch）
        if not self._tp1_triad_ok(real_amt, curr_px, require_fresh=True):
            logger.info(
                f"📡 [{self.symbol}] 雷达交棒拒绝：TP1 三重验证未通过 | {reason or ''}"
            )
            return False

        new_sl = self._compute_radar_sl_for_stage(1, curr_px)
        if new_sl is None:
            new_sl = self._compute_radar_sl()
        if new_sl is None:
            return False

        # 理想保本线必须已在安全侧；禁止 clamp 成贴市毛刺止损
        if not self._ideal_radar_sl_is_safe(curr_px, new_sl):
            gap = self._radar_handoff_min_gap(curr_px)
            logger.info(
                f"📡 [{self.symbol}] 雷达交棒延迟：理想保本 {new_sl:.2f} 距现价 "
                f"{float(curr_px or 0):.2f} 不足 {gap:.2f}U "
                f"（{self._unit()}）→ 保留宽硬止损呼吸空间 | {reason or ''}"
            )
            return False

        if self.current_side == "LONG":
            if new_sl > float(self.current_sl or 0):
                self.current_sl = new_sl
        else:
            if (
                new_sl < float(self.current_sl or 999999)
                or float(self.current_sl or 0) >= float(self.watched_entry or 0)
            ):
                self.current_sl = new_sl

        safe_sl = round(float(self.current_sl or new_sl), 2)
        if not self._can_safely_place_radar_sl(curr_px, safe_sl):
            logger.info(
                f"📡 [{self.symbol}] 雷达交棒延迟：保本 {safe_sl:.2f} 仍不安全"
            )
            return False

        had_tv_shield = (
            getattr(self, "shield_active", False)
            or self._shield_present_on_exchange()
        )
        old_tv = self._shield_stop_price()
        self.current_sl = safe_sl
        self._save_state()

        sl_placed = self._ensure_radar_sl(safe_sl, real_amt)
        sl_verified = sl_placed and self._wait_verify(
            lambda: self._has_stop_sl_near(safe_sl, exclude_shield=False),
            retries=10,
            delay=0.45,
        )
        if not sl_verified:
            logger.warning(
                f"📡 [{self.symbol}] 雷达交棒中止：保本 @ {safe_sl:.2f} 未核实，"
                f"不撤宽硬止损"
            )
            if had_tv_shield and old_tv:
                self._maintain_hard_shield(real_amt, curr_px, force=True, radar_sl=None)
            return False

        # 仅核实成功后锁存
        self._radar_armed_after_tp1 = True
        self._radar_handoff_done = True
        self._radar_stage_last = max(int(getattr(self, "_radar_stage_last", 0) or 0), 1)
        self._save_state()

        logger.info(
            f"📡 [{self.symbol}] 雷达交棒成功：保本 @ {safe_sl:.2f} | "
            f"best={self.best_price:.2f} | 现价 {float(curr_px or 0):.2f} | "
            f"{self._unit()} {real_amt}"
        )
        if had_tv_shield and not getattr(self, "_shield_handoff_notified", False):
            self._notify_shield_handoff_to_radar(
                real_amt, curr_px, safe_sl,
                reason=reason or "TP1三重验证 · 雷达交棒",
                sl_verified=True,
                cancelled_hint=1 if old_tv else 0,
            )
        if not getattr(self, "_radar_activation_notified", False):
            self._report_radar_first_activation(
                real_amt, curr_px, safe_sl, sl_placed,
            )
        stage = self._radar_stage(curr_px)
        self._log_radar_update(
            stage, old_tv or float(getattr(self, "tv_sl", 0) or 0),
            safe_sl, reason or "雷达交棒", curr_px,
        )
        self._cancel_stale_tp_beyond_radar(safe_sl, real_amt)
        return True

    def _radar_handoff_min_gap(self, curr_px=0.0):
        px = float(curr_px or 0)
        base = self._radar_min_stop_gap(px)
        if px <= 0:
            return base
        return max(base, px * RADAR_HANDOFF_EXTRA_GAP_PCT)

    def _ideal_radar_sl_is_safe(self, curr_px, sl):
        """
        理想保本线必须已在安全侧（有利润缓冲）。
        若需把止损往现价方向夹才能挂上 → 视为不安全，延迟交棒。
        """
        curr_px = float(curr_px or 0)
        sl = float(sl or 0)
        entry = float(self.watched_entry or 0)
        if curr_px <= 0 or sl <= 0 or entry <= 0:
            return False
        gap = self._radar_handoff_min_gap(curr_px)
        if self.current_side == "LONG":
            # 多：保本须高于成本，且低于现价-gap
            if sl <= entry:
                return False
            return sl <= curr_px - gap
        if self.current_side == "SHORT":
            # 空：保本须低于成本，且高于现价+gap
            if sl >= entry:
                return False
            return sl >= curr_px + gap
        return False

    def _force_disarm_shield_before_radar(self, curr_px, reason="", notify=True):
        """兼容旧调用 → 统一走原子交棒（先挂保本，禁止先撤硬止损）"""
        real_amt = self._resolve_live_qty(self.watched_qty or 0)
        if real_amt <= 0:
            return {"cancelled": 0, "cleared": True, "verified": True}
        ok = self._perform_radar_handoff(
            real_amt, curr_px, reason=reason or "雷达接管",
        )
        return {"cancelled": 1 if ok else 0, "cleared": ok, "verified": ok}

    def _should_disarm_shield_for_favorable(self, curr_px):
        """仅 TP1 成交后 → 撤宽硬止损交棒雷达保本"""
        if not self._radar_legitimately_armed(self.watched_qty, curr_px):
            return False
        stop_px = self._shield_stop_price()
        has_shield = bool(
            getattr(self, "shield_active", False)
            or (stop_px and self._has_shield_stop_at_price(stop_px))
        )
        if not has_shield:
            return False
        return self._is_radar_active() or self._should_radar_trail(curr_px)

    def _shield_needs_exchange_action(self, live_qty, audit):
        """是否值得动 API：叠单/缺档/仓位离谱变化才动，微漂不动"""
        status = audit.get("status")
        if status == "duplicate":
            return True
        if status == "missing":
            return True
        if status == "qty_mismatch":
            sized = float(getattr(self, "shield_sized_qty", 0) or 0)
            if sized > 0 and self._qty_change_ratio(sized, live_qty) < QTY_ALIGN_MIN_PCT:
                return False
            return audit.get("max_drift_pct", 1.0) > SHIELD_QTY_TOLERANCE_PCT
        return False

    def _process_directional_defenses(self, real_amt, curr_px):
        """
        双层风控：雷达移动保本（VPS）+ TV tv_sl 硬止损底线（合并为单 closePosition）。
        雷达线不得低于 tv_sl；UPDATE_SL 只更新底线，雷达逻辑独立运行。
        """
        self._disarm_premature_radar(real_amt, curr_px, source="哨兵防线")
        radar_sl = None
        if self._resolve_defense_regime(curr_px) == "FAVORABLE":
            if self._should_radar_trail(curr_px) or self._is_radar_active():
                self._process_radar_trailing(real_amt, curr_px)
                if self.current_sl and (
                    self._is_radar_active() or self._should_radar_trail(curr_px)
                ):
                    radar_sl = self._clamp_radar_to_tv_floor(self.current_sl)
        self._maintain_hard_shield(real_amt, curr_px, radar_sl=radar_sl)

    def _should_activate_shield(self, curr_px):
        """始终维护 TV 硬止损底线（可与雷达合并挂单）"""
        if not self.watched_entry or not self.current_side:
            return False
        return True

    def _remaining_shield_tier_indices(self):
        consumed = set(getattr(self, "shield_tiers_consumed", []) or [])
        return [i for i, pct in enumerate(SHIELD_TIER_PCTS) if pct not in consumed]

    def _shield_quantities_for_remaining(self, live_qty):
        remaining = self._remaining_shield_tier_indices()
        live_qty = self._resolve_live_qty(live_qty)
        if not remaining or live_qty <= 0:
            return {}
        if len(remaining) == 1:
            return {remaining[0]: live_qty}
        weights = [SHIELD_TIER_RATIOS[i] for i in remaining]
        wsum = sum(weights) or 1.0
        out = {}
        budget = live_qty
        for j, idx in enumerate(remaining[:-1]):
            q = round(live_qty * weights[j] / wsum, 3)
            out[idx] = q
            budget -= q
        out[remaining[-1]] = round(budget, 3)
        return out

    def _has_shield_stop_at_price(self, tp, tier_prices=None):
        tier_prices = tier_prices or self._shield_tier_prices()
        for o in binance_client.get_open_orders(self.symbol):
            if not self._is_shield_stop_order(o, tier_prices):
                continue
            px = self._order_stop_price(o)
            if px is not None and abs(px - tp) <= SHIELD_STOP_TOLERANCE:
                return True
        return False

    def _shield_orders_at_tiers(self, tier_prices):
        """统计各档位价位上的硬止损单（含 closePosition 全平）"""
        live_qty = self._resolve_live_qty(self.watched_qty or 0)
        buckets = {i: [] for i in range(len(tier_prices))}
        for o in binance_client.get_open_orders(self.symbol):
            order_type = str(o.get("type") or o.get("orderType") or "").upper()
            if order_type not in ("STOP", "STOP_MARKET"):
                continue
            px = self._order_stop_price(o)
            if px is None:
                continue
            if not self._is_shield_stop_order(o, tier_prices):
                continue
            if binance_client._truthy_close_position(o.get("closePosition")):
                oqty = live_qty
            else:
                oqty = round(float(o.get("origQty", o.get("quantity", 0)) or 0), 3)
            for i, tp in enumerate(tier_prices):
                if tp and abs(px - tp) <= SHIELD_STOP_TOLERANCE:
                    buckets[i].append({"order": o, "qty": oqty})
                    break
        return buckets

    def _purge_shield_stop_orders(self, tier_prices=None):
        """撤净防护盾档位上的全部止损（含 closePosition 全平）"""
        tier_prices = tier_prices or self._shield_tier_prices()
        if not tier_prices:
            return 0
        cancelled = 0
        for o in binance_client.get_open_orders(self.symbol):
            if not self._is_shield_stop_order(o, tier_prices):
                continue
            oid = o.get("orderId")
            if oid:
                binance_client.cancel_order(self.symbol, order=o)
                cancelled += 1
                time.sleep(0.15)
        return cancelled

    def _split_shield_quantities(self, qty):
        return (round(qty * SHIELD_TIER_RATIOS[0], 3),)

    def _can_maintain_shield_now(self, force=False, audit=None):
        """限频：重启宽限期 + 维护冷却 + 失败指数退避；缺硬止损时宽限期内仍允许补挂"""
        if force:
            return True
        now = time.time()
        audit = audit or {}
        missing_shield = audit.get("status") == "missing"
        if now < getattr(self, "_sentinel_grace_until", 0):
            if missing_shield:
                if now - getattr(self, "_last_shield_maintain_ts", 0) < 12:
                    return False
                return True
            return False
        if now - getattr(self, "_last_shield_maintain_ts", 0) < SHIELD_MAINTAIN_COOLDOWN_SEC:
            if missing_shield and now - getattr(self, "_last_shield_maintain_ts", 0) >= 12:
                return True
            return False
        streak = getattr(self, "_shield_fail_streak", 0)
        if streak > 0:
            backoff = min(
                SHIELD_FAIL_BACKOFF_BASE_SEC * (2 ** (streak - 1)),
                SHIELD_FAIL_BACKOFF_MAX_SEC,
            )
            if now - getattr(self, "_last_shield_fail_ts", 0) < backoff:
                if missing_shield and now - getattr(self, "_last_shield_fail_ts", 0) >= 12:
                    return True
                return False
        return True

    def _wait_shield_audit_ok(self, live_qty, entry=None, retries=10, delay=0.45):
        """挂单后 REST 延迟：轮询直到硬止损核实通过"""
        entry = float(entry or self.watched_entry or 0)
        live_qty = self._resolve_live_qty(live_qty)

        def _probe():
            audit = self._audit_shield_orders(live_qty, entry)
            return audit if self._shield_orders_adequate(audit) else None

        verified = self._wait_verify(_probe, retries=retries, delay=delay)
        return verified or self._audit_shield_orders(live_qty, entry)

    def _record_shield_maintain(self, success):
        self._last_shield_maintain_ts = time.time()
        if success:
            self._shield_fail_streak = 0
        else:
            self._shield_fail_streak = getattr(self, "_shield_fail_streak", 0) + 1
            self._last_shield_fail_ts = time.time()

    def _audit_shield_orders(self, live_qty, entry=None):
        """先查盘口再决策：ok / duplicate / missing / qty_mismatch"""
        tier_prices = self._shield_tier_prices(entry)
        live_qty = self._resolve_live_qty(live_qty)
        remaining = self._remaining_shield_tier_indices()
        result = {
            "status": "none",
            "live_qty": live_qty,
            "remaining": remaining,
            "tier_prices": tier_prices,
            "buckets": {},
            "qty_map": {},
            "max_drift_pct": 0.0,
            "issues": [],
        }
        if not remaining:
            result["status"] = "ok" if live_qty <= 0 else "none"
            return result
        if live_qty <= 0:
            result["status"] = "missing"
            result["issues"].append("no_position")
            return result

        qty_map = self._shield_quantities_for_remaining(live_qty)
        result["qty_map"] = qty_map
        buckets = self._shield_orders_at_tiers(tier_prices)
        result["buckets"] = buckets

        has_duplicate = False
        has_missing = False
        has_qty_mismatch = False
        max_drift_pct = 0.0

        for idx in remaining:
            q = qty_map.get(idx, 0)
            if q <= 0:
                continue
            orders = buckets.get(idx, [])
            if not orders:
                has_missing = True
                result["issues"].append(f"tier{idx + 1}_missing")
            elif len(orders) > SHIELD_MAX_TIER_ORDERS:
                has_duplicate = True
                result["issues"].append(f"tier{idx + 1}_dup:{len(orders)}")
            else:
                drift = abs(orders[0]["qty"] - q) / q if q > 0 else 1.0
                max_drift_pct = max(max_drift_pct, drift)
                if drift > SHIELD_QTY_TOLERANCE_PCT:
                    has_qty_mismatch = True
                    result["issues"].append(
                        f"tier{idx + 1}_qty:{orders[0]['qty']}vs{q}"
                    )

        for idx, orders in buckets.items():
            if idx not in remaining and orders:
                has_duplicate = True
                result["issues"].append(f"tier{idx + 1}_orphan:{len(orders)}")

        # 盘口任意非目标价 STOP（如重启残留旧档位%）→ 视为叠单，触发统一
        target_px = float(tier_prices[0] or 0) if tier_prices else 0.0
        live_stops = binance_client.find_protective_stop_prices(self.symbol)
        orphan_px = [
            p for p in live_stops
            if target_px <= 0 or abs(float(p) - target_px) > SHIELD_STOP_TOLERANCE
        ]
        if len(live_stops) > 1 or orphan_px:
            has_duplicate = True
            result["issues"].append(f"orphan_stops:{orphan_px or live_stops}")

        result["max_drift_pct"] = max_drift_pct
        if has_duplicate:
            result["status"] = "duplicate"
        elif has_missing:
            result["status"] = "missing"
        elif has_qty_mismatch:
            result["status"] = "qty_mismatch"
        else:
            result["status"] = "ok"
        return result

    def _shield_orders_adequate(self, audit):
        if audit["status"] == "ok":
            return True
        if audit["status"] == "qty_mismatch":
            return audit.get("max_drift_pct", 1.0) <= SHIELD_QTY_TOLERANCE_PCT
        return False

    def _shield_orders_ok(self, live_qty, entry=None):
        return self._shield_orders_adequate(self._audit_shield_orders(live_qty, entry))

    @staticmethod
    def _recover_lock_pid_alive(info):
        """锁文件中的 pid 仍存活才视为有效占用（避免重部署后误跳过钉钉接管）"""
        if not info:
            return False
        for part in info.replace("\n", " ").split():
            if part.startswith("pid="):
                try:
                    pid = int(part.split("=", 1)[1])
                except (TypeError, ValueError):
                    return False
                if pid <= 0:
                    return False
                try:
                    os.kill(pid, 0)
                    return True
                except OSError:
                    return False
                except Exception:
                    return False
        return False

    def _try_acquire_recover_singleton(self):
        """多 worker 导入时仅允许一个进程执行重启接管，避免双钉钉/双撤挂"""
        try:
            os.makedirs("logs", exist_ok=True)
            if os.path.exists(RECOVER_LOCK_FILE):
                age = time.time() - os.path.getmtime(RECOVER_LOCK_FILE)
                try:
                    with open(RECOVER_LOCK_FILE, encoding="utf-8") as f:
                        info = f.read().strip()
                except Exception:
                    info = "?"
                holder_alive = self._recover_lock_pid_alive(info)
                if age < RECOVER_LOCK_TTL_SEC and holder_alive:
                    logger.info(
                        f"🔄 跳过重复重启接管 (进程 {info} 仍存活, {age:.0f}s 前)"
                    )
                    return False
                if age < RECOVER_LOCK_TTL_SEC and not holder_alive:
                    logger.info(
                        f"🔄 旧接管锁已失效 (原 {info})，重新执行闪电接管"
                    )
            with open(RECOVER_LOCK_FILE, "w", encoding="utf-8") as f:
                f.write(f"pid={os.getpid()} ts={datetime.now().isoformat()}")
            return True
        except Exception as e:
            logger.warning(f"recover singleton lock: {e}")
            return True

    def _build_recover_health_report(self, pos, curr_px, tp_audit, shield_audit=None):
        """重启全域核查：实盘头寸 + TV + TP123 + 硬止损 + 浮盈/浮亏防线路由"""
        entry = float(pos.get("entry_price", self.watched_entry) or 0)
        curr_px = float(curr_px or 0)
        favorable = self._favorable_move_pct(curr_px) if curr_px > 0 else 0.0
        adverse = self._adverse_move_pct(curr_px) if curr_px > 0 else 0.0
        radar_progress = self._radar_activation_progress(curr_px) if curr_px > 0 else 0.0
        radar_active = self._is_radar_active()
        should_radar = self._should_radar_trail(curr_px) if curr_px > 0 else radar_active

        shield_audit = shield_audit or self._audit_shield_orders(pos["size"], entry)
        shield_ok = self._shield_orders_adequate(shield_audit)

        if should_radar or radar_active:
            pnl_label = f"浮盈·雷达区 (进度 {radar_progress:.0%}·5阶段)"
            defense_plan = "雷达移动保本(优先级高于硬止损)"
        elif adverse > 0.001:
            pnl_label = f"浮亏 {adverse:.1%}"
            defense_plan = "持有 TP123 + VPS宽硬止损"
        elif favorable > 0.001:
            tp1_prog = self._tp1_direction_progress(curr_px)
            pnl_label = f"浮盈 {favorable:.1%}·朝TP1 {tp1_prog:.0%}(雷达待命)"
            defense_plan = "持有 TP123 + VPS硬止损 (TP1成交后才激活雷达)"
        else:
            pnl_label = "保本附近"
            defense_plan = "持有 TP123 + VPS硬止损"

        stop_px = self._shield_stop_price(entry)
        if should_radar or radar_active:
            radar_sl = (
                self._clamp_radar_to_tv_floor(self.current_sl)
                if self._is_radar_active() else None
            )
            merged = self._effective_exchange_stop(radar_sl)
            shield_status = (
                f"合并止损 @ {merged:.2f}" if merged
                else f"TV底线 @ {stop_px:.2f}" if stop_px else "雷达区·待合并"
            )
        elif shield_ok:
            shield_status = f"已挂 @ {stop_px:.2f}" if stop_px else "已核实"
        else:
            shield_status = (
                f"待补挂 @ {stop_px:.2f}" if stop_px
                else shield_audit.get("status", "missing")
            )

        tv_side = self.last_tv_side or "?"
        tv_match = (pos.get("side") == tv_side)
        qty_saved = float(self.watched_qty or 0)
        qty_match = qty_saved <= 0 or not self._is_material_qty_change(qty_saved, pos["size"])

        return {
            "pnl_label": pnl_label,
            "defense_plan": defense_plan,
            "favorable_pct": favorable,
            "adverse_pct": adverse,
            "radar_progress": radar_progress,
            "radar_active": radar_active,
            "should_radar": should_radar,
            "shield_ok": shield_ok,
            "shield_status": shield_status,
            "shield_audit": shield_audit,
            "tp_matched": tp_audit.get("matched_full", 0),
            "tp_expected": tp_audit.get("expected", 0),
            "tv_match": tv_match,
            "qty_match": qty_match,
        }

    def _apply_recover_defense_policy(self, real_amt, curr_px, health):
        """
        重启一次性防线：TV tv_sl 硬止损 + 雷达合并（若应激活）。
        force=True 绕过哨兵宽限期，避免重启后45s内无硬止损。
        """
        actions = []
        radar_sl = None
        if health.get("should_radar") or health.get("radar_active"):
            if not self._is_radar_active():
                self._refresh_radar_state_on_recover(curr_px, self.watched_entry)
            if self._is_radar_active():
                radar_sl = self._clamp_radar_to_tv_floor(self.current_sl)

        ok = self._maintain_hard_shield(real_amt, curr_px, force=True, radar_sl=radar_sl)
        stop_px = self._effective_exchange_stop(radar_sl) or self._shield_stop_price()
        tv_note = (
            f"TV硬止损"
            if getattr(self, "tv_sl", 0) > 0
            else "TV tv_sl 缺失"
        )
        tag = (
            f"合并止损@{stop_px:.2f}"
            if radar_sl and stop_px
            else f"{tv_note}@{stop_px:.2f}" if stop_px else tv_note
        )
        actions.append(f"{tag}已齐" if ok else f"{tag}待补")
        return actions

    def _bootstrap_live_defenses_after_recover(self, real_amt, curr_px, audit=None):
        """
        重启/关机后全域自适应：核查 TP123+止损 → 缺则补挂不重复 → 雷达立即干活锁利。
        """
        if real_amt <= 0 or not self.current_side:
            return {"actions": [], "audit": audit or {}}

        curr_px = float(curr_px or binance_client.get_current_price(self.symbol) or 0)
        actions = []
        try:
            audit = audit or self._audit_tp_levels(real_amt)

            if not self._tp_audit_ok(audit):
                repaired, n_actions = self._surgical_repair_tp_defenses(
                    real_amt, self.watched_entry,
                )
                if n_actions > 0:
                    actions.append(f"智能补挂TP({n_actions}步)")
                    audit = repaired

            self._refresh_radar_state_on_recover(curr_px, self.watched_entry)
            health = self._build_recover_health_report(
                {"side": self.current_side, "size": real_amt, "entry_price": self.watched_entry},
                curr_px, audit,
            )
            actions.extend(self._apply_recover_defense_policy(real_amt, curr_px, health))

            if curr_px > 0 and (health.get("should_radar") or health.get("radar_active")):
                self._process_radar_trailing(real_amt, curr_px)
                sl = self._radar_sl_to_pass()
                if sl and not self._has_stop_sl_near(sl):
                    if self._ensure_radar_sl(sl, real_amt):
                        actions.append(f"雷达SL@{sl:.2f}")
                if self._is_radar_active() and not getattr(self, "_radar_activation_notified", False):
                    self._report_radar_first_activation(
                        real_amt, curr_px, self._clamp_radar_to_tv_floor(self.current_sl),
                        self._has_stop_sl_near(self.current_sl),
                    )
                actions.append(f"雷达激活·进度{health.get('radar_progress', 0):.0%}")

            self._radar_guardian_audit(real_amt, curr_px)
        except Exception as e:
            logger.error(f"重启全域核查部分失败(继续哨兵): {e}")
            actions.append(f"核查异常:{e}")
            audit = audit or self._audit_tp_levels(real_amt)
            health = {}

        self._post_recover_radar_pulse = True
        self._save_state()
        logger.info(
            f"📡 [重启全域核查] {' · '.join(actions) if actions else '盘口已齐，雷达待命'} | "
            f"TP {audit.get('matched_full', 0)}/{audit.get('expected', 0)}"
        )
        return {"actions": actions, "audit": audit, "health": health}

    def _reconcile_shield_on_recover(self, live_qty, curr_px):
        """重启接管：只读盘口同步状态，不抢在 TP 对齐前反复撤挂"""
        if live_qty <= 0 or not self.watched_entry:
            return
        if self._is_radar_active() or (curr_px > 0 and self._should_radar_trail(curr_px)):
            return

        audit = self._audit_shield_orders(live_qty)
        if self._shield_orders_adequate(audit):
            self.shield_active = True
            self._shield_fail_streak = 0
            self.shield_sized_qty = live_qty
            self._shield_arm_notified = True
            stop_px = self._shield_stop_price()
            logger.info(
                f"🛡️ 重启：盘口 TV硬止损已齐"
                + (f" @ {stop_px:.2f}" if stop_px else "")
                + "，跳过重挂"
            )
            self._save_state()
            return

        if audit["status"] == "duplicate":
            purged = self._purge_shield_stop_orders(audit["tier_prices"])
            self._record_shield_maintain(success=False)
            logger.warning(
                f"🛡️ 重启：撤净防护盾叠单 {purged} 笔，宽限期后哨兵按实盘补挂"
            )
            self.shield_active = True
            self._save_state()
            return

        if curr_px > 0 and self._should_activate_shield(curr_px):
            self.shield_active = True
            logger.info(
                "🛡️ 重启：TV硬止损待补挂（宽限期后哨兵按冷却处理）"
            )
            self._save_state()

    def _disarm_shield(self, reason="", notify=False):
        n = self._cancel_stop_orders(scope="shield")
        if self._shield_present_on_exchange():
            n += self._purge_shield_stop_orders()
            time.sleep(0.4)
        had = getattr(self, "shield_active", False) or bool(
            getattr(self, "shield_tiers_consumed", [])
        ) or self._shield_present_on_exchange()
        live_qty = float(self._resolve_live_qty(self.watched_qty or 0) or 0)
        entry = self.watched_entry
        self.shield_active = False
        self.shield_tiers_consumed = []
        self.shield_sized_qty = 0.0
        self._shield_arm_notified = False
        self._save_state()
        if reason and (had or n):
            logger.info(f"🛡️ [硬止损解除] {reason} | 撤销 {n} 笔 TV硬止损")
        if notify and n > 0 and live_qty > 0:
            progress = 0.0
            try:
                curr_px = binance_client.get_current_price(self.symbol) or 0
                progress = self._radar_activation_progress(curr_px)
            except Exception:
                curr_px = 0
            self._call_dingtalk(
                dingtalk.report_shield_disarmed,
                side=self.current_side,
                live_qty=live_qty,
                entry=entry,
                cancelled_count=n,
                reason=reason,
                radar_progress=progress,
                verify_note=(
                    f"撤 {n} 笔 TV硬止损 | "
                    + (
                        "雷达已激活，专注移动保本"
                        if self._is_radar_active()
                        else f"雷达进度 {progress:.0%}，TP1成交后推升止损"
                    )
                ),
            )

    def _place_shield_stops(self, live_qty, entry=None, reason="", force=False,
                            recover_mode=False, suppress_alert=False):
        entry = float(entry or self.watched_entry or 0)
        live_qty = self._resolve_live_qty(live_qty)
        if live_qty <= 0 or entry <= 0 or not self.current_side:
            return False
        tier_prices = self._shield_tier_prices(entry)
        remaining = self._remaining_shield_tier_indices()
        if not remaining:
            self.shield_active = False
            self._save_state()
            return True

        audit = self._audit_shield_orders(live_qty, entry)
        if self._shield_orders_adequate(audit):
            self.shield_active = True
            self._shield_fail_streak = 0
            if not getattr(self, "shield_sized_qty", 0):
                self.shield_sized_qty = live_qty
            self._save_state()
            return True

        if not self._shield_needs_exchange_action(live_qty, audit) and not force:
            self.shield_active = True
            self.shield_sized_qty = live_qty
            self._save_state()
            return True

        if not self._can_maintain_shield_now(force=force, audit=audit):
            return getattr(self, "shield_active", False)

        if audit["status"] == "duplicate" and not force:
            purged = self._purge_all_protective_stops(keep_near=0)
            self._record_shield_maintain(success=False)
            logger.warning(
                f"🛡️ 防护盾叠单/孤儿清理：撤 {purged} 笔，冷却后再按实盘 {live_qty} ETH 补挂唯一硬止损"
            )
            return False

        purged = self._purge_all_protective_stops(keep_near=0)
        if purged:
            logger.warning(
                f"🛡️ 撤净旧硬止损 {purged} 笔 → 按实盘 {live_qty} ETH 重挂唯一 @ tv_sl"
            )
            time.sleep(0.6)

        placed = 0
        for idx in remaining:
            tp = tier_prices[idx]
            if tp <= 0:
                continue
            res = self._place_vps_hard_sl_order(live_qty, tp, use_stop_limit=True)
            if res:
                placed += 1
                limit_px = compute_vps_hard_sl_limit_price(self.current_side, tp)
                logger.info(
                    f"🛡️ VPS硬止损: Stop-Limit 触发@{tp:.2f} 限价@{limit_px:.2f} "
                    f"(实盘 {live_qty} ETH)"
                )
            time.sleep(0.35)

        post_audit = self._wait_shield_audit_ok(
            live_qty, entry,
            retries=12 if recover_mode else 8,
            delay=0.5,
        )
        ok = self._shield_orders_adequate(post_audit)
        self._record_shield_maintain(success=ok)
        if ok:
            self.shield_active = True
            self.shield_sized_qty = live_qty
            self._save_state()
            stop_px = tier_prices[0] if tier_prices else entry
            logger.warning(
                f"🛡️ [VPS硬止损] 已挂 | Stop-Limit @ {stop_px:.2f} | "
                f"新挂 {placed} 笔 | 雷达激活后合并为移动止损"
            )
            if not getattr(self, "_shield_arm_notified", False):
                self._shield_arm_notified = True
                self._call_dingtalk(
                    dingtalk.report_adverse_shield_armed,
                    side=self.current_side,
                    entry=entry,
                    live_qty=live_qty,
                    adverse_pct=0,
                    tier_prices=[stop_px],
                    tier_pcts=SHIELD_TIER_PCTS,
                    vps_hard_sl_note=format_vps_hard_sl_note(
                        self.current_side, entry,
                        float(getattr(self, "open_atr", None) or self.current_atr or 30),
                        int(getattr(self, "open_regime", None) or self.regime or 3),
                        tv_sl_ref=getattr(self, "tv_sl_ref", 0),
                    ),
                    verify_note=(
                        (reason or f"VPS硬止损 @ {stop_px:.2f}")
                        + f" | Stop-Limit 触发@{stop_px:.2f} | 仅播报一次"
                    ),
                )
        elif placed > 0 and not suppress_alert:
            dingtalk.report_system_alert(
                "TV硬止损未对齐",
                f"已撤旧单 {purged} 笔、新挂 {placed} 笔，但核实未通过 | "
                f"实盘 {live_qty} ETH | {', '.join(post_audit.get('issues', []))}",
                suggestion="系统已退避冷却，下轮自动重试；请勿手动重复挂",
            )
        elif placed > 0:
            logger.warning(
                f"🛡️ 硬止损核实延迟 | 新挂 {placed} 笔 | "
                f"{', '.join(post_audit.get('issues', []))} | 哨兵将继续补核实"
            )
        return ok

    def _adopt_exchange_hard_sl(self, source=""):
        """
        实盘已有唯一 STOP 时写回账本；若多笔（重启叠单）则拒采纳，交统一同步清理。
        """
        entry = float(self.watched_entry or 0)
        side = (self.current_side or "").upper()
        stops = binance_client.find_protective_stop_prices(self.symbol)
        if not stops:
            return 0.0
        uniq = sorted({round(float(p), 2) for p in stops if float(p) > 0})
        if len(uniq) > 1:
            logger.warning(
                f"🛡️ 盘口多笔硬止损 STOP{uniq} → 拒单笔采纳，强制统一"
                + (f" | {source}" if source else "")
            )
            return 0.0
        chosen = uniq[0]
        if side == "LONG" and entry > 0 and chosen >= entry - 0.01:
            return 0.0
        if side == "SHORT" and entry > 0 and chosen <= entry + 0.01:
            return 0.0
        old = round(float(getattr(self, "tv_sl", 0) or 0), 2)
        self.tv_sl = chosen
        if not self.current_sl or float(self.current_sl) <= 0:
            self.current_sl = chosen
        self.shield_active = True
        self._tv_sl_missing_alerted = False
        self._last_applied_exchange_sl = chosen
        self._save_state()
        logger.info(
            f"🛡️ 盘口采纳硬止损 @{chosen:.2f}"
            + (f" (原账本 {old:.2f})" if old and abs(old - chosen) > 0.01 else "")
            + (f" | {source}" if source else "")
        )
        return chosen

    def _ensure_hard_sl_ledger(self, live_qty=0, source=""):
        """账本无 tv_sl 时：先按开仓价×档位%重算，再核对盘口挂单"""
        if float(getattr(self, "tv_sl", 0) or 0) > 0:
            return True
        entry = float(self.watched_entry or 0)
        side = (self.current_side or "").upper()
        if entry > 0 and side in ("LONG", "SHORT"):
            if self._refresh_vps_hard_sl(
                entry=entry, side=side,
                regime=int(getattr(self, "open_regime", None) or self.regime or 3),
                atr=float(getattr(self, "open_atr", None) or self.current_atr or 0),
                tv_sl_ref=getattr(self, "tv_sl_ref", 0) or None,
                source=source or "账本自愈",
            ):
                self._tv_sl_missing_alerted = False
                return True
        adopted = self._adopt_exchange_hard_sl(source=source or "账本自愈·盘口")
        return adopted > 0

    def _maintain_hard_shield(self, real_amt, curr_px=None, force=False, radar_sl=None):
        """维护 VPS 硬止损 Stop-Limit；雷达激活时合并为 max/min(雷达, vps_sl)"""
        if real_amt <= 0 or not self.watched_entry:
            return False
        curr_px = float(curr_px or 0)
        if radar_sl is None and (
            self._is_radar_active() or (curr_px > 0 and self._should_radar_trail(curr_px))
        ):
            radar_sl = self._clamp_radar_to_tv_floor(self.current_sl)

        if float(getattr(self, "tv_sl", 0) or 0) <= 0 and not radar_sl:
            self._ensure_hard_sl_ledger(real_amt, source="维护硬止损自愈")

        if getattr(self, "tv_sl", 0) > 0 or radar_sl:
            if not force and not self._can_maintain_shield_now(force=force):
                return getattr(self, "shield_active", False)
            return self._sync_exchange_stop(
                real_amt,
                radar_sl=radar_sl,
                reason="维护VPS硬止损/雷达合并",
                force=force,
            ).get("ok", False)

        # 最终核对：盘口已有 STOP → 采纳，禁止误报「缺失」
        live_stops = binance_client.find_protective_stop_prices(self.symbol)
        if live_stops:
            self._adopt_exchange_hard_sl(source="维护核对·盘口已有")
            logger.warning(
                f"🛡️ 账本缺tv_sl但盘口已有STOP{live_stops} → 已采纳，跳过缺失告警"
            )
            self._tv_sl_missing_alerted = False
            return True

        if real_amt > 0 and not getattr(self, "_tv_sl_missing_alerted", False):
            logger.error(
                f"维护硬止损失败：持仓 {real_amt} ETH | entry={self.watched_entry} "
                f"| side={self.current_side} | regime={self.regime} | 盘口无STOP"
            )
            dingtalk.report_system_alert(
                "VPS硬止损缺失",
                f"持仓 {real_amt} ETH · 账本与盘口均无硬止损 "
                f"(entry={self.watched_entry or '空'} side={self.current_side or '空'} "
                f"R{int(getattr(self, 'open_regime', None) or self.regime or 0)})",
                suggestion="哨兵将重算开仓价×档位%并补挂；若盘口已有请忽略本条",
            )
            self._tv_sl_missing_alerted = True
        return False

    def _process_adverse_shield(self, real_amt, curr_px):
        """兼容旧调用 → 维护硬止损"""
        return self._maintain_hard_shield(real_amt, curr_px)

    def _is_radar_active(self):
        """
        雷达移动保本已武装：必须 stage≥1（TP1 交棒后），且止损已越过成本。
        禁止仅靠 current_sl≈entry 误判为雷达（开仓后曾把 current_sl 设成成本价）。
        """
        if int(getattr(self, "_radar_stage_last", 0) or 0) < 1:
            return False
        if not self.watched_entry or not self.current_sl:
            return False
        sl = float(self.current_sl)
        entry = float(self.watched_entry)
        if self.current_side == "LONG":
            return sl > entry + 0.01
        if self.current_side == "SHORT":
            return sl < entry - 0.01
        return False

    def _radar_sl_to_pass(self):
        return self.current_sl if self._is_radar_active() else None

    def _collect_tp_limit_orders(self):
        """reduceOnly / 平仓方向限价止盈单明细"""
        orders = []
        for o in binance_client.get_open_orders(self.symbol):
            if not self._is_tp_limit_order(o):
                continue
            px = float(o.get("price", 0) or 0)
            if px <= 0:
                continue
            orders.append({
                "orderId": o.get("orderId"),
                "price": round(px, 2),
                "qty": round(float(o.get("origQty", o.get("quantity", 0)) or 0), 3),
            })
        return orders

    def _has_duplicate_tp_orders(self, tolerance=1.0):
        """同一 TP 价位出现多张单，或总张数超过应有档数"""
        orders = self._collect_tp_limit_orders()
        expected = self._expected_tp_count()
        if expected <= 0:
            return False
        if len(orders) > expected:
            return True
        for tp in self.tv_tps:
            if tp <= 0:
                continue
            at_px = [o for o in orders if abs(o["price"] - tp) <= tolerance]
            if len(at_px) > 1:
                return True
        return False

    def _defenses_fully_ok(self, live_qty, dynamic_sl=None, tolerance=1.0, qty_tol=0.005):
        """头寸对应的 TP123 价位+数量均已正确挂好，且雷达止损（若需要）也在"""
        tp_pxs = self.tv_tps
        expected = self._expected_tp_count(tp_pxs)
        if expected == 0:
            return dynamic_sl is None or self._has_stop_sl_near(dynamic_sl, tolerance)

        orders = self._collect_tp_limit_orders()
        ratios = self.regime_settings[self._tp_split_regime()]["ratios"]
        qty1, qty2, qty3 = self._split_tp_quantities(live_qty, ratios)
        levels = [(qty1, tp_pxs[0]), (qty2, tp_pxs[1]), (qty3, tp_pxs[2])]

        matched_levels = 0
        expected_prices = []
        for q, px in levels:
            if q <= 0 or px <= 0:
                continue
            expected_prices.append(px)
            at_px = [o for o in orders if abs(o["price"] - px) <= tolerance]
            if len(at_px) != 1:
                return False
            if abs(at_px[0]["qty"] - q) > qty_tol:
                return False
            matched_levels += 1

        if matched_levels < expected:
            return False

        for o in orders:
            if not any(abs(o["price"] - p) <= tolerance for p in expected_prices):
                return False

        if dynamic_sl and not self._has_stop_sl_near(dynamic_sl, tolerance):
            return False
        return True

    def _patch_missing_tp_levels(self, live_qty, tolerance=1.0, qty_tol=0.005):
        """只补缺失档；重复/偏差交核武，禁止叠单；已成交档位不再补挂"""
        live_qty = self._resolve_live_qty(live_qty)
        audit = self._audit_tp_levels(live_qty, tolerance, qty_tol)
        if self._defense_needs_immediate_fix(audit):
            logger.warning("补挂跳过：检测到重复/缺失/偏差，改走核武对齐")
            return 0
        close_side = "SHORT" if self.current_side == "LONG" else "LONG"
        placed = 0

        for lv in self._expected_tp_levels(live_qty):
            q, px = lv["qty"], lv["price"]
            if q <= 0 or px <= 0:
                continue
            orders = self._collect_tp_limit_orders()
            at_px = [o for o in orders if abs(o["price"] - px) <= tolerance]
            if len(at_px) == 1 and abs(at_px[0]["qty"] - q) <= qty_tol:
                logger.info(f"  ✓ TP{lv['level']} @ {px:.2f} 已存在 {at_px[0]['qty']} ETH，跳过")
                continue
            for o in at_px:
                if o.get("orderId"):
                    binance_client.cancel_order(self.symbol, order=o)
                    time.sleep(0.25)
            logger.info(f"  + 补挂 TP{lv['level']} @ {px:.2f} qty={q} ETH")
            if binance_client.place_limit_order(close_side, q, px, symbol=self.symbol, reduce_only=True):
                placed += 1
            time.sleep(0.4)
        return placed

    def _audit_requires_nuclear(self, audit):
        """重复/多档缺失/总单数超标 → 必须核武级清场重挂，增量补挂权限不足"""
        expected = audit.get("expected", 0)
        if expected <= 0:
            return False
        if audit.get("matched_full", 0) >= expected and not audit.get("orphans"):
            return False
        orders = self._collect_tp_limit_orders()
        if len(orders) > expected:
            return True
        if audit.get("matched_full", 0) == 0 and audit.get("issues"):
            return True
        bad = [lv for lv in audit.get("levels", []) if lv.get("status") in ("duplicate", "qty_mismatch")]
        if bad:
            return True
        missing = sum(1 for lv in audit.get("levels", []) if lv.get("status") == "missing")
        if missing >= 1:
            return True
        if audit.get("orphans"):
            return True
        return False

    def _cancel_all_tp_limit_orders(self, max_rounds=4):
        """撤销全部限价止盈（不动 STOP）；多轮直到盘口无残留 TP"""
        total = 0
        for round_i in range(max_rounds):
            orders = [
                o for o in binance_client.get_open_orders(self.symbol)
                if self._is_tp_limit_order(o)
            ]
            if not orders:
                break
            for o in orders:
                oid = o.get("orderId")
                if oid:
                    binance_client.cancel_order(self.symbol, oid)
                    total += 1
                    time.sleep(0.12)
            logger.info(f"🧹 撤限价止盈 第{round_i + 1}轮: {len(orders)} 张")
            time.sleep(0.6)
        if total:
            logger.info(f"🧹 已撤销限价止盈合计 {total} 张")
        return total

    def _scorched_earth_cancel_for_recover(self):
        """重启接管：撤净全部挂单（含重复 TP），随后由核武重挂 TP + 雷达 SL"""
        for attempt in range(6):
            binance_client.cancel_all_open_orders(self.symbol)
            time.sleep(0.8)
            self._cancel_all_tp_limit_orders(max_rounds=4)
            time.sleep(0.6)
            remaining = self._collect_tp_limit_orders()
            if not remaining:
                logger.info(f"☢️ 重启撤单完成，限价止盈已清零 (第 {attempt + 1} 轮)")
                return True
            remain_txt = ", ".join(f"{o['qty']}@{o['price']}" for o in remaining[:4])
            logger.warning(
                f"⚠️ 撤单后仍剩 {len(remaining)} 张限价止盈 ({remain_txt}) "
                f"→ 重试 {attempt + 1}/6"
            )
        logger.error("❌ 重启撤单未净：重复 TP 可能残留，非权限问题时请币安 APP 手动全撤后重启")
        return False

    def _remaining_open_order_count(self):
        try:
            return len(binance_client.get_open_orders(self.symbol) or [])
        except Exception:
            return -1

    def _purge_all_defense_orders_on_flat(self, reason="", max_rounds=6):
        """
        全平/人工平仓后：多轮撤净 TP123 + tv_sl/雷达 STOP + Algo 条件单。
        防止残留 reduceOnly 止盈在空仓后成交 → 反向开 orphan 仓。
        """
        tag = reason or "全平撤单"
        tp_cancelled = 0
        for attempt in range(max_rounds):
            binance_client.cancel_all_open_orders(self.symbol)
            time.sleep(0.35)
            tp_cancelled += self._cancel_all_tp_limit_orders(max_rounds=3)
            purged_stops = self._purge_all_close_position_stops()
            time.sleep(0.45)
            remaining = self._remaining_open_order_count()
            tp_left = self._collect_tp_limit_orders()
            if remaining == 0 and not tp_left:
                logger.info(
                    f"🧹 [{tag}] 挂单已清零 (第 {attempt + 1} 轮) | "
                    f"撤 TP {tp_cancelled} 张"
                )
                return {
                    "ok": True,
                    "rounds": attempt + 1,
                    "tp_cancelled": tp_cancelled,
                    "remaining": 0,
                }
            if tp_left:
                remain_txt = ", ".join(
                    f"{o['qty']}@{o['price']}" for o in tp_left[:4]
                )
                logger.warning(
                    f"⚠️ [{tag}] 第 {attempt + 1}/{max_rounds} 轮后仍剩 "
                    f"{len(tp_left)} 张 TP ({remain_txt}) | 全盘 {remaining} 单"
                )
            else:
                logger.warning(
                    f"⚠️ [{tag}] 第 {attempt + 1}/{max_rounds} 轮后仍剩 "
                    f"{remaining} 张挂单"
                )
        tp_left = self._collect_tp_limit_orders()
        remaining = self._remaining_open_order_count()
        ok = remaining == 0 and not tp_left
        if not ok:
            logger.error(
                f"❌ [{tag}] 全平后挂单未净：剩余 {remaining} 单 | "
                f"TP {len(tp_left)} 张"
            )
        return {
            "ok": ok,
            "rounds": max_rounds,
            "tp_cancelled": tp_cancelled,
            "remaining": remaining,
            "tp_remaining": len(tp_left),
        }

    def _ensure_radar_sl(self, dynamic_sl, live_qty=None):
        if not dynamic_sl:
            return False
        clamped = self._clamp_radar_to_tv_floor(dynamic_sl)
        clamped = round(float(clamped), 2)
        # 同价已挂 → 幂等跳过（禁止反复撤挂刷新）
        if self._has_stop_sl_near(clamped, exclude_shield=False):
            self._last_applied_exchange_sl = clamped
            return True
        last = round(float(getattr(self, "_last_applied_exchange_sl", 0) or 0), 2)
        if last > 0 and abs(last - clamped) <= SHIELD_STOP_TOLERANCE:
            if self._has_stop_sl_near(last, exclude_shield=False):
                return True
        result = self._sync_exchange_stop(
            live_qty or self.watched_qty,
            radar_sl=clamped,
            reason=f"雷达保本 @ {clamped:.2f}",
            force=False,
        )
        return result.get("ok", False)

    def _realign_radar_defenses(self, live_qty, entry, new_sl):
        """雷达推升：TP 异常才核武；同价止损已在则跳过撤挂"""
        new_sl = round(float(new_sl or 0), 2)
        if new_sl > 0 and self._has_stop_sl_near(new_sl, exclude_shield=False):
            logger.info(f"📡 雷达止损已在 @{new_sl:.2f}，跳过撤挂")
            return True
        self._cancel_stop_orders(scope="radar")
        time.sleep(0.35)
        audit = self._audit_tp_levels(live_qty)
        if self._defense_needs_immediate_fix(audit):
            self._enforce_defense_alignment(
                live_qty, entry, dynamic_sl=new_sl,
                reason="雷达推升前 TP 纠偏", rounds=2,
            )
        sl_placed = self._ensure_radar_sl(new_sl, live_qty)
        if not sl_placed and not self._has_stop_sl_near(new_sl, exclude_shield=False):
            close_side = "SHORT" if self.current_side == "LONG" else "LONG"
            sl_placed = binance_client.place_stop_market_order(close_side, new_sl, symbol=self.symbol) is not None
        time.sleep(0.4)
        return bool(sl_placed or self._has_stop_sl_near(new_sl, exclude_shield=False))

    def _report_radar_first_activation(self, real_amt, curr_px, new_sl, sl_placed):
        """雷达首次激活：核实实盘后推送（价格推进或 TP 成交触发）"""
        if getattr(self, "_radar_activation_notified", False):
            return
        if not self._radar_legitimately_armed(real_amt, curr_px):
            logger.warning(
                f"📡 雷达激活钉钉跳过：未达价格推进阈值 "
                f"(entry={self.watched_entry:.2f} sl={new_sl:.2f})"
            )
            return
        if self.current_side == "LONG" and float(new_sl or 0) <= float(self.watched_entry or 0):
            logger.warning(
                f"📡 雷达激活钉钉跳过：LONG 止损 {new_sl:.2f} 未高于 entry"
            )
            return
        if self.current_side == "SHORT" and float(new_sl or 0) >= float(self.watched_entry or 0):
            logger.warning(
                f"📡 雷达激活钉钉跳过：SHORT 止损 {new_sl:.2f} 未低于 entry"
            )
            return
        verified = self._wait_verify(
            lambda: self._has_stop_sl_near(new_sl),
            retries=10,
            delay=0.45,
        )
        progress = self._radar_activation_progress(curr_px) if curr_px > 0 else 1.0
        stage = self._radar_stage(curr_px) if curr_px > 0 else 0
        tv_floor = round(float(getattr(self, "tv_sl", 0) or 0), 2)
        verify_note = (
            f"雷达阶段{stage} {self._radar_stage_label(stage)} | 进度 {progress:.0%} | "
            f"合并止损 @ {new_sl:.2f} | "
            f"VPS硬止损底线={tv_floor or 'fallback'} | "
            f"持仓 {real_amt} {self._unit()} @ {self.watched_entry:.2f}"
        )
        if not verified and not sl_placed:
            logger.warning(f"雷达首次激活钉钉跳过：止损 @ {new_sl:.2f} 未核实")
            return
        if not verified:
            verify_note += f" | {dingtalk.VERIFY_DELAY_MARK}"
        self._call_dingtalk(
            dingtalk.report_radar_activated,
            side=self.current_side,
            qty=real_amt,
            entry=self.watched_entry,
            new_sl=new_sl,
            radar_progress=progress,
            regime=self.regime,
            shield_cleared=True,
            verify_note=verify_note,
            verified=verified,
        )
        self._radar_activation_notified = True
        self._save_state()

    def _nuclear_realign_tp(self, live_qty, entry, dynamic_sl=None, rounds=3):
        """
        核武级止盈对齐：只撤限价 TP → 重挂 TP123 → 始终续挂 tv_sl/雷达合并止损。
        """
        last_audit = self._audit_tp_levels(live_qty)
        for r in range(rounds):
            logger.warning(
                f"☢️ 核武级止盈清场重挂 {r + 1}/{rounds} | 持仓 {live_qty} ETH | "
                f"当前 {last_audit['matched_full']}/{last_audit['expected']} | "
                f"{self._format_audit_summary(last_audit)}"
            )
            self._cancel_all_tp_limit_orders()
            time.sleep(1.0)
            placed = self._rebuild_defenses(live_qty, entry, dynamic_sl=None)
            logger.info(f"☢️ 核武轮 {r + 1} 新挂 {placed} 笔限价止盈")
            self._maintain_hard_shield(
                live_qty, None, force=True, radar_sl=dynamic_sl,
            )
            time.sleep(1.0)
            last_audit = self._audit_tp_levels(live_qty)
            stop_px = self._resolve_defense_stop_for_audit(dynamic_sl)
            if self._defenses_fully_ok(live_qty, stop_px):
                logger.info(f"☢️ 核武重挂成功: {self._format_audit_summary(last_audit)}")
                return last_audit
            logger.warning(
                f"☢️ 核武轮 {r + 1} 仍未对齐: {self._format_audit_summary(last_audit)}"
            )
            time.sleep(1.5)
        return last_audit

    def _tp_audit_ok(self, audit):
        expected = audit.get("expected", 0)
        if expected <= 0:
            return True
        tp_prices = sum(1 for t in (self.tv_tps or []) if t > 0)
        if (
            tp_prices >= 3
            and not self._tp_level_consumed(1)
            and expected < 3
        ):
            return False
        return (
            audit.get("matched_full", 0) >= expected
            and not audit.get("orphans")
            and not self._defense_needs_immediate_fix(audit)
        )

    def _mark_defense_align_ok(self):
        self._last_defense_align_ok_ts = time.time()
        self._guardian_bad_streak = 0

    def _defense_needs_immediate_fix(self, audit):
        """重复/缺失/数量偏差/孤儿单 → 必须撤单重算重挂（禁止增量叠单）"""
        if self._audit_requires_nuclear(audit):
            return True
        for lv in audit.get("levels", []):
            if lv.get("status") in ("duplicate", "missing", "qty_mismatch"):
                return True
        return bool(audit.get("issues") or audit.get("orphans"))

    def _enforce_defense_alignment(self, live_qty, entry, dynamic_sl=None, reason="", rounds=3,
                                   recover_mode=False):
        """
        防线对齐总线：先审计 → 已齐则不动 TP → 异常才撤净重挂。
        recover_mode=True 时先核武撤全部挂单，避免重启后重复 TP 残留。
        """
        live_qty = self._resolve_live_qty(live_qty)
        if live_qty <= 0:
            audit = self._audit_tp_levels(live_qty)
            return {
                "matched": 0, "expected": audit.get("expected", 0),
                "pending_prices": [], "rebuilt": False, "audit": audit, "nuclear": False,
            }
        if reason:
            logger.info(f"🛡️ 防线对齐: {reason} | 持仓 {live_qty} ETH")

        self._defense_align_in_progress = True
        try:
            audit = self._audit_tp_levels(live_qty)

            if recover_mode and self._tp_audit_ok(audit):
                logger.info(
                    f"✅ 重启接管：盘口 TP 已齐，跳过核武撤挂 | "
                    f"{self._format_audit_summary(audit)}"
                )
                if dynamic_sl and not self._has_stop_sl_near(dynamic_sl):
                    self._ensure_radar_sl(dynamic_sl, live_qty)
                self._mark_defense_align_ok()
                return {
                    "matched": audit["matched_full"],
                    "expected": audit["expected"],
                    "pending_prices": audit["pending_prices"],
                    "rebuilt": False,
                    "audit": audit,
                    "nuclear": False,
                }

            if recover_mode and self._defense_needs_immediate_fix(audit):
                repaired, n_actions = self._surgical_repair_tp_defenses(live_qty, entry)
                audit = repaired
                if self._tp_audit_ok(audit):
                    logger.info(
                        f"✅ 重启智能修复成功 ({n_actions} 步)，无需核武 | "
                        f"{self._format_audit_summary(audit)}"
                    )
                    if dynamic_sl and not self._has_stop_sl_near(dynamic_sl):
                        self._ensure_radar_sl(dynamic_sl, live_qty)
                    self._mark_defense_align_ok()
                    return {
                        "matched": audit["matched_full"],
                        "expected": audit["expected"],
                        "pending_prices": audit["pending_prices"],
                        "rebuilt": n_actions > 0,
                        "audit": audit,
                        "nuclear": False,
                    }
                logger.warning(
                    f"⚠️ 重启智能修复后仍不齐 ({n_actions} 步) → 升级核武 | "
                    f"{self._format_audit_summary(audit)}"
                )

            if not recover_mode and self._tp_audit_ok(audit):
                logger.info(f"✅ TP 已齐，跳过撤单: {self._format_audit_summary(audit)}")
                if dynamic_sl and not self._has_stop_sl_near(dynamic_sl):
                    self._ensure_radar_sl(dynamic_sl, live_qty)
                self._mark_defense_align_ok()
                return {
                    "matched": audit["matched_full"],
                    "expected": audit["expected"],
                    "pending_prices": audit["pending_prices"],
                    "rebuilt": False,
                    "audit": audit,
                    "nuclear": False,
                }

            if recover_mode:
                self._scorched_earth_cancel_for_recover()
            else:
                self._cancel_all_tp_limit_orders()
            time.sleep(0.45)
            audit = self._audit_tp_levels(live_qty)
            if self._tp_audit_ok(audit):
                logger.info(f"✅ 撤单后 TP 已齐: {self._format_audit_summary(audit)}")
                if dynamic_sl and not self._has_stop_sl_near(dynamic_sl):
                    self._ensure_radar_sl(dynamic_sl, live_qty)
                self._mark_defense_align_ok()
                return {
                    "matched": audit["matched_full"],
                    "expected": audit["expected"],
                    "pending_prices": audit["pending_prices"],
                    "rebuilt": False,
                    "audit": audit,
                    "nuclear": False,
                }

            sl_preserve = dynamic_sl if self._is_radar_active() else None
            audit = self._nuclear_realign_tp(
                live_qty, entry, dynamic_sl=sl_preserve or dynamic_sl, rounds=rounds,
            )
            self._maintain_hard_shield(
                live_qty, None, force=True, radar_sl=sl_preserve or dynamic_sl,
            )
            if audit["matched_full"] < audit["expected"]:
                logger.warning("☢️ 首轮核武未齐，追加一轮重挂")
                if recover_mode:
                    self._scorched_earth_cancel_for_recover()
                else:
                    self._cancel_all_tp_limit_orders(max_rounds=4)
                time.sleep(0.6)
                audit = self._nuclear_realign_tp(
                    live_qty, entry, dynamic_sl=sl_preserve or dynamic_sl,
                    rounds=max(2, rounds - 1),
                )
                self._maintain_hard_shield(
                    live_qty, None, force=True, radar_sl=sl_preserve or dynamic_sl,
                )
            stop_px = self._resolve_defense_stop_for_audit(dynamic_sl)
            if stop_px and not self._has_stop_sl_near(stop_px):
                self._maintain_hard_shield(live_qty, None, force=True, radar_sl=dynamic_sl)
            elif dynamic_sl and not self._has_stop_sl_near(dynamic_sl):
                self._ensure_radar_sl(dynamic_sl, live_qty)
            if self._tp_audit_ok(audit):
                self._mark_defense_align_ok()
            return {
                "matched": audit["matched_full"],
                "expected": audit["expected"],
                "pending_prices": audit["pending_prices"],
                "rebuilt": True,
                "audit": audit,
                "nuclear": True,
            }
        finally:
            self._defense_align_in_progress = False

    def _radar_guardian_audit(self, real_amt, curr_px):
        """
        雷达守护：仅 TP 异常才撤单重挂；止损缺失单独补挂，禁止动已齐 TP。
        """
        if real_amt <= 0 or not self.monitoring:
            return None
        if getattr(self, "_recover_in_progress", False):
            return None
        if getattr(self, "_open_in_progress", False):
            return None
        if getattr(self, "_defense_align_in_progress", False):
            return None

        cap = self._radar_enforce_regime_cap(real_amt, curr_px)
        if cap:
            real_amt = cap["new_qty"]
            if self._tp_audit_ok(cap["result"]["audit"]):
                return cap

        audit = self._audit_tp_levels(real_amt)
        sl = self._radar_sl_to_pass()

        if self._tp_audit_ok(audit):
            self._guardian_bad_streak = 0
            if sl and not self._has_stop_sl_near(sl, exclude_shield=False):
                self._ensure_radar_sl(sl, real_amt)
            return None

        self._guardian_bad_streak += 1
        now = time.time()
        severe = self._defense_needs_immediate_fix(audit)
        in_grace = now < getattr(self, "_sentinel_grace_until", 0)
        in_cooldown = (
            now - getattr(self, "_last_defense_align_ok_ts", 0)
            < DEFENSE_ALIGN_COOLDOWN_SEC
        )
        if (in_grace or in_cooldown) and not severe and self._guardian_bad_streak < 2:
            logger.info(
                f"📡 [雷达守护] TP 审计波动，暂不重挂 "
                f"({'重启宽限期' if in_grace else '冷却期'}) | "
                f"{self._format_audit_summary(audit)}"
            )
            return None

        logger.warning(
            f"📡 [雷达守护] TP 未对齐 → 撤单重算重挂 | "
            f"{self._format_audit_summary(audit)}"
        )
        sl_preserve = sl if self._is_radar_active() else None
        result = self._enforce_defense_alignment(
            real_amt, self.watched_entry, dynamic_sl=sl_preserve,
            reason="雷达守护实时纠偏", rounds=3,
        )
        new_audit = result["audit"]
        if new_audit["matched_full"] < new_audit["expected"]:
            self._call_dingtalk(
                dingtalk.report_system_alert,
                "雷达守护：止盈仍未对齐",
                (
                    f"{self.current_side} {real_amt} ETH | "
                    f"{self._format_audit_summary(new_audit)} | 请人工核查币安挂单"
                ),
            )
        elif self._defense_needs_immediate_fix(audit):
            logger.info(
                f"📡 [雷达守护] 纠偏完成: "
                f"{new_audit['matched_full']}/{new_audit['expected']} | "
                f"{self._format_audit_summary(new_audit)}"
            )
            if getattr(self, "_recover_tp_unconfirmed", False):
                self._recover_tp_unconfirmed = False
                self._call_dingtalk(
                    dingtalk.report_radar_guardian_realigned,
                    side=self.current_side,
                    qty=real_amt,
                    tp_audit=new_audit,
                    verify_note=(
                        f"重启接管竞态后雷达已纠偏 | "
                        f"{new_audit['matched_full']}/{new_audit['expected']} | "
                        f"{self._format_audit_summary(new_audit)}"
                    ),
                )
            elif getattr(self, "_open_tp_unconfirmed", False):
                self._open_tp_unconfirmed = False
                self._call_dingtalk(
                    dingtalk.report_radar_guardian_realigned,
                    side=self.current_side,
                    qty=real_amt,
                    tp_audit=new_audit,
                    verify_note=(
                        f"开仓后雷达已纠偏 | "
                        f"{new_audit['matched_full']}/{new_audit['expected']} | "
                        f"{self._format_audit_summary(new_audit)}"
                    ),
                )
        return result


    def _smart_realign_defenses(self, live_qty, entry, dynamic_sl=None, reason=""):
        """统一委托防线对齐总线（撤单 → 重算 → 重挂）"""
        return self._enforce_defense_alignment(
            live_qty, entry, dynamic_sl=dynamic_sl, reason=reason or "智能防线对齐", rounds=3,
        )

    def _full_rebuild_tp_loop(self, live_qty, entry, dynamic_sl=None):
        result = self._enforce_defense_alignment(
            live_qty, entry, dynamic_sl=dynamic_sl, reason="全量重建", rounds=3,
        )
        audit = result["audit"]
        return audit["matched_full"], audit["pending_prices"], audit["expected"]

    def _ensure_defenses_on_recover(self, live_qty, entry, dynamic_sl=None):
        """
        重启/异动接管：审计 → 齐全跳过 → 增量补挂 → 仍失败才清场重建
        返回 (matched, pending_prices, expected, rebuilt)
        """
        audit = self._audit_tp_levels(live_qty)
        expected = audit["expected"]
        matched = audit["matched_full"]
        pending_prices = audit["pending_prices"]
        logger.info(
            f"📊 防线审计: 持仓 {live_qty} ETH | TP {matched}/{expected} | "
            f"{self._format_audit_summary(audit)}"
        )

        if self._audit_requires_nuclear(audit) or self._has_duplicate_tp_orders():
            logger.warning(
                f"☢️ 审计触发核武级重挂: {len(self._collect_tp_limit_orders())} 张止盈 | "
                f"{self._format_audit_summary(audit)}"
            )
            audit = self._nuclear_realign_tp(live_qty, entry, dynamic_sl=dynamic_sl, rounds=3)
            return audit["matched_full"], audit["pending_prices"], audit["expected"], True

        if self._defenses_fully_ok(live_qty, dynamic_sl):
            logger.info(
                f"✅ TP123 比例齐全 ({matched}/{expected}) @ {pending_prices}，跳过补挂"
            )
            if dynamic_sl and not self._has_stop_sl_near(dynamic_sl):
                close_side = "SHORT" if self.current_side == "LONG" else "LONG"
                binance_client.place_stop_market_order(close_side, dynamic_sl, symbol=self.symbol)
            return matched, pending_prices, expected, False

        self._cancel_orphan_tp_orders(live_qty)
        logger.info(f"📋 止盈未齐 ({matched}/{expected})，增量补挂缺失档（保留已有正确单）")
        self._patch_missing_tp_levels(live_qty)
        time.sleep(0.8)
        matched, pending_prices = self._wait_tp_hung(
            self.tv_tps, live_qty=live_qty, retries=5, delay=1.0,
        )
        audit = self._audit_tp_levels(live_qty)
        matched = audit["matched_full"]

        if self._defenses_fully_ok(live_qty, dynamic_sl):
            logger.info(f"✅ 增量补挂成功 ({matched}/{expected}) @ {audit['pending_prices']}")
            if dynamic_sl and not self._has_stop_sl_near(dynamic_sl):
                close_side = "SHORT" if self.current_side == "LONG" else "LONG"
                binance_client.place_stop_market_order(close_side, dynamic_sl, symbol=self.symbol)
            return matched, audit["pending_prices"], expected, True

        logger.warning(
            f"⚠️ 增量补挂仍不足 ({matched}/{expected}) {audit['issues']}，升级核武级重挂"
        )
        audit = self._nuclear_realign_tp(live_qty, entry, dynamic_sl=dynamic_sl, rounds=3)
        self._maintain_hard_shield(live_qty, None, force=True, radar_sl=dynamic_sl)
        return audit["matched_full"], audit["pending_prices"], expected, True

    def _wait_tp_hung(self, tp_pxs, live_qty=None, retries=5, delay=0.8):
        expected = self._expected_tp_count(tp_pxs)
        matched, pending = 0, []
        for _ in range(retries):
            if live_qty is not None and live_qty > 0:
                audit = self._audit_tp_levels(live_qty)
                matched = audit["matched_full"]
                pending = audit["pending_prices"]
            else:
                matched, pending = self._count_matched_tp_orders(tp_pxs)
            if expected == 0 or matched >= expected:
                return matched, pending
            time.sleep(delay)
        return matched, pending

    def _wait_defense_settled(self, live_qty, dynamic_sl=None, retries=8, delay=0.75):
        """给撤单/重挂留 REST 同步窗口，避免接管未完成时误报"""
        sl = dynamic_sl if dynamic_sl is not None else self._resolve_defense_stop_for_audit()
        last = self._audit_tp_levels(live_qty)
        for i in range(retries):
            if not self._defense_needs_immediate_fix(last) and self._defenses_fully_ok(live_qty, sl):
                return last
            if i + 1 < retries:
                time.sleep(delay)
                last = self._audit_tp_levels(live_qty)
        return last

    def _has_stop_sl_near(self, sl_price, tolerance=2.0, exclude_shield=True):
        target = round(float(sl_price), 2)
        shield_prices = self._shield_tier_prices() if exclude_shield else []
        for o in binance_client.get_open_orders(self.symbol):
            order_type = str(o.get("type") or o.get("orderType") or "").upper()
            if order_type not in ("STOP_MARKET", "STOP"):
                continue
            if exclude_shield and shield_prices and self._is_shield_stop_order(o, shield_prices):
                continue
            for key in ("stopPrice", "triggerPrice", "activatePrice"):
                val = o.get(key)
                if val is None or str(val).strip() in ("", "0"):
                    continue
                try:
                    if abs(round(float(val), 2) - target) <= tolerance:
                        return True
                except (TypeError, ValueError):
                    continue
        return False

    def _has_tp_limit_at_price(self, price, tolerance=2.0):
        if price <= 0:
            return False
        for o in self._collect_tp_limit_orders():
            if abs(o["price"] - price) <= tolerance:
                return True
        return False

    def _detect_tp_fills_from_trades(self, old_qty, new_qty, initial=None, lookback_ms=180000):
        """用成交历史核对：平仓方向成交价贴近某档 TP → 判为止盈成交"""
        reduced = round(float(old_qty) - float(new_qty), 3)
        if reduced <= 0.0005 or not self.current_side:
            return []
        initial = float(initial or self.initial_qty or old_qty or 0)
        if initial <= 0:
            return []
        close_side = "SELL" if self.current_side == "LONG" else "BUY"
        trades = binance_client.get_recent_user_trades(self.symbol, limit=40)
        if not trades:
            return []
        now_ms = int(time.time() * 1000)
        recent = []
        for t in trades:
            if str(t.get("side") or "").upper() != close_side:
                continue
            try:
                t_ms = int(t.get("time") or 0)
            except (TypeError, ValueError):
                continue
            if t_ms and now_ms - t_ms > lookback_ms:
                continue
            try:
                px = float(t.get("price") or 0)
                qty = float(t.get("qty") or 0)
            except (TypeError, ValueError):
                continue
            if px <= 0 or qty <= 0:
                continue
            recent.append({"price": px, "qty": qty, "time": t_ms})
        if not recent:
            return []

        consumed = set(getattr(self, "tp_levels_consumed", []) or [])
        fills = []
        for sl in sorted(self._tp_slices_for_initial(initial), key=lambda x: x["level"]):
            if sl["level"] in consumed or sl["price"] <= 0 or sl["qty"] <= 0.0005:
                continue
            px_tol = max(1.5, float(sl["price"]) * 0.0012)
            matched = sum(
                r["qty"] for r in recent
                if abs(r["price"] - sl["price"]) <= px_tol
            )
            qty_tol = max(0.003, sl["qty"] * TP_SLICE_MATCH_TOL_PCT)
            if matched < sl["qty"] - qty_tol:
                continue
            if abs(reduced - sl["qty"]) > max(qty_tol, reduced * 0.25) and matched + 0.001 < reduced:
                # 多档同时成交时允许 matched≈reduced
                if abs(matched - reduced) > max(0.005, reduced * 0.15):
                    continue
            fills.append({
                "level": sl["level"],
                "price": sl["price"],
                "qty": round(min(sl["qty"], reduced), 3),
                "source": "trades",
            })
            break
        if fills:
            logger.info(
                f"🎯 成交历史核实 TP{fills[0]['level']} @ {fills[0]['price']:.2f} "
                f"(减仓 {reduced} ETH)"
            )
        return fills

    def _detect_tp_fills_by_order_disappear(self, old_qty, new_qty, initial=None):
        """盘口某档限价 TP 消失 + 减仓量匹配 → 止盈成交"""
        reduced = round(float(old_qty) - float(new_qty), 3)
        if reduced <= 0.0005:
            return []
        initial = float(
            getattr(self, "_open_settled_qty", 0)
            or initial
            or self.initial_qty
            or old_qty
            or 0
        )
        noise = max(0.003, initial * TP_FILL_NOISE_VS_OPEN_PCT)
        if reduced < noise:
            return []
        consumed = set(getattr(self, "tp_levels_consumed", []) or [])
        curr_px = float(binance_client.get_current_price(self.symbol) or 0)
        for sl in sorted(self._tp_slices_for_initial(initial), key=lambda x: x["level"]):
            if sl["level"] in consumed or sl["price"] <= 0 or sl["qty"] <= 0.0005:
                continue
            if self._has_tp_limit_at_price(sl["price"]):
                continue
            # TP1 消失也必须价到区域，避免核武撤单窗口误判
            if int(sl["level"]) == 1 and not self._price_reached_tp1_zone(
                curr_px, sl["price"]
            ):
                continue
            tol = max(0.003, float(sl["qty"]) * TP_SLICE_MATCH_TOL_PCT)
            if abs(reduced - sl["qty"]) <= tol:
                logger.info(
                    f"🎯 盘口 TP{sl['level']} @{sl['price']:.2f} 已消失且减仓匹配 "
                    f"→ 判止盈成交 ({reduced} ETH)"
                )
                return [{
                    "level": sl["level"],
                    "price": sl["price"],
                    "qty": round(sl["qty"], 3),
                    "source": "order_gone",
                }]
        return []

    def _tp_baseline_qty(self, fallback=0.0):
        """开仓核实锚定数量：禁止用偏高 target 造成「已减仓 5%」幻觉"""
        settled = float(getattr(self, "_open_settled_qty", 0) or 0)
        initial = float(self.initial_qty or 0)
        fb = float(fallback or 0)
        if settled > 0:
            return settled
        if initial > 0:
            return initial
        return fb

    def _detect_tp_fills(self, old_qty, new_qty, curr_px=0.0):
        """
        识别 TP 成交：仅成交历史 / 盘口消失+价到+量匹配。
        禁止纯 sequential/reduction 软推断（R4 TP1=5% 会与开仓微差撞车）。
        """
        if new_qty >= old_qty - 0.0005:
            return []
        self._ensure_tv_tps_for_fill_detect()
        baseline = self._tp_baseline_qty(old_qty)
        # 真加仓后抬高基线；禁止把 baseline 抬到高于 old 而无实盘加仓证据
        if float(old_qty or 0) > baseline + 0.001:
            baseline = float(old_qty)
            self._open_settled_qty = baseline
            self.initial_qty = baseline
            self._save_state()
        initial = baseline

        trade_fills = self._detect_tp_fills_from_trades(old_qty, new_qty, initial)
        if trade_fills:
            return trade_fills
        gone_fills = self._detect_tp_fills_by_order_disappear(old_qty, new_qty, initial)
        if gone_fills:
            return gone_fills

        # 软推断仅记日志，不作为成交证据（避免雷达误启）
        soft = self._infer_tp_consumed_sequential(initial, new_qty, curr_px)
        if soft:
            still = []
            for lv in soft:
                px = self.tv_tps[lv - 1] if 0 <= lv - 1 < len(self.tv_tps) else 0
                if px > 0 and self._has_tp_limit_at_price(px):
                    still.append(lv)
            logger.info(
                f"🧮 软推断 TP{soft} 已忽略作成交证据 "
                f"(基线 {initial}→{new_qty} | 仍挂限价档={still or '无'} | "
                f"需成交史或「价到TP+限价消失+量匹配」)"
            )
        return []

    def _detect_tp_fills_by_reduction(self, old_qty, new_qty, curr_px=0.0, initial=None):
        """已废弃为雷达证据；保留空实现以免旧调用崩。"""
        return []

    def _cancel_tp_orders_at_levels(self, levels):
        """撤掉已成交档位的残留限价单（防 REST 延迟导致误判未成交）"""
        cancelled = 0
        for level in levels:
            idx = int(level) - 1
            if idx < 0 or idx >= len(self.tv_tps):
                continue
            px = self.tv_tps[idx]
            if px <= 0:
                continue
            for o in self._collect_tp_limit_orders():
                if abs(o["price"] - px) <= 1.0 and o.get("orderId"):
                    binance_client.cancel_order(self.symbol, order=o)
                    cancelled += 1
                    time.sleep(0.2)
        if cancelled:
            logger.info(f"🧹 撤净已成交 TP 残留单 {cancelled} 笔")
        return cancelled

    def _cancel_stale_tp_beyond_radar(self, radar_sl, live_qty=None, tolerance=1.5):
        """
        雷达止损已越过 TP1/TP2 → 撤销无意义的限价止盈（防孤儿单干扰）。
        多头：雷达价 ≥ TP 价；空头：雷达价 ≤ TP 价。
        """
        radar_sl = float(radar_sl or 0)
        if radar_sl <= 0 or not self.current_side:
            return 0
        live_qty = float(live_qty if live_qty is not None else self.watched_qty or 0)
        cancelled = 0
        stale_levels = []
        for level in (1, 2):
            idx = level - 1
            if idx >= len(self.tv_tps) or float(self.tv_tps[idx] or 0) <= 0:
                continue
            tp_px = float(self.tv_tps[idx])
            stale = False
            if self.current_side == "LONG" and radar_sl >= tp_px - tolerance:
                stale = True
            elif self.current_side == "SHORT" and radar_sl <= tp_px + tolerance:
                stale = True
            if not stale:
                continue
            for o in self._collect_tp_limit_orders():
                if abs(o["price"] - tp_px) > tolerance:
                    continue
                oid = o.get("orderId")
                if oid:
                    res = binance_client.cancel_order(self.symbol, order_id=oid)
                    self._log_exchange_api(
                        f"撤孤儿TP{level}",
                        f"雷达SL={radar_sl:.2f} 越过 TP{level}@{tp_px:.2f}",
                        res,
                    )
                    cancelled += 1
                    time.sleep(0.15)
            stale_levels.append(level)
        if cancelled:
            logger.warning(
                f"🧹 雷达越过 TP{stale_levels} → 撤孤儿限价止盈 {cancelled} 笔 "
                f"(雷达SL={radar_sl:.2f})"
            )
            dingtalk.report_system_alert(
                "雷达孤儿TP清理",
                f"{self.current_side} {live_qty} ETH | 雷达SL `{radar_sl:.2f}` | "
                f"已撤 TP{stale_levels} 限价 {cancelled} 笔 | "
                f"等止盈已无意义，改由雷达锁利",
            )
        return cancelled

    def _merge_wider_vps_hard_sl(self, old_sl, new_sl):
        """加仓合并：宽止损取更宽者（多头更低、空头更高）"""
        old_sl = float(old_sl or 0)
        new_sl = float(new_sl or 0)
        if old_sl <= 0:
            return new_sl
        if new_sl <= 0:
            return old_sl
        if self.current_side == "LONG":
            return min(old_sl, new_sl)
        if self.current_side == "SHORT":
            return max(old_sl, new_sl)
        return new_sl

    def _sweep_orphan_reverse_after_flat(self, prev_side=None, reason=""):
        """全平后复核：残留限价反向成交 → 反向蚂蚁仓扫尾"""
        prev_side = (prev_side or self.current_side or "").upper()
        time.sleep(1.0)
        pos = self._get_active_position()
        if not pos or float(pos.get("size", 0) or 0) <= 0:
            return False
        amt = float(pos["size"])
        side = pos["side"]
        if prev_side and side != prev_side:
            logger.warning(
                f"🐜 孤儿单反向成交: 原{prev_side} → 现{side} {amt} ETH | {reason}"
            )
            dingtalk.report_system_alert(
                "孤儿单反向蚂蚁仓",
                f"全平后检测到反向持仓 {side} {amt} ETH @ {pos['entry_price']:.2f} | "
                f"疑似残留 TP 成交 → 立即扫尾",
            )
            close_side = "SELL" if side == "LONG" else "BUY"
            res = binance_client.place_market_order(close_side, amt, symbol=self.symbol, reduce_only=True)
            self._log_exchange_api("孤儿反向扫尾", f"{close_side} {amt} ETH", res)
            time.sleep(1.0)
            self._purge_all_defense_orders_on_flat("孤儿反向扫尾后撤单")
            return self._verify_flat()
        if self._is_dust_qty(amt):
            self._sweep_dust_and_finalize(reason or "全平后蚂蚁仓扫尾")
            return True
        return False

    def _cancel_mismatched_remaining_tps(self, live_qty, tolerance=1.0, qty_tol=0.005):
        """撤掉剩余档数量与当前仓位比例不符的旧单（部分止盈后常见）"""
        cancelled = 0
        for lv in self._expected_tp_levels(live_qty):
            px, target_q = lv["price"], lv["qty"]
            if px <= 0 or target_q <= 0:
                continue
            at_px = [
                o for o in self._collect_tp_limit_orders()
                if abs(o["price"] - px) <= tolerance
            ]
            for o in at_px:
                if abs(o["qty"] - target_q) > qty_tol and o.get("orderId"):
                    binance_client.cancel_order(self.symbol, order=o)
                    cancelled += 1
                    time.sleep(0.2)
                    logger.info(
                        f"🔧 撤偏差 TP{lv['level']} @{px:.2f}: "
                        f"盘口 {o['qty']} → 应 {target_q} ETH"
                    )
        return cancelled

    def _detect_stale_consumed_tp_levels(self, initial_qty, live_qty, curr_px=0.0):
        """开单 vs 现仓 → 顺序推断已成交档；撤已成交档残留限价"""
        initial_qty = float(initial_qty or 0)
        live_qty = float(live_qty or 0)
        if initial_qty <= 0 or live_qty <= 0:
            return []
        consumed = self._sanitize_tp_consumed(initial_qty, live_qty, curr_px)
        for lv in consumed:
            idx = int(lv) - 1
            px = self.tv_tps[idx] if 0 <= idx < len(self.tv_tps) else 0
            if px > 0 and self._has_tp_limit_at_price(px):
                logger.warning(
                    f"⚠️ 多余 TP{lv} @{px:.2f} "
                    f"(开单 {initial_qty} → 现仓 {live_qty}，该档应已成交)"
                )
        return consumed

    def _repair_partial_tp_on_recover(self, live_qty, entry, initial_qty, curr_px=0.0):
        """
        重启修复：开单头寸 + 部分止盈 → 撤多余已成交档，现仓=TP2+TP3 重分。
        """
        live_qty = self._resolve_live_qty(live_qty)
        initial_qty = float(initial_qty or live_qty or 0)
        actions = []

        self._sanitize_tp_consumed(initial_qty, live_qty, curr_px)
        consumed = getattr(self, "tp_levels_consumed", []) or []
        if consumed and initial_qty <= live_qty + 0.001:
            inferred = self._infer_tp_consumed_sequential(initial_qty, live_qty, curr_px)
            if not inferred:
                logger.warning(
                    f"跳过部分止盈修复：无减仓证据，清除 TP{consumed}"
                )
                self.tp_levels_consumed = []
                self._save_state()
                return {"repaired": False, "actions": actions, "result": None, "consumed": []}

        stale_levels = self._detect_stale_consumed_tp_levels(
            initial_qty, live_qty, curr_px,
        )
        if stale_levels:
            prev = set(getattr(self, "tp_levels_consumed", []) or [])
            if stale_levels != sorted(prev):
                self.tp_levels_consumed = stale_levels
                self._save_state()
            actions.append(
                f"已成交档 TP{stale_levels} | 开单 {initial_qty} → 现仓 {live_qty} ETH"
            )

        consumed = getattr(self, "tp_levels_consumed", []) or []
        if not consumed and initial_qty > live_qty + 0.001:
            inferred = self._infer_tp_consumed_sequential(
                initial_qty, live_qty, curr_px,
            )
            if inferred:
                self.tp_levels_consumed = inferred
                self._save_state()
                consumed = inferred
                actions.append(f"推断已成交 TP{inferred}")
        if not consumed:
            return {"repaired": False, "actions": actions, "result": None, "consumed": []}

        # 有现仓且仍有未成交档 → 必须 repair（含仅余 TP3 全仓 0.405 的情况）
        if live_qty > DUST_QTY_ETH and self._expected_tp_count() == 0:
            self._sanitize_tp_consumed(initial_qty, live_qty, curr_px)
            consumed = getattr(self, "tp_levels_consumed", []) or []
            if self._expected_tp_count() == 0 and live_qty > DUST_QTY_ETH:
                logger.warning(
                    f"⚠️ 仍有 {live_qty} ETH 但无待挂 TP 档 → 强制挂最后一档 TP3"
                )
                self.tp_levels_consumed = [1, 2]
                self._save_state()

        n_stale = self._cancel_tp_orders_at_levels(consumed)
        if n_stale:
            actions.append(f"撤多余已成交档 {n_stale} 笔")

        n_mismatch = self._cancel_mismatched_remaining_tps(live_qty)
        if n_mismatch:
            actions.append(f"撤偏差 TP2/TP3 {n_mismatch} 笔")

        time.sleep(0.4)

        sl_to_pass = self._radar_sl_to_pass()
        if sl_to_pass is None and curr_px and curr_px > 0:
            top_level = max(consumed)
            px = self.tv_tps[top_level - 1] if top_level <= len(self.tv_tps) else 0
            if px > 0:
                sl_to_pass = self._advance_radar_on_tp_fill(
                    [{"level": top_level, "price": px, "qty": 0}],
                    curr_px, live_qty,
                )

        result = self._realign_remaining_tps_after_fill(
            live_qty, dynamic_sl=sl_to_pass, reason="重启部分止盈修复",
        )
        audit = result.get("audit") or {}
        rem_levels = self._expected_tp_levels(live_qty)
        rem_sum = round(sum(lv["qty"] for lv in rem_levels), 3)
        actions.append(
            f"剩余 TP 重分 {rem_sum}/{live_qty} ETH | "
            f"对齐 {audit.get('matched_full', 0)}/{audit.get('expected', 0)} 档"
        )
        return {
            "repaired": True,
            "actions": actions,
            "result": result,
            "consumed": consumed,
            "initial_qty": initial_qty,
            "rem_sum": rem_sum,
        }

    def _realign_remaining_tps_after_fill(self, live_qty, dynamic_sl=None, reason=""):
        """
        TP 成交后：只维护剩余 TP2/TP3，不重挂已成交 TP1；同步雷达止损。
        """
        live_qty = self._resolve_live_qty(live_qty)
        if live_qty <= 0:
            audit = self._audit_tp_levels(live_qty)
            return {
                "matched": 0, "expected": 0, "pending_prices": [],
                "rebuilt": False, "audit": audit, "nuclear": False,
            }
        consumed = getattr(self, "tp_levels_consumed", []) or []
        logger.info(
            f"🎯 TP 成交后静默对齐: 剩余 {live_qty} ETH | "
            f"已成交 TP{consumed} | 只补未成交档"
        )
        self._cancel_tp_orders_at_levels(consumed)
        time.sleep(0.35)
        n_fix = self._cancel_mismatched_remaining_tps(live_qty)
        if n_fix:
            logger.info(f"🔧 TP 成交对齐：撤偏差剩余档 {n_fix} 笔")
            time.sleep(0.35)
        placed = self._patch_missing_tp_levels(live_qty)
        time.sleep(0.5)
        audit = self._audit_tp_levels(live_qty)
        if dynamic_sl and not self._has_stop_sl_near(dynamic_sl):
            self._ensure_radar_sl(dynamic_sl, live_qty)
        if placed == 0 and self._tp_audit_ok(audit):
            logger.info(
                f"✅ TP 成交后盘口已齐 ({audit['matched_full']}/{audit['expected']})，"
                f"未重挂已成交档"
            )
        elif not self._tp_audit_ok(audit):
            logger.warning(
                f"⚠️ TP 成交后仍不齐 → 增量修复 | {self._format_audit_summary(audit)}"
            )
            repaired, _ = self._surgical_repair_tp_defenses(live_qty, self.watched_entry)
            audit = repaired
        self._mark_defense_align_ok()
        return {
            "matched": audit["matched_full"],
            "expected": audit["expected"],
            "pending_prices": audit["pending_prices"],
            "rebuilt": placed > 0,
            "audit": audit,
            "nuclear": False,
        }

    def _detect_shield_fills(self, old_qty, new_qty, curr_px):
        if not getattr(self, "shield_active", False):
            return []
        if new_qty >= old_qty - 0.0005:
            return []
        if self._detect_tp_fills(old_qty, new_qty, curr_px):
            return []
        stop_px = self._shield_stop_price()
        if not stop_px:
            return []
        if curr_px > 0 and self._should_radar_trail(curr_px):
            return []
        if self._has_shield_stop_at_price(stop_px):
            return []
        if curr_px > 0:
            px_tol = max(3.0, stop_px * 0.002)
            if self.current_side == "LONG" and curr_px > stop_px + px_tol:
                return []
            if self.current_side == "SHORT" and curr_px < stop_px - px_tol:
                return []
        fill_qty = round(old_qty - new_qty, 3)
        if fill_qty <= 0.0005:
            return []
        return [{
            "tier": 1,
            "pct": SHIELD_HARD_STOP_PCT,
            "price": stop_px,
            "qty": fill_qty,
        }]

    def _classify_position_change(self, old_qty, new_qty, curr_px):
        if new_qty > old_qty + 0.0005:
            return {"kind": "add", "tp_fills": [], "shield_fills": []}
        if new_qty >= old_qty - 0.0005:
            return {"kind": "unchanged", "tp_fills": [], "shield_fills": []}
        # 开仓/防线核武重建时 TP 会短暂消失，禁止当成 TP 成交启雷达
        if getattr(self, "_open_in_progress", False) or getattr(
            self, "_defense_align_in_progress", False
        ):
            return {"kind": "reduce_unknown", "tp_fills": [], "shield_fills": []}
        self._ensure_tv_tps_for_fill_detect()
        # 禁止抬高 initial 超过实盘观测：会制造 R4「减仓≈TP1=5%」伪影
        peak = max(
            float(getattr(self, "_open_settled_qty", 0) or 0),
            float(self.initial_qty or 0),
            float(old_qty or 0),
        )
        settled = float(getattr(self, "_open_settled_qty", 0) or 0)
        if settled <= 0 and peak > float(self.initial_qty or 0) + 0.0005:
            self.initial_qty = peak
            self._save_state()
        tp_fills = self._detect_tp_fills(old_qty, new_qty, curr_px)
        shield_fills = self._detect_shield_fills(old_qty, new_qty, curr_px)
        favorable = (
            self._is_radar_active()
            or (curr_px > 0 and self._should_radar_trail(curr_px))
        )
        if tp_fills and shield_fills and favorable:
            shield_fills = []
        if tp_fills:
            return {"kind": "tp_fill", "tp_fills": tp_fills, "shield_fills": []}
        if shield_fills:
            return {"kind": "shield_fill", "tp_fills": [], "shield_fills": shield_fills}
        return {"kind": "reduce_unknown", "tp_fills": [], "shield_fills": []}

    def _price_reached_tp1_zone(self, curr_px=0.0, tp1_px=None):
        """现价或 best 是否已触及/越过 TP1（主判断；容差按品种相对价）。"""
        tp1_px = float(
            tp1_px
            if tp1_px is not None
            else ((self.tv_tps[0] if self.tv_tps else 0) or 0)
        )
        entry = float(self.watched_entry or 0)
        if tp1_px <= 0 or entry <= 0:
            return False
        px_tol = max(float(getattr(self, "qty_step", 0.001) or 0.001), tp1_px * TP1_PRICE_ZONE_PCT)
        for px in (
            float(curr_px or 0),
            float(self.best_price or 0),
        ):
            if px <= 0:
                continue
            if self.current_side == "LONG" and px >= tp1_px - px_tol:
                return True
            if self.current_side == "SHORT" and px <= tp1_px + px_tol:
                return True
        return False

    def _tp_fill_ok_to_arm_radar(self, tp_fills, curr_px, old_qty, new_qty):
        """
        三重对账武装雷达（ETH/XAU 同逻辑）：
        ① 主：现价/best 达 TP1
        ② 辅：TP1 限价已消失 + 来源 trades/order_gone
        ③ 参：相对开仓基线减仓量匹配 TP1 切片（过滤微漂）
        """
        if getattr(self, "_open_in_progress", False) or getattr(
            self, "_defense_align_in_progress", False
        ):
            return False
        fills = list(tp_fills or [])
        if not fills:
            return False
        if not any(int(f.get("level") or 0) == 1 for f in fills):
            return False
        f1 = next(f for f in fills if int(f.get("level") or 0) == 1)
        src = str(f1.get("source") or "")
        tp1_px = float(
            f1.get("price")
            or ((self.tv_tps[0] if self.tv_tps else 0) or 0)
        )
        # ② 订单辅判
        if tp1_px > 0 and self._has_tp_limit_at_price(tp1_px):
            logger.warning(
                f"📡 [{self.symbol}] 三角对账拒绝：TP1 限价仍在盘口 @{tp1_px:.2f}"
            )
            return False
        if src not in ("trades", "order_gone"):
            logger.warning(
                f"📡 [{self.symbol}] 三角对账拒绝：来源={src or '?'} 非 trades/order_gone"
            )
            return False
        # ① 价格主判
        if not self._price_reached_tp1_zone(curr_px, tp1_px):
            logger.warning(
                f"📡 [{self.symbol}] 三角对账拒绝：现价/best 未达 TP1 区 "
                f"(px={float(curr_px or 0):.2f} tp1={tp1_px:.2f})"
            )
            return False
        # ③ 减仓参考
        if not self._tp1_qty_matches_baseline(new_qty, old_qty=old_qty):
            logger.warning(
                f"📡 [{self.symbol}] 三角对账拒绝：减仓量不匹配开仓基线 "
                f"({old_qty}→{new_qty} {self._unit()})"
            )
            return False
        return True

    def _tp1_qty_matches_baseline(self, live_qty, old_qty=None):
        baseline = self._tp_baseline_qty(old_qty or live_qty)
        live = float(live_qty or 0)
        step = float(getattr(self, "qty_step", 0.001) or 0.001)
        if baseline <= live + step:
            return False
        reduced = round(baseline - live, 6)
        noise = self._qty_noise_floor(baseline)
        if reduced < noise:
            return False
        slices = {
            sl["level"]: sl for sl in self._tp_slices_for_initial(baseline)
        }
        tp1 = slices.get(1)
        if not tp1 or float(tp1.get("qty") or 0) <= step * 0.5:
            return False
        tol = max(step, float(tp1["qty"]) * TP_SLICE_MATCH_TOL_PCT)
        return reduced + step * 0.5 >= float(tp1["qty"]) - tol

    def _advance_radar_on_tp_fill(self, tp_fills, curr_px, live_qty):
        if not tp_fills:
            return None
        if not self._tp_fill_ok_to_arm_radar(
            tp_fills, curr_px, float(self.initial_qty or live_qty), live_qty,
        ) and not getattr(self, "_radar_handoff_done", False):
            logger.warning(
                f"📡 [{self.symbol}] 雷达推进跳过：TP1 三重证据不足，保持阶段0宽硬止损"
            )
            return None
        for f in tp_fills:
            px = f["price"]
            if self.current_side == "LONG":
                self.best_price = max(self.best_price, px, curr_px or 0)
            else:
                bp = curr_px if curr_px and curr_px > 0 else px
                self.best_price = min(self.best_price, px, bp)
        max_level = max(f["level"] for f in tp_fills)
        tp3 = self.tv_tps[2] if len(self.tv_tps) > 2 else 0.0
        curr_px_safe = curr_px or binance_client.get_current_price(self.symbol) or 0
        # 仅计算理想保本线，不在此锁存 armed / 不挂贴市单
        stage_sl = self._compute_radar_sl_for_stage(1, curr_px_safe)
        if stage_sl:
            self.current_sl = stage_sl
        note = f"TP{max_level}成交"
        if max_level >= 2 and tp3 > 0:
            note += f" → 待交棒后向 TP3({tp3:.2f}) 收紧"
        elif max_level == 1:
            note += " → 待安全交棒（理想保本须距现价足够）"
        logger.info(
            f"📈 [{self.symbol}] 雷达推进预备 {note} | "
            f"理想SL={self.current_sl} | best={self.best_price:.2f}"
        )
        self._save_state()
        return self.current_sl if self.current_sl else None

    def _tp1_triad_ok(self, live_qty=None, curr_px=0.0, require_fresh=False):
        """
        TP1 三重验证（ETH/XAU/全交易所统一）：
        ① 价格主判：现价/best 达 TP1 区
        ② 订单辅判：账本已消费 TP1 + 盘口无 TP1 限价
        ③ 减仓参考：相对开仓基线明显减仓且匹配 TP1 切片
        """
        if getattr(self, "_open_in_progress", False) or getattr(
            self, "_defense_align_in_progress", False
        ):
            return False
        if (not require_fresh) and getattr(self, "_radar_handoff_done", False):
            return True

        live_qty = float(live_qty if live_qty is not None else self.watched_qty or 0)
        tp1_px = float(self.tv_tps[0] or 0) if self.tv_tps else 0.0

        # ② 订单
        if tp1_px > 0 and self._has_tp_limit_at_price(tp1_px):
            return False
        if not self._tp_filled_verified(1, live_qty, curr_px):
            return False
        # ③ 减仓
        if not self._tp1_qty_matches_baseline(live_qty):
            return False
        # ① 价格（WS hint 仅辅助，不能单独替代）
        price_ok = self._price_reached_tp1_zone(curr_px, tp1_px)
        if not price_ok and not getattr(self, "_ws_tp1_fill_hint", False):
            return False
        if not price_ok and getattr(self, "_ws_tp1_fill_hint", False):
            # WS 提示必须同时具备订单+减仓（上面已过），才允许
            logger.info(
                f"📡 [{self.symbol}] 三重验证：价格未触区但 WS 成交提示+"
                f"订单/减仓已齐 → 允许"
            )
        return True

    def _tp1_filled_verified(self, live_qty=None, curr_px=0.0):
        """兼容旧名 → 三重验证（交棒完成后短路）。"""
        return self._tp1_triad_ok(live_qty, curr_px, require_fresh=False)

    def _handle_smart_qty_change(self, old_qty, new_qty, curr_px):
        """按减仓原因分流：TP成交→雷达推进；防护盾成交→保留剩余档位；其他→通用对齐"""
        change = self._classify_position_change(old_qty, new_qty, curr_px)
        kind = change["kind"]
        result = None
        sl_to_pass = None

        if kind == "add":
            logger.info(f"🔄 [智慧大脑] 加仓 {old_qty} ➔ {new_qty}")
            sl_to_pass = self._radar_sl_to_pass()
            result = self._smart_realign_defenses(
                new_qty, self.watched_entry, dynamic_sl=sl_to_pass,
                reason="加仓后防线对齐",
            )
            if self._should_activate_shield(curr_px):
                self._maintain_hard_shield(new_qty, curr_px, force=True)
        elif kind == "tp_fill":
            levels = ",".join(f"TP{f['level']}" for f in change["tp_fills"])
            evidence_ok = self._tp_fill_ok_to_arm_radar(
                change["tp_fills"], curr_px, old_qty, new_qty,
            )
            if not evidence_ok:
                logger.warning(
                    f"🎯 [智慧大脑] {levels} 疑似伪成交"
                    f"（价未达TP1 / 开仓重建竞态）→ 不标记·不启雷达 | "
                    f"{old_qty}→{new_qty}"
                )
                dingtalk.report_system_alert(
                    f"雷达拒启·伪TP1拦截 [{self.symbol}]",
                    f"{self.current_side} {old_qty}→{new_qty} {self._unit()} | {levels} | "
                    f"现价 {float(curr_px or 0):.2f} | "
                    f"规则：价格+订单+减仓三重验证前不激活雷达",
                )
                # 内联未知减仓：禁止递归（否则会再次判成 tp_fill）
                pct = abs(new_qty - old_qty) / old_qty if old_qty > 0 else 1.0
                logger.info(
                    f"🔄 [智慧大脑] 伪TP拦截后按人工/异动对齐 "
                    f"{old_qty}→{new_qty} ({pct:.1%})"
                )
                result = self._smart_realign_defenses(
                    new_qty, self.watched_entry, dynamic_sl=None,
                    reason="伪TP拦截·保宽硬止损",
                )
                if self._should_activate_shield(curr_px) or getattr(
                    self, "shield_active", False
                ):
                    self._maintain_hard_shield(new_qty, curr_px, force=True)
                change = {"kind": "reduce_unknown", "tp_fills": [], "shield_fills": []}
                self._save_state()
                return change, result
            logger.info(
                f"🎯 [智慧大脑] {levels} 成交减仓 {old_qty} ➔ {new_qty} → 雷达推进 + 守剩余TP"
            )
            self._mark_tp_levels_consumed([f["level"] for f in change["tp_fills"]])
            # 禁止在交棒核实前就锁存 armed（否则会跳过三重验证）
            curr_px_safe = curr_px or binance_client.get_current_price(self.symbol) or 0
            sl_to_pass = self._advance_radar_on_tp_fill(
                change["tp_fills"], curr_px, new_qty,
            )
            result = self._realign_remaining_tps_after_fill(
                new_qty, dynamic_sl=None,
                reason=f"{levels} 成交静默对齐",
            )
            # 三重已过 → 尝试安全交棒；失败则保持宽硬止损
            handed = self._perform_radar_handoff(
                new_qty, curr_px_safe, reason=f"{levels} 成交雷达接管",
            )
            if handed:
                sl_to_pass = self._radar_sl_to_pass()
            elif sl_to_pass:
                logger.info(
                    f"📡 [{self.symbol}] TP成交但交棒未完成 → 保留宽硬止损，"
                    f"不挂贴市保本线"
                )
                self._maintain_hard_shield(new_qty, curr_px_safe, force=True, radar_sl=None)
        elif kind == "shield_fill":
            f = change["shield_fills"][0]
            logger.warning(
                f"🛡️ [智慧大脑] TV硬止损成交 "
                f"{old_qty} ➔ {new_qty} @ {f['price']:.2f}"
            )
            if new_qty <= 0.0005 or self._is_dust_qty(new_qty):
                flat_meta = self._build_close_meta(
                    "CLOSE_STOPLOSS",
                    self.current_side,
                    self._estimate_pnl_pct(curr_px),
                    "触碰硬止损平仓（TV tv_sl）",
                )
                flat_meta["close_type"] = CLOSE_TYPE_VPS_SHIELD
                self._disarm_shield("TV硬止损全平", notify=False)
                self._handle_manual_flat_detected(
                    flat_meta["tv_reason"],
                    close_meta=flat_meta,
                    curr_px=curr_px,
                )
                self._save_state()
                return change, None
            self._disarm_shield("TV硬止损成交", notify=True)
            self.shield_tiers_consumed = []
            result = self._smart_realign_defenses(
                new_qty, self.watched_entry, dynamic_sl=None,
                reason=f"硬止损成交后 TP 重算",
            )
            self._call_dingtalk(
                dingtalk.report_shield_tier_fill,
                side=self.current_side,
                tier_pct=f["pct"],
                tier_price=f["price"],
                filled_qty=f["qty"],
                remain_qty=new_qty,
                entry_px=self.watched_entry,
                remaining_tiers=[],
                verify_note=(
                    f"硬止损 -{f['pct']:.0%} @ {f['price']:.2f} 成交 | "
                    f"剩余 {new_qty} ETH"
                ),
            )
        else:
            pct = abs(new_qty - old_qty) / old_qty if old_qty > 0 else 1.0
            # 再查一次成交历史（放宽窗口），优先改判为 TP 成交
            self._ensure_tv_tps_for_fill_detect()
            retry_fills = self._detect_tp_fills(old_qty, new_qty, curr_px)
            if not retry_fills:
                peak = max(float(self.initial_qty or 0), float(old_qty or 0))
                retry_fills = self._detect_tp_fills_from_trades(
                    old_qty, new_qty, initial=peak, lookback_ms=300000,
                )
            if retry_fills:
                if self._tp_fill_ok_to_arm_radar(
                    retry_fills, curr_px, old_qty, new_qty,
                ):
                    change = {
                        "kind": "tp_fill",
                        "tp_fills": retry_fills,
                        "shield_fills": [],
                    }
                    self._save_state()
                    return self._handle_smart_qty_change(old_qty, new_qty, curr_px)
                logger.warning(
                    f"🎯 [智慧大脑] 重试判 TP 仍缺证据 "
                    f"{[f.get('level') for f in retry_fills]} → 不启雷达"
                )
            action_msg = (
                "手动加仓" if new_qty > old_qty
                else "手动减仓（成交史未匹配TP）"
            )
            logger.info(
                f"🔄 [智慧大脑] 仓位变化 {old_qty} ➔ {new_qty} ({pct:.1%})，"
                f"成交史未核实为止盈 → 通用重对齐"
            )
            self._bump_best_on_tp_fill(old_qty, new_qty, curr_px)
            self._sync_radar_sl_from_best(curr_px)
            sl_to_pass = self._radar_sl_to_pass()
            result = self._smart_realign_defenses(
                new_qty, self.watched_entry, dynamic_sl=sl_to_pass,
                reason=f"人工异动: {action_msg}",
            )
            if self._should_disarm_shield_for_favorable(curr_px):
                self._perform_radar_handoff(
                    new_qty, curr_px, reason="TP后切换雷达保本",
                )
            elif self._should_activate_shield(curr_px) or getattr(self, "shield_active", False):
                self._maintain_hard_shield(new_qty, curr_px, force=True)

        self._save_state()
        return change, result

    def _report_qty_change_dingtalk(self, old_qty, new_qty, realign_result, change=None):
        verified_pos = self._wait_verify(
            lambda: self._verify_position(self.current_side),
            retries=8,
            delay=0.5,
        )
        verified = (
            verified_pos is not None
            and abs(float(verified_pos.get("size", 0)) - new_qty) < 0.001
        )
        entry_px = (
            float(verified_pos.get("entry_price", self.watched_entry))
            if verified_pos else self.watched_entry
        )
        verify_note = (
            f"核实 {new_qty} ETH @ {entry_px:.2f} | "
            f"止盈 {realign_result['matched']}/{realign_result['expected']} 档 | "
            f"{self._format_audit_summary(realign_result['audit'])}"
        )
        if not verified:
            verify_note += " | REST 同步略延迟"

        fills = []
        if change and change.get("kind") == "tp_fill":
            fills = change.get("tp_fills") or []
        if not fills:
            fills = self._detect_tp_fills(old_qty, new_qty)
        if fills:
            for fill in fills:
                self._call_dingtalk(
                    dingtalk.report_tp_fill,
                    tp_level=fill["level"],
                    tp_price=fill["price"],
                    filled_qty=fill["qty"],
                    remain_qty=new_qty,
                    entry_px=entry_px,
                    side=self.current_side or "?",
                    regime=self.regime,
                    verify_note=verify_note,
                    verified=verified,
                )
                logger.info(
                    f"📣 TP{fill['level']} 成交钉钉已推送 @ {fill['price']:.2f} "
                    f"({fill['qty']} ETH)"
                )
        else:
            action_msg = (
                "手动加仓" if new_qty > old_qty else "部分止盈吃单 / 手动减仓"
            )
            self._call_dingtalk(
                dingtalk.report_manual_position_change,
                action_type=action_msg,
                old_qty=old_qty,
                new_qty=new_qty,
                new_entry_price=entry_px,
                verify_note=verify_note,
                tp_audit=realign_result["audit"],
                verified=verified,
            )

        if realign_result["expected"] > 0 and realign_result["matched"] < realign_result["expected"]:
            dingtalk.report_system_alert(
                "人工异动后止盈未对齐",
                f"{self._format_audit_summary(realign_result['audit'])}",
            )

    def _report_radar_intervention(self, real_amt, new_sl, action_msg, sl_placed=True):
        """雷达推止损后推送钉钉：同价位冷却期内不重复播报"""
        now = time.time()
        if (
            abs(new_sl - getattr(self, "_last_radar_report_sl", 0)) < 2.0
            and now - getattr(self, "_last_radar_report_ts", 0) < RADAR_DINGTALK_COOLDOWN_SEC
        ):
            return
        verified = self._wait_verify(
            lambda: self._has_stop_sl_near(new_sl),
            retries=8,
            delay=0.5,
        )
        base_note = (
            f"止损 @ {new_sl:.2f} | 阶段{getattr(self, '_radar_stage_last', 0)} | "
            f"持仓 {real_amt} ETH | 轮询 {SENTINEL_POLL_RADAR}s"
        )
        if not sl_placed and not verified:
            logger.warning(f"雷达钉钉跳过：止损 @ {new_sl:.2f} 提交失败且盘口未核查到")
            return
        if verified:
            verify_note = base_note
        else:
            verify_note = f"{base_note} | 止损已提交，REST 同步略延迟"
            logger.info(f"雷达钉钉：止损已挂 REST 延迟，仍推送捷报 @{new_sl:.2f}")
        self._call_dingtalk(
            dingtalk.report_intervention,
            qty=real_amt,
            entry_px=self.watched_entry,
            new_sl=new_sl,
            action_msg=action_msg,
            verify_note=verify_note,
            verified=verified,
        )
        self._last_radar_report_ts = now
        self._last_radar_report_sl = new_sl

    def _wait_verify(self, checks_fn, retries=3, delay=0.6):
        for _ in range(retries):
            result = checks_fn()
            if result:
                return result
            time.sleep(delay)
        return checks_fn()

    def _resolve_live_qty(self, fallback_qty: float) -> float:
        pos = self._get_active_position()
        if pos and pos["size"] > 0:
            live = round(pos["size"], 3)
            if abs(live - fallback_qty) > 0.001:
                logger.info(f"📐 实盘数量校正: 账本 {fallback_qty} → 交易所 {live} ETH")
            return live
        return fallback_qty

    def _split_tp_quantities(self, qty: float, ratios: list) -> tuple:
        """余数吸收：qty1+qty2+qty3 == qty"""
        qty1 = round(qty * ratios[0], 3)
        qty2 = round(qty * ratios[1], 3)
        qty3 = round(qty - qty1 - qty2, 3)
        return qty1, qty2, qty3

    def handle_signal(self, payload):
        """兼容旧调用路径"""
        payload = self._enrich_tv_payload(dict(payload or {}))
        self.enqueue_signal(payload)

    def _enrich_tv_payload(self, payload):
        """v6.9.75：TV 全量 regime/atr/tp 优先，仅缺失项本地补全。"""
        action = str(payload.get("action", "")).strip().upper()
        live_px = binance_client.get_current_price(self.symbol) or self.tv_price or 0.0
        return enrich_signal_fields(
            payload,
            action,
            fetch_atr=lambda: binance_client.fetch_atr_14(self.symbol),
            fallback_regime=self.regime or 3,
            fallback_atr=self.current_atr or 30.0,
            fallback_price=live_px,
        )

    def _tv_field_source_note(self, payload):
        return format_tv_field_sources(payload or {})

    def _format_close_extra(self, close_side, pnl_pct, tv_price, regime=None, atr=None):
        parts = []
        if close_side:
            parts.append(f"TV方向 {close_side}")
        if regime:
            parts.append(f"TV档位 R{int(regime)}")
        if atr and float(atr) > 0:
            parts.append(f"TV ATR {float(atr):.2f}")
        if tv_price and float(tv_price) > 0:
            parts.append(f"TV价 {float(tv_price):.2f}")
        if pnl_pct is not None and pnl_pct != "":
            parts.append(f"TV盈亏 {self._safe_float(pnl_pct):+.2f}%")
        return (" | " + " | ".join(parts)) if parts else ""

    def _estimate_pnl_pct(self, curr_px):
        entry = float(self.watched_entry or 0)
        px = float(curr_px or 0)
        if entry <= 0 or px <= 0 or not self.current_side:
            return None
        if self.current_side == "LONG":
            return (px - entry) / entry * 100.0
        return (entry - px) / entry * 100.0

    def _build_close_meta(self, raw_action, close_side, pnl_pct, tv_reason=""):
        reason = str(tv_reason or "").strip()
        close_type = classify_tv_close(raw_action, reason, pnl_pct)
        return {
            "action": raw_action,
            "close_type": close_type,
            "side": close_side or self.current_side,
            "pnl_pct": pnl_pct,
            "tv_reason": reason,
            "tv_price": self.tv_price,
            "regime": self.regime,
            "atr": self.current_atr,
            "field_sources": getattr(self, "_last_tv_field_sources", {}),
            "entry_px": self.watched_entry,
            "closed_qty": self.watched_qty or self.initial_qty,
        }

    def _infer_flat_close_meta(self, curr_px=0.0, hint_reason=""):
        """哨兵/重启推断全平类型（无 fresh TV 信号时）"""
        if self._likely_exchange_stop_exit(curr_px) and not getattr(
            self, "_radar_activation_notified", False
        ):
            est = self._estimate_pnl_pct(curr_px)
            sl = float(
                getattr(self, "_last_applied_exchange_sl", 0)
                or getattr(self, "tv_sl", 0)
                or 0
            )
            return self._build_close_meta(
                "CLOSE_STOPLOSS",
                self.current_side,
                est,
                f"交易所止损触发 @ {sl:.2f} (TP1前宽止损/非雷达保本钉钉) | {hint_reason}",
            )

        last = self.last_tv_signal or {}
        if (
            last.get("action") in ("CLOSE_TP3", "CLOSE_PROTECT", "CLOSE_STOPLOSS")
            and time.time() - float(last.get("ts", 0) or 0) < 180
        ):
            return self._build_close_meta(
                last.get("action"),
                last.get("side") or self.current_side,
                last.get("pnl_pct"),
                last.get("reason") or hint_reason,
            )

        consumed = set(getattr(self, "tp_levels_consumed", []) or [])
        if consumed >= {1, 2, 3}:
            return self._build_close_meta(
                "CLOSE_TP3", self.current_side,
                self._estimate_pnl_pct(curr_px), "TP3完美收网",
            )
        if getattr(self, "_shield_handoff_notified", False) or getattr(
            self, "_radar_activation_notified", False
        ) or self._is_radar_active():
            est = self._estimate_pnl_pct(curr_px)
            sl = float(
                getattr(self, "_last_applied_exchange_sl", 0)
                or getattr(self, "current_sl", 0)
                or 0
            )
            return self._build_close_meta(
                "CLOSE_STOPLOSS", self.current_side, est,
                f"雷达保本止损触发 @ {sl:.2f} | {hint_reason}",
            )
        if getattr(self, "shield_active", False):
            est = self._estimate_pnl_pct(curr_px)
            return self._build_close_meta(
                "CLOSE_STOPLOSS", self.current_side, est,
                "触碰硬止损平仓（TV tv_sl）",
            )
        return self._build_close_meta("CLOSE", self.current_side, None, hint_reason or "仓位归零")

    def _enrich_close_meta_live(self, meta, curr_px=0.0):
        """核实前补全实盘字段（须在账本清零前调用）"""
        out = dict(meta or {})
        if not out.get("entry_px"):
            out["entry_px"] = self.watched_entry
        if not out.get("closed_qty"):
            out["closed_qty"] = self.watched_qty or self.initial_qty
        if not out.get("side"):
            out["side"] = self.current_side
        px = float(curr_px or 0) or binance_client.get_current_price(self.symbol) or 0.0
        if px > 0:
            out["live_exit_px"] = px
            if out.get("pnl_pct") is None:
                saved_side = out.get("side") or self.current_side
                entry = float(out.get("entry_px") or 0)
                if entry > 0 and saved_side:
                    if saved_side == "LONG":
                        out["pnl_pct"] = (px - entry) / entry * 100.0
                    else:
                        out["pnl_pct"] = (entry - px) / entry * 100.0
        if not out.get("close_type"):
            out["close_type"] = classify_tv_close(
                out.get("action", ""), out.get("tv_reason", ""), out.get("pnl_pct"),
            )
        return out

    def _safe_float(self, val, default=0.0):
        try:
            if val is None or val == "":
                return default
            return float(val)
        except (TypeError, ValueError):
            return default

    def _safe_int(self, val, default=3):
        try:
            if val is None or val == "":
                return default
            return int(float(val))
        except (TypeError, ValueError):
            return default

    def _process_signal(self, payload):
        raw_action = str(payload.get("action", "")).strip().upper()
        is_tp_sl_update = raw_action in ("UPDATE_TP", "UPDATE_SL")

        # UPDATE_* 可能不带 regime/atr：禁止用默认值覆盖开仓快照
        if not is_tp_sl_update or payload.get("regime") is not None:
            self.regime = self._safe_int(payload.get("regime"), self.regime or 3)
            if self.regime not in self.regime_settings:
                self.regime = 3

        atr_in = self._safe_float(payload.get("atr"), 0.0)
        if atr_in > 0:
            self.current_atr = atr_in
        elif not is_tp_sl_update:
            self.current_atr = self._safe_float(payload.get("atr"), 30.0)

        px_in = self._safe_float(payload.get("price"), 0.0)
        if px_in > 0:
            self.tv_price = px_in
        elif not is_tp_sl_update:
            self.tv_price = 0.0

        new_tps = self._sanitize_tp_prices([
            self._safe_float(payload.get("tv_tp1"), 0),
            self._safe_float(payload.get("tv_tp2"), 0),
            self._safe_float(payload.get("tv_tp3"), 0),
        ])
        if raw_action == "UPDATE_TP":
            self._prev_tv_tps_before_update = list(self.tv_tps or [0.0, 0.0, 0.0])
            self.tv_tps = new_tps
        elif raw_action in ("LONG", "SHORT"):
            self.tv_tps = new_tps
            if self.tv_price > 0:
                if not validate_tp_prices_for_side(raw_action, self.tv_price, self.tv_tps):
                    enriched = enrich_entry_tp_prices(
                        raw_action, self.tv_price, self.current_atr, self.regime, payload,
                    )
                    self.tv_tps = self._sanitize_tp_prices([
                        self._safe_float(enriched.get("tv_tp1"), 0),
                        self._safe_float(enriched.get("tv_tp2"), 0),
                        self._safe_float(enriched.get("tv_tp3"), 0),
                    ])
                    if enriched.get("_tp_source"):
                        payload = dict(payload)
                        payload["_tp_source"] = enriched.get("_tp_source")
        elif sum(1 for t in new_tps if t > 0) >= 2:
            # 其它信号若带齐 TP 才覆盖，避免 UPDATE_SL/CLOSE 把账本 TP 清零
            self.tv_tps = new_tps

        self._last_tv_field_sources = {
            "regime": payload.get("_regime_source", "tv"),
            "atr": payload.get("_atr_source", "tv"),
            "tp": payload.get("_tp_source", "tv"),
            "price": payload.get("_price_source", "tv"),
        }
        close_reason = str(payload.get("reason") or "策略指标反转/波动率安全退出").strip()
        close_side = str(payload.get("side") or "").strip().upper()
        pnl_pct = payload.get("pnl_pct")
        close_meta = self._build_close_meta(raw_action, close_side, pnl_pct, close_reason)
        close_extra = self._format_close_extra(
            close_side, pnl_pct, self.tv_price, self.regime, self.current_atr,
        )

        if not raw_action:
            logger.warning("TV 信号缺少 action，已忽略")
            return
        if raw_action in (
            "LONG", "SHORT", "CLOSE", "CLOSE_PROTECT", "CLOSE_TP3",
            "CLOSE_STOPLOSS", "UPDATE_SL", "UPDATE_TP",
        ) or raw_action.startswith("CLOSE"):
            self._record_tv_signal(payload, raw_action)

        if not self._lock.acquire(timeout=120.0):
            logger.error(f"⏱️ 锁等待 120s 超时，信号 {raw_action} 重新入队")
            self._signal_queue.put(payload)
            return

        try:
            is_close = (
                raw_action in ("CLOSE", "CLOSE_PROTECT", "CLOSE_TP3", "CLOSE_STOPLOSS")
                or raw_action.startswith("CLOSE")
            )
            if is_close:
                self.monitoring = False
            if raw_action == "CLOSE_PROTECT" or raw_action.startswith("CLOSE_PROTECT"):
                pos = self._get_active_position()
                tv_reason = close_reason or "保护性全平"
                if not pos or pos.get("size", 0) <= 0:
                    logger.info(f"🛡️ 保护性全平到达但盘口已空仓 → 撤单复位 | {tv_reason}{close_extra}")
                    self._handle_manual_flat_detected(
                        tv_reason,
                        close_meta=close_meta,
                        curr_px=self.tv_price,
                    )
                else:
                    self._close_all(
                        f"🛡️ 风控拦截：{tv_reason}{close_extra}",
                        close_meta=close_meta,
                    )
            elif raw_action == "CLOSE_TP3":
                pos = self._get_active_position()
                tv_reason = close_reason or "TP3完美收网"
                if not pos or pos.get("size", 0) <= 0:
                    self._handle_manual_flat_detected(
                        tv_reason,
                        close_meta=close_meta,
                        curr_px=self.tv_price,
                    )
                else:
                    self._close_all(
                        f"🏆 TP3止盈：{tv_reason}{close_extra}",
                        close_meta=close_meta,
                    )
            elif raw_action == "CLOSE_STOPLOSS":
                pos = self._get_active_position()
                tv_reason = close_reason or "被动止损/保本"
                sl_compare = ""
                if self.watched_entry and self.current_side:
                    sl_compare = format_tv_vps_sl_compare(
                        self.current_side, self.watched_entry,
                        self.current_atr, self.regime,
                        tv_sl_ref=payload.get("tv_sl") or getattr(self, "tv_sl_ref", 0),
                    )
                logger.warning(
                    f"🛑 [TV第一指令] CLOSE_STOPLOSS 立即全平 | {tv_reason}"
                    + (f" | {sl_compare}" if sl_compare else "")
                )
                if not pos or pos.get("size", 0) <= 0:
                    self._handle_manual_flat_detected(
                        tv_reason,
                        close_meta=close_meta,
                        curr_px=self.tv_price,
                    )
                else:
                    tag = (
                        "防回吐保本"
                        if close_meta.get("close_type") == CLOSE_TYPE_BREAKEVEN
                        else "TV紧止损"
                    )
                    self._close_all(
                        f"🛑 {tag}·TV第一指令全平：{tv_reason}{close_extra}",
                        close_meta=close_meta,
                    )
            elif raw_action == "CLOSE":
                self._close_all(f"🧹 换防清场：{close_reason}{close_extra}", close_meta=close_meta)
            elif raw_action == "UPDATE_SL":
                self._handle_tv_sl_update(payload)
            elif raw_action == "UPDATE_TP":
                self._handle_tv_tp_update(payload)
            elif raw_action in ["LONG", "SHORT"]:
                self._apply_tv_sl_from_payload(payload, source=f"{raw_action}开仓")
                self._apply_tv_sizing_params(payload)
                self.last_tv_side = raw_action
                self._save_state()
                self._handle_smart_entry(raw_action, payload)
            else:
                logger.warning(f"未识别的 TV action: {raw_action}")
        finally:
            self._lock.release()

    def _entry_price_diff_pct(self, price_a, price_b, ref_px):
        ref = ref_px or max(abs(price_a), abs(price_b), 1.0)
        return abs(float(price_a) - float(price_b)) / ref * 100.0

    def _is_similar_atr(self, atr_a, atr_b):
        a, b = float(atr_a or 0), float(atr_b or 0)
        if a <= 0 and b <= 0:
            return True
        if a <= 0 or b <= 0:
            return False
        return abs(a - b) / max(a, b) <= ATR_SIMILAR_RATIO

    def _touch_entry_signal_signature(self, action):
        self._last_entry_signal = {
            "action": action,
            "tv_price": self.tv_price,
            "atr": self.current_atr,
            "regime": self.regime,
            "tv_tps": list(self.tv_tps),
            "ts": time.time(),
        }

    def _is_duplicate_flat_entry(self, action, curr_px):
        sig = self._last_entry_signal
        if not sig or sig.get("action") != action:
            return False
        if time.time() - float(sig.get("ts", 0)) > SAME_DIR_DEDUP_SEC:
            return False
        if not self._is_similar_atr(sig.get("atr"), self.current_atr):
            return False
        if int(sig.get("regime", 0)) != int(self.regime):
            return False
        ref_px = curr_px or self.tv_price or sig.get("tv_price") or 1.0
        diff = self._entry_price_diff_pct(sig.get("tv_price", 0), self.tv_price, ref_px)
        return diff < SAME_DIR_MIN_SPREAD_PCT

    def _same_direction_entry_mode(self, action, pos, curr_px):
        """同向智能决策：① ATR → ② 档位 → ③ 理论开仓价差"""
        ref_px = curr_px or self.tv_price or pos["entry_price"]
        live_entry = pos["entry_price"]
        diff_pct = self._entry_price_diff_pct(live_entry, self.tv_price, ref_px)
        open_regime = int(getattr(self, "open_regime", self.regime) or self.regime)
        open_atr = float(getattr(self, "open_atr", self.current_atr) or self.current_atr)
        tv_atr = float(self.current_atr)

        if not self._is_similar_atr(open_atr, tv_atr):
            logger.info(
                f"🔄 同向 [{action}] ATR {open_atr:.2f}→{tv_atr:.2f} 变化 "
                f"(>{ATR_SIMILAR_RATIO:.0%}) → 先平后开重入"
            )
            return "FULL_REENTRY", diff_pct, "atr_changed", open_atr, tv_atr

        if int(self.regime) != open_regime:
            logger.info(
                f"🔄 同向 [{action}] 档位 R{open_regime}→R{self.regime} → 先平后开重入"
            )
            return "FULL_REENTRY", diff_pct, "regime_changed", open_atr, tv_atr

        if diff_pct >= SAME_DIR_MIN_SPREAD_PCT:
            logger.info(
                f"🔄 同向 [{action}] 价差 {diff_pct:.3f}% ≥ {SAME_DIR_MIN_SPREAD_PCT}% → 先平后开"
            )
            return "FULL_REENTRY", diff_pct, "spread_ok", open_atr, tv_atr

        logger.info(
            f"🧠 同向 [{action}] ATR {tv_atr:.2f} 未变 + 价差 {diff_pct:.3f}% "
            f"< {SAME_DIR_MIN_SPREAD_PCT}% → 仅刷新 TP123"
        )
        return "REFRESH_TP", diff_pct, "refresh_tp", open_atr, tv_atr

    def _report_smart_reentry(self, action, pos, diff_pct, reason, open_atr, tv_atr):
        live_entry = pos["entry_price"]
        reason_txt = {
            "atr_changed": f"TV ATR `{tv_atr:.2f}` ≠ 持仓 ATR `{open_atr:.2f}` → 刷新仓位",
            "regime_changed": f"档位 R{self.open_regime}→R{self.regime} → 刷新仓位",
            "spread_ok": f"理论价差 {diff_pct:.3f}% ≥ {SAME_DIR_MIN_SPREAD_PCT}% → 刷新仓位",
        }.get(reason, "同向刷新仓位")
        self._call_dingtalk(
            dingtalk.report_smart_same_dir_decision,
            side=action,
            decision=f"reentry_{reason}",
            live_entry=live_entry,
            tv_price=self.tv_price,
            diff_pct=diff_pct,
            threshold_pct=SAME_DIR_MIN_SPREAD_PCT,
            open_regime=self.open_regime,
            tv_regime=self.regime,
            open_atr=open_atr,
            tv_atr=tv_atr,
            qty=pos["size"],
            verify_note=(
                f"核实持仓 {pos['size']} ETH @ {live_entry:.2f} | {reason_txt} | 执行先平后开"
            ),
        )

    def _same_direction_refresh_tp(self, action, pos, curr_px, diff_pct, open_atr, tv_atr):
        live_pos = self._get_active_position()
        if not live_pos or live_pos["size"] <= 0:
            logger.warning("🧠 同向刷新: 实盘已无持仓，跳过")
            return

        real_qty = live_pos["size"]
        entry = live_pos["entry_price"]
        self.current_side = action
        self.watched_qty = real_qty
        self.watched_entry = entry
        self.monitoring = True
        self._save_state()

        sl_to_pass = self._radar_sl_to_pass()
        result = self._smart_realign_defenses(
            real_qty, entry, dynamic_sl=sl_to_pass,
            reason="同向TV智能刷新止盈",
        )
        self._ensure_sentinel_running()

        verify_note = (
            f"核实持仓 {real_qty} ETH @ {entry:.2f} | TV理论 {self.tv_price:.2f} | "
            f"持仓ATR {open_atr:.2f} = TV ATR {tv_atr:.2f} | "
            f"价差 {diff_pct:.3f}% (< {SAME_DIR_MIN_SPREAD_PCT}%) | "
            f"止盈 {result['matched']}/{result['expected']} 档 | "
            f"{self._format_audit_summary(result['audit'])}"
        )
        self._call_dingtalk(
            dingtalk.report_smart_same_dir_decision,
            side=action,
            decision="skip_refresh_tp",
            live_entry=entry,
            tv_price=self.tv_price,
            diff_pct=diff_pct,
            threshold_pct=SAME_DIR_MIN_SPREAD_PCT,
            open_regime=self.open_regime,
            tv_regime=self.regime,
            open_atr=open_atr,
            tv_atr=tv_atr,
            qty=real_qty,
            tp_audit=result["audit"],
            verify_note=verify_note,
        )
        logger.info("🧠 同向智能处理完成: ATR未变+价差不足，未再开仓，TP123 已按新 TV 价刷新")

    def _ensure_sentinel_running(self):
        if self.monitoring and not self._sentinel_active:
            threading.Thread(
                target=self._sentinel_loop, daemon=True, name="sentinel",
            ).start()

    def _full_reentry(self, action, close_reason):
        binance_client.cancel_all_open_orders(self.symbol)
        time.sleep(0.5)
        if not self._close_all(close_reason, reset_state=True):
            logger.error("❌ 先平后开中止：平仓未归零，拒绝叠仓开仓")
            dingtalk.report_system_alert(
                "先平后开中止 · 平仓未归零",
                "6 轮强平后盘口仍有持仓，已拒绝新开仓，请人工核查币安盘口",
            )
            return
        if not self._wait_verify(self._verify_flat, retries=8, delay=0.5):
            logger.error("❌ 先平后开中止：空仓核查未通过")
            dingtalk.report_system_alert(
                "先平后开中止 · 空仓核查失败",
                "平仓指令已发但 REST 仍显示持仓，已拒绝叠仓开仓",
            )
            return
        binance_client.cancel_all_open_orders(self.symbol)
        time.sleep(0.5)
        curr_px = binance_client.get_current_price(self.symbol) or self.tv_price
        if curr_px > 0:
            self._open_position(action, curr_px)

    def _handle_manual_flat_detected(self, reason, close_meta=None, curr_px=0.0):
        """人工全平 / 止盈吃满 / 止损触发：智能复位账本 + 四标签收网钉钉"""
        prev_side = self.current_side
        purge = self._purge_all_defense_orders_on_flat(
            reason or "感知空仓·抢先撤TP123",
        )
        meta = self._enrich_close_meta_live(
            close_meta or self._infer_flat_close_meta(curr_px, hint_reason=reason),
            curr_px,
        )
        logger.info(f"📭 感知空仓: {meta.get('tv_reason') or reason}")
        self.monitoring = False
        self.watched_qty = 0.0
        self.initial_qty = 0.0
        self._open_settled_qty = 0.0
        self.base_qty = 0.0
        self.add_count = 0
        self.tp_levels_consumed = []
        self.shield_active = False
        self.current_side = None
        self.tv_tps = [0.0, 0.0, 0.0]
        if not purge.get("ok"):
            dingtalk.report_system_alert(
                "全平后挂单未净 · 请人工核查",
                f"{reason or '感知空仓'} | 剩余挂单 {purge.get('remaining', '?')} | "
                f"残留 TP {purge.get('tp_remaining', '?')} 张 | "
                "未撤净的 TP 可能在空仓后反向成交",
            )
        self._save_state()
        verify_note = ""
        if purge.get("tp_cancelled"):
            verify_note = f"已撤 TP {purge['tp_cancelled']} 张"
        if not purge.get("ok"):
            verify_note += (" | " if verify_note else "") + "⚠️ 挂单未完全清零"
        self._report_flat_close(
            meta.get("tv_reason") or reason or "仓位归零",
            close_meta=meta,
            curr_px=curr_px,
        )
        self._sweep_orphan_reverse_after_flat(
            prev_side=prev_side,
            reason=meta.get("tv_reason") or reason,
        )

    def _realign_after_position_add(self, new_qty, new_entry, curr_px, entry_type,
                                    old_entry=None, old_qty=None, old_vps_sl=None):
        """
        加仓成功后：加权均价(交易所) + 合并宽止损 + 重置雷达 + 替换 TP123。
        """
        old_entry = float(old_entry if old_entry is not None else self.watched_entry or 0)
        old_qty = float(old_qty if old_qty is not None else 0)
        old_vps_sl = float(old_vps_sl if old_vps_sl is not None else getattr(self, "tv_sl", 0) or 0)

        self.watched_entry = new_entry
        self._refresh_vps_hard_sl(
            entry=new_entry, side=self.current_side,
            regime=int(getattr(self, "open_regime", None) or self.regime or 3),
            atr=float(getattr(self, "open_atr", None) or self.current_atr or 30),
            tv_sl_ref=getattr(self, "tv_sl_ref", 0) or None,
            source=f"{entry_type}加仓",
        )
        new_vps_sl = float(self.tv_sl or 0)
        merged_sl = self._merge_wider_vps_hard_sl(old_vps_sl, new_vps_sl)
        if merged_sl > 0 and abs(merged_sl - new_vps_sl) > 0.01:
            self.tv_sl = merged_sl
            self._last_applied_exchange_sl = 0.0
            logger.info(
                f"🛡️ 加仓合并宽止损: 旧{old_vps_sl:.2f} + 新{new_vps_sl:.2f} → {merged_sl:.2f} "
                f"(取更宽)"
            )
        self.best_price = new_entry
        self.current_sl = merged_sl if merged_sl > 0 else new_vps_sl
        self._radar_stage_last = 0
        self._radar_activation_notified = False
        self._shield_handoff_notified = False

        if old_qty > 0 and old_entry > 0:
            logger.info(
                f"➕ 加仓合并: {old_qty:.3f}@{old_entry:.2f} + 追加 → "
                f"加权均价 {new_entry:.2f} | 总仓 {new_qty:.3f} ETH"
            )
        self._ensure_tp123_prices_from_tv(new_entry)
        tp_txt = "/".join(
            f"{float(p):.0f}" for p in (self.tv_tps or []) if float(p or 0) > 0
        ) or "—"
        ratios = self.regime_settings[self._tp_split_regime()]["ratios"]
        consumed = list(getattr(self, "tp_levels_consumed", []) or [])

        sl_to_pass = None
        radar_note = "雷达待命(未达激活比)"
        if self._radar_legitimately_armed(new_qty, curr_px):
            self._refresh_radar_state_on_recover(curr_px, new_entry)
            sl_to_pass = self._radar_sl_to_pass()
            radar_note = (
                f"雷达已激活 SL={sl_to_pass:.2f}"
                if sl_to_pass else "雷达跟进中"
            )

        logger.info(
            f"🕸️ [{entry_type}] 加仓后防线重挂 | 新仓 {new_qty} ETH @ {new_entry:.2f} "
            f"| TV TP={tp_txt} | R{self._tp_split_regime()} 比例 {ratios} "
            f"| 已成交档 {consumed or '无'}"
        )
        self._cancel_all_tp_limit_orders()
        time.sleep(0.45)

        result = self._enforce_defense_alignment(
            new_qty, new_entry,
            dynamic_sl=sl_to_pass,
            reason=f"{entry_type}加仓后TP123重挂",
            rounds=4,
        )
        audit = result.get("audit") or self._audit_tp_levels(new_qty)
        if not self._tp_audit_ok(audit):
            logger.warning(
                f"⚠️ [{entry_type}] 加仓后 TP 未齐 → 核武重挂 | "
                f"{self._format_audit_summary(audit)}"
            )
            audit = self._nuclear_realign_tp(
                new_qty, new_entry, dynamic_sl=sl_to_pass, rounds=3,
            )
            result["audit"] = audit

        shield_ok = self._maintain_hard_shield(
            new_qty, curr_px, force=True, radar_sl=sl_to_pass,
        )
        if self._radar_legitimately_armed(new_qty, curr_px):
            self._process_radar_trailing(new_qty, curr_px)
            sl = self._radar_sl_to_pass()
            if sl:
                shield_ok = self._maintain_hard_shield(
                    new_qty, curr_px, force=True, radar_sl=sl,
                ) or shield_ok
                radar_note = f"雷达推升 SL={sl:.2f}"

        self.shield_sized_qty = float(new_qty)
        self._save_state()
        return {
            "shield_ok": shield_ok,
            "audit": audit,
            "radar_note": radar_note,
            "tp_prices": tp_txt,
            "ratios": ratios,
            "result": result,
        }

    def _add_to_position(self, action, payload):
        """PYRAMID / PROFIT_ADD：base_qty × TV qty_ratio 追加，并重挂 TP123 + 同步雷达"""
        entry_type = normalize_entry_type(payload.get("entry_type"))
        max_add = self._max_add_times_for_regime()
        tv_ratio = float(getattr(self, "tv_qty_ratio", 0) or 0)
        pos = self._get_active_position()
        if not pos or pos.get("size", 0) <= 0:
            logger.warning(f"{entry_type} 到达但盘口无持仓，已忽略")
            return
        if pos["side"] != action:
            dingtalk.report_system_alert(
                f"{entry_type} 方向不符",
                f"TV {action} vs 实盘 {pos['side']}，已拒绝加仓",
            )
            return
        if tv_ratio <= 0:
            logger.warning(
                f"{entry_type} 跳过：R{self.regime} TV加仓比例={tv_ratio:.2f}（档位禁止加仓）"
            )
            dingtalk.report_system_alert(
                f"{entry_type} 加仓跳过",
                f"R{self.regime} TV qty_ratio={tv_ratio:.2f} ≤ 0 | "
                f"base={getattr(self, 'base_qty', 0):.3f} ETH",
            )
            return
        if int(getattr(self, "add_count", 0) or 0) >= max_add:
            logger.warning(
                f"{entry_type} 跳过：已达 R{self.regime} 最大加仓次数 {max_add} "
                f"(base={getattr(self, 'base_qty', 0):.3f})"
            )
            dingtalk.report_system_alert(
                f"{entry_type} 加仓跳过",
                f"R{self.regime} 已达最大加仓 {max_add} 次 | base={getattr(self, 'base_qty', 0):.3f} "
                f"| 现仓 {pos['size']} ETH",
            )
            return

        curr_px = binance_client.get_current_price(self.symbol) or self.tv_price
        old_qty = float(pos["size"])
        old_entry = float(pos["entry_price"])
        old_vps_sl = float(getattr(self, "tv_sl", 0) or 0)
        add_qty, meta = self._calc_vps_add_qty(tv_ratio)
        if add_qty <= 0:
            logger.error(f"{entry_type} 跳过：计算加仓量无效 {meta}")
            dingtalk.report_system_alert(
                f"{entry_type} 数量无效",
                f"加仓计算失败: {self._tv_sizing_note(add_qty, meta, entry_type=entry_type)}",
            )
            return

        binance_client.set_leverage(self.symbol, leverage=EXCHANGE_LEVERAGE)
        logger.info(
            f"➕ [{entry_type}] {action} 追加 {add_qty} ETH | "
            f"{self._tv_sizing_note(add_qty, meta, entry_type=entry_type)}"
        )
        order = binance_client.place_market_order(action, add_qty, symbol=self.symbol)
        if not order:
            dingtalk.report_system_alert(
                f"{entry_type} 下单失败",
                f"{action} 追加 {add_qty} ETH 市价单未成交",
            )
            return
        time.sleep(1.5)

        new_pos = self._get_active_position()
        if not new_pos or new_pos["size"] <= old_qty + 0.0005:
            dingtalk.report_system_alert(
                f"{entry_type} 核实失败",
                f"追加 {add_qty} ETH 后实盘未增长",
            )
            return

        new_qty = float(new_pos["size"])
        new_entry = float(new_pos["entry_price"])
        self.watched_qty = new_qty
        self.watched_entry = new_entry
        self.current_side = action
        self.monitoring = True
        self._save_state()

        realign = self._realign_after_position_add(
            new_qty, new_entry, curr_px, entry_type,
            old_entry=old_entry, old_qty=old_qty, old_vps_sl=old_vps_sl,
        )
        sl_ok = realign.get("shield_ok", False)
        audit = realign.get("audit") or {}
        self.add_count = int(getattr(self, "add_count", 0) or 0) + 1
        self._save_state()
        type_label = "浮盈加仓" if entry_type == ENTRY_TYPE_PROFIT_ADD else "金字塔加仓"
        tp_summary = self._format_audit_summary(audit)
        verify_note = (
            f"{type_label} | {self._tv_sizing_note(add_qty, meta, entry_type=entry_type)} "
            f"| base={getattr(self, 'base_qty', 0):.3f} "
            f"| 加仓次数 {self.add_count}/{max_add} "
            f"| 持仓 {old_qty:.3f}→{new_qty:.3f} ETH @ {old_entry:.2f}→{new_entry:.2f} "
            f"| TV TP={realign.get('tp_prices', '—')} "
            f"| TP {audit.get('matched_full', 0)}/{audit.get('expected', 0)} "
            f"| {tp_summary} "
            f"| {realign.get('radar_note', '')} "
            f"| {format_tv_vps_sl_compare(action, new_entry, self.current_atr, self.regime, getattr(self, 'tv_sl_ref', 0))} "
            f"| {'防线已核实' if sl_ok and self._tp_audit_ok(audit) else '防线待核实'}"
        )
        self._call_dingtalk(
            dingtalk.report_tv_position_add,
            side=action,
            entry_type=entry_type,
            add_qty=add_qty,
            old_qty=old_qty,
            new_qty=new_qty,
            old_entry=old_entry,
            new_entry=new_entry,
            tv_sl=getattr(self, "tv_sl", 0),
            risk_pct=self.tv_risk_pct,
            leverage=self.tv_sizing_leverage,
            qty_ratio=tv_ratio,
            base_qty=getattr(self, "base_qty", 0),
            vps_sizing_meta=meta,
            add_count=self.add_count,
            max_add_times=max_add,
            regime=self.regime,
            tp_audit=tp_summary,
            radar_note=realign.get("radar_note", ""),
            open_regime=self._tp_split_regime(),
            tp_ratio_label=format_regime_tp_ratios_label(self._tp_split_regime()),
            verify_note=verify_note,
            verified=sl_ok and self._tp_audit_ok(audit),
        )
        self._ensure_sentinel_running()

    def _is_fresh_open_cooldown(self, pos=None, cooldown_sec=None):
        """刚开仓同向冷却窗：重复 OPEN 禁止立刻先平后开"""
        cooldown_sec = float(
            cooldown_sec if cooldown_sec is not None else OPEN_SAME_DIR_COOLDOWN_SEC
        )
        if cooldown_sec <= 0:
            return False
        now = time.time()
        sig = getattr(self, "_last_entry_signal", None) or {}
        if (
            self.monitoring
            and self.current_side
            and (not pos or pos.get("side") == self.current_side)
            and float(sig.get("ts") or 0) > 0
            and now - float(sig["ts"]) < cooldown_sec
        ):
            return True
        last_open = self._load_last_journal_entry(OPEN_JOURNAL)
        if not last_open:
            return False
        side = str(last_open.get("side") or "").upper()
        if pos and side and side != str(pos.get("side") or "").upper():
            return False
        ts_raw = last_open.get("ts")
        try:
            if isinstance(ts_raw, (int, float)):
                age = now - float(ts_raw)
            else:
                age = now - datetime.strptime(str(ts_raw), "%Y-%m-%d %H:%M:%S").timestamp()
            return 0 <= age < cooldown_sec
        except Exception:
            return False

    def _handle_smart_entry(self, action, payload=None):
        """VPS sizing：OPEN 同向智能；反向/确需刷新才先平后开；加仓重挂 TP123"""
        payload = payload or {}
        entry_type = normalize_entry_type(payload.get("entry_type"))

        if entry_type in (ENTRY_TYPE_PYRAMID, ENTRY_TYPE_PROFIT_ADD):
            self._add_to_position(action, payload)
            self._touch_entry_signal_signature(action)
            return

        if entry_type == ENTRY_TYPE_OPEN:
            pos = self._get_active_position()
            if pos and pos.get("size", 0) > 0:
                if pos["side"] != action:
                    logger.info(
                        f"⚡ 反方向 OPEN [{action}] vs 实盘 [{pos['side']}] → 先平后开"
                    )
                    self._full_reentry(
                        action, "反方向 OPEN 到达，触发【先平后开】原子对冲换防",
                    )
                    self._touch_entry_signal_signature(action)
                    return

                curr_px = binance_client.get_current_price(self.symbol) or self.tv_price
                # 刚开仓冷却：同向重复 OPEN 禁止市价清仓（这是“开单立刻被平”的主因）
                if self._is_fresh_open_cooldown(pos):
                    logger.warning(
                        f"🛡️ 同向 OPEN 冷却 {OPEN_SAME_DIR_COOLDOWN_SEC:.0f}s 内 "
                        f"→ 禁止先平后开，仅刷新 TP123 [{action}]"
                    )
                    mode, diff_pct, reason, open_atr, tv_atr = (
                        self._same_direction_entry_mode(action, pos, curr_px)
                    )
                    self._same_direction_refresh_tp(
                        action, pos, curr_px, diff_pct, open_atr, tv_atr,
                    )
                    self._touch_entry_signal_signature(action)
                    return

                mode, diff_pct, reason, open_atr, tv_atr = (
                    self._same_direction_entry_mode(action, pos, curr_px)
                )
                if mode == "REFRESH_TP":
                    self._same_direction_refresh_tp(
                        action, pos, curr_px, diff_pct, open_atr, tv_atr,
                    )
                    self._touch_entry_signal_signature(action)
                    return

                close_msgs = {
                    "atr_changed": (
                        f"同向 TV ATR 变化 ({open_atr:.2f}→{tv_atr:.2f})，"
                        f"触发【先平后开】刷新仓位"
                    ),
                    "regime_changed": "同向 TV 档位变化，触发【先平后开】重入",
                    "spread_ok": (
                        f"同向理论价差 {diff_pct:.3f}% 达标，触发【先平后开】重入"
                    ),
                }
                self._report_smart_reentry(
                    action, pos, diff_pct, reason, open_atr, tv_atr,
                )
                self._full_reentry(
                    action,
                    close_msgs.get(reason, "同方向刷新仓位，触发【先平后开】重入"),
                )
                self._touch_entry_signal_signature(action)
                return
            curr_px = binance_client.get_current_price(self.symbol) or self.tv_price
            if self._is_duplicate_flat_entry(action, curr_px):
                logger.info(f"🧠 TV OPEN 短时重复 [{action}] → 忽略")
                self._touch_entry_signal_signature(action)
                return
            if not self._ensure_flat_before_open("TV OPEN"):
                dingtalk.report_system_alert("TV OPEN 中止", "盘口非空，拒绝叠仓")
                return
            binance_client.cancel_all_open_orders(self.symbol)
            time.sleep(0.5)
            curr_px = curr_px or binance_client.get_current_price(self.symbol) or self.tv_price
            if curr_px > 0:
                self._open_position(action, curr_px, payload=payload)
            self._touch_entry_signal_signature(action)
            return

        curr_px = binance_client.get_current_price(self.symbol) or self.tv_price
        pos = self._get_active_position()

        if pos and pos["size"] > 0:
            current_side = pos["side"]
            if current_side != action:
                logger.info(f"⚡ 反方向 [{action}] vs 实盘 [{current_side}] → 先平后开")
                self._full_reentry(action, "反方向指令到达，触发【先平后开】原子对冲换防")
                self._touch_entry_signal_signature(action)
                return

            mode, diff_pct, reason, open_atr, tv_atr = self._same_direction_entry_mode(action, pos, curr_px)
            if mode == "REFRESH_TP":
                self._same_direction_refresh_tp(action, pos, curr_px, diff_pct, open_atr, tv_atr)
                self._touch_entry_signal_signature(action)
                return

            close_msgs = {
                "atr_changed": f"同向 TV ATR 变化 ({open_atr:.2f}→{tv_atr:.2f})，触发【先平后开】刷新仓位",
                "regime_changed": "同向 TV 档位变化，触发【先平后开】重入",
                "spread_ok": f"同向理论价差 {diff_pct:.3f}% 达标，触发【先平后开】重入",
            }
            self._report_smart_reentry(action, pos, diff_pct, reason, open_atr, tv_atr)
            self._full_reentry(action, close_msgs.get(reason, "同方向刷新仓位，触发【先平后开】重入"))
            self._touch_entry_signal_signature(action)
            return

        if self._is_duplicate_flat_entry(action, curr_px):
            ref_px = curr_px or self.tv_price or 1.0
            diff_pct = self._entry_price_diff_pct(
                self._last_entry_signal.get("tv_price", 0), self.tv_price, ref_px,
            )
            logger.info(f"🧠 空仓短时重复同向 TV [{action}] → 忽略开仓")
            self._call_dingtalk(
                dingtalk.report_smart_same_dir_decision,
                side=action,
                decision="skip_duplicate_flat",
                live_entry=0.0,
                tv_price=self.tv_price,
                diff_pct=diff_pct,
                threshold_pct=SAME_DIR_MIN_SPREAD_PCT,
                open_regime=self.regime,
                tv_regime=self.regime,
                open_atr=self._last_entry_signal.get("atr", self.current_atr),
                tv_atr=self.current_atr,
                qty=0.0,
                verify_note=(
                    f"5分钟内重复 {action} | ATR {self.current_atr:.2f} 未变 | "
                    f"TV {self.tv_price:.2f} 价差 {diff_pct:.3f}% | 档位 R{self.regime} | 未重复下单"
                ),
            )
            self._touch_entry_signal_signature(action)
            return

        logger.info(f"⚡ 收到建仓信号 [{action}]，空仓极速开仓")
        if not self._ensure_flat_before_open("空仓开仓"):
            dingtalk.report_system_alert(
                "开仓中止 · 盘口非空",
                f"收到 TV {action} 但实盘仍有残留持仓，已拒绝叠仓开仓",
            )
            return
        binance_client.cancel_all_open_orders(self.symbol)
        time.sleep(0.5)
        curr_px = curr_px or binance_client.get_current_price(self.symbol)
        if curr_px > 0:
            self._open_position(action, curr_px, payload=payload)
        self._touch_entry_signal_signature(action)

    def _open_position(self, action, curr_px, payload=None):
        payload = payload or {}
        if self._open_in_progress:
            logger.error(f"开仓中止：已有开仓流程进行中，拒绝叠仓 [{action}]")
            return
        self._open_in_progress = True
        try:
            self._snapshot_sizing_principal(
                f"开仓前 {normalize_entry_type(payload.get('entry_type'))} R{self.regime}"
            )
            qty, balance, margin_usdt, margin_pct, sizing_meta = self._calc_target_open_qty(
                curr_px, payload=payload,
            )
            if qty <= 0:
                logger.error(f"开仓跳过：目标数量无效 balance={balance:.2f} px={curr_px}")
                return

            binance_client.set_leverage(self.symbol, leverage=EXCHANGE_LEVERAGE)
            notional = qty * curr_px
            budget_txt = format_vps_sizing_note(sizing_meta, qty=qty, entry_type=ENTRY_TYPE_OPEN)
            logger.info(f"📐 仓位预算 [{self.symbol}]: {budget_txt} (名义 ~{notional:.0f}U)")

            cap_ok, _cap_meta = self._assert_notional_cap_or_reject(
                qty, curr_px, sizing_meta=sizing_meta,
            )
            if not cap_ok:
                return

            if not self._wait_verify(self._verify_flat, retries=4, delay=0.35):
                logger.error("开仓中止：市价下单前盘口仍非空")
                dingtalk.report_system_alert(
                    f"开仓中止 · 下单前盘口非空 [{self.symbol}]",
                    f"TV {action} 目标 {qty} {self.unit_label}，下单前 REST 仍显示持仓，已拒绝叠仓",
                )
                return

            open_side = "BUY" if action == "LONG" else "SELL"
            logger.info(
                f"🚀 [唯一主仓] 极速开仓: {open_side} {qty} {self.unit_label} "
                f"| {self.symbol} | 档位 {self.regime}"
            )
            order = binance_client.place_market_order(action, qty, symbol=self.symbol)
            if not order:
                logger.error("开仓失败：市价单未成交")
                dingtalk.report_system_alert(
                    f"开仓失败 [{self.symbol}]",
                    f"TV {action} {qty} {self.unit_label} 市价单失败",
                )
                return
            time.sleep(2.0)

            pos = self._get_active_position()
            if not pos or pos["size"] <= 0:
                logger.error("开仓失败：成交后 REST 无持仓")
                return

            real_qty = pos["size"]
            if real_qty > qty * OPEN_OVERSIZE_RATIO:
                logger.error(
                    f"🚨 持仓超标: 目标 {qty} {self.unit_label}，实盘 {real_qty} "
                    f"(>{qty * OPEN_OVERSIZE_RATIO:.3f})，启动裁减"
                )
                dingtalk.report_system_alert(
                    f"持仓超标 · 自动裁减 [{self.symbol}]",
                    f"目标 {qty} {self.unit_label} (保证金 {margin_usdt:.0f}U)，"
                    f"实盘 {real_qty} @ {pos['entry_price']:.2f}，正在 reduceOnly 裁减",
                )
                real_qty = self._trim_position_to_target(qty, action)
                pos = self._get_active_position()
                if pos:
                    pos["size"] = real_qty

            self.current_side = action
            self.open_regime = self.regime
            self.open_atr = self.current_atr
            self.initial_qty = real_qty
            self.base_qty = float(real_qty)
            self.add_count = 0
            self._protect_and_monitor(
                real_qty, pos["entry_price"],
                budget_note=f"[{self.symbol}] {budget_txt} | ",
                target_qty=qty,
                sizing_meta=sizing_meta,
            )
        finally:
            self._open_in_progress = False

    def _protect_and_monitor(self, qty, entry_price, budget_note="", target_qty=0.0, sizing_meta=None):
        tp_pxs = self.tv_tps
        # 开仓后 current_sl 必须是 VPS 宽硬止损，绝不能写成成本价（否则会被当成雷达）
        self._refresh_vps_hard_sl(
            entry=entry_price, side=self.current_side,
            regime=int(getattr(self, "open_regime", None) or self.regime or 3),
            atr=float(getattr(self, "open_atr", None) or self.current_atr or 30),
            tv_sl_ref=getattr(self, "tv_sl_ref", 0) or None,
            source="开仓保护",
        )
        vps_sl = float(getattr(self, "tv_sl", 0) or 0)
        self.current_sl = vps_sl if vps_sl > 0 else 0.0
        self.best_price = entry_price
        self.shield_active = False
        self.shield_tiers_consumed = []
        self.tp_levels_consumed = []
        self._radar_stage_last = 0
        self._radar_activation_notified = False
        self._radar_armed_after_tp1 = False
        self._radar_handoff_done = False
        self._ws_tp1_fill_hint = False
        self._shield_handoff_notified = False
        self._open_settled_qty = float(qty or 0)
        self.initial_qty = float(qty or 0)
        self.watched_qty, self.watched_entry, self.monitoring = qty, entry_price, True
        self._save_state()

        self._ensure_price_ws()

        verified = self._wait_verify(lambda: self._verify_position(self.current_side))
        if verified:
            live_qty = verified["size"]
            if target_qty > 0 and live_qty > target_qty * OPEN_OVERSIZE_RATIO:
                live_qty = self._trim_position_to_target(target_qty, self.current_side)
                verified = self._get_active_position() or verified
                if verified:
                    verified = dict(verified)
                    verified["size"] = live_qty
                self.watched_qty = live_qty
                self.initial_qty = live_qty
                self._open_settled_qty = float(live_qty)
                self._save_state()
            else:
                # 以实盘核实仓为开仓基线，禁止保留偏高 target 制造「TP1=5% 已吃」幻觉
                self.watched_qty = live_qty
                self.initial_qty = float(live_qty)
                self._open_settled_qty = float(live_qty)
                self._save_state()

            self._scorched_earth_cancel_for_recover()
            self._enforce_pre_tp1_radar_standby(
                live_qty, verified["entry_price"], source="开仓保护",
            )
            self._enforce_defense_alignment(
                live_qty, verified["entry_price"],
                dynamic_sl=None, reason="开仓后防线对齐", rounds=4,
                recover_mode=True,
            )
            audit = self._wait_defense_settled(live_qty)
            matched, expected = audit["matched_full"], audit["expected"]
            curr_px = binance_client.get_current_price(self.symbol) or entry_price
            if expected > 0 and matched < expected:
                logger.warning(
                    f"⚠️ 开仓首轮 TP 仅 {matched}/{expected} → 追加核武重挂"
                )
                audit = self._nuclear_realign_tp(
                    live_qty, verified["entry_price"], dynamic_sl=None, rounds=3,
                )
                self._maintain_hard_shield(live_qty, curr_px, force=True)
                audit = self._wait_defense_settled(live_qty)
                matched, expected = audit["matched_full"], audit["expected"]
            verify_note = (
                f"{budget_note} | " if budget_note else ""
            ) + (
                f"持仓 {live_qty} ETH @ {verified['entry_price']:.2f} | "
                f"限价止盈 {matched}/{expected} 档 | {self._format_audit_summary(audit)} | "
                f"{self._tv_field_source_note(getattr(self, '_last_tv_field_sources', {}))}"
            )
            if target_qty > 0 and live_qty > target_qty * OPEN_OVERSIZE_RATIO:
                verify_note += f" | ⚠️ 超标目标 {target_qty} ETH"
            if self._should_activate_shield(curr_px):
                shield_ok = self._maintain_hard_shield(live_qty, curr_px, force=True)
                stop_px = self._shield_stop_price(verified["entry_price"])
                sl_note = format_vps_hard_sl_note(
                    self.current_side, verified["entry_price"],
                    float(getattr(self, "open_atr", None) or self.current_atr or 30),
                    int(getattr(self, "open_regime", None) or self.regime or 3),
                    tv_sl_ref=getattr(self, "tv_sl_ref", 0),
                ) if getattr(self, "tv_sl", 0) > 0 else "VPS硬止损待计算"
                if shield_ok:
                    verify_note += (
                        f" | {sl_note}已核实"
                        + (f" @ {stop_px:.2f}" if stop_px else "")
                    )
                else:
                    shield_audit = self._audit_shield_orders(live_qty, verified["entry_price"])
                    verify_note += (
                        f" | {sl_note}待核实"
                        + (f" ({','.join(shield_audit.get('issues', []))})" if shield_audit.get("issues") else "")
                    )
            self._record_open_log(
                self.current_side, live_qty, verified["entry_price"], source="open",
            )
            self._call_dingtalk(
                dingtalk.report_supervisor_open,
                side=self.current_side,
                entry_price=verified['entry_price'],
                tv_price=self.tv_price,
                qty=live_qty,
                tp_pxs=tp_pxs,
                atr=self.current_atr,
                regime=self.open_regime,
                tv_tps=self.tv_tps,
                verify_note=verify_note,
                tp_audit=audit,
                verified=(expected == 0 or matched >= expected),
                principal_balance=self.sizing_principal or binance_client.get_principal_wallet_balance(),
                margin_pct=float((sizing_meta or {}).get("effective_risk_pct", VPS_RISK_PCT) or VPS_RISK_PCT) / 100.0,
                margin_usdt=float((sizing_meta or {}).get("order_amount", 0) or 0),
                leverage=EXCHANGE_LEVERAGE,
                vps_sizing_meta=sizing_meta,
                tv_field_sources=getattr(self, "_last_tv_field_sources", {}),
                symbol=self.symbol,
                unit_label=self.unit_label,
            )
            if expected > 0 and matched < expected:
                self._open_tp_unconfirmed = True
                dupes = [lv for lv in audit.get("levels", []) if lv.get("status") == "duplicate"]
                hint = (
                    "重复 TP 占满可减仓额度 | 雷达将接力纠偏"
                    if dupes else "请查 logs/binance_brain.log"
                )
                dingtalk.report_system_alert(
                    f"开仓后限价止盈未全部挂上 [{self.symbol}]",
                    f"{self.current_side} {live_qty} {self.unit_label} | 仅 {matched}/{expected} 档 | "
                    f"{self._format_audit_summary(audit)} | {hint}",
                )
        else:
            logger.warning("开仓钉钉跳过：实盘持仓核查未通过")

        self._ensure_sentinel_running()

    def _tp_level_consumed(self, level):
        return level in (getattr(self, "tp_levels_consumed", []) or [])

    def _tp_filled_verified(self, level, live_qty=None, curr_px=0.0):
        """账本标记 + 减仓证据 + 该档限价已不在盘口 → 才认定 TP 真正成交"""
        level = int(level)
        if not self._tp_level_consumed(level):
            return False
        live_qty = float(live_qty if live_qty is not None else self.watched_qty or 0)
        initial = self._trusted_initial_qty(live_qty)
        inferred = self._infer_tp_consumed_sequential(initial, live_qty, curr_px)
        if level not in inferred:
            return False
        idx = level - 1
        if 0 <= idx < len(self.tv_tps) and self.tv_tps[idx] > 0:
            if self._has_tp_limit_at_price(self.tv_tps[idx]):
                return False
        return True

    def _likely_exchange_stop_exit(self, curr_px=0.0):
        """现价贴近最后挂出的止损价 → 多为交易所 STOP 触发（非 VPS 市价全平）"""
        px = float(
            curr_px or binance_client.get_current_price(self.symbol) or 0
        )
        sl = float(
            getattr(self, "_last_applied_exchange_sl", 0)
            or getattr(self, "tv_sl", 0)
            or 0
        )
        if sl <= 0 or px <= 0:
            return False
        return abs(px - sl) <= max(2.5, px * 0.002)

    def _enforce_pre_tp1_radar_standby(self, live_qty=None, curr_px=0.0, source=""):
        """
        TP1 成交前：强制雷达待命，止损仅 VPS 宽硬止损（完整呼吸空间）。
        TP1 成交后 / 雷达已锁存：不干预。
        """
        if self._radar_legitimately_armed(live_qty, curr_px):
            return False

        tv = float(getattr(self, "tv_sl", 0) or 0)
        entry = float(self.watched_entry or 0)
        changed = False

        consumed = list(getattr(self, "tp_levels_consumed", []) or [])
        if consumed:
            # 伪 TP 标记（账本有但未核实成交）→ 清除
            fake = [lv for lv in consumed if not self._tp_filled_verified(lv, live_qty, curr_px)]
            if fake:
                self.tp_levels_consumed = [lv for lv in consumed if lv not in fake]
                changed = True
                logger.warning(
                    f"📡 [{source or '雷达'}] 清除伪 TP{fake} 标记 "
                    f"(TP1 未实盘成交)"
                )

        if entry > 0 and self.current_sl:
            sl = float(self.current_sl)
            if self.current_side == "LONG" and sl > entry + 0.01:
                self.current_sl = tv if tv > 0 else sl
                changed = True
            elif self.current_side == "SHORT" and sl < entry - 0.01:
                self.current_sl = tv if tv > 0 else sl
                changed = True

        if tv > 0 and entry > 0:
            if self.current_side == "LONG" and (
                not self.current_sl or float(self.current_sl) > entry + 0.01
            ):
                self.current_sl = tv
                changed = True
            elif self.current_side == "SHORT" and (
                not self.current_sl or float(self.current_sl) < entry - 0.01
            ):
                self.current_sl = tv
                changed = True

        if getattr(self, "_radar_activation_notified", False):
            self._radar_activation_notified = False
            changed = True
        if getattr(self, "_shield_handoff_notified", False):
            self._shield_handoff_notified = False
            changed = True
        if getattr(self, "_radar_armed_after_tp1", False):
            self._radar_armed_after_tp1 = False
            changed = True
        if getattr(self, "_radar_handoff_done", False):
            self._radar_handoff_done = False
            changed = True
        if getattr(self, "_ws_tp1_fill_hint", False):
            self._ws_tp1_fill_hint = False
            changed = True
        if int(getattr(self, "_radar_stage_last", 0) or 0) > 0:
            self._radar_stage_last = 0
            changed = True

        if changed:
            self.best_price = entry if entry > 0 else self.best_price
            self._save_state()
            logger.info(
                f"📡 [{source or '雷达'}] TP1前待命·完整呼吸空间 | "
                f"vps_sl={tv:.2f} | entry={entry:.2f}"
            )
        return changed

    def _disarm_premature_radar(self, live_qty=None, curr_px=0.0, source=""):
        """
        TP1 未成交却出现过早保本线 → 恢复 VPS 宽硬止损。
        TP1 已成交 / 雷达已锁存 → 永不解除。
        """
        live_qty = float(live_qty or self.watched_qty or 0)
        curr_px = float(curr_px or binance_client.get_current_price(self.symbol) or 0)
        if self._radar_legitimately_armed(live_qty, curr_px):
            return False

        disarmed = False
        stale = list(getattr(self, "tp_levels_consumed", []) or [])
        tv = float(getattr(self, "tv_sl", 0) or 0)
        entry = float(self.watched_entry or 0)

        fake = [lv for lv in stale if not self._tp_filled_verified(lv, live_qty, curr_px)]
        if fake:
            self.tp_levels_consumed = [lv for lv in stale if lv not in fake]
            disarmed = True

        if entry > 0 and self.current_sl:
            if self.current_side == "LONG" and float(self.current_sl) > entry + 0.01:
                self.current_sl = tv if tv > 0 else float(self.current_sl)
                disarmed = True
            elif self.current_side == "SHORT" and float(self.current_sl) < entry - 0.01:
                self.current_sl = tv if tv > 0 else float(self.current_sl)
                disarmed = True

        if not disarmed:
            return False

        self._radar_activation_notified = False
        self._shield_handoff_notified = False
        self._radar_stage_last = 0
        self._radar_armed_after_tp1 = False
        self._radar_handoff_done = False
        self._ws_tp1_fill_hint = False
        self._save_state()
        logger.warning(
            f"📡 [{self.symbol}] [{source or '雷达'}] 解除过早雷达/伪TP{fake or stale or '标记'} "
            f"→ 恢复 vps_sl={tv:.2f} 宽止损 | entry={entry:.2f}"
        )
        dingtalk.report_system_alert(
            f"雷达解除·恢复呼吸空间 [{self.symbol}]",
            f"{self.current_side} {live_qty} {self._unit()} @ {entry:.2f} | "
            f"清除伪TP{fake or stale or '标记'} | vps_sl={tv:.2f} | "
            f"规则：三重验证+安全交棒前不激活雷达",
        )
        if live_qty > 0 and tv > 0:
            self._maintain_hard_shield(live_qty, curr_px, force=True, radar_sl=None)
        return True

    def _segment_progress(self, curr_px, from_px, to_px):
        """0~1：现价在 from_px→to_px 区间的推进比例"""
        from_px = float(from_px or 0)
        to_px = float(to_px or 0)
        if from_px <= 0 or to_px <= 0 or curr_px <= 0:
            return 0.0
        span = abs(to_px - from_px)
        if span <= 0:
            return 0.0
        if self.current_side == "LONG":
            return max(0.0, min(1.0, (curr_px - from_px) / span))
        return max(0.0, min(1.0, (from_px - curr_px) / span))

    def _radar_stage(self, curr_px):
        """
        雷达 5 阶段（全档位统一）：
        0=TP1前硬止损 · 1=TP1成交保本 · 2=TP1→TP2 50% · 3=达TP2 · 4=TP2→TP3 50% · 5=达TP3
        """
        live_qty = float(self.watched_qty or 0)
        # 禁止「stage 软锁存」在无 TP1 证据时自举为阶段≥1
        if not self._radar_legitimately_armed(live_qty, curr_px):
            return 0
        latched = int(getattr(self, "_radar_stage_last", 0) or 0) >= 1
        if curr_px <= 0 or not self.watched_entry:
            return max(1, latched) if latched else 1

        tp1 = float(self.tv_tps[0] or 0) if self.tv_tps else 0.0
        tp2 = float(self.tv_tps[1] or 0) if len(self.tv_tps) > 1 else 0.0
        tp3 = float(self.tv_tps[2] or 0) if len(self.tv_tps) > 2 else 0.0
        is_long = self.current_side == "LONG"
        stage = 1  # TP1 已成交基线

        if tp1 > 0 and tp2 > 0:
            p12 = self._segment_progress(curr_px, tp1, tp2)
            if p12 >= 0.50:
                stage = max(stage, 2)
        if tp2 > 0:
            if (is_long and curr_px >= tp2) or (not is_long and curr_px <= tp2):
                stage = max(stage, 3)
        if tp2 > 0 and tp3 > 0:
            p23 = self._segment_progress(curr_px, tp2, tp3)
            if p23 >= 0.50:
                stage = max(stage, 4)
        if tp3 > 0:
            if (is_long and curr_px >= tp3) or (not is_long and curr_px <= tp3):
                stage = max(stage, 5)
        return stage

    def _radar_stage_label(self, stage):
        return RADAR_STAGE_LABELS.get(int(stage or 0), f"阶段{stage}")

    def _compute_radar_sl_for_stage(self, stage, curr_px=0.0):
        """按阶段计算雷达止损价（多头：最高价-ATR×倍数；空头：最低价+ATR×倍数）"""
        stage = int(stage or 0)
        if stage <= 0:
            return None
        entry = float(self.watched_entry or 0)
        atr = float(self.current_atr or 30.0)
        best = float(self.best_price or entry)
        if stage == 1:
            cushion = entry * RADAR_STAGE_COST_BUFFER_PCT
            if self.current_side == "LONG":
                return round(entry + cushion, 2)
            if self.current_side == "SHORT":
                return round(entry - cushion, 2)
            return None
        mult = RADAR_STAGE_ATR_MULT.get(stage, 0.3)
        if self.current_side == "LONG":
            return round(best - atr * mult, 2)
        if self.current_side == "SHORT":
            return round(best + atr * mult, 2)
        return None

    def _refresh_radar_state_on_recover(self, curr_px, entry):
        """重启：仅当曾交棒成功且理想保本仍安全时恢复雷达；否则宽硬止损待命。"""
        if curr_px <= 0 or not entry:
            return

        if self.best_price == 0.0:
            self.best_price = entry
        if self.current_side == "LONG":
            self.best_price = max(self.best_price, curr_px)
        else:
            self.best_price = min(self.best_price, curr_px)

        live_qty = float(self.watched_qty or 0)
        triad = self._tp1_triad_ok(live_qty, curr_px, require_fresh=True)
        had_handoff = bool(getattr(self, "_radar_handoff_done", False))

        if not (triad and had_handoff):
            if self.current_sl == 0.0 and float(getattr(self, "tv_sl", 0) or 0) > 0:
                self.current_sl = float(self.tv_sl)
            self._radar_stage_last = 0
            self._radar_armed_after_tp1 = False
            self._radar_handoff_done = False
            self._ws_tp1_fill_hint = False
            logger.info(
                f"📡 [{self.symbol}] 重启雷达待命: 阶段0 | 保留 VPS硬止损 "
                f"(三重={triad} 交棒={had_handoff})"
            )
            return

        stage1 = self._compute_radar_sl_for_stage(1, curr_px)
        if stage1 is None or not self._ideal_radar_sl_is_safe(curr_px, stage1):
            self._radar_handoff_done = False
            self._radar_armed_after_tp1 = False
            self._radar_stage_last = 0
            if float(getattr(self, "tv_sl", 0) or 0) > 0:
                self.current_sl = float(self.tv_sl)
            logger.info(
                f"📡 [{self.symbol}] 重启：曾交棒但现价距理想保本不足 → "
                f"退回宽硬止损，待哨兵再交棒"
            )
            return

        self._radar_armed_after_tp1 = True
        self._radar_handoff_done = True
        stage = self._effective_radar_stage(curr_px)
        new_sl = self._compute_radar_sl_for_stage(stage, curr_px) or stage1
        if not self._ideal_radar_sl_is_safe(curr_px, new_sl):
            new_sl = stage1
        new_sl = self._clamp_radar_to_tv_floor(new_sl)
        if self.current_side == "LONG":
            self.current_sl = max(float(self.current_sl or entry), new_sl)
        else:
            self.current_sl = min(float(self.current_sl or entry), new_sl)
        self._radar_stage_last = max(stage, 1)
        logger.info(
            f"📡 [{self.symbol}] 重启雷达恢复: 阶段{stage} {self._radar_stage_label(stage)} | "
            f"best={self.best_price:.2f} | SL={self.current_sl:.2f}"
        )

    def _ensure_price_ws(self):
        """雷达/哨兵：公开行情 WS + 私有 User Data Stream（持仓/订单）"""
        binance_client.start_public_price_ws(self.symbol)
        binance_client.start_user_data_ws(
            self.symbol, on_event=self._on_user_data_ws_event,
        )

    def _on_user_data_ws_event(self, event_type, data):
        """WS 事件脉冲：仅处理本品种；LIMIT 近 TP1 成交仅作提示，不直接启雷达"""
        et = str(event_type or "")
        # 多品种共听同一 listenKey：非本品种订单/仓位变动忽略（ACCOUNT 仍可脉冲）
        if et == "ORDER_TRADE_UPDATE":
            o = (data or {}).get("o") or {}
            sym = str(o.get("s") or "").upper()
            if sym and sym != self.symbol.upper():
                return
        if et in ("ACCOUNT_UPDATE", "ORDER_TRADE_UPDATE", "CONDITIONAL_ORDER_TRIGGER",
                  "listenKeyExpired"):
            self._ws_defense_pulse = True
            if et == "ORDER_TRADE_UPDATE":
                o = (data or {}).get("o") or {}
                otype = str(o.get("o") or o.get("orderType") or "").upper()
                status = str(o.get("X") or o.get("status") or "").upper()
                if otype in ("LIMIT",) and status in (
                    "FILLED", "PARTIALLY_FILLED",
                ):
                    reduce_only = bool(o.get("R") if "R" in o else o.get("reduceOnly"))
                    try:
                        px = float(o.get("ap") or o.get("p") or o.get("price") or 0)
                    except (TypeError, ValueError):
                        px = 0.0
                    tp1 = float(self.tv_tps[0] or 0) if self.tv_tps else 0.0
                    if (
                        reduce_only
                        and tp1 > 0
                        and px > 0
                        and abs(px - tp1) <= max(1.5, tp1 * 0.0012)
                    ):
                        self._ws_tp1_fill_hint = True
                        logger.info(
                            f"📡 [{self.symbol}] UD-WS TP1 限价成交提示 @ {px:.2f} "
                            f"(仍需哨兵减仓核实后才启雷达)"
                        )
                elif otype in ("STOP", "STOP_MARKET") and status in (
                    "NEW", "PARTIALLY_FILLED", "FILLED",
                ):
                    self._tv_sl_missing_alerted = False
            logger.debug(f"📡 [{self.symbol}] UD-WS 脉冲 {et}")

    def _tp1_distance(self):
        if self.tv_tps[0] > 0 and self.watched_entry:
            return abs(self.tv_tps[0] - self.watched_entry)
        return self.current_atr * 1.5

    def _tp1_direction_progress(self, curr_px):
        """0~1：现价朝 TP1 价位的推进比例（仅展示/日志，不触发雷达）"""
        if curr_px <= 0 or not self.watched_entry:
            return 0.0
        tp1_dist = self._tp1_distance()
        if tp1_dist <= 0:
            return 0.0
        if self.current_side == "LONG":
            return max(0.0, min(1.0, (curr_px - self.watched_entry) / tp1_dist))
        if self.current_side == "SHORT":
            return max(0.0, min(1.0, (self.watched_entry - curr_px) / tp1_dist))
        return 0.0

    def _radar_legitimately_armed(self, live_qty=None, curr_px=0.0):
        """仅交棒核实后才武装；三重证据不足时绝不软锁存。"""
        if getattr(self, "_open_in_progress", False):
            return False
        if getattr(self, "_radar_handoff_done", False):
            return True
        # 证据齐但尚未安全交棒 → 仍不算武装（避免贴市保本）
        return False

    def _effective_radar_stage(self, curr_px):
        """雷达阶段：已武装后只升不降"""
        stage = self._radar_stage(curr_px)
        if not self._radar_legitimately_armed(self.watched_qty, curr_px):
            return 0
        latched = int(getattr(self, "_radar_stage_last", 0) or 0)
        return max(stage, latched, 1)

    def _radar_activation_progress(self, curr_px):
        """0~1：雷达阶段进度（5 阶段制）"""
        return min(1.0, self._effective_radar_stage(curr_px) / 5.0)

    def _should_radar_trail(self, curr_px):
        """仅交棒成功后追踪；交棒前即使三重通过也只保留宽硬止损。"""
        return bool(getattr(self, "_radar_handoff_done", False))

    def _compute_radar_sl(self):
        if not self.watched_entry or self.best_price <= 0:
            return None
        curr_px = float(binance_client.get_current_price(self.symbol) or 0)
        stage = self._effective_radar_stage(curr_px)
        if stage < 1:
            return None
        raw = self._compute_radar_sl_for_stage(stage, curr_px)
        if raw is None:
            return None
        # 止损只向有利方向移动，永不回退
        if self.current_sl and float(self.current_sl) > 0:
            if self.current_side == "LONG" and raw < float(self.current_sl):
                raw = float(self.current_sl)
            elif self.current_side == "SHORT" and raw > float(self.current_sl):
                raw = float(self.current_sl)
        prev_stage = int(getattr(self, "_radar_stage_last", 0) or 0)
        if stage > prev_stage:
            logger.info(
                f"📡 雷达阶段 {prev_stage}→{stage} {self._radar_stage_label(stage)} | "
                f"SL→{raw:.2f} | best={self.best_price:.2f}"
            )
            self._radar_stage_last = stage
            self._save_state()
        elif prev_stage < 1 and stage >= 1:
            self._radar_stage_last = stage
            self._save_state()
        return self._clamp_radar_to_tv_floor(raw)

    def _sync_radar_sl_from_best(self, curr_px):
        if not self._should_radar_trail(curr_px):
            return self.current_sl
        new_sl = self._compute_radar_sl()
        if new_sl is None:
            return self.current_sl
        if self.current_side == "LONG" and new_sl > self.current_sl:
            logger.info(
                f"📈 雷达止损预算刷新: {self.current_sl:.2f} → {new_sl:.2f} "
                f"(best={self.best_price:.2f})"
            )
            self.current_sl = new_sl
            self._save_state()
        elif self.current_side == "SHORT" and (
                self.current_sl >= self.watched_entry or new_sl < self.current_sl
        ):
            logger.info(
                f"📉 雷达止损预算刷新: {self.current_sl:.2f} → {new_sl:.2f} "
                f"(best={self.best_price:.2f})"
            )
            self.current_sl = new_sl
            self._save_state()
        return self.current_sl

    def _bump_best_on_tp_fill(self, old_qty, new_qty, curr_px):
        if new_qty >= old_qty or curr_px <= 0:
            return
        if self.current_side == "LONG":
            candidates = [self.best_price, curr_px]
            for tp in self.tv_tps:
                if tp > 0 and curr_px >= tp - 2.0:
                    candidates.append(tp)
            new_best = max(candidates)
            if new_best > self.best_price + 0.001:
                logger.info(
                    f"📊 止盈吃单刷新 best_price: {self.best_price:.2f} → {new_best:.2f} "
                    f"(qty {old_qty}→{new_qty})"
                )
                self.best_price = new_best
        else:
            candidates = [self.best_price, curr_px]
            for tp in self.tv_tps:
                if tp > 0 and curr_px <= tp + 2.0:
                    candidates.append(tp)
            new_best = min(candidates)
            if new_best < self.best_price - 0.001:
                logger.info(
                    f"📊 止盈吃单刷新 best_price: {self.best_price:.2f} → {new_best:.2f} "
                    f"(qty {old_qty}→{new_qty})"
                )
                self.best_price = new_best

    def _sentinel_poll_sec(self, curr_px=0.0):
        """雷达已激活=5s；有仓位常态=8s（5~10s 区间）"""
        if self._is_radar_active() or self._radar_legitimately_armed(self.watched_qty, curr_px):
            return SENTINEL_POLL_RADAR
        return SENTINEL_POLL_NORMAL

    def _process_radar_trailing(self, real_amt, curr_px):
        """实时雷达：TP1 成交后激活；跟踪 best_price，止损只向有利方向移动"""
        if not self._should_radar_trail(curr_px):
            return False
        real_amt = float(self._resolve_live_qty(real_amt) or 0)
        if real_amt <= 0:
            return False

        if not self._is_radar_active():
            return self._perform_radar_handoff(
                real_amt, curr_px, reason="TP1成交 · 雷达保本激活",
            )

        new_sl = self._compute_radar_sl()
        if new_sl is None:
            return False
        new_sl = self._clamp_radar_sl_for_market(curr_px, new_sl)
        if not self._can_safely_place_radar_sl(curr_px, new_sl):
            return False

        moved = False
        stage = self._effective_radar_stage(curr_px)

        if self.current_side == "LONG":
            if new_sl > self.current_sl + 1.0:
                old_sl = float(self.current_sl or 0)
                self.current_sl = new_sl
                self._save_state()
                sl_placed = self._realign_radar_defenses(real_amt, self.watched_entry, new_sl)
                self._log_radar_update(stage, old_sl, new_sl, "实时跟踪推升", curr_px)
                self._cancel_stale_tp_beyond_radar(new_sl, real_amt)
                self._report_radar_intervention(
                    real_amt, new_sl,
                    f"🚀 R{self.regime} 阶段{stage} {self._radar_stage_label(stage)} "
                    f"雷达推升至 {new_sl:.2f}",
                    sl_placed=sl_placed,
                )
                moved = True
        else:
            if self.current_sl >= self.watched_entry or new_sl < self.current_sl - 1.0:
                old_sl = float(self.current_sl or 0)
                self.current_sl = new_sl
                self._save_state()
                sl_placed = self._realign_radar_defenses(real_amt, self.watched_entry, new_sl)
                self._log_radar_update(stage, old_sl, new_sl, "实时跟踪下压", curr_px)
                self._cancel_stale_tp_beyond_radar(new_sl, real_amt)
                self._report_radar_intervention(
                    real_amt, new_sl,
                    f"🚀 R{self.regime} 阶段{stage} {self._radar_stage_label(stage)} "
                    f"雷达下压至 {new_sl:.2f}",
                    sl_placed=sl_placed,
                )
                moved = True
        return moved

    def _sentinel_loop(self):
        """哨兵：持仓/TP 防线 + 雷达移动保本（WS推送优先，5~8 秒轮询兜底）"""
        self._sentinel_active = True
        self._ensure_price_ws()
        last_px = 0.0
        try:
            while self.monitoring:
                try:
                    if not self._lock.acquire(timeout=2.0):
                        continue
                    try:
                        ws_pulse = bool(getattr(self, "_ws_defense_pulse", False))
                        if ws_pulse:
                            self._ws_defense_pulse = False
                            self._ws_fast_poll = True
                        pos = self._get_active_position()
                        real_amt = pos["size"] if pos else 0.0
                        actual_side = pos["side"] if pos else None

                        if not pos or real_amt == 0:
                            if time.time() < getattr(self, "_sentinel_grace_until", 0):
                                logger.debug(
                                    "哨兵宽限期：跳过空仓判定（防重启误清场）"
                                )
                                continue
                            if self.watched_qty > 0:
                                self._purge_all_defense_orders_on_flat(
                                    "哨兵感知空仓·抢先撤TP123",
                                )
                                if not self._confirm_position_flat():
                                    logger.warning(
                                        "⚠️ [哨兵] 首次无仓但复核仍有持仓 → 跳过误清场"
                                    )
                                    continue
                                flat_meta = self._infer_flat_close_meta(
                                    curr_px=last_px,
                                    hint_reason="仓位归零 (止盈吃单 / 人工全平 / 止损触发)",
                                )
                                self._handle_manual_flat_detected(
                                    flat_meta.get("tv_reason", "仓位归零"),
                                    close_meta=flat_meta,
                                    curr_px=last_px,
                                )
                            break

                        if self.watched_qty > 0 and self._should_finalize_tp_victory(real_amt):
                            self._sweep_dust_and_finalize(
                                "仓位归零 (止盈吃单 / 人工全平 / TV 强制平仓)"
                            )
                            break

                        tv_opposite = self._strict_tv_opposite_side(actual_side)
                        if (
                            tv_opposite
                            and actual_side
                            and not self._live_aligns_with_credible_tv(actual_side)
                        ):
                            reason = (
                                f"致命方向背离：实盘({actual_side}) vs "
                                f"最新TV({tv_opposite}) [实盘监督]"
                            )
                            verify_note = (
                                f"触发源: 实盘监督 | 最新TV {tv_opposite} | "
                                f"实盘反向 {actual_side}"
                            )
                            self._close_all(
                                reason,
                                force_align=(actual_side, tv_opposite),
                                force_verify_note=verify_note,
                            )
                            break

                        curr_px = binance_client.get_current_price(self.symbol)
                        if curr_px <= 0:
                            curr_px = last_px
                        elif curr_px > 0:
                            last_px = curr_px
                        if curr_px > 0:
                            if self.current_side == "LONG":
                                self.best_price = max(self.best_price, curr_px)
                            else:
                                self.best_price = min(self.best_price, curr_px)

                        qty_changed = False
                        if abs(real_amt - self.watched_qty) > REGIME_CAP_TOLERANCE_ETH:
                            if self._is_material_qty_change(self.watched_qty, real_amt):
                                qty_changed = True
                                old_qty = self.watched_qty
                                if real_amt <= DUST_QTY_ETH:
                                    self._purge_all_defense_orders_on_flat(
                                        "仓位归零·抢先撤TP123",
                                    )
                                self.watched_qty = real_amt
                                self.watched_entry = pos["entry_price"]
                                change, result = self._handle_smart_qty_change(
                                    old_qty, real_amt, curr_px,
                                )
                                if result:
                                    self._report_qty_change_dingtalk(
                                        old_qty, real_amt, result, change=change,
                                    )
                            else:
                                drift = self._qty_change_ratio(self.watched_qty, real_amt)
                                if drift >= QTY_DRIFT_TOLERANCE_PCT:
                                    logger.info(
                                        f"📎 [哨兵] 仓位微漂 {self.watched_qty}→{real_amt} ETH "
                                        f"({drift:.2%}，未达 {QTY_ALIGN_MIN_PCT:.0%} 对齐阈值)，仅同步账本"
                                    )
                                self.watched_qty = real_amt
                                self.watched_entry = pos["entry_price"]
                                self._save_state()

                        self._scan_ticks += 1
                        skip_radar = getattr(self, "_open_in_progress", False) or getattr(
                            self, "_defense_align_in_progress", False
                        )
                        if not skip_radar:
                            if getattr(self, "_post_recover_radar_pulse", False):
                                self._post_recover_radar_pulse = False
                                if curr_px > 0:
                                    self._process_radar_trailing(real_amt, curr_px)
                                self._radar_guardian_audit(real_amt, curr_px)
                                logger.info("📡 [哨兵] 重启后立即雷达脉冲完成")
                            elif not qty_changed:
                                self._radar_guardian_audit(real_amt, curr_px)

                            if curr_px <= 0:
                                continue

                            self._process_directional_defenses(real_amt, curr_px)
                            if (
                                self._tp1_filled_verified(real_amt, curr_px)
                                and not self._is_radar_active()
                                and self._scan_ticks % 5 == 0
                            ):
                                logger.info(
                                    f"📡 雷达待交棒: TP1已成交 | "
                                    f"现价 {curr_px:.2f} | 轮询 {SENTINEL_POLL_RADAR}s"
                                )
                        elif curr_px <= 0:
                            continue
                    finally:
                        self._lock.release()
                except Exception as e:
                    logger.error(f"哨兵异常: {e}")
                if self.monitoring:
                    if getattr(self, "_ws_fast_poll", False):
                        self._ws_fast_poll = False
                        time.sleep(1.5)
                    else:
                        time.sleep(self._sentinel_poll_sec(last_px))
        finally:
            self._sentinel_active = False

    def _rebuild_defenses(self, qty, entry, dynamic_sl=None):
        close_side = "SHORT" if self.current_side == "LONG" else "LONG"

        live_qty = self._resolve_live_qty(qty)
        if live_qty <= 0:
            logger.warning(f"重建防线跳过：交易所无可用持仓 (传入 {qty} ETH)")
            return 0

        self._cancel_all_tp_limit_orders()
        time.sleep(0.35)

        if abs(live_qty - qty) > 0.001:
            self.watched_qty = live_qty
            self._save_state()

        consumed = getattr(self, "tp_levels_consumed", []) or []
        placed = 0

        logger.info(
            f"🕸️ 补挂 TP: 总 {live_qty} ETH | 已成交 TP{consumed or '无'} | "
            f"R{self._tp_split_regime()} 剩余档"
        )

        for lv in self._expected_tp_levels(live_qty):
            q, px = lv["qty"], lv["price"]
            if q > 0 and px > 0:
                res = binance_client.place_limit_order(close_side, q, px, symbol=self.symbol, reduce_only=True)
                if res:
                    placed += 1
                time.sleep(0.35)

        self._maintain_hard_shield(
            live_qty, None, force=True, radar_sl=dynamic_sl,
        )
        return placed

    def _close_all(self, reason="", force_align=None, reset_state=True, close_meta=None,
                   force_verify_note=""):
        """先撤全部挂单再阶梯强平；返回是否已空仓"""
        prev_side = self.current_side
        self._purge_all_defense_orders_on_flat(reason or "强平前撤单")
        closed_successfully = False

        for round_i in range(6):
            pos = position_manager.get_position(self.symbol)
            if not pos or float(pos.get("positionAmt", 0)) == 0:
                closed_successfully = True
                break

            amt = float(pos["positionAmt"])
            close_side = "SELL" if amt > 0 else "BUY"
            live_sz = round(abs(amt), 3)
            logger.info(f"🔪 强平第 {round_i + 1}/6 轮: {close_side} {live_sz} ETH reduceOnly")
            binance_client.place_market_order(close_side, live_sz, symbol=self.symbol, reduce_only=True)
            time.sleep(1.5)

        if not closed_successfully:
            residual = self._get_active_position()
            residual_sz = residual["size"] if residual else 0.0
            if residual_sz > 0 and self._is_dust_qty(residual_sz):
                close_side = "SELL" if residual["side"] == "LONG" else "BUY"
                logger.warning(f"🐜 强平后残 {residual_sz} ETH，触发蚂蚁仓扫尾")
                binance_client.place_market_order(close_side, residual_sz, symbol=self.symbol, reduce_only=True)
                time.sleep(1.0)
                closed_successfully = self._verify_flat()
            if not closed_successfully:
                residual = self._get_active_position()
                residual_sz = residual["size"] if residual else 0.0
                logger.error(f"❌ 6 轮强平后仍有残单: {residual_sz} ETH")
                dingtalk.report_system_alert(
                    "强平未完全归零",
                    f"6 轮市价平仓后仍剩 {residual_sz} ETH，请人工核查币安盘口",
                )

        if reset_state:
            if closed_successfully:
                self.monitoring = False
                self.watched_qty = 0.0
                self.base_qty = 0.0
                self.add_count = 0
                self.current_side = None
                self.shield_active = False
                self.shield_tiers_consumed = []
                self.tp_levels_consumed = []
                self._snapshot_sizing_principal("全平后本金重置")
            else:
                residual = self._get_active_position()
                if residual:
                    self.watched_qty = residual["size"]
                    self.current_side = residual["side"]
                    self.watched_entry = residual["entry_price"]
                    logger.warning(
                        f"强平未归零，账本同步实盘: {self.current_side} {self.watched_qty} ETH"
                    )
            self._save_state()

        purge = self._purge_all_defense_orders_on_flat(reason or "强平后撤单")
        if closed_successfully and not purge.get("ok"):
            dingtalk.report_system_alert(
                "强平后挂单未净",
                f"{reason} | 剩余 {purge.get('remaining', '?')} 单 | "
                f"TP {purge.get('tp_remaining', '?')} 张",
            )

        if reason and closed_successfully:
            if force_align:
                real_side, expected_side = force_align
                flat = self._wait_verify(self._verify_flat, retries=6, delay=0.5)
                verify_note = "盘口无持仓 | 挂单已清空 | 智慧大脑复位待命"
                if not flat:
                    verify_note += " | REST 同步略延迟"
                self._call_dingtalk(
                    dingtalk.report_force_align,
                    real_side=real_side,
                    expected_side=expected_side,
                    verify_note=force_verify_note or verify_note,
                    verified=flat,
                )
            else:
                self._report_flat_close(reason, close_meta=close_meta)

        if closed_successfully:
            self._sweep_orphan_reverse_after_flat(prev_side=prev_side, reason=reason)

        return closed_successfully

    def recover_state_on_startup(self):
        """重启闪电接管：对账 TV/开仓日志 → 核实实盘 → 智能补挂 TP123 → 恢复雷达"""
        if not self._try_acquire_recover_singleton():
            return
        try:
            saved_monitoring = False
            if os.path.exists(self.state_file):
                with open(self.state_file, 'r') as f:
                    s = json.load(f)
                    saved_monitoring = bool(s.get("monitoring"))
                    self.last_tv_side = s.get("last_tv_side")
                    self.current_side = s.get("current_side")
                    self.current_sl = s.get("current_sl", 0.0)
                    self.regime = s.get("regime", 3)
                    self.current_atr = s.get("current_atr", 30.0)
                    self.tv_tps = self._sanitize_tp_prices(s.get("tv_tps", [0.0, 0.0, 0.0]))
                    self.tv_price = float(s.get("tv_price", 0.0) or 0.0)
                    self.best_price = s.get("best_price", 0.0)
                    self.watched_qty = s.get("watched_qty", 0.0)
                    self.watched_entry = s.get("watched_entry", 0.0)
                    self.initial_qty = s.get("initial_qty", 0.0)
                    self.last_tv_signal = s.get("last_tv_signal")
                    self.open_regime = int(s.get("open_regime", s.get("regime", 3)) or 3)
                    self.open_atr = float(s.get("open_atr", s.get("current_atr", 30.0)) or 30.0)
                    self.shield_active = bool(s.get("shield_active", False))
                    self.shield_tiers_consumed = list(s.get("shield_tiers_consumed", []) or [])
                    self.tp_levels_consumed = list(s.get("tp_levels_consumed", []) or [])
                    self.shield_sized_qty = float(s.get("shield_sized_qty", 0) or 0)
                    if self.shield_sized_qty > 0:
                        self._shield_arm_notified = True
                    self.sizing_principal = float(s.get("sizing_principal", 0) or 0)
                    self.tv_sl = float(s.get("tv_sl", 0) or 0)
                    self.tv_sl_ref = float(s.get("tv_sl_ref", 0) or 0)
                    self._radar_stage_last = int(s.get("radar_stage_last", 0) or 0)
                    self._radar_armed_after_tp1 = bool(
                        s.get("radar_armed_after_tp1", False)
                    )
                    self._radar_handoff_done = bool(
                        s.get("radar_handoff_done", s.get("radar_armed_after_tp1", False))
                    )
                    self._open_settled_qty = float(
                        s.get("open_settled_qty", s.get("initial_qty", 0)) or 0
                    )
                    self._last_applied_exchange_sl = float(
                        s.get("last_applied_exchange_sl", 0) or 0
                    )
                    self.tv_risk_pct = float(s.get("tv_risk_pct", 0) or 0)
                    self.tv_qty_ratio = float(s.get("tv_qty_ratio", 1.0) or 1.0)
                    self.tv_entry_type = s.get("tv_entry_type", ENTRY_TYPE_OPEN)
                    self.tv_sizing_leverage = float(
                        s.get("tv_sizing_leverage", s.get("leverage", EXCHANGE_LEVERAGE))
                        or EXCHANGE_LEVERAGE
                    )
                    self.leverage = EXCHANGE_LEVERAGE
                    self.base_qty = float(s.get("base_qty", 0) or 0)
                    self.add_count = int(s.get("add_count", 0) or 0)
                    if self.sizing_principal <= 0:
                        eq = binance_client.get_principal_wallet_balance()
                        if eq > 0:
                            self.sizing_principal = eq

            if self.base_qty <= 0 and os.path.exists(self.state_file):
                last_open = self._load_last_journal_entry(OPEN_JOURNAL)
                if last_open:
                    jq = float(last_open.get("qty", 0) or 0)
                    if jq > 0:
                        self.base_qty = jq
                        logger.info(f"📖 恢复 base_qty 取自开仓日志 {jq} ETH")

            if self._scan_and_sweep_dust_on_startup(was_monitoring=saved_monitoring):
                return

            if self._recover_missed_flat_on_startup(was_monitoring=saved_monitoring):
                return

            pos = self._get_active_position()
            if pos:
                self._recover_in_progress = True
                recover_ok = False
                recover_err = ""
                radar_active = False
                sl_ok = False
                if not self._lock.acquire(timeout=120.0):
                    logger.error("❌ 重启接管无法获取锁，跳过")
                    self._recover_in_progress = False
                    dingtalk.report_system_alert(
                        "重启接管失败",
                        "无法获取仓位锁（120s超时），请稍后重启或检查是否有僵死进程",
                    )
                    return
                try:
                    reconcile = self._reconcile_context_on_recover(pos)
                    reconcile_notes = reconcile["notes"]
                    side = pos["side"]

                    if self._live_aligns_with_credible_tv(side):
                        if reconcile.get("direction_mismatch"):
                            logger.warning(
                                f"🔄 [重启] 陈旧对账报方向背离，但实盘 {side} "
                                f"与最新TV信源同向 → 闪电接管"
                            )
                            self.last_tv_side = side
                            reconcile["direction_mismatch"] = False
                    elif self._enforce_tv_direction_or_flat(pos, source="VPS重启"):
                        self._recover_in_progress = False
                        return

                    if reconcile.get("manual_open") or float(self.watched_qty or 0) <= 0:
                        logger.info(
                            f"🔄 [重启] 人工/孤儿同向仓 {side} {pos['size']} ETH "
                            f"→ 闪电接管 TP123+止损+雷达"
                        )
                        self._perform_live_takeover(
                            pos,
                            source="VPS重启",
                            manual_open=bool(reconcile.get("manual_open")),
                            qty_change=reconcile.get("qty_manual_change"),
                        )
                        recover_ok = True
                        self._recover_in_progress = False
                        return

                    real_amt = pos["size"]
                    self.current_side = side

                    hydrate_notes = self._hydrate_tv_defense_context(pos)
                    reconcile_notes.extend(hydrate_notes)

                    align_notes = self._apply_recover_live_alignment(side, reconcile)
                    reconcile_notes.extend(align_notes)

                    saved_initial = self._resolve_open_initial_qty(real_amt, self.watched_entry)
                    if saved_initial <= 0:
                        saved_initial = real_amt
                    if self.base_qty <= 0:
                        self.base_qty = float(saved_initial or real_amt)
                    self.watched_qty = real_amt
                    self.initial_qty = saved_initial
                    self.watched_entry = pos["entry_price"]
                    if not getattr(self, "open_regime", None):
                        self.open_regime = self.regime
                    if not getattr(self, "open_atr", None):
                        self.open_atr = self.current_atr
                    qty_change = reconcile.get("qty_manual_change")

                    curr_px = binance_client.get_current_price(self.symbol)
                    stack = self._ensure_full_defense_stack(
                        real_amt, self.watched_entry, curr_px or 0,
                        source="VPS重启", manual_fresh=bool(reconcile.get("manual_open")),
                    )
                    audit = stack.get("audit") or {}
                    result = stack.get("result") or {}
                    health = stack.get("health") or {}
                    sl_ok = stack.get("shield_ok", False)
                    matched = audit.get("matched_full", 0)
                    expected = audit.get("expected", 0)
                    radar_active = (
                        health.get("radar_active")
                        or health.get("should_radar")
                        or self._is_radar_active()
                    )
                    reconcile_notes.extend(stack.get("notes") or [])
                    _rebuilt = result.get("rebuilt", False)

                    logger.info(
                        f"🔄 [系统重启点火] 检测到实盘持仓 {self.current_side} {real_amt} ETH @ "
                        f"{self.watched_entry:.2f} | 开单 {saved_initial} ETH | "
                        f"已成交 TP{getattr(self, 'tp_levels_consumed', []) or '无'} | "
                        f"雷达={'已激活' if radar_active else '待命(TP1后)'} | "
                        f"TV对齐 {self.last_tv_side} | 对账 {len(reconcile_notes)} 项"
                    )

                    self.monitoring = True
                    self._save_state()
                    self._ensure_price_ws()
                    self._record_open_log(
                        self.current_side, real_amt, self.watched_entry, source="recover",
                    )

                    verified = self._wait_verify(
                        lambda: self._verify_position_qty(real_amt, self.current_side),
                        retries=8,
                        delay=0.5,
                    )
                    entry_px = float((verified or pos)["entry_price"])

                    tv_note = ""
                    if self.last_tv_signal:
                        tv_note = (
                            f" | 最新TV: {self.last_tv_signal.get('action')} "
                            f"@{self.last_tv_signal.get('ts', '')}"
                        )
                    reconcile_txt = (" | " + " ; ".join(reconcile_notes)) if reconcile_notes else ""
                    skip_note = " | 盘口已齐全，未重复补挂" if not _rebuilt else ""
                    verify_note = (
                        f"接管 {real_amt} ETH @ {entry_px:.2f} | "
                        f"开单 {saved_initial} ETH | "
                        f"已成交 TP{getattr(self, 'tp_levels_consumed', []) or '无'} | "
                        f"TV方向 {self.last_tv_side} | "
                        f"tv_sl={float(getattr(self, 'tv_sl', 0) or 0):.2f} | "
                        f"止盈 {matched}/{expected} 档 | "
                        f"{self._format_audit_summary(audit)}{skip_note}{tv_note}{reconcile_txt}"
                    )
                    if not verified:
                        verify_note += " | REST 同步略延迟"
                    if qty_change:
                        old_q, new_q, action_msg = qty_change
                        self._call_dingtalk(
                            dingtalk.report_manual_position_change,
                            action_type=action_msg,
                            old_qty=old_q,
                            new_qty=new_q,
                            new_entry_price=entry_px,
                            verify_note=f"重启接管检测 | {verify_note}",
                            tp_audit=audit,
                            verified=bool(verified),
                        )
                    if expected > 0 and matched < expected:
                        dupes = [lv for lv in audit.get("levels", []) if lv.get("status") == "duplicate"]
                        hint = (
                            "重复 TP 占满可减仓额度→TP3 无法挂 | 非 API 权限问题"
                            if dupes else "请查 logs/binance_brain.log 是否有 [撤单失败]/[限价单失败]"
                        )
                        self._recover_tp_unconfirmed = True
                        dingtalk.report_system_alert(
                            "重启接管后限价止盈未对齐",
                            f"{self.current_side} {real_amt} ETH @ {entry_px:.2f} | "
                            f"仅 {matched}/{expected} 档 | {self._format_audit_summary(audit)} | "
                            f"{hint} | 雷达哨兵将接力纠偏；仍失败请 APP 手动全撤后重启",
                        )

                    health_txt = (
                        f" | 盈亏态 {health.get('pnl_label', '未知')} | "
                        f"硬止损 {health.get('shield_status', '待核实')} | "
                        f"策略 {health.get('defense_plan', 'TP123+硬止损')}"
                    )
                    verify_note = verify_note + health_txt

                    self._sentinel_grace_until = time.time() + SENTINEL_GRACE_AFTER_RECOVER_SEC

                    self._call_dingtalk(
                        dingtalk.report_recover_takeover,
                        side=self.current_side,
                        qty=real_amt,
                        entry=entry_px,
                        tv_tps=self.tv_tps,
                        regime=self.regime,
                        radar_active=radar_active,
                        sl_price=self.current_sl,
                        verify_note=verify_note,
                        tp_matched=matched,
                        tp_expected=expected,
                        tp_audit=audit,
                        last_tv_signal=self.last_tv_signal,
                        radar_sl_ok=sl_ok,
                        pnl_label=health.get("pnl_label", ""),
                        defense_plan=health.get("defense_plan", ""),
                        shield_status=health.get("shield_status", ""),
                        radar_progress=health.get("radar_progress", 0),
                        tv_aligned=health.get("tv_match", True),
                        qty_aligned=health.get("qty_match", True),
                        initial_qty=saved_initial,
                        tp_consumed_levels=getattr(self, "tp_levels_consumed", []) or [],
                    )
                    policy_actions = stack.get("notes") or []
                    logger.info(
                        f"  -> 🎉 实盘阵地接管完毕 | {health.get('pnl_label', '')} | "
                        f"防线 {' · '.join(policy_actions) if policy_actions else '已核实'}"
                    )
                    recover_ok = True
                except Exception as e:
                    import traceback
                    recover_err = f"{e}\n{traceback.format_exc()[-800:]}"
                    logger.error(f"❌ 重启接管步骤异常: {recover_err}")
                    self.monitoring = True
                    self._save_state()
                    dingtalk.report_system_alert(
                        "重启接管部分失败",
                        f"实盘仍有仓，已尽力启动哨兵接力 | {recover_err}",
                    )
                finally:
                    self._recover_in_progress = False
                    self._lock.release()

                if recover_ok and radar_active:
                    logger.info(
                        f"📡 [重启] 雷达哨兵已点火 | SL={self.current_sl:.2f} | "
                        f"止损={'已挂/已确认' if sl_ok else '待哨兵补挂'}"
                    )

                if not self._sentinel_active:
                    threading.Thread(
                        target=self._sentinel_loop, daemon=True, name="sentinel",
                    ).start()
                elif recover_err:
                    self._post_recover_radar_pulse = True
            else:
                binance_client.cancel_all_open_orders(self.symbol)
                logger.info("🔄 [系统重启点火] 盘口干净无持仓，账本复位为空仓待命。")
                self.monitoring = False
                self.watched_qty = 0.0
                self.base_qty = 0.0
                self.add_count = 0
                self.current_side = None
                self._save_state()
                flat_ok = self._wait_verify(self._verify_flat, retries=6, delay=0.5)
                standby_note = (
                    f"重启完成 | 盘口无持仓 | 挂单已清空 | {BINANCE_VPS_VERSION}"
                )
                if not flat_ok:
                    standby_note += " | REST 同步略延迟"
                dingtalk.report_recover_standby(
                    verify_note=standby_note,
                    version=BINANCE_VPS_VERSION,
                )
        except Exception as e:
            import traceback
            err_detail = traceback.format_exc()[-1200:]
            logger.error(f"❌ 闪电接管异常: {e}\n{err_detail}")
            pos = self._get_active_position()
            if pos:
                self.monitoring = True
                self._post_recover_radar_pulse = True
                if not self._sentinel_active:
                    threading.Thread(
                        target=self._sentinel_loop, daemon=True, name="sentinel",
                    ).start()
            dingtalk.report_system_alert("重启接管失败", f"{e}\n{err_detail[-400:]}")


position_supervisor = None  # 兼容旧 import；见 get_supervisor / SUPERVISORS
SUPERVISORS = {}


def get_supervisor(symbol="ETHUSDT"):
    """按品种取军师（懒创建）。"""
    from symbol_config import resolve_binance_symbol
    meta = resolve_binance_symbol(symbol)
    sym = meta["symbol"]
    if sym not in SUPERVISORS:
        SUPERVISORS[sym] = PositionSupervisorBinance(sym)
    return SUPERVISORS[sym]


def get_supervisor_for_payload(data):
    """从 TV 载荷路由到对应品种军师；缺 symbol 一律拒绝（禁止默念 ETH）。"""
    from symbol_config import extract_symbol_from_payload, resolve_binance_symbol, active_binance_symbols
    raw = extract_symbol_from_payload(data) if isinstance(data, dict) else ""
    if not str(raw or "").strip():
        logger.warning("[路由] 载荷缺少 symbol/ticker → 拒绝（防 ETH/XAU 误判）")
        return None, "MISSING_SYMBOL"
    meta = resolve_binance_symbol(raw, default="")
    sym = meta.get("symbol") or ""
    if not sym:
        logger.warning(f"[路由] 无法识别品种 raw={raw!r} → 拒绝")
        return None, str(raw or "UNKNOWN")
    allowed = set(active_binance_symbols())
    if sym not in allowed:
        return None, sym
    return get_supervisor(sym), sym


def bootstrap_supervisors():
    """启动全部活动品种军师并恢复状态。"""
    from symbol_config import active_binance_symbols
    global position_supervisor
    symbols = active_binance_symbols()
    for sym in symbols:
        get_supervisor(sym)
    position_supervisor = SUPERVISORS.get("ETHUSDT") or next(iter(SUPERVISORS.values()), None)
    if __name__ != "__main__":
        for sym, sup in SUPERVISORS.items():
            try:
                logger.info(f"🔄 启动恢复 [{sym}] …")
                sup.recover_state_on_startup()
            except Exception as e:
                logger.error(f"启动恢复失败 [{sym}]: {e}")
    return SUPERVISORS


bootstrap_supervisors()
