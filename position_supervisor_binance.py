#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# position_supervisor_binance.py — 与深币 VPS 逻辑对齐（仓位/杠杆一律跟 TV）
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
    compute_tv_order_qty,
    check_total_notional_cap,
    MAX_TOTAL_NOTIONAL_MULT,
    HARD_NOTIONAL_CAP,
    compute_vps_hard_sl,
    compute_vps_hard_sl_distance,
    format_vps_hard_sl_note,
    format_tv_vps_sl_compare,
    get_vps_hard_sl_params,
    format_vps_sizing_note,
    enrich_entry_tp_prices,
    get_regime_max_add_times,
    resolve_tv_add_qty_ratio,
    get_regime_tp_ratios,
    format_regime_tp_ratios_label,
    format_radar_activation_ratios_label,
    validate_tp_prices_for_side,
    normalize_entry_type,
    ENTRY_TYPE_OPEN,
    ENTRY_TYPE_PYRAMID,
    ENTRY_TYPE_PROFIT_ADD,
    CLOSE_TYPE_TP3,
    CLOSE_TYPE_BREAKEVEN,
    CLOSE_TYPE_VPS_SHIELD,
    CLOSE_TYPE_PROTECT,
    EXIT_SOURCE_RADAR_BE,
    EXIT_SOURCE_VPS_HARD_SL,
    EXIT_SOURCE_TP3,
    EXIT_SOURCE_TV_CLOSE,
    EXIT_SOURCE_TV_PROTECT,
    EXIT_SOURCE_MANUAL,
    EXIT_SOURCE_LABELS,
    RADAR_STAGE_COST_BUFFER_PCT,
    RADAR_STAGE_LABELS,
    get_radar_activation_ratio,
    get_radar_trail_step,
    get_radar_breath_atr,
)
from tv_seq import (
    TVSeqBuffer,
    extract_seq_meta,
    is_close_action,
    is_open_action,
    reorder_batch_close_then_open,
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

BINANCE_VPS_VERSION = "v13.88.0-tv-sl-raw"


SENTINEL_POLL_NORMAL = 8
SENTINEL_POLL_ARMING = 5
SENTINEL_POLL_RADAR = 5
IDLE_PATROL_INTERVAL_SEC = 12
IDLE_TAKEOVER_COOLDOWN_SEC = 30
DUST_QTY_ETH = 0.004
TP_COMPLETE_RESIDUAL_RATIO = 0.12
OPEN_OVERSIZE_RATIO = 1.10  # 与 QTY_ALIGN_MIN_PCT 一致：偏离 ≥10% 才裁减
SIGNAL_DEDUP_SEC = 45  # 无 bar_index/seq 的旧信号指纹去重；有时序时用幂等键
DEFENSE_ALIGN_COOLDOWN_SEC = 60
SENTINEL_GRACE_AFTER_RECOVER_SEC = 45
SENTINEL_GRACE_AFTER_OPEN_SEC = 90
# 开仓后禁止雷达/近市保本：只允许 TP123 + TV 硬止损
POST_OPEN_RADAR_BLOCK_SEC = 180
RADAR_TRAIL_MIN_INTERVAL_SEC = 25  # 雷达推升最短间隔，防撤挂死循环
RADAR_WS_APPROACH_RATIO = 0.90  # 朝激活线走过 90% 即 WS 加速盯价（mark@1s）
RADAR_WS_URGENT_SLEEP_SEC = 0.25  # 已达激活线/交棒：哨兵几乎立即执行
# 核武撤挂 thrash 刹车：失败/全缺后最短间隔，避免秒挂秒撤
NUCLEAR_REALIGN_MIN_INTERVAL_SEC = 45
NUCLEAR_FAIL_BACKOFF_MAX_SEC = 180
FLAT_CONFIRM_RETRIES = 6
FLAT_CONFIRM_DELAY_SEC = 0.85
STARTUP_FLAT_CONFIRM_RETRIES = 10
STARTUP_FLAT_CONFIRM_DELAY_SEC = 1.0
RECOVER_LOCK_FILE = "logs/.recover_singleton.lock"  # 兼容旧路径；实际按品种隔离
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
DINGTALK_EVENT_DEDUP_SEC = 45  # 同一行为钉钉 45s 内不重复
# 异常减仓·非TP：同仓位生命周期只告警一次（防哨兵/WS 连环刷屏）
ABNORMAL_REDUCE_ALERT_COOLDOWN_SEC = 600
# 告警门槛：低于此视为微漂/对账噪声，静默锚定实盘，不发钉钉
ABNORMAL_REDUCE_ALERT_PCT = 0.05  # 相对基线 5%
ABNORMAL_REDUCE_ALERT_MIN_QTY = 0.05  # ETH/XAU 绝对下限
RADAR_STOP_MIN_GAP_USD = 2.5
RADAR_STOP_MIN_GAP_PCT = 0.0012
# 交棒额外安全：理想保本线相对现价至少再留 0.15% 利润缓冲，禁止夹成贴市毛刺止损
RADAR_HANDOFF_EXTRA_GAP_PCT = 0.0015
MIN_TP_LEG_QTY = 0.001
# 空仓短时重复 OPEN 去重；有仓 OPEN 一律先平后开（废除「同向仅刷 TP」）
SAME_DIR_MIN_SPREAD_PCT = 0.15
SAME_DIR_DEDUP_SEC = 300
OPEN_SAME_DIR_COOLDOWN_SEC = 0  # v13.83：废除冷却禁先平后开
ATR_SIMILAR_RATIO = 0.03  # 持仓 ATR 与 TV ATR 偏差 ≤3% 视为未变
# 旧版共享日志（兼容读取）；新写入一律按品种隔离，禁止 ETH/XAU 串档位
TV_JOURNAL = "logs/binance_tv_journal.jsonl"
OPEN_JOURNAL = "logs/binance_open_journal.jsonl"
EXCHANGE_JOURNAL = "logs/binance_exchange_journal.jsonl"
# 硬止损同步冷却：同目标禁止反复撤挂（R3/R4 横跳抢权限）
HARD_SL_SYNC_COOLDOWN_SEC = 45
OPEN_REGIME_ENTRY_MATCH_PCT = 0.008  # 开仓日志匹配入场价容差 0.8%


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

        # TP123 分仓比例（margin 字段已废弃，仅保留 ratios）
        self.regime_settings = {
            1: {"margin": 0.0, "ratios": get_regime_tp_ratios(1)},
            2: {"margin": 0.0, "ratios": get_regime_tp_ratios(2)},
            3: {"margin": 0.0, "ratios": get_regime_tp_ratios(3)},
            4: {"margin": 0.0, "ratios": get_regime_tp_ratios(4)},
        }
        self.leverage = 0  # 与 TV sizing/API 同源；缺省 0=拒绝下单
        self.tv_sizing_leverage = 0.0

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
        self._signal_queue = queue.Queue()  # 锁超时重入 / 旁路，不参与时序排序
        self._seq_buffer = TVSeqBuffer(
            self.symbol,
            on_gap_alert=self._on_tv_seq_gap,
        )
        self._signal_worker_started = False
        self._sentinel_active = False
        self.open_regime = 3
        self.open_atr = 30.0
        self._last_entry_signal = None
        self._recover_in_progress = False
        self._recover_tp_unconfirmed = False
        self._post_recover_radar_pulse = False
        self._takeover_price_skip = False
        self._pending_open_defense_snap = None
        self._open_in_progress = False
        self._open_tp_unconfirmed = False
        self._last_signal_fp = None
        self._last_signal_fp_ts = 0.0
        self._defense_align_in_progress = False
        self._last_defense_align_ok_ts = 0.0
        self._guardian_bad_streak = 0
        self._last_nuclear_realign_ts = 0.0
        self._nuclear_fail_streak = 0
        self._sentinel_grace_until = 0.0
        self._post_open_radar_block_until = 0.0
        self._last_regime_cap_ts = 0.0
        self._abnormal_reduce_alert_ts = 0.0
        self._abnormal_reduce_alert_sig = ""
        self._dingtalk_recent = {}
        self._dingtalk_recent_lock = threading.Lock()
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
        self._radar_notify_pending = False  # 交棒成功但钉钉未发出 → 哨兵补发
        self._radar_trigger_gate = ""  # TP1成交 / 档位激活线
        self._radar_armed_after_tp1 = False
        self._radar_handoff_done = False  # 仅保本 STOP 核实后才 True
        self._open_settled_qty = 0.0
        self._last_radar_report_ts = 0.0
        self._last_radar_report_sl = 0.0
        self.sizing_principal = 0.0
        self.tv_sl = 0.0
        self.tv_sl_ref = 0.0
        self._open_regime_sticky = False
        self._last_hard_sl_sync_ts = 0.0
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
        self._ws_tp_fill_levels = set()  # UD-WS 提示的 TP 档（1/2/3）
        self._tv_sl_missing_alerted = False
        self._last_close_bar_index = None  # 同K线先平后开链
        self._last_close_flat_ts = 0.0
        self._close_open_chain_active = False
        self._dingtalk_recent = {}  # event_key -> ts，防同行为刷屏
        self._last_radar_trail_ts = 0.0
        self._last_radar_trail_stage = 0
        self._last_radar_trail_progress = 0.0
        self._radar_work_urgent = False  # WS 达激活线 → 哨兵立刻跑雷达

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
            f"双品种·价触激活线启雷达·5阶段锁利 · {self.leverage}x · {self.unit_label}"
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
        """
        有 tp_levels_consumed 但无减仓证据 → 仅清「现价也未达」的假记账。
        现价已过该档（接管场景）→ 保留，禁止清掉后重挂 TP1。
        """
        initial_qty = float(initial_qty or 0)
        live_qty = float(live_qty or 0)
        curr_px = float(
            curr_px or binance_client.get_current_price(self.symbol) or 0
        )
        consumed = list(getattr(self, "tp_levels_consumed", []) or [])
        if not consumed:
            return False
        inferred = self._infer_tp_consumed_sequential(initial_qty, live_qty, curr_px)
        price_past = [
            lv for lv in consumed
            if self._price_reached_tp_zone(lv, curr_px, live_only=True)
        ]
        if price_past:
            keep = self._sequential_tp_prefix(sorted(set(inferred or []) | set(price_past)))
            if keep != consumed:
                logger.warning(
                    f"⚠️ 接管保留现价已过档 TP{keep} "
                    f"(原 {consumed} | 开单 {initial_qty} 现仓 {live_qty} mark={curr_px:.2f})"
                )
                self.tp_levels_consumed = keep
                self._save_state()
                return True
            return False
        if initial_qty <= live_qty + 0.001 and not inferred:
            logger.warning(
                f"⚠️ 清除陈旧 tp_levels_consumed={consumed} "
                f"(开单 {initial_qty}≈现仓 {live_qty}，无减仓且现价未过)"
            )
            self.tp_levels_consumed = []
            self._save_state()
            return True
        if 1 in consumed and self.tv_tps and self.tv_tps[0] > 0:
            if (
                1 not in inferred
                and not self._has_tp_limit_at_price(self.tv_tps[0])
                and not self._price_reached_tp_zone(1, curr_px, live_only=True)
            ):
                logger.warning(
                    f"⚠️ TP1 已标记成交但无减仓/无挂单/现价未过 → 重置 {consumed}"
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
            f"TV硬止损@{float(self._tv_hard_sl_target(entry_px) or 0):.2f} "
            f"(开仓R{self._resolve_hard_sl_regime()}) | "
            f"TV参考tv_sl={float(getattr(self, 'tv_sl_ref', 0) or 0) or '—'} | "
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
            open_r = int(
                getattr(self, "open_regime", None) or self._resolve_hard_sl_regime()
            )
            tv_r = self._resolve_tv_open_regime_for_position(side, entry_px) or (
                int((self.last_tv_signal or {}).get("regime") or 0) or None
            )
            sl_px = float(self._vps_hard_sl_target(entry_px) or self.current_sl or 0)
            self._call_dingtalk(
                dingtalk.report_recover_takeover,
                side=side,
                qty=real_amt,
                entry=entry_px,
                tv_tps=self.tv_tps,
                regime=open_r,
                radar_active=radar_active,
                sl_price=sl_px,
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
                tv_regime=tv_r,
                hard_sl_pct=get_vps_hard_sl_params(open_r).get("pct"),
                radar_act_pct=get_radar_activation_ratio(open_r),
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

    def _dingtalk_event_key(self, fn, kwargs):
        name = getattr(fn, "__name__", str(fn))
        side = str(kwargs.get("side") or kwargs.get("action") or "")[:12]
        phase = str(kwargs.get("phase") or kwargs.get("decision") or "")[:24]
        reason = str(kwargs.get("reason") or kwargs.get("title") or "")[:48]
        tp_lv = kwargs.get("tp_level")
        tp_part = f"|TP{tp_lv}" if tp_lv is not None else ""
        return f"{name}|{side}|{phase}|{reason}{tp_part}"

    def _dingtalk(self, fn, **kwargs):
        """钉钉播报：强制绑定本军师品种单位（XAU/ETH）；同行为短窗去重（一条即可）。"""
        kwargs.setdefault("symbol", self.symbol)
        kwargs.setdefault("unit_label", self.unit_label)
        name = getattr(fn, "__name__", "")
        # 同类过程/告警/重复 TP 对账一律去重；开仓/收网本体保留（标题不同）
        dedupe_names = {
            "report_principal_snapshot",
            "report_close_then_open_chain",
            "report_system_alert",
            "report_smart_same_dir_decision",
            "report_radar_guardian_realigned",
            "report_tv_signal_received",
            "report_tp_fill",
            "report_manual_position_change",
            "report_radar_regime_cap_trim",
            "report_position_qty_reconcile",
        }
        if name in dedupe_names:
            key = self._dingtalk_event_key(fn, kwargs)
            # 异常减仓类：更长冷却，避免哨兵/WS 双线程刷屏
            title = str(kwargs.get("title") or "")
            cooldown = float(DINGTALK_EVENT_DEDUP_SEC)
            if name == "report_system_alert" and "异常减仓" in title:
                cooldown = float(ABNORMAL_REDUCE_ALERT_COOLDOWN_SEC)
            now = time.time()
            lock = getattr(self, "_dingtalk_recent_lock", None)
            if lock is None:
                self._dingtalk_recent_lock = threading.Lock()
                lock = self._dingtalk_recent_lock
            with lock:
                recent = getattr(self, "_dingtalk_recent", None)
                if recent is None:
                    self._dingtalk_recent = {}
                    recent = self._dingtalk_recent
                dead = [
                    k for k, ts in recent.items()
                    if now - float(ts) > max(cooldown, DINGTALK_EVENT_DEDUP_SEC) * 3
                ]
                for k in dead:
                    recent.pop(k, None)
                last = float(recent.get(key) or 0)
                if last > 0 and now - last < cooldown:
                    logger.info(
                        f"🔇 钉钉去重跳过 {name} ({cooldown:.0f}s内同事件)"
                    )
                    return None
                recent[key] = now
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

    def _on_tv_seq_gap(self, msg):
        """乱序等待超时：钉钉告警，缓冲仍按已有 seq 冲刷。"""
        logger.error(f"⚠️ TV时序缺口: {msg}")
        try:
            self._call_dingtalk(
                dingtalk.report_system_alert,
                title="TV时序缺口 · 前置seq缺失",
                detail=str(msg),
                level="警告",
                suggestion="检查 TradingView 警报是否漏发；已按已有 bar_index/seq 顺序继续执行",
            )
        except Exception as e:
            logger.warning(f"时序缺口钉钉失败: {e}")

    def _start_signal_worker(self):
        if self._signal_worker_started:
            return
        self._signal_worker_started = True
        threading.Thread(target=self._signal_worker_loop, daemon=True, name="tv-signal-worker").start()

    def _signal_worker_loop(self):
        while True:
            batch = []
            try:
                batch.extend(self._seq_buffer.pop_ready(timeout=0.5) or [])
            except Exception as e:
                logger.error(f"时序缓冲 pop 异常: {e}", exc_info=True)
            while True:
                try:
                    batch.append(self._signal_queue.get_nowait())
                except queue.Empty:
                    break
            if not batch:
                continue
            # 铁律：同 bar 开+平并存 → 永远先平后开（无视 TV seq 颠倒 / 到达先后）
            batch = reorder_batch_close_then_open(batch)
            self._annotate_close_open_chain(batch)
            for payload in batch:
                try:
                    bi, sq = extract_seq_meta(payload or {})
                    act = str((payload or {}).get("action", "")).upper()
                    if bi is not None:
                        logger.info(
                            f"⚙️ [{self.symbol}] 时序消费 bar={bi} seq={sq} action={act}"
                        )
                    self._process_signal(payload)
                except Exception as e:
                    logger.error(f"❌ 信号处理异常: {e}", exc_info=True)
                finally:
                    try:
                        self._signal_queue.task_done()
                    except ValueError:
                        pass

    def _annotate_close_open_chain(self, batch):
        """
        同K线同时开+平 → 标记先平后开链。
        最终状态必须开仓；TV 即便 OPEN.seq < CLOSE.seq 也已强制重排。
        """
        by_bar = {}
        for p in batch or []:
            bi, sq = extract_seq_meta(p or {})
            if bi is None:
                continue
            act = str((p or {}).get("action", "")).strip().upper()
            by_bar.setdefault(bi, []).append((sq, act, p))
        for bi, items in by_bar.items():
            acts = [a for _, a, _ in items]
            has_close = any(is_close_action(a) for a in acts)
            has_open = any(is_open_action(a) for a in acts)
            if not (has_close and has_open):
                continue
            exec_chain = " → ".join(f"seq{sq}:{a}" for sq, a, _ in items)
            tv_by_seq = " → ".join(
                f"seq{sq}:{a}" for sq, a, _ in sorted(items, key=lambda x: (x[0] or 0))
            )
            tv_open_sq = next(
                (sq for sq, a, _ in sorted(items, key=lambda x: (x[0] or 0))
                 if is_open_action(a)),
                None,
            )
            tv_close_sq = next(
                (sq for sq, a, _ in sorted(items, key=lambda x: (x[0] or 0))
                 if is_close_action(a)),
                None,
            )
            tv_inverted = (
                tv_open_sq is not None
                and tv_close_sq is not None
                and int(tv_open_sq) < int(tv_close_sq)
            )
            logger.info(
                f"📬 [{self.symbol}] 同K线强制先平后开 bar={bi} | "
                f"执行序 {exec_chain} | TV按seq {tv_by_seq} | "
                f"{'已纠正seq颠倒' if tv_inverted else 'seq已合序'} → 平干净再开·终态开仓"
            )
            self._close_open_chain_active = True
            self._last_close_bar_index = int(bi)
            try:
                self._call_dingtalk(
                    dingtalk.report_close_then_open_chain,
                    phase="同秒开平·强制先平后开",
                    side=next(
                        (a for _, a, _ in items if is_open_action(a)), ""
                    ),
                    reason=(
                        f"bar={bi} | 执行 {exec_chain}"
                        + (f" | TV seq颠倒已纠正({tv_by_seq})" if tv_inverted else "")
                    ),
                    bar_index=bi,
                    chain_same_bar=True,
                    verify_note="同秒开+平：先无菌平仓，最后执行开仓（终态必须有仓）",
                    ok=True,
                )
            except Exception as e:
                logger.warning(f"先平后开链钉钉失败: {e}")

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
        payload = dict(payload or {})
        bi, sq = extract_seq_meta(payload)
        action = str(payload.get("action", "")).strip().upper() or "?"
        # 有 bar_index+seq：幂等键去重 + 有序缓冲；不再用 45s 指纹（避免误杀同 bar 不同 seq）
        if bi is not None and sq is not None:
            if self._open_in_progress and action in ("LONG", "SHORT"):
                logger.warning(f"📬 开仓进行中，忽略重复建仓信号 {action} bar={bi} seq={sq}")
                return
            status = self._seq_buffer.add(payload)
            logger.info(
                f"📬 TV时序入队: {action} bar={bi} seq={sq} → {status} | "
                f"缓冲深度 {self._seq_buffer.depth()} 旁路 {self._signal_queue.qsize()}"
            )
            return

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
        status = self._seq_buffer.add(payload)  # legacy 旁路立即可弹
        logger.info(
            f"📬 TV信号入队(无时序): {action} → {status} | "
            f"缓冲深度 {self._seq_buffer.depth()}"
        )

    def signal_queue_depth(self):
        return self._seq_buffer.depth() + self._signal_queue.qsize()

    def _journal_path(self, kind):
        """kind=open|tv|exchange → 品种隔离路径"""
        return f"logs/binance_{kind}_journal_{self.symbol}.jsonl"

    def _legacy_journal_path(self, kind):
        return f"logs/binance_{kind}_journal.jsonl"

    def _append_journal(self, path, record):
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        record = dict(record)
        record.setdefault("symbol", self.symbol)
        record["ts"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def _iter_journal_entries(self, kind, symbol_only=True):
        """按时间正序读取品种日志；兼容旧共享文件（仅本 symbol 行）。"""
        paths = [self._journal_path(kind)]
        legacy = self._legacy_journal_path(kind)
        if legacy not in paths:
            paths.append(legacy)
        entries = []
        seen = set()
        for path in paths:
            if not os.path.exists(path):
                continue
            try:
                with open(path, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            e = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        if symbol_only:
                            sym = str(e.get("symbol") or "").upper()
                            # 旧共享日志无 symbol：仅当 path 已是品种文件时采纳；共享文件无 symbol 则跳过防串单
                            if path == legacy and not sym:
                                continue
                            if sym and sym != self.symbol.upper():
                                continue
                        key = (
                            e.get("ts"), e.get("source"), e.get("side"),
                            e.get("entry"), e.get("open_regime"), e.get("regime"),
                        )
                        if key in seen:
                            continue
                        seen.add(key)
                        entries.append(e)
            except Exception:
                continue
        return entries

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
        self._append_journal(self._journal_path("exchange"), {
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
        self._append_journal(self._journal_path("exchange"), {
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

    def _load_last_journal_entry(self, path, kind=None):
        """path 可为显式路径；kind=open|tv|exchange 时读品种隔离(+兼容旧文件)。"""
        if kind:
            entries = self._iter_journal_entries(kind, symbol_only=True)
            return entries[-1] if entries else None
        if not os.path.exists(path):
            return None
        last = None
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        e = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    sym = str(e.get("symbol") or "").upper()
                    if sym and sym != self.symbol.upper():
                        continue
                    last = e
        return last

    def _record_tv_signal(self, payload, raw_action):
        full_payload = dict(payload or {})
        entry = {
            "action": raw_action,
            "symbol": self.symbol,
            "regime": self.regime,
            "atr": self.current_atr,
            "price": self.tv_price,
            "tv_tps": list(self.tv_tps or [0.0, 0.0, 0.0]),
            "reason": payload.get("reason", ""),
            "side": payload.get("side", ""),
            "pnl_pct": payload.get("pnl_pct"),
            "tv_sl": payload.get("tv_sl"),
            "tv_tp1": payload.get("tv_tp1"),
            "tv_tp2": payload.get("tv_tp2"),
            "tv_tp3": payload.get("tv_tp3"),
            "entry_type": payload.get("entry_type"),
            "risk_pct": payload.get("risk_pct"),
            "leverage": payload.get("leverage"),
            "qty_ratio": payload.get("qty_ratio"),
            "bar_index": payload.get("bar_index"),
            "seq": payload.get("seq"),
            "payload": full_payload,
            "ts": time.time(),
        }
        self.last_tv_signal = entry
        self._append_journal(self._journal_path("tv"), entry)
        try:
            payload_txt = json.dumps(full_payload, ensure_ascii=False)[:1800]
        except (TypeError, ValueError):
            payload_txt = str(full_payload)[:1800]
        bi, sq = extract_seq_meta(payload)
        seq_tag = f" bar={bi} seq={sq}" if bi is not None else ""
        logger.info(f"📥 [TV警报全文]{seq_tag} {raw_action} | {payload_txt}")
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
            f"📡 TV日志: {raw_action}{seq_tag} R{self.regime} @ {self.tv_price:.2f} "
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
            risk_pct=payload.get("risk_pct") or getattr(self, "tv_risk_pct", 0),
            leverage=payload.get("leverage") or getattr(self, "tv_sizing_leverage", 0),
            qty_ratio=payload.get("qty_ratio") or getattr(self, "tv_qty_ratio", 1.0),
            reason=payload.get("reason", ""),
            vps_sizing_meta=open_sizing_meta,
            bar_index=payload.get("bar_index"),
            seq=payload.get("seq"),
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
        # 永远写开仓档位 open_regime，禁止 recover 日志把后续 TV UPDATE 的紧档写回锁点
        open_r = int(getattr(self, "open_regime", None) or self.regime or 3)
        open_a = float(getattr(self, "open_atr", None) or self.current_atr or 0)
        self._append_journal(self._journal_path("open"), {
            "source": source,
            "symbol": self.symbol,
            "side": side,
            "qty": qty,
            "entry": entry,
            "regime": open_r,
            "open_regime": open_r,
            "open_atr": open_a,
            "tv_tps": self.tv_tps,
            "tv_price": self.tv_price,
            "last_tv_side": self.last_tv_side,
            "vps_hard_sl": float(self._vps_hard_sl_target(entry, side, open_r) or 0),
        })

    def _resolve_tv_open_regime_for_position(self, side=None, entry=None):
        """
        从 TV LONG/SHORT 开仓信源解析本仓应锁档位。
        优先：同向 + 价格贴近入场；禁止用 recover 开仓日志 / 陈旧粘性覆盖 TV。
        """
        side = str(side or self.current_side or "").strip().upper()
        entry = float(entry if entry is not None else (self.watched_entry or 0))
        match_pct = max(float(OPEN_REGIME_ENTRY_MATCH_PCT), 0.012)

        def _score_tv_entry(e):
            if not isinstance(e, dict):
                return 0, 0
            action = str(e.get("action") or "").strip().upper()
            e_side = action if action in ("LONG", "SHORT") else str(
                e.get("side") or ""
            ).strip().upper()
            if e_side not in ("LONG", "SHORT"):
                return 0, 0
            if side and e_side != side:
                return 0, 0
            reg = int(e.get("regime") or 0)
            if reg < 1 or reg > 4:
                return 0, 0
            score = 100
            px = float(e.get("price") or e.get("entry") or 0)
            if entry > 0 and px > 0:
                drift = abs(px - entry) / entry
                if drift <= match_pct:
                    score += 80
                elif drift <= match_pct * 2.5:
                    score += 30
                elif drift > 0.05:
                    return 0, 0
            et = normalize_entry_type(e.get("entry_type"))
            if et == ENTRY_TYPE_OPEN:
                score += 25
            return score, reg

        best_score, best_reg = 0, 0
        # 内存最新信号
        sc, rg = _score_tv_entry(self.last_tv_signal)
        if sc > best_score:
            best_score, best_reg = sc, rg
        # TV 日志由新到旧
        for e in reversed(self._iter_journal_entries("tv", symbol_only=True)):
            sc, rg = _score_tv_entry(e)
            if sc > best_score:
                best_score, best_reg = sc, rg
                if sc >= 180:  # 同向+贴近入场+OPEN：足够采信
                    break
        return int(best_reg or 0)

    def _lock_open_regime_from_sources(self, force=False):
        """
        硬止损/TP 比例档位锁定开仓 R（本品种）：
        1) TV LONG/SHORT 开仓档位（同向+入场价匹配）——最高优先级
        2) 本品种 source=open 开仓日志（排除 recover/重启/接管）
        3) 粘性 state / 当前 regime
        禁止：recover 日志或错误粘性把 R1 改成 R4 → 8.33% 宽止损误挂
        """
        entry = float(getattr(self, "watched_entry", 0) or 0)
        side = str(getattr(self, "current_side", "") or "").upper()
        cur = int(getattr(self, "open_regime", None) or 0)

        tv_reg = self._resolve_tv_open_regime_for_position(side, entry)
        if tv_reg > 0:
            if cur and cur != tv_reg:
                logger.warning(
                    f"🔒 [{self.symbol}] 开仓档位以 TV 为准：粘性/账本 R{cur} → TV R{tv_reg} "
                    f"(entry={entry:.2f})"
                )
            self.open_regime = tv_reg
            self._open_regime_sticky = True
            if not cur:
                logger.info(
                    f"🔒 [{self.symbol}] 开仓档位锁定 R{tv_reg} (tv_open_signal)"
                )
            return tv_reg

        # 无可信 TV 时：粘性保护（避免哨兵横跳）；force 时仍可扫 open 日志
        if (
            not force
            and cur > 0
            and getattr(self, "_open_regime_sticky", False)
        ):
            return cur

        best = None
        best_src = ""
        best_score = -1
        for e in reversed(self._iter_journal_entries("open", symbol_only=True)):
            src = str(e.get("source") or "").lower()
            # recover/重启/接管行不可信（可能已写入错误 open_regime）
            if (
                src.startswith("recover")
                or "重启" in src
                or "接管" in src
                or "巡检" in src
            ):
                continue
            or_field = e.get("open_regime")
            reg = or_field if or_field else e.get("regime")
            if not reg:
                continue
            score = 10 if or_field else 5
            e_side = str(e.get("side") or e.get("last_tv_side") or "").upper()
            if side and e_side and e_side == side:
                score += 20
            e_entry = float(e.get("entry") or 0)
            if entry > 0 and e_entry > 0:
                drift = abs(e_entry - entry) / entry
                if drift <= OPEN_REGIME_ENTRY_MATCH_PCT:
                    score += 50
                elif drift > 0.03:
                    continue
            if score > best_score:
                best_score = score
                best = int(reg)
                best_src = src or "open_journal"

        if best is None and cur > 0:
            best = cur
            best_src = "state"
        if best is None and self.last_tv_signal:
            r = int(self.last_tv_signal.get("regime") or 0)
            if r > 0:
                best = r
                best_src = "last_tv_signal"
        if best is None:
            best = int(self.regime or 3)
            best_src = "tv_regime_fallback"

        old = cur
        self.open_regime = best
        self._open_regime_sticky = True
        if old and old != best:
            logger.warning(
                f"🔒 [{self.symbol}] 开仓档位校正 R{old}→R{best} ({best_src})"
            )
        elif not old:
            logger.info(
                f"🔒 [{self.symbol}] 开仓档位锁定 R{best} ({best_src})"
            )
        return best

    def _load_active_tv_direction_from_journal(self):
        """从 TV 日志末尾向前：跳过尾部 CLOSE，取当前活跃周期的 LONG/SHORT"""
        entries = self._iter_journal_entries("tv", symbol_only=True)
        if not entries:
            return None
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
        last_tv = self._load_last_journal_entry(None, kind="tv")
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
        for src in (self.last_tv_signal, self._load_last_journal_entry(None, kind="tv")):
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
        last_open = None
        for entry in self._iter_journal_entries("tv", symbol_only=True):
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
        last_tv = self._load_last_journal_entry(None, kind="tv")
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
            s for s in (
                self.last_tv_signal,
                self._load_last_journal_entry(None, kind="tv"),
                self._load_last_tv_open_signal(),
                self._load_last_journal_entry(None, kind="open"),
            )
            if isinstance(s, dict)
        ]

        # regime：只采信 TV 信源，禁止被 open/recover 日志最后一项覆盖成错误档
        for src in (
            self.last_tv_signal,
            self._load_last_tv_open_signal(),
            self._load_last_journal_entry(None, kind="tv"),
        ):
            if isinstance(src, dict) and src.get("regime"):
                self.regime = int(src["regime"])
                break
        for src in sources:
            if src.get("atr"):
                self.current_atr = float(src["atr"])
            if float(self.tv_price or 0) <= 0 and float(src.get("price", 0) or 0) > 0:
                self.tv_price = float(src["price"])

        # 硬止损档位：TV 开仓 R 优先（force 纠正错误粘性 R4）
        hard_regime = self._lock_open_regime_from_sources(force=True)
        notes.append(f"开仓档位锁定 R{hard_regime}")
        # 钉钉/展示用 regime 与开仓锁档对齐
        self.regime = int(hard_regime)

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
                    self.tv_sl = sl
                    notes.append(f"TV硬止损tv_sl={sl:.2f}")
                    break

        # 硬止损按 TV tv_sl 写入账本并随后挂盘
        if entry > 0 and side in ("LONG", "SHORT"):
            if self._refresh_vps_hard_sl(
                entry=entry, side=side,
                regime=hard_regime, atr=self.current_atr,
                tv_sl_ref=getattr(self, "tv_sl_ref", 0) or getattr(self, "tv_sl", 0) or None,
                source="接管补全",
            ):
                notes.append(
                    f"TV硬止损@{float(getattr(self, 'tv_sl', 0) or 0):.2f}"
                )
            else:
                adopted = self._adopt_exchange_hard_sl(source="接管盘口采纳")
                if adopted:
                    notes.append(f"盘口采纳硬止损@{adopted:.2f}")

            # 重启叠单/错价 → 强制统一为 TV 硬止损
            live_stops = binance_client.find_protective_stop_prices(self.symbol)
            uniq = sorted({round(float(p), 2) for p in live_stops if float(p) > 0})
            target = round(float(self._tv_hard_sl_target(entry, side) or 0), 2)
            if target > 0 and (
                len(uniq) > 1
                or (uniq and all(abs(p - target) > SHIELD_STOP_TOLERANCE for p in uniq))
                or not uniq
            ):
                qty = float(pos.get("size") or pos.get("positionAmt") or self.watched_qty or 0)
                qty = abs(qty)
                if qty <= 0:
                    qty = float(self.watched_qty or 0)
                if qty > 0:
                    sync = self._sync_exchange_stop(
                        qty, radar_sl=None, reason="接管强制TV硬止损", force=True,
                    )
                    if sync.get("ok"):
                        notes.append(
                            f"TV硬止损@{sync.get('target'):.2f}"
                            f"(撤{sync.get('purged', 0)})"
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
        self._radar_notify_pending = False
        self._radar_trigger_gate = ""
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
            s for s in (
                self.last_tv_signal,
                self._load_last_journal_entry(None, kind="tv"),
                self._load_last_tv_open_signal(),
                self._load_last_journal_entry(None, kind="open"),
            )
            if isinstance(s, dict)
        ]
        for src in sources:
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
        全链防线：TP123 比例限价 + TV 硬止损；
        雷达仅档位激活线(R1=50%/R2=60%/R3=70%/R4=80%)或TP1真实成交后交棒；
        激活线前仅 TV 硬止损；交棒后止损只前进不回撤。
        硬止损与雷达共用 closePosition 单槽（不抢 TP reduceOnly）。
        重启禁止用历史 best 误触保本（防无缘无故贴成本平仓）。
        """
        notes = []
        live_qty = float(self._resolve_live_qty(live_qty) or live_qty)
        entry = float(entry or self.watched_entry or 0)
        curr_px = float(curr_px or binance_client.get_current_price(self.symbol) or 0)
        self._takeover_price_skip = True  # 接管：现价已过档禁止重挂
        try:
            return self._ensure_full_defense_stack_inner(
                live_qty, entry, curr_px, source=source, manual_fresh=manual_fresh,
            )
        finally:
            self._takeover_price_skip = False

    def _ensure_full_defense_stack_inner(self, live_qty, entry, curr_px, source="接管", manual_fresh=False):
        """_ensure_full_defense_stack 主体（由外层保证 takeover 标志复位）"""
        notes = []

        if manual_fresh:
            self._reset_fresh_takeover_state()

        # 先补全 TP 价，再开仓价/现价两头对账（先于任何「清记账」老逻辑）
        if not self._ensure_tp123_prices_from_tv(entry):
            notes.append("TP123补全失败")
        progress = self._apply_takeover_price_progress(
            entry, curr_px, live_qty, source=source,
        )
        notes.extend(progress.get("notes") or [])
        price_plan = progress

        self._disarm_premature_radar(live_qty, curr_px, source=source)
        self._reconcile_stale_tp_consumed(
            self._trusted_initial_qty(live_qty, entry), live_qty, curr_px,
        )
        trusted_initial = self._trusted_initial_qty(live_qty, entry)
        if float(self.initial_qty or 0) != trusted_initial:
            self.initial_qty = trusted_initial
        self._sanitize_tp_consumed(trusted_initial, live_qty, curr_px)
        # 接管禁止无脑清记账；仅清「现价未过且无减仓」的假成交
        self._clear_spurious_tp_consumed_if_full_size(live_qty, source=source)
        # 再次应用现价进度（防止中间步骤清掉已过档）
        price_plan = self._apply_takeover_price_progress(
            entry, curr_px, live_qty, source=f"{source}·复核",
        )
        if float(getattr(self, "tv_sl", 0) or 0) <= 0:
            self._hydrate_tv_defense_context({
                "side": self.current_side, "entry_price": entry, "size": live_qty,
            })
        # 无论账本是否有价，一律消毒对齐 TV tv_sl
        self._sanitize_vps_hard_sl_ledger(source=f"{source} boot")
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

        # 未达激活线/未过TP1 才强制待命；已过则勿清记账、勿解雷达
        if not price_plan.get("should_radar"):
            self._enforce_pre_tp1_radar_standby(live_qty, curr_px, source=source)
        else:
            logger.info(
                f"📡 [{source}] 现价已达雷达门槛 → 跳过激活线前待命清账"
            )

        # 接管/重启：禁止 force 档位裁减（会 cancel_all 后裸仓/误减仓）
        try:
            if not getattr(self, "_recover_in_progress", False) and not any(
                k in str(source) for k in ("重启", "接管", "recover", "Recover")
            ):
                cap = self._radar_enforce_regime_cap(live_qty, curr_px, force=False)
                if cap:
                    live_qty = float(cap["new_qty"])
                    self.watched_qty = live_qty
                    if float(self.initial_qty or 0) <= live_qty + 0.001:
                        self.initial_qty = live_qty
            else:
                logger.info(
                    f"📡 [{source}] 跳过档位限额裁减（重启/接管禁误减仓·禁平仓）"
                )
        except Exception as e:
            logger.warning(f"接管档位限额跳过: {e}")

        # 与开仓一致：穿价 TP 先推离再挂，禁止跳过全档导致无 TP
        try:
            self._sanitize_open_tps_vs_mark(entry, curr_px)
        except Exception as e:
            logger.warning(f"接管 TP 消毒跳过: {e}")

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
            if price_plan.get("should_radar"):
                # 应启雷达：尝试交棒，禁止再清 TP 过价记账
                try:
                    self._process_radar_trailing(live_qty, curr_px)
                    radar_sl = self._radar_sl_to_pass()
                    stop_check = self._resolve_defense_stop_for_audit(radar_sl)
                except Exception as e:
                    logger.warning(f"[{source}] 应启雷达追随失败: {e}")
            else:
                radar_sl = None
                self._enforce_pre_tp1_radar_standby(live_qty, curr_px, source=source)
                stop_check = self._shield_stop_price()
        shield_ok = self._maintain_hard_shield(live_qty, curr_px, force=True, radar_sl=radar_sl)
        audit = self._wait_defense_settled(live_qty, stop_check)

        if not self._tp_audit_ok(audit) or (
            stop_check and not self._has_stop_sl_near(stop_check, tolerance=2.5)
        ):
            # 核武前再钉一次现价已过档，禁止核武把 TP1 挂回
            self._apply_takeover_price_progress(
                entry, curr_px, live_qty, source=f"{source}·核武前",
            )
            logger.warning(
                f"⚠️ [{source}] TP/止损未齐 ({audit.get('matched_full', 0)}/"
                f"{audit.get('expected', 0)}) → 核武重挂剩余档 "
                f"(已过 {getattr(self, 'tp_levels_consumed', [])})"
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
        if price_plan.get("should_radar"):
            health["should_radar"] = True

        if (
            self._radar_legitimately_armed(live_qty, curr_px)
            or price_plan.get("should_radar")
        ) and (
            health.get("should_radar") or health.get("radar_active")
            or price_plan.get("should_radar")
        ):
            self._process_radar_trailing(live_qty, curr_px)
            sl = self._radar_sl_to_pass()
            if sl and not self._has_stop_sl_near(sl):
                self._maintain_hard_shield(live_qty, curr_px, force=True, radar_sl=sl)
        else:
            act_prog = self._radar_activation_progress(curr_px) if curr_px > 0 else 0.0
            tp1_prog = self._tp1_direction_progress(curr_px) if curr_px > 0 else 0.0
            logger.info(
                f"📡 [{source}] 雷达待命 激活进度{act_prog:.0%} "
                f"朝TP1{tp1_prog:.0%} | "
                f"VPS@{float(self._vps_hard_sl_target() or 0):.2f}"
                f"(R{self._resolve_hard_sl_regime()}) | "
                f"TP {audit.get('matched_full', 0)}/{audit.get('expected', 0)} | "
                f"已过档 {getattr(self, 'tp_levels_consumed', [])}"
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
                    f"VPS@{float(self._vps_hard_sl_target() or 0):.2f} | 哨兵接力",
                )

        # 终检：盘口无保护 STOP / 剩余 TP 未齐 → 强制闭环（接管模式：禁重挂已过价档）
        hung = binance_client.find_protective_stop_prices(self.symbol)
        if live_qty > 0 and (
            not hung
            or (
                int(audit.get("expected") or 0) > 0
                and int(audit.get("matched_full") or 0) < int(audit.get("expected") or 0)
            )
        ):
            logger.error(
                f"🚨 [{source}] 终检防线未齐 TP "
                f"{audit.get('matched_full', 0)}/{audit.get('expected', 0)} "
                f"stop={hung} 已过={getattr(self, 'tp_levels_consumed', [])} → 强制闭环"
            )
            audit, hung = self._force_hang_open_defenses(
                live_qty, entry, rounds=2, takeover_mode=True,
            )
            shield_ok = bool(hung)
            if not hung:
                dingtalk.report_system_alert(
                    f"{source} · 裸仓无硬止损 [{self.symbol}]",
                    f"{self.current_side} {live_qty} @ {entry:.2f} | "
                    f"TP {audit.get('matched_full', 0)}/{audit.get('expected', 0)} | "
                    f"已过档 {getattr(self, 'tp_levels_consumed', [])} | "
                    f"请立即人工挂 closePosition",
                )

        self._post_recover_radar_pulse = True
        return {
            "audit": audit,
            "result": result,
            "health": health,
            "shield_ok": shield_ok,
            "notes": notes,
            "price_plan": price_plan,
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

        last_tv = self._load_last_journal_entry(None, kind="tv")
        last_open = self._load_last_journal_entry(None, kind="open")
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
        last_open = self._load_last_journal_entry(None, kind="open")
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
                    "radar_activation_notified": bool(
                        getattr(self, "_radar_activation_notified", False)
                    ),
                    "radar_notify_pending": bool(
                        getattr(self, "_radar_notify_pending", False)
                    ),
                    "radar_trigger_gate": str(
                        getattr(self, "_radar_trigger_gate", "") or ""
                    ),
                    "shield_handoff_notified": bool(
                        getattr(self, "_shield_handoff_notified", False)
                    ),
                    "open_settled_qty": float(
                        getattr(self, "_open_settled_qty", 0) or 0
                    ),
                    "last_applied_exchange_sl": float(
                        getattr(self, "_last_applied_exchange_sl", 0) or 0
                    ),
                    "open_regime_sticky": bool(
                        getattr(self, "_open_regime_sticky", False)
                    ),
                    "tv_risk_pct": float(getattr(self, "tv_risk_pct", 0) or 0),
                    "tv_qty_ratio": float(getattr(self, "tv_qty_ratio", 1.0) or 1.0),
                    "tv_entry_type": getattr(self, "tv_entry_type", ENTRY_TYPE_OPEN),
                    "leverage": float(
                        getattr(self, "tv_sizing_leverage", 0) or 0
                    ),
                    "tv_sizing_leverage": float(
                        getattr(self, "tv_sizing_leverage", 0) or 0
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

    def _get_active_position(self, prefer_ws=True):
        # 重启探测必须 prefer_ws=False，避免空缓存误判空仓
        pos = binance_client.get_position(self.symbol, prefer_ws=prefer_ws)
        if not pos or float(pos.get("positionAmt", 0)) == 0:
            return None
        amt = float(pos["positionAmt"])
        return {
            "size": abs(amt),
            "entry_price": round(float(pos.get("entryPrice", 0)), 2),
            "side": "LONG" if amt > 0 else "SHORT",
        }

    def _probe_position_for_recover(self):
        """
        重启专用持仓探测：强制 REST 多轮；若仓位空但挂单仍在 → 禁止报空仓/清挂单。
        返回: dict 持仓 | None 确认空仓 | "AMBIGUOUS" 查询与挂单矛盾
        """
        last = None
        for i in range(6):
            last = self._get_active_position(prefer_ws=False)
            if last and float(last.get("size") or 0) > 0:
                if i > 0:
                    logger.info(
                        f"🔄 [{self.symbol}] 重启持仓探测第{i + 1}轮命中 "
                        f"{last['side']} {last['size']}"
                    )
                return last
            time.sleep(0.55)
        try:
            orders = binance_client.get_open_orders(self.symbol, include_algo=True) or []
        except Exception as e:
            logger.warning(f"🔄 [{self.symbol}] 重启挂单探测失败: {e}")
            orders = []
        if orders:
            logger.error(
                f"🚨 [{self.symbol}] 重启：REST 仓位为空但盘口仍有 {len(orders)} 笔挂单 "
                f"→ 禁止空仓清场，交哨兵接力"
            )
            return "AMBIGUOUS"
        # 账本曾监控：再给一次总账户持仓扫描兜底（防 symbol 瞬时查询抖动）
        if float(getattr(self, "watched_qty", 0) or 0) > 0 or getattr(
            self, "current_side", None
        ):
            logger.warning(
                f"🔄 [{self.symbol}] 账本曾有仓但 REST 空+无挂单，再扫一次账户持仓"
            )
            try:
                rows = binance_client.client.futures_position_information()
                for p in rows or []:
                    if str(p.get("symbol") or "").upper() != self.symbol.upper():
                        continue
                    amt = float(p.get("positionAmt") or 0)
                    if abs(amt) > 0:
                        return {
                            "size": abs(amt),
                            "entry_price": round(float(p.get("entryPrice", 0) or 0), 2),
                            "side": "LONG" if amt > 0 else "SHORT",
                        }
            except Exception as e:
                logger.warning(f"账户持仓兜底扫描失败: {e}")
        return None

    def _verify_flat(self):
        pos = self._get_active_position()
        return pos is None

    def _verify_sterile_flat(self):
        """无菌空仓：持仓=0 且挂单=0（防先平后开竞态残留 TP/STOP 成交）。"""
        if not self._verify_flat():
            return False
        remaining = self._remaining_open_order_count()
        if remaining < 0:
            return False
        if remaining > 0:
            return False
        try:
            tp_left = self._collect_tp_limit_orders()
        except Exception:
            tp_left = []
        return not tp_left

    def _sterile_flat_gate(self, reason_tag="开仓前", force_close=True):
        """
        先平后开无菌闸：撤单 → 平仓 → 再撤单 → 扫孤儿 → 验 qty=0+orders=0。
        TV 同K线 / 同秒开+平：永远先平后开；开仓前必须净场。
        即使 TV 把 OPEN 标成更小 seq，缓冲层已强制重排。
        """
        tag = reason_tag or "无菌清场"
        prev_side = self.current_side
        # 1) 先撤一切防御单，避免平仓过程中 TP 成交反向开仓
        self._purge_all_defense_orders_on_flat(f"{tag}·开仓前抢先撤单")
        time.sleep(0.35)
        # 2) 有仓则阶梯强平（含平后撤单）
        if not self._verify_flat():
            if not force_close:
                logger.error(f"❌ [{tag}] 盘口非空且未授权强平，拒绝开仓")
                return False
            logger.warning(f"⚠️ [{tag}] 检测到残留持仓，启动强制平仓")
            if not self._close_all(f"{tag} · 强制清场", reset_state=True):
                logger.error(f"❌ [{tag}] 强平未归零，拒绝开仓")
                return False
            if not self._wait_verify(self._verify_flat, retries=8, delay=0.45):
                logger.error(f"❌ [{tag}] 空仓核查未通过，拒绝开仓")
                return False
        # 3) 平后再撤一轮（CLOSE→OPEN 间隔极短时残留 Algo/限价）
        purge = self._purge_all_defense_orders_on_flat(f"{tag}·平后净挂单", max_rounds=8)
        # 4) 扫孤儿反向（残留 TP 在空仓成交）
        self._sweep_orphan_reverse_after_flat(prev_side=prev_side, reason=tag)
        time.sleep(0.35)
        # 5) 终检：仓+单皆零
        if self._wait_verify(self._verify_sterile_flat, retries=6, delay=0.4):
            logger.info(
                f"🧹 [{tag}] 无菌空仓通过 | qty=0 orders=0 | "
                f"撤轮={purge.get('rounds')} TP撤={purge.get('tp_cancelled', 0)}"
            )
            self._last_close_flat_ts = time.time()
            return True
        remaining = self._remaining_open_order_count()
        tp_left = []
        try:
            tp_left = self._collect_tp_limit_orders()
        except Exception:
            pass
        pos = self._get_active_position()
        if not pos:
            pos_txt = "无"
        else:
            pos_txt = f"{pos.get('side')} {pos.get('size')}"
        detail = f"持仓={pos_txt} | 挂单={remaining} | TP残留={len(tp_left)}"
        logger.error(f"❌ [{tag}] 无菌空仓失败 → 拒绝开仓 | {detail}")
        try:
            self._call_dingtalk(
                dingtalk.report_system_alert,
                title=f"无菌空仓失败·拒绝开仓 [{self.symbol}]",
                detail=f"{tag} | {detail} | 防残留限价成交导致反手/超档位",
                level="紧急",
                suggestion="币安 APP 手动全部撤单+平仓后，等下一根 TV 信号",
            )
        except Exception:
            pass
        return False

    def _ensure_flat_before_open(self, reason_tag="开仓前"):
        """开仓前一律无菌净场（有仓强平+撤单；空仓也清残留挂单）。"""
        return self._sterile_flat_gate(reason_tag=reason_tag or "开仓前", force_close=True)

    def _snapshot_sizing_principal(self, reason="", notify=True):
        """全平/开仓前：锁定账户总权益（marginBalance），供本周期开仓与 13x 硬顶共用"""
        principal = binance_client.get_total_equity()
        if principal > 0:
            self.sizing_principal = principal
            self._save_state()
            logger.info(f"📸 本金快照 {principal:.2f} USDT ({reason})")
            # 开仓前快照并入开仓钉钉，避免「快照+开仓」双条刷屏
            if notify and reason and "全平" in reason:
                try:
                    self._call_dingtalk(
                        dingtalk.report_principal_snapshot,
                        reason=reason,
                        principal=principal,
                        regime=None,
                        margin_pct=None,
                        target_qty=None,
                        leverage=float(getattr(self, "tv_sizing_leverage", 0) or 0),
                        vps_sizing_meta=None,
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
        """TV 公式 → 仓位上限（用于超仓裁减对照）"""
        regime = int(regime if regime is not None else self.regime)
        qty, meta = self._calc_vps_open_qty(curr_px, regime=regime)
        balance = float(meta.get("principal", 0) or self._resolve_cap_sizing_base())
        order_amount = float(meta.get("order_amount", 0) or 0)
        eff = float(meta.get("risk_pct") or meta.get("effective_risk_pct") or 0) / 100.0
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
        """解析 TV risk_pct / qty_ratio / leverage；VPS 不重算，直接用于唯一公式。"""
        self.tv_entry_type = normalize_entry_type(payload.get("entry_type"))
        rp = self._safe_float(payload.get("risk_pct"), None)
        if rp is not None and rp > 0:
            self.tv_risk_pct = float(rp)
        lev = self._safe_float(payload.get("leverage"), None)
        if lev is not None and lev > 0:
            self.tv_sizing_leverage = float(lev)
        if self.tv_entry_type in (ENTRY_TYPE_PYRAMID, ENTRY_TYPE_PROFIT_ADD):
            self.tv_qty_ratio = resolve_tv_add_qty_ratio(
                self.regime,
                self._safe_float(payload.get("qty_ratio"), None),
            )
        else:
            qr = self._safe_float(payload.get("qty_ratio"), 1.0)
            self.tv_qty_ratio = float(qr if qr and qr > 0 else 1.0)
        # 仓位公式 + 交易所 set_leverage 一律用 TV leverage（禁止固定 25x）
        self.leverage = float(getattr(self, "tv_sizing_leverage", 0) or 0)
        self._save_state()
        max_add = self._max_add_times_for_regime()
        logger.info(
            f"📐 TV参数: type={self.tv_entry_type} "
            f"| risk_pct={float(getattr(self, 'tv_risk_pct', 0) or 0):.3f}% "
            f"| qty_ratio={self.tv_qty_ratio:.2f} "
            f"| leverage={float(getattr(self, 'tv_sizing_leverage', 0) or 0):.0f}x "
            f"(仓位+API同源) "
            f"| R{self.regime} 最多加仓{max_add}次"
        )

    def _calc_vps_add_qty(self, qty_ratio=None):
        """加仓：同一 TV 公式 × qty_ratio（禁止旧 base×ratio）。"""
        principal = self._resolve_cap_sizing_base()
        px = float(self.tv_price or 0) or float(
            binance_client.get_current_price(self.symbol) or 0
        )
        sl = float(getattr(self, "tv_sl", 0) or 0)
        ratio = resolve_tv_add_qty_ratio(
            self.regime,
            qty_ratio if qty_ratio is not None else getattr(self, "tv_qty_ratio", None),
        )
        risk_pct = float(getattr(self, "tv_risk_pct", 0) or 0)
        lev = float(getattr(self, "tv_sizing_leverage", 0) or 0)
        qty, meta = compute_vps_add_qty(
            qty_ratio=ratio,
            regime=self.regime,
            principal=principal,
            price=px,
            tv_sl=sl,
            risk_pct=risk_pct,
            leverage=lev,
            qty_step=float(getattr(self, "qty_step", 0.001) or 0.001),
            min_qty=float(getattr(self, "min_qty", 0.001) or 0.001),
        )
        meta["principal"] = principal
        meta["add_count"] = int(getattr(self, "add_count", 0) or 0)
        meta["max_add_times"] = self._max_add_times_for_regime()
        meta["symbol"] = self.symbol
        return float(qty or 0), meta

    def _calc_vps_open_qty(self, curr_px, regime=None):
        """OPEN：TV risk_pct / leverage / qty_ratio 唯一公式。"""
        principal = self._resolve_cap_sizing_base()
        px = float(curr_px or self.tv_price or 0)
        sl = float(getattr(self, "tv_sl", 0) or 0)
        risk_pct = float(getattr(self, "tv_risk_pct", 0) or 0)
        lev = float(getattr(self, "tv_sizing_leverage", 0) or 0)
        ratio = float(getattr(self, "tv_qty_ratio", 1.0) or 1.0)
        if risk_pct <= 0 or lev <= 0:
            logger.error(
                f"🚫 开仓 sizing 拒绝：缺少 TV 参数 "
                f"risk_pct={risk_pct} leverage={lev}（禁止回退旧保证金%逻辑）"
            )
            return 0.0, {
                "error": "missing_tv_risk_or_leverage",
                "principal": principal,
                "risk_pct": risk_pct,
                "leverage": lev,
                "sizing_mode": "TV_RISK_FORMULA",
            }
        qty, meta = compute_tv_order_qty(
            principal=principal,
            risk_pct=risk_pct,
            leverage=lev,
            qty_ratio=ratio,
            price=px,
            tv_sl=sl,
            regime=int(regime if regime is not None else self.regime),
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
        """双品种硬顶：其它品种名义 + 本笔名义 ≤ equity×13。"""
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
        if payload:
            self._apply_tv_sizing_params(payload)
        qty, meta = self._calc_vps_open_qty(curr_px)
        principal = float(meta.get("principal", 0) or 0)
        margin_usdt = float(meta.get("order_amount", 0) or 0)
        margin_pct = float(meta.get("risk_pct") or meta.get("effective_risk_pct") or 0) / 100.0
        return qty, principal, margin_usdt, margin_pct, meta

    def _calc_regime_margin_qty(self, curr_px):
        qty, meta = self._calc_vps_open_qty(curr_px)
        principal = float(meta.get("principal", 0) or 0)
        return (
            qty,
            principal,
            float(meta.get("order_amount", 0) or 0),
            float(meta.get("risk_pct") or meta.get("effective_risk_pct") or 0) / 100.0,
        )

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
        # 刚开仓宽限内禁止档位裁减（防开完立刻秒平大半）
        if not force and time.time() < float(
            getattr(self, "_post_open_radar_block_until", 0) or 0
        ):
            logger.info(
                f"📡 [档位限额] 开仓宽限内跳过裁减 "
                f"(剩 {max(0, self._post_open_radar_block_until - time.time()):.0f}s)"
            )
            return None
        if not force and time.time() < float(
            getattr(self, "_sentinel_grace_until", 0) or 0
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
        if meta.get("exit_source"):
            label = meta.get("exit_source_label") or EXIT_SOURCE_LABELS.get(
                meta.get("exit_source"), meta.get("exit_source")
            )
            base_note += f" | 归因 {label}"
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
            exit_source=meta.get("exit_source"),
            exit_source_label=meta.get("exit_source_label"),
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
        self._open_regime_sticky = False
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
            last_open = self._load_last_journal_entry(None, kind="open")
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
        otype = str(o.get("type") or o.get("orderType") or "").upper()
        if otype != "LIMIT":
            return False
        val = o.get("reduceOnly")
        if val is True or str(val).lower() in ("true", "1"):
            return True
        if not self.current_side:
            return False
        close_side = "BUY" if self.current_side == "SHORT" else "SELL"
        return str(o.get("side") or "").upper() == close_side

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

    def _live_mark_for_tp_detect(self, curr_px=0.0):
        """对账前刷新实时标记价；现价优先，其次 best（方向极值）。"""
        px = float(curr_px or 0)
        if px <= 0:
            try:
                px = float(binance_client.get_current_price(self.symbol) or 0)
            except Exception:
                px = 0.0
        return px

    def _infer_tp_consumed_sequential(self, initial_qty, live_qty, curr_px=0.0):
        """
        按开单→现仓累计减仓，顺序推断已 fully 成交的 TP 档。
        硬约束：
        1) 该档限价仍在盘口 → 本档及后续一律未成交
        2) 相对开仓基线的减仓若 <2% → 噪声，不推断
        3) 累计减仓须覆盖该档切片
        4) 每一档（含 TP2/TP3）都必须：现价或 best 已触及该档 TP 价
           —— 禁止「头寸不对就瞎说 TP12 成交」
        """
        initial_qty = float(initial_qty or 0)
        live_qty = float(live_qty or 0)
        if initial_qty <= live_qty + 0.001:
            return []

        reduced = round(initial_qty - live_qty, 3)
        noise = max(0.003, initial_qty * TP_FILL_NOISE_VS_OPEN_PCT)
        if reduced < noise:
            return []

        curr_px = self._live_mark_for_tp_detect(curr_px)
        consumed = []
        cum = 0.0

        for sl in self._tp_slices_for_initial(initial_qty):
            if sl["qty"] <= 0.0005 or sl["price"] <= 0:
                continue
            # 限价单还在 → 绝不可能已成交该档
            if self._has_tp_limit_at_price(sl["price"]):
                break
            lv = int(sl["level"])
            # 每一档都必须价到（现价或 best），TP2/TP3 与 TP1 同铁律
            if not self._price_reached_tp_zone(lv, curr_px, sl["price"]):
                logger.info(
                    f"🧮 [{self.symbol}] 减仓像TP{lv}但现价/best未达 "
                    f"@{float(sl['price']):.2f} (mark={curr_px:.2f} "
                    f"best={float(self.best_price or 0):.2f}) → 拒认成交"
                )
                break
            cum = round(cum + sl["qty"], 3)
            tol = max(0.003, float(sl["qty"]) * TP_SLICE_MATCH_TOL_PCT)
            # 双侧：累计减仓必须达到本档；过冲可继续吃下一档，但首档至少吃到切片
            if len(consumed) == 0 and reduced + 0.0005 < float(sl["qty"]) - tol:
                break
            if reduced + 0.0005 >= cum - tol:
                consumed.append(lv)
                continue
            break

        return self._sequential_tp_prefix(consumed)

    def _sanitize_tp_consumed(self, initial_qty, live_qty, curr_px=0.0):
        """纠正 tp_levels_consumed：合并减仓推断 + 现价已过档；禁止清掉已过价档"""
        live_qty = float(live_qty or 0)
        initial_qty = float(initial_qty or 0)
        curr_px = float(
            curr_px or binance_client.get_current_price(self.symbol) or 0
        )
        if live_qty <= DUST_QTY_ETH:
            self.tp_levels_consumed = []
            self._save_state()
            return []

        saved = self._sequential_tp_prefix(getattr(self, "tp_levels_consumed", []) or [])
        inferred = self._infer_tp_consumed_sequential(initial_qty, live_qty, curr_px)
        price_past = []
        for lv in (1, 2, 3):
            if self._price_reached_tp_zone(lv, curr_px, live_only=True):
                price_past.append(lv)
            else:
                break
        price_past = self._sequential_tp_prefix(price_past)

        if price_past:
            merged = self._sequential_tp_prefix(
                sorted(set(saved or []) | set(inferred or []) | set(price_past))
            )
            if merged != saved:
                logger.info(
                    f"🎯 已成交档含现价已过: TP{saved or '无'} → TP{merged} "
                    f"(开单 {initial_qty} → 现仓 {live_qty} mark={curr_px:.2f})"
                )
            saved = merged
        elif initial_qty <= live_qty + 0.001 and saved and not inferred:
            logger.warning(
                f"⚠️ 无减仓且现价未过但 tp_levels_consumed={saved} → 清空（避免漏挂）"
            )
            saved = []
        elif initial_qty <= live_qty + 0.001 and saved and inferred and saved != inferred:
            logger.info(
                f"🎯 无减仓以推断为准: TP{saved} → TP{inferred or '无'}"
            )
            saved = inferred

        if len(saved) >= 3 and live_qty > DUST_QTY_ETH and not price_past:
            logger.warning(
                f"⚠️ tp_levels_consumed={saved} 但仍有 {live_qty} ETH → "
                f"按开单 {initial_qty} 重算为 TP{inferred or '无'}"
            )
            saved = inferred
        elif inferred and (not saved or len(inferred) < len(saved)) and not price_past:
            if saved != inferred:
                logger.info(
                    f"🎯 已成交档修正: TP{saved or '无'} → TP{inferred} "
                    f"(开单 {initial_qty} → 现仓 {live_qty})"
                )
            saved = inferred
        elif saved and inferred and saved != inferred and not price_past:
            logger.info(
                f"🎯 已成交档以减仓为准: TP{saved} → TP{inferred}"
            )
            saved = inferred

        if saved != list(getattr(self, "tp_levels_consumed", []) or []):
            self.tp_levels_consumed = saved
            self._save_state()
        return saved

    def _tp_level_price_and_order_gone(self, level, curr_px=0.0, live_qty=None):
        """
        铁律：该档 TP 价已达到 + 开单限价已消失 → 必为成交。
        附加：自撤窗口/无减仓证据 → 不算成交（防开仓假吃 TP1）。
        """
        level = int(level or 0)
        if level < 1 or level > 3:
            return False
        if self._tp_level_consumed(level):
            return True
        self._ensure_tv_tps_for_fill_detect()
        if not self.tv_tps or level - 1 >= len(self.tv_tps):
            return False
        px = float(self.tv_tps[level - 1] or 0)
        if px <= 0:
            return False
        if self._has_tp_limit_at_price(px):
            return False
        live_qty = float(
            live_qty if live_qty is not None else self.watched_qty or 0
        )
        return self._may_mark_tp_filled_missing_limit(
            level, live_qty, curr_px, tp_px=px,
        )

    def _infer_tp_consumed_by_price_and_gone(self, curr_px=0.0, live_qty=None):
        """
        按顺序：价到(现价或best) + 限价消失 + 减仓证据 → 记账已成交。
        限价消失但价未到 / 自撤窗口 / 无减仓 → 不记账。
        """
        self._ensure_tv_tps_for_fill_detect()
        curr_px = self._live_mark_for_tp_detect(curr_px)
        live_qty = float(
            live_qty if live_qty is not None else self.watched_qty or 0
        )
        consumed = []
        for lv in (1, 2, 3):
            if not self.tv_tps or lv - 1 >= len(self.tv_tps):
                break
            px = float(self.tv_tps[lv - 1] or 0)
            if px <= 0:
                break
            if self._has_tp_limit_at_price(px):
                break
            if self._may_mark_tp_filled_missing_limit(lv, live_qty, curr_px, tp_px=px):
                consumed.append(lv)
                continue
            if self._price_reached_tp_zone(lv, curr_px, px):
                logger.info(
                    f"🧮 [{self.symbol}] TP{lv}@{px:.2f} 价到但拒认成交 "
                    f"(自撤窗口或无减仓证据) → 不记账"
                )
            else:
                logger.info(
                    f"🧮 [{self.symbol}] TP{lv}@{px:.2f} 限价已消失但现价/best未达 "
                    f"(mark={curr_px:.2f} best={float(self.best_price or 0):.2f}) "
                    f"→ 不记账成交"
                )
            break
        return self._sequential_tp_prefix(consumed)

    def _qty_reduction_looks_like_tp(self, old_qty, new_qty, curr_px=0.0):
        """
        减仓是否值得走 TP 成交检测。
        铁律：必须「该档价到(现价/best) + 限价消失」；仅头寸变小不够格。
        """
        old_qty = float(old_qty or 0)
        new_qty = float(new_qty or 0)
        curr_px = self._live_mark_for_tp_detect(curr_px)
        # 价到+限价消失：才值得进对账
        for lv in (1, 2, 3):
            if self._tp_level_price_and_order_gone(lv, curr_px):
                return True
        if new_qty >= old_qty - 0.0005:
            return False
        # 有明显减仓但无一档价到 → 不是 TP 成交，禁止误触发 TP12 播报
        return False

    def _resync_tp_baseline(self, live_qty, reason=""):
        """把开仓基线锚定到实盘数量，避免同一微差反复触发「异常减仓」告警。"""
        live_qty = float(live_qty or 0)
        if live_qty <= 0:
            return
        old = float(self._tp_baseline_qty(live_qty) or 0)
        self._open_settled_qty = live_qty
        self.initial_qty = live_qty
        self.watched_qty = live_qty
        try:
            self._save_state()
        except Exception:
            pass
        logger.info(
            f"📌 [{self.symbol}] TP基线锚定 {old:.4f}→{live_qty:.4f} "
            f"| {reason or '对账'}"
        )

    def _should_alert_abnormal_reduce(self, initial, live_qty):
        """同仓位生命周期内异常减仓钉钉只允许一条（长冷却）。"""
        now = time.time()
        sig = f"{self.current_side}|{round(float(initial), 3)}|{round(float(live_qty), 3)}"
        last_ts = float(getattr(self, "_abnormal_reduce_alert_ts", 0) or 0)
        last_sig = str(getattr(self, "_abnormal_reduce_alert_sig", "") or "")
        if last_ts > 0 and now - last_ts < ABNORMAL_REDUCE_ALERT_COOLDOWN_SEC:
            return False
        # 同签名更严：同一组数字绝不重复
        if last_sig == sig and last_ts > 0 and now - last_ts < ABNORMAL_REDUCE_ALERT_COOLDOWN_SEC * 2:
            return False
        self._abnormal_reduce_alert_ts = now
        self._abnormal_reduce_alert_sig = sig
        return True

    def _reconcile_tp_consumed_from_live_qty(self, live_qty, curr_px=0.0, source="",
                                            notify=True):
        """
        TP 成交对账（铁律）：
        ① 现价或 best 已触及该档 TP 价 + 开单限价消失 → 才记账
        ② 减仓切片仅作旁证，不能单独把 TP2/TP3 盖章
        ③ WS 提示同样必须价到+限价消失
        头寸不对齐 ≠ TP 成交（穿价秒平/人工减仓不得报 TP12）。
        """
        live_qty = float(live_qty or 0)
        if live_qty < 0:
            live_qty = 0.0
        if getattr(self, "_open_in_progress", False):
            return []
        curr_px = self._live_mark_for_tp_detect(curr_px)

        before = list(getattr(self, "tp_levels_consumed", []) or [])
        # 主判：价到 + 限价消失 + 减仓证据（强制拉实时价）
        inferred = self._infer_tp_consumed_by_price_and_gone(curr_px, live_qty=live_qty)

        initial = float(self._tp_baseline_qty(live_qty) or 0)
        # 辅助减仓：每一档已内置「价到」校验；与主判取交集扩张时仍须价到
        if initial > live_qty + 0.001:
            by_qty = self._infer_tp_consumed_sequential(initial, live_qty, curr_px)
            if by_qty:
                confirmed = []
                for lv in by_qty:
                    if lv in (inferred or []) or self._tp_level_price_and_order_gone(
                        lv, curr_px
                    ):
                        confirmed.append(int(lv))
                    else:
                        logger.warning(
                            f"⚠️ [{self.symbol}] 拒认仅凭减仓记 TP{lv}："
                            f"现价 {curr_px:.2f} / best {float(self.best_price or 0):.2f} "
                            f"未达该档"
                        )
                        break
                if confirmed:
                    merged = sorted(set(inferred or []) | set(confirmed))
                    inferred = self._sequential_tp_prefix(merged)
            elif not inferred:
                # 明显减仓但现价未达任何 TP → 非 TP；微漂静默锚定，重大才告警一次
                reduced = round(initial - live_qty, 3)
                noise = max(0.003, initial * TP_FILL_NOISE_VS_OPEN_PCT)
                alert_floor = max(
                    float(ABNORMAL_REDUCE_ALERT_MIN_QTY),
                    initial * float(ABNORMAL_REDUCE_ALERT_PCT),
                )
                in_open_grace = time.time() < float(
                    getattr(self, "_post_open_radar_block_until", 0) or 0
                )
                if reduced >= noise:
                    logger.warning(
                        f"⚠️ [{self.symbol}] 减仓未达TP {initial}→{live_qty} "
                        f"(Δ{reduced}) mark={curr_px:.2f} | {source} | "
                        f"{'开仓宽限静默锚定' if in_open_grace or reduced < alert_floor else '评估告警'}"
                    )
                    if notify and (
                        not in_open_grace
                        and reduced >= alert_floor
                        and self._should_alert_abnormal_reduce(initial, live_qty)
                    ):
                        try:
                            self._call_dingtalk(
                                dingtalk.report_system_alert,
                                title=f"异常减仓·非TP成交 [{self.symbol}]",
                                detail=(
                                    f"{self.current_side} {initial}→{live_qty} | "
                                    f"现价 {curr_px:.2f} best {float(self.best_price or 0):.2f} | "
                                    f"TP {list(self.tv_tps or [])} 均未触及 | "
                                    f"{source or '对账'} | 已拒绝误报TP；"
                                    f"本仓位同类告警 {ABNORMAL_REDUCE_ALERT_COOLDOWN_SEC:.0f}s 内不再重复"
                                ),
                                level="警告",
                                suggestion="核对是否穿价秒平/人工减仓；仓位以交易所为准",
                            )
                        except Exception:
                            pass
                    elif notify and (
                        in_open_grace or reduced < alert_floor
                    ):
                        # 开仓后一次仓位核实播报（微漂也对一次账），同类去重
                        try:
                            self._call_dingtalk(
                                dingtalk.report_position_qty_reconcile,
                                side=self.current_side or "",
                                baseline=initial,
                                live_qty=live_qty,
                                curr_px=curr_px,
                                note=(
                                    "开仓宽限/微漂对账·已锚定实盘"
                                    if in_open_grace
                                    else "头寸微差对账·已锚定实盘（非TP）"
                                ),
                            )
                        except Exception:
                            pass
                    # 无论是否告警：锚定实盘，杜绝连环刷屏
                    self._resync_tp_baseline(
                        live_qty,
                        reason=f"非TP减仓对账|{source or '—'}",
                    )
        # WS 提示：仅限价已消失且价到才并入
        ws_levels = set(getattr(self, "_ws_tp_fill_levels", set()) or set())
        if getattr(self, "_ws_tp1_fill_hint", False):
            ws_levels.add(1)
        if ws_levels:
            merged = list(inferred or [])
            for lv in sorted(ws_levels):
                if lv in merged:
                    continue
                if self._tp_level_price_and_order_gone(lv, curr_px):
                    merged.append(int(lv))
                else:
                    logger.info(
                        f"🧮 [{self.symbol}] WS提示TP{lv}但现价未达 → 忽略"
                    )
            inferred = self._sequential_tp_prefix(merged)

        # 清掉账本里「未价到却已标记」的假 TP 成交
        if before:
            cleaned = [
                lv for lv in before
                if self._price_reached_tp_zone(
                    lv, curr_px,
                    float(self.tv_tps[lv - 1]) if self.tv_tps and lv - 1 < len(self.tv_tps) else 0,
                )
            ]
            cleaned = self._sequential_tp_prefix(cleaned)
            if cleaned != before and not inferred:
                logger.warning(
                    f"⚠️ [{self.symbol}] 清除未价到的假TP记账 "
                    f"TP{before}→TP{cleaned or '无'} | mark={curr_px:.2f}"
                )
                self.tp_levels_consumed = cleaned
                self._save_state()
                before = cleaned

        newly = [lv for lv in (inferred or []) if lv not in before]
        if inferred and inferred != before:
            self._mark_tp_levels_consumed(inferred)
            remain = [lv for lv in (1, 2, 3) if lv not in set(inferred)]
            logger.warning(
                f"🎯 [{self.symbol}] [{source or 'TP价到对账'}] 价到+限价消失记账 "
                f"TP{before or '无'}→TP{inferred} | 现价 {curr_px:.2f} "
                f"best {float(self.best_price or 0):.2f} | "
                f"剩余耐心等 TP{remain or '无'}"
            )
            if notify and newly:
                try:
                    baseline = initial if initial > 0 else float(
                        self._tp_baseline_qty(live_qty) or live_qty
                    )
                    slices = {
                        int(s["level"]): s for s in self._tp_slices_for_initial(baseline)
                    }
                    # 同一次对账只发一条汇总钉钉，不按档连环刷
                    lv_txt = ",".join(str(x) for x in newly)
                    first_lv = newly[0]
                    sl = slices.get(first_lv) or {}
                    remain_txt = (
                        f"耐心等 TP{remain}" if remain else "TP123 已吃完"
                    )
                    self._call_dingtalk(
                        dingtalk.report_tp_fill,
                        tp_level=first_lv,
                        tp_price=float(sl.get("price") or 0),
                        filled_qty=float(sl.get("qty") or 0),
                        remain_qty=live_qty,
                        entry_px=float(self.watched_entry or 0),
                        side=self.current_side or "?",
                        regime=int(
                            getattr(self, "open_regime", None) or self.regime or 3
                        ),
                        verify_note=(
                            f"价到+限价消失=成交 TP[{lv_txt}] | {source} | "
                            f"现价 {curr_px:.2f} best {float(self.best_price or 0):.2f} | "
                            f"{remain_txt} | 禁止再挂已成交档"
                        ),
                        verified=True,
                    )
                except Exception as e:
                    logger.warning(f"TP成交对账钉钉失败: {e}")
                self._ws_tp_fill_levels = set()
                if 1 in inferred:
                    self._ws_tp1_fill_hint = False
            # TP1 成交后尝试雷达交棒（与剩余 TP23 并行，不撤 TP）
            if 1 in newly and live_qty > 0 and curr_px > 0:
                try:
                    if self._radar_ready_to_handoff(curr_px, live_qty):
                        self._perform_radar_handoff(
                            live_qty, curr_px,
                            reason="TP1价到成交·雷达保本交棒",
                        )
                except Exception as e:
                    logger.warning(f"TP1记账后雷达交棒异常: {e}")
        return newly

    def _block_rehang_filled_tps_note(self, live_qty, curr_px=0.0):
        """补挂前对账；若有新成交记账返回说明，调用方应重审 audit。"""
        newly = self._reconcile_tp_consumed_from_live_qty(
            live_qty, curr_px, source="补挂前·价到对账", notify=True,
        )
        if newly:
            return (
                f"价到+限价消失已记账 TP{newly}，禁止补挂这些档，耐心等剩余TP"
            )
        return ""

    def _mark_tp_levels_consumed(self, levels):
        consumed = set(getattr(self, "tp_levels_consumed", []) or [])
        for lv in levels:
            consumed.add(int(lv))
        self.tp_levels_consumed = self._sequential_tp_prefix(sorted(consumed))
        self._save_state()

    def _in_self_tp_purge_window(self):
        """刚自撤 TP / 开仓对齐中：限价消失是我们干的，不是成交"""
        if getattr(self, "_open_in_progress", False):
            return True
        if getattr(self, "_defense_align_in_progress", False):
            return True
        purged = float(getattr(self, "_tp_purge_ts", 0) or 0)
        return purged > 0 and (time.time() - purged) < 30.0

    def _qty_evidence_tp_consumed(self, level, live_qty):
        """
        头寸相对开仓基线确有减仓，才允许把「限价消失」记成 TP 成交。
        防：开仓先撤 TP → 现价贴近 TP1 → 假成交 → 整档不挂（裸仓）。
        """
        level = int(level or 0)
        live_qty = float(live_qty if live_qty is not None else self.watched_qty or 0)
        initial = float(self._tp_baseline_qty(live_qty) or self.initial_qty or 0)
        if level < 1 or initial <= 0:
            return False
        reduced = initial - live_qty
        noise = max(0.003, initial * 0.01)
        if reduced < noise:
            return False
        need = 0.0
        for sl in self._tp_slices_for_initial(initial):
            if int(sl.get("level") or 0) <= level:
                need += float(sl.get("qty") or 0)
        return reduced >= max(noise, need * 0.35)

    def _may_mark_tp_filled_missing_limit(self, level, live_qty, curr_px=0.0, tp_px=None):
        """
        价到 + 限价消失 → 可记账的前提：
        ① 非自撤/开仓对齐窗口
        ② 有减仓证据（开仓宽限内强制；平时也要求，防假吃 TP1）
        """
        level = int(level or 0)
        if level < 1 or level > 3:
            return False
        if self._in_self_tp_purge_window():
            return False
        live_qty = float(live_qty if live_qty is not None else self.watched_qty or 0)
        idx = level - 1
        px = float(
            tp_px
            if tp_px is not None
            else ((self.tv_tps[idx] if self.tv_tps and 0 <= idx < len(self.tv_tps) else 0) or 0)
        )
        if px <= 0:
            return False
        if self._has_tp_limit_at_price(px):
            return False
        if not self._price_reached_tp_zone(level, curr_px, px):
            return False
        if not self._qty_evidence_tp_consumed(level, live_qty):
            logger.warning(
                f"🧩 [{self.symbol}] 拒认 TP{level} 假成交：价到+限价无，但头寸无减仓证据 "
                f"(live={live_qty:.4f} base={float(self._tp_baseline_qty(live_qty) or 0):.4f}) "
                f"→ 视为漏挂，允许补挂/推离"
            )
            return False
        return True

    def _clear_spurious_tp_consumed_if_full_size(self, live_qty, source=""):
        """
        开仓后仓位≈基线却已记账 TP → 清掉假成交。
        例外：现价已过该档（接管）→ 保留，禁止清后重挂 TP1。
        """
        live_qty = float(live_qty or 0)
        consumed = list(getattr(self, "tp_levels_consumed", []) or [])
        if not consumed or live_qty <= 0:
            return False
        if any(self._qty_evidence_tp_consumed(lv, live_qty) for lv in consumed):
            return False
        curr_px = float(binance_client.get_current_price(self.symbol) or 0)
        price_keep = [
            lv for lv in consumed
            if self._price_reached_tp_zone(lv, curr_px, live_only=True)
        ]
        if price_keep:
            keep = self._sequential_tp_prefix(price_keep)
            if keep != consumed:
                logger.warning(
                    f"⚠️ [{self.symbol}] 保留现价已过档 TP{keep}，"
                    f"清除其余假记账 {consumed} | {source}"
                )
                self.tp_levels_consumed = keep
                self._save_state()
                return True
            logger.info(
                f"📌 [{self.symbol}] 现价已过 TP{keep} → 不清除记账 | {source}"
            )
            return False
        logger.error(
            f"🚨 [{self.symbol}] 清除假 TP 成交记账 {consumed} | live={live_qty} | {source}"
        )
        self.tp_levels_consumed = []
        self._save_state()
        return True

    def _apply_takeover_price_progress(self, entry, curr_px, live_qty, source="接管"):
        """
        重启/接管铁律（开仓价 + 实时价两头对账）：
        - 现价已达/越过 TPn → 记账跳过，禁止再挂该档（防 TP1 反复补挂秒成）
        - 只挂尚未达价的剩余档（TP1过→只挂23；TP2过→只挂3）
        - 达雷达激活比例或 TP1 已过 → 应启雷达动态追随
        不要求减仓证据（接管时限价可能已成交或从未挂上）。
        """
        entry = float(entry or self.watched_entry or 0)
        curr_px = float(
            curr_px
            or binance_client.get_current_price(self.symbol)
            or 0
        )
        live_qty = float(live_qty or 0)
        notes = []
        if entry <= 0 or curr_px <= 0 or not self.current_side:
            return {
                "consumed": list(getattr(self, "tp_levels_consumed", []) or []),
                "hang_levels": [1, 2, 3],
                "should_radar": False,
                "notes": ["缺开仓价或现价"],
            }

        self._ensure_tp123_prices_from_tv(entry)
        past = []
        for lv in (1, 2, 3):
            if not self.tv_tps or lv - 1 >= len(self.tv_tps):
                break
            px = float(self.tv_tps[lv - 1] or 0)
            if px <= 0:
                break
            if self._price_reached_tp_zone(lv, curr_px, px, live_only=True):
                past.append(lv)
            else:
                break
        past = self._sequential_tp_prefix(past)
        prev = list(getattr(self, "tp_levels_consumed", []) or [])
        merged = self._sequential_tp_prefix(sorted(set(prev) | set(past)))
        if merged != prev:
            self.tp_levels_consumed = merged
            self._save_state()

        hang = []
        for lv in (1, 2, 3):
            if lv in set(merged):
                continue
            if self.tv_tps and lv - 1 < len(self.tv_tps) and float(self.tv_tps[lv - 1] or 0) > 0:
                hang.append(lv)

        act_ratio = float(self._radar_activation_ratio() or 0)
        prog = float(self._tp1_direction_progress(curr_px) or 0)
        should_radar = bool(
            self._price_reached_radar_activation(curr_px, live_only=True)
            or (1 in merged)
            or (act_ratio > 0 and prog >= act_ratio)
        )
        if past:
            rem = hang if hang else ["无(全过)"]
            notes.append(
                f"现价已过TP{past}→跳过不挂 | 应挂{rem} | "
                f"entry={entry:.2f} mark={curr_px:.2f}"
            )
            logger.warning(
                f"🧭 [{source}] [{self.symbol}] 开仓价/现价对账: "
                f"已过 TP{past} → 禁止补挂这些档 | 只挂 {hang or '无'} | "
                f"雷达={'应激活' if should_radar else '待命'} "
                f"(朝TP1 {prog:.0%}/{act_ratio:.0%})"
            )
        else:
            notes.append(
                f"现价未过TP1 | 应挂{hang or [1, 2, 3]} | "
                f"entry={entry:.2f} mark={curr_px:.2f} | "
                f"雷达={'应激活' if should_radar else '待命'}"
            )
            logger.info(
                f"🧭 [{source}] [{self.symbol}] 开仓价/现价对账: "
                f"未过TP1 → 可挂 {hang or [1, 2, 3]} | "
                f"雷达={'应激活' if should_radar else '待命'} "
                f"(朝TP1 {prog:.0%}/{act_ratio:.0%})"
            )
        return {
            "consumed": merged,
            "hang_levels": hang,
            "should_radar": should_radar,
            "notes": notes,
            "progress": prog,
            "act_ratio": act_ratio,
            "entry": entry,
            "mark": curr_px,
        }

    def _force_tps_unmarketable(self, curr_px=None, entry=None):
        """穿价 TP 多轮加大间隙推离，直到可挂；禁止整档跳过导致 0 TP"""
        side = str(self.current_side or "").strip().upper()
        entry = float(entry or self.watched_entry or 0)
        curr_px = float(
            curr_px
            or binance_client.get_current_price(self.symbol)
            or self.tv_price
            or 0
        )
        atr = float(getattr(self, "open_atr", None) or self.current_atr or 30)
        if side not in ("LONG", "SHORT") or curr_px <= 0:
            return list(self.tv_tps or [])
        self._sanitize_open_tps_vs_mark(entry, curr_px)
        tps = list(self.tv_tps or [0.0, 0.0, 0.0])
        while len(tps) < 3:
            tps.append(0.0)
        for attempt in range(6):
            if not any(
                self._tp_is_marketable(side, float(p or 0), curr_px)
                for p in tps if float(p or 0) > 0
            ):
                break
            gap = max(curr_px * 0.0015, atr * 0.15, 0.5) * (1.6 ** attempt)
            fixed = []
            for i, p in enumerate(tps[:3]):
                px = float(p or 0)
                if px <= 0:
                    fixed.append(0.0)
                    continue
                if self._tp_is_marketable(side, px, curr_px):
                    if side == "LONG":
                        px = round(curr_px + gap * (i + 1), 2)
                    else:
                        px = round(curr_px - gap * (i + 1), 2)
                    logger.error(
                        f"🚨 [{self.symbol}] 强制推离穿价 TP{i + 1} → @{px:.2f} "
                        f"(mark={curr_px:.2f} gap={gap:.2f} round={attempt + 1})"
                    )
                fixed.append(px)
            if side == "LONG":
                for i in range(1, 3):
                    if fixed[i] > 0 and fixed[i - 1] > 0 and fixed[i] <= fixed[i - 1]:
                        fixed[i] = round(fixed[i - 1] + gap, 2)
            else:
                for i in range(1, 3):
                    if fixed[i] > 0 and fixed[i - 1] > 0 and fixed[i] >= fixed[i - 1]:
                        fixed[i] = round(fixed[i - 1] - gap, 2)
            tps = fixed
        self.tv_tps = self._sanitize_tp_prices(tps)
        self._save_state()
        return list(self.tv_tps)

    def _force_hang_open_defenses(self, live_qty, entry, rounds=3, takeover_mode=False):
        """
        开仓/接管铁律闭环：挂齐「应挂」剩余 TP + TV 硬止损。
        takeover_mode：先按现价跳过已过档，禁止清记账后重挂 TP1。
        """
        live_qty = float(live_qty or 0)
        entry = float(entry or self.watched_entry or 0)
        last_audit = self._audit_tp_levels(live_qty)
        hung = binance_client.find_protective_stop_prices(self.symbol)
        for r in range(max(1, int(rounds))):
            mark = float(binance_client.get_current_price(self.symbol) or entry or 0)
            if takeover_mode:
                self._apply_takeover_price_progress(
                    entry, mark, live_qty, source=f"接管强制挂#{r + 1}",
                )
            else:
                self._clear_spurious_tp_consumed_if_full_size(
                    live_qty, source=f"开仓强制挂防线#{r + 1}",
                )
            self._nuclear_fail_streak = 0
            self._ensure_tp123_prices_from_tv(entry)
            # 只对尚未达价的剩余档推离穿价
            remaining = [
                lv for lv in self._expected_tp_levels(live_qty)
                if float(lv.get("price") or 0) > 0
            ]
            if remaining:
                self._force_tps_unmarketable(mark, entry)
            placed = self._place_tp_levels_only(live_qty, retries=3)
            radar_sl = None
            if takeover_mode and self._radar_ready_to_handoff(mark, live_qty):
                try:
                    self._process_radar_trailing(live_qty, mark)
                    radar_sl = self._radar_sl_to_pass()
                except Exception as e:
                    logger.warning(f"接管强制挂·雷达追随跳过: {e}")
            self._sync_exchange_stop(
                live_qty, radar_sl=radar_sl,
                reason=(
                    f"接管强制挂#{r + 1}·保护止损" if takeover_mode
                    else f"开仓强制挂防线#{r + 1}·TV硬止损"
                ),
                force=True,
            )
            time.sleep(0.7)
            last_audit = self._audit_tp_levels(live_qty)
            hung = binance_client.find_protective_stop_prices(self.symbol)
            matched = int(last_audit.get("matched_full") or 0)
            expected = int(last_audit.get("expected") or 0)
            logger.warning(
                f"🛡️ [{self.symbol}] "
                f"{'接管' if takeover_mode else '开仓'}强制挂防线 "
                f"#{r + 1}/{rounds} placed={placed} TP {matched}/{expected} "
                f"stop={hung} consumed={getattr(self, 'tp_levels_consumed', [])}"
            )
            if expected > 0 and matched >= expected and hung:
                self._mark_defense_align_ok()
                return last_audit, hung
            if expected <= 0 and hung and takeover_mode:
                # 全档现价已过：只保硬止损/雷达即可
                self._mark_defense_align_ok()
                return last_audit, hung
            if expected <= 0 and not takeover_mode:
                self._ensure_tp123_prices_from_tv(entry)
                self._clear_spurious_tp_consumed_if_full_size(
                    live_qty, source="开仓强制·expected=0",
                )
        return last_audit, hung

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
        """
        应挂 TP 列表。铁律：已消费档 / 现价已达档（非开仓瞬间）→ 永不进入应挂，
        杜绝「TP1 成交后当漏挂 → 补挂 → 头寸在 TP1 吃光」低级 bug。
        """
        consumed = set(getattr(self, "tp_levels_consumed", []) or [])
        qty_map = self._split_remaining_tp_quantities(live_qty)
        qty_map = self._normalize_tp_qty_map(qty_map, live_qty)
        levels = []
        curr_px = 0.0
        open_busy = bool(getattr(self, "_open_in_progress", False))
        if not open_busy:
            try:
                curr_px = float(binance_client.get_current_price(self.symbol) or 0)
            except Exception:
                curr_px = 0.0
        for level in (1, 2, 3):
            if level in consumed:
                continue
            price = self.tv_tps[level - 1] if self.tv_tps and level - 1 < len(self.tv_tps) else 0
            # 持仓期：现价已达该档 → 记账并跳过，禁止再出现在应挂列表
            if (
                not open_busy
                and curr_px > 0
                and float(price or 0) > 0
                and self._price_reached_tp_zone(level, curr_px, price, live_only=True)
            ):
                self._mark_tp_levels_consumed([level])
                consumed.add(level)
                logger.warning(
                    f"📌 [{self.symbol}] 现价已达 TP{level}@{float(price):.2f} "
                    f"(mark={curr_px:.2f}) → 记账跳过，禁止补挂"
                )
                continue
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
        """实盘硬止损价 = TV tv_sl（严格）。"""
        return self._tv_hard_sl_target(entry) or None

    def _resolve_hard_sl_regime(self):
        """开仓档位锁定（雷达/TP 比例用）；硬止损价本身只认 TV tv_sl。"""
        return int(getattr(self, "open_regime", None) or self.regime or 3)

    def _tv_hard_sl_target(self, entry=None, side=None, regime=None):
        """
        实盘硬止损唯一来源：TV tv_sl（账本）→ 回退 tv_sl_ref。
        禁止再用开仓价×档位% 的 VPS 宽止损。
        """
        # 优先 tv_sl_ref（真 TV）；旧账本 tv_sl 可能是遗留 VPS% 宽价
        px = round(float(getattr(self, "tv_sl_ref", 0) or 0), 2)
        if px <= 0:
            px = round(float(getattr(self, "tv_sl", 0) or 0), 2)
        if px > 0:
            return px
        last = self.last_tv_signal if isinstance(self.last_tv_signal, dict) else {}
        for src in (
            last,
            last.get("payload") if isinstance(last.get("payload"), dict) else {},
            getattr(self, "_pending_open_defense_snap", None) or {},
        ):
            if not isinstance(src, dict):
                continue
            cand = round(self._safe_float(src.get("tv_sl"), 0), 2)
            if cand > 0:
                self.tv_sl = cand
                self.tv_sl_ref = cand
                return cand
        return 0.0

    def _vps_hard_sl_target(self, entry=None, side=None, regime=None):
        """兼容旧名 → 已改为 TV 硬止损。"""
        return self._tv_hard_sl_target(entry, side, regime)

    def _matches_any_vps_regime_stop(self, stop_px, entry=None, side=None):
        """旧 VPS% 档位匹配已废弃；恒 False（不再用 VPS 宽价识别）。"""
        return 0

    def _looks_like_tv_tight_stop(self, stop_px, entry=None, side=None):
        """
        旧逻辑：把 TV 止损当「紧价」禁止挂盘 — 已废除。
        恒返回 False，允许/要求挂 TV 硬止损。
        """
        return False

    def _is_valid_radar_sl(self, sl, entry=None, side=None):
        """雷达保本只能在浮盈侧：LONG > entry，SHORT < entry。"""
        entry = float(entry if entry is not None else (self.watched_entry or 0))
        side = str(side or self.current_side or "").strip().upper()
        sl = round(float(sl or 0), 2)
        if entry <= 0 or sl <= 0 or side not in ("LONG", "SHORT"):
            return False
        if side == "LONG":
            return sl > entry + 0.01
        return sl < entry - 0.01

    def _is_exchange_stop_acceptable_as_vps_floor(self, stop_px, entry=None, side=None):
        """盘口 STOP 贴近 TV 硬止损（或合法雷达）即可写回。"""
        stop_px = round(float(stop_px or 0), 2)
        if stop_px <= 0:
            return False
        tv = self._tv_hard_sl_target(entry, side)
        tol = max(float(SHIELD_STOP_TOLERANCE), stop_px * 0.002)
        if tv > 0 and abs(stop_px - tv) <= tol:
            return True
        return self._is_valid_radar_sl(stop_px, entry, side)

    def _sanitize_vps_hard_sl_ledger(self, source=""):
        """
        强制账本硬止损 = TV tv_sl（不得用 VPS% 覆盖）。
        若仅有 tv_sl_ref → 写入 tv_sl；两者皆无 → False（调用方告警）。
        """
        entry = float(self.watched_entry or 0)
        side = str(self.current_side or "").strip().upper()
        tv = self._tv_hard_sl_target(entry, side)
        if tv <= 0:
            logger.error(
                f"🚨 [{self.symbol}] 硬止损账本消毒失败：无 TV tv_sl | {source}"
            )
            return False
        cur = round(float(getattr(self, "tv_sl", 0) or 0), 2)
        if abs(cur - tv) > SHIELD_STOP_TOLERANCE or cur <= 0:
            old = cur
            self.tv_sl = tv
            if float(getattr(self, "tv_sl_ref", 0) or 0) <= 0:
                self.tv_sl_ref = tv
            self._last_applied_exchange_sl = 0.0
            self._save_state()
            logger.info(
                f"🛡️ TV硬止损账本对齐 @{tv:.2f} "
                f"(原 {old or 0:.2f}) | {source or '消毒'}"
            )
        return True

    def _refresh_vps_hard_sl(self, entry=None, side=None, regime=None, atr=None,
                             tv_sl_ref=None, source=""):
        """
        硬止损刷新：严格写入 TV tv_sl 并作为盘口挂单价。
        禁止开仓价×档位% 的 VPS 宽止损覆盖。
        """
        entry = float(entry or self.watched_entry or self.tv_price or 0)
        side = (side or self.current_side or "").strip().upper()

        ref = 0.0
        if tv_sl_ref is not None:
            ref = round(self._safe_float(tv_sl_ref, 0), 2)
        if ref <= 0:
            ref = round(float(getattr(self, "tv_sl_ref", 0) or 0), 2)
        if ref <= 0:
            # 仅当无 ref 时才读 tv_sl（避免旧 VPS% 污染当「TV」）
            last = self.last_tv_signal if isinstance(self.last_tv_signal, dict) else {}
            for src in (
                last,
                last.get("payload") if isinstance(last.get("payload"), dict) else {},
                getattr(self, "_pending_open_defense_snap", None) or {},
            ):
                if not isinstance(src, dict):
                    continue
                cand = round(self._safe_float(src.get("tv_sl"), 0), 2)
                if cand > 0:
                    ref = cand
                    break
        if ref <= 0:
            ref = round(float(getattr(self, "tv_sl", 0) or 0), 2)

        if ref <= 0:
            logger.error(
                f"🚨 [{self.symbol}] TV硬止损缺失，无法刷新 | {source} "
                f"entry={entry} side={side}"
            )
            return False

        old = round(float(getattr(self, "tv_sl", 0) or 0), 2)
        self.tv_sl_ref = ref
        self.tv_sl = ref
        if abs(ref - old) > SHIELD_STOP_TOLERANCE:
            self._last_applied_exchange_sl = 0.0
        self._save_state()
        logger.info(
            f"🛡️ TV硬止损 @{ref:.2f} | {side or '?'} entry={entry:.2f}"
            + (f" ({source})" if source else "")
            + (f" | 原 {old:.2f}" if old > 0 and abs(ref - old) > SHIELD_STOP_TOLERANCE else "")
        )
        return True

    def _apply_tv_sl_from_payload(self, payload, source=""):
        """TV tv_sl → 账本硬止损（严格）；开仓后由 sync 挂到交易所。"""
        tv_ref = payload.get("tv_sl")
        if tv_ref is None or tv_ref == "":
            ok = self._refresh_vps_hard_sl(source=source or "信号·无tv_sl字段")
            if not ok:
                dingtalk.report_system_alert(
                    f"TV硬止损缺失 [{self.symbol}]",
                    f"{source or '信号'} payload 无 tv_sl，无法挂硬止损",
                )
            return ok
        ref_px = round(self._safe_float(tv_ref, 0), 2)
        if ref_px <= 0:
            return False
        entry = float(self.tv_price or self.watched_entry or 0)
        side = str(payload.get("action") or payload.get("side") or self.current_side or "").upper()
        if side not in ("LONG", "SHORT"):
            side = self.current_side
        return self._refresh_vps_hard_sl(
            entry=entry, side=side,
            regime=self._resolve_hard_sl_regime(), atr=self.current_atr,
            tv_sl_ref=ref_px, source=source or "TV硬止损",
        )

    def _effective_exchange_stop(self, radar_sl=None):
        """
        合并止损：底线 = TV 硬止损；雷达已交棒且在浮盈侧时可替换为雷达保本。
        """
        floor = self._tv_hard_sl_target()
        if floor > 0:
            self.tv_sl = floor
        radar = None
        if radar_sl and float(radar_sl) > 0:
            cand = round(float(radar_sl), 2)
            if self._is_valid_radar_sl(cand):
                radar = cand
            else:
                logger.warning(
                    f"🛡️ [{self.symbol}] 拒绝非法雷达价 @{cand:.2f} "
                    f"→ 仅挂 TV硬止损@{floor or 0:.2f}"
                )
        if not floor and not radar:
            return None
        if not floor:
            return radar
        if not radar:
            return floor
        if self.current_side == "LONG":
            return max(radar, floor) if radar > floor else radar
        if self.current_side == "SHORT":
            return radar
        return floor

    def _clamp_radar_to_vps_floor(self, radar_sl):
        """雷达保本：非法 → 回退 TV 硬止损。"""
        if not radar_sl:
            return self._tv_hard_sl_target() or radar_sl
        if self._is_valid_radar_sl(radar_sl):
            return round(float(radar_sl), 2)
        return self._tv_hard_sl_target() or None

    def _clamp_radar_to_tv_floor(self, radar_sl):
        """兼容旧名 → TV 硬止损底线夹紧"""
        return self._clamp_radar_to_vps_floor(radar_sl)

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
        keep_near: 若给出目标价，保留触发价贴近该价的单仓位；其余一律撤。
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

    def _place_vps_hard_sl_order(self, live_qty, trigger_px, use_stop_limit=False):
        """
        TV 硬止损：Stop-Market closePosition（不占 reduceOnly 额度）。
        触发价 = TV tv_sl **原值**，禁止任何 ±buffer / 推宽 / 推低。
        若贴市导致交易所拒单 → 返回 None，由上层紧急平仓（禁止改价保活）。
        """
        live_qty = self._resolve_live_qty(live_qty)
        trigger_px = round(float(trigger_px or 0), 2)
        if live_qty <= 0 or trigger_px <= 0 or not self.current_side:
            return None
        curr_px = float(binance_client.get_current_price(self.symbol) or 0)
        if curr_px > 0:
            # 仅检测，不改价：穿价/贴市则失败，禁止 gap*1.25 推宽旧逻辑
            if self.current_side == "LONG" and trigger_px >= curr_px:
                logger.error(
                    f"🚨 [{self.symbol}] LONG TV硬止损 @{trigger_px:.2f} 已穿/贴市 "
                    f"{curr_px:.2f} → 禁止推宽，交紧急平仓"
                )
                return None
            if self.current_side == "SHORT" and trigger_px <= curr_px:
                logger.error(
                    f"🚨 [{self.symbol}] SHORT TV硬止损 @{trigger_px:.2f} 已穿/贴市 "
                    f"{curr_px:.2f} → 禁止推宽，交紧急平仓"
                )
                return None
        close_side = "SHORT" if self.current_side == "LONG" else "LONG"
        if use_stop_limit:
            # 限价=触发价原值，禁止 VPS_HARD_SL_LIMIT_PCT 偏移
            return binance_client.place_stop_limit_order(
                close_side, live_qty, trigger_px, symbol=self.symbol, limit_price=trigger_px,
            )
        return binance_client.place_stop_market_order(
            close_side, trigger_px, symbol=self.symbol, quantity=None,
        )

    def _sync_exchange_stop(self, live_qty, radar_sl=None, reason="", force=False):
        """
        统一交易所保护止损为单槽：挂 TV 硬止损（或合法浮盈侧雷达）。
        禁止改回 VPS%；无 TV 价 → 告警且失败。
        """
        live_qty = self._resolve_live_qty(live_qty)
        if live_qty <= 0 or not self.current_side or not self.watched_entry:
            return {"ok": False, "skipped": True, "reason": "no_position"}

        self._lock_open_regime_from_sources(force=False)
        self._sanitize_vps_hard_sl_ledger(source=reason or "同步止损消毒")
        target = self._effective_exchange_stop(radar_sl)
        if not target or target <= 0:
            logger.error(
                f"🚨 [{self.symbol}] 同步硬止损失败：无 TV tv_sl | {reason}"
            )
            try:
                self._call_dingtalk(
                    dingtalk.report_system_alert,
                    title=f"TV硬止损缺失·无法挂单 [{self.symbol}]",
                    detail=(
                        f"{self.current_side} qty={live_qty} | {reason or '同步'} | "
                        f"请核对 TV payload tv_sl"
                    ),
                    level="紧急",
                    suggestion="等待带 tv_sl 的 TV 信号或人工挂止损",
                )
            except Exception:
                pass
            return {"ok": False, "skipped": True, "reason": "no_tv_sl"}
        target = round(float(target), 2)

        live_stops = self._count_protective_stops()
        near = [p for p in live_stops if abs(p - target) <= SHIELD_STOP_TOLERANCE]
        orphans = [p for p in live_stops if abs(p - target) > SHIELD_STOP_TOLERANCE]

        last = round(float(getattr(self, "_last_applied_exchange_sl", 0) or 0), 2)
        now = time.time()
        if not orphans and len(near) == 1:
            self._last_applied_exchange_sl = target
            self._last_hard_sl_sync_ts = now
            self.shield_active = True
            self.shield_sized_qty = live_qty
            self._tv_sl_missing_alerted = False
            if abs(last - target) > SHIELD_STOP_TOLERANCE:
                self._save_state()
            return {
                "ok": True, "skipped": True, "target": target,
                "reason": "idempotent_unified",
            }

        if (
            not force
            and last > 0
            and abs(last - target) <= SHIELD_STOP_TOLERANCE
            and (now - float(getattr(self, "_last_hard_sl_sync_ts", 0) or 0))
            < HARD_SL_SYNC_COOLDOWN_SEC
        ):
            if not orphans and (near or self._has_stop_sl_near(target, exclude_shield=False)):
                return {
                    "ok": True, "skipped": True, "target": target,
                    "reason": "cooldown_same_target",
                }

        purged = 0
        ok = False
        res = None
        had_old_stops = bool(live_stops)
        for attempt in range(3):
            if self._has_stop_sl_near(target, exclude_shield=False):
                ok = True
                break
            res = self._place_vps_hard_sl_order(
                live_qty, target, use_stop_limit=False,
            )
            time.sleep(0.45 if attempt == 0 else 0.7)
            ok = res is not None and self._has_stop_sl_near(
                target, exclude_shield=False,
            )
            if ok:
                break
            logger.warning(
                f"🛡️ [{self.symbol}] TV硬止损挂单未核实 @{target:.2f} "
                f"重试 {attempt + 1}/3"
            )

        if ok:
            purged = self._purge_all_protective_stops(keep_near=target)
            if purged or orphans:
                logger.warning(
                    f"🛡️ 统一TV硬止损：新挂已核实 @{target:.2f}，清孤儿 {purged} 笔 "
                    f"(原盘口{live_stops})"
                )
                time.sleep(0.35)
                if not self._has_stop_sl_near(target, exclude_shield=False):
                    res = self._place_vps_hard_sl_order(
                        live_qty, target, use_stop_limit=False,
                    )
                    time.sleep(0.45)
                    ok = res is not None and self._has_stop_sl_near(
                        target, exclude_shield=False,
                    )
        elif had_old_stops:
            logger.error(
                f"❌ [{self.symbol}] TV硬止损新挂失败 @{target:.2f}，"
                f"保留原盘口 STOP {live_stops}，禁止撤净裸仓 | {reason}"
            )
            self._record_shield_maintain(success=True)
            return {
                "ok": True, "skipped": False, "target": target, "purged": 0,
                "reason": "place_failed_keep_old",
            }
        else:
            logger.error(
                f"❌ [{self.symbol}] TV硬止损新挂失败且盘口无 STOP → 裸仓 | {reason}"
            )
            try:
                self._call_dingtalk(
                    dingtalk.report_system_alert,
                    title=f"裸仓告警·TV硬止损未挂上 [{self.symbol}]",
                    detail=(
                        f"{self.current_side} qty={live_qty} 目标TV_SL@{target:.2f} "
                        f"| {reason or '同步'} | 请人工挂 closePosition"
                    ),
                    level="紧急",
                    suggestion="币安 APP 按 TV tv_sl 手动挂止损；勿反复重启核武撤单",
                )
            except Exception:
                pass
            self._record_shield_maintain(success=False)
            return {"ok": False, "skipped": False, "target": target, "purged": 0}

        leftovers = [
            p for p in (self._count_protective_stops() or [])
            if abs(float(p) - target) > SHIELD_STOP_TOLERANCE
        ]
        if leftovers and ok:
            extra = self._purge_all_protective_stops(keep_near=target)
            purged += extra
            logger.warning(f"🛡️ 二次清孤儿 STOP{leftovers} 撤 {extra} 笔")
            time.sleep(0.3)
            if not self._has_stop_sl_near(target, exclude_shield=False):
                self._place_vps_hard_sl_order(live_qty, target, use_stop_limit=False)
                time.sleep(0.4)
                ok = self._has_stop_sl_near(target, exclude_shield=False)

        if ok:
            self._last_applied_exchange_sl = target
            self._last_hard_sl_sync_ts = time.time()
            self.shield_active = True
            self.shield_sized_qty = live_qty
            self._shield_fail_streak = 0
            self._tv_sl_missing_alerted = False
            self.current_sl = target
            self._save_state()
            self._record_shield_maintain(success=True)
            logger.info(
                f"✅ [{self.symbol}] TV硬止损已挂 @{target:.2f} | {reason} | "
                f"tv_sl={float(getattr(self, 'tv_sl', 0) or 0) or target:.2f} | "
                f"撤孤儿 {purged} 笔"
            )
        else:
            self._record_shield_maintain(success=False)
        return {"ok": ok, "skipped": False, "target": target, "purged": purged}

    def _handle_tv_sl_update(self, payload):
        """UPDATE_SL：按 TV 新硬止损改盘口（多空一致，严格挂单）。"""
        ref = round(self._safe_float(payload.get("tv_sl"), 0), 2)
        if ref <= 0:
            logger.error(f"UPDATE_SL 忽略：无有效 tv_sl | payload={payload}")
            dingtalk.report_system_alert(
                f"UPDATE_SL 无 tv_sl [{self.symbol}]",
                "TV UPDATE_SL 未带有效 tv_sl，盘口硬止损未改",
            )
            return
        self.tv_sl_ref = ref
        self.tv_sl = ref
        self._last_applied_exchange_sl = 0.0
        self._save_state()
        pos = self._get_active_position()
        live_qty = float((pos or {}).get("size") or self.watched_qty or 0)
        hung = []
        ok = False
        if live_qty > 0 and self.current_side:
            sync = self._sync_exchange_stop(
                live_qty, radar_sl=self._radar_sl_to_pass(),
                reason="UPDATE_SL·按TV硬止损重挂", force=True,
            )
            ok = bool(sync.get("ok"))
            hung = binance_client.find_protective_stop_prices(self.symbol)
        logger.info(
            f"UPDATE_SL 已按 TV 硬止损执行 | tv_sl={ref:.2f} | "
            f"盘口={hung} | ok={ok}"
        )
        try:
            self._call_dingtalk(
                dingtalk.report_tv_sl_updated,
                side=self.current_side or "",
                live_qty=live_qty,
                entry=float(self.watched_entry or 0),
                tv_sl=ref,
                exchange_stop=float(hung[0]) if hung else ref,
                radar_active=self._is_radar_active(),
                radar_sl=self._radar_sl_to_pass(),
                regime=self._resolve_hard_sl_regime(),
                verify_note=f"已按 TV tv_sl={ref:.2f} 同步盘口 | stop={hung}",
                verified=ok or bool(hung),
            )
        except Exception as e:
            logger.warning(f"UPDATE_SL 钉钉失败: {e}")

    def _tp_is_marketable(self, side, tp_px, curr_px, buffer_pct=0.0002):
        """
        穿价 TP：挂出即成交。LONG 卖限价 ≤ 市价；SHORT 买限价 ≥ 市价。
        """
        side = str(side or "").strip().upper()
        tp = float(tp_px or 0)
        px = float(curr_px or 0)
        if tp <= 0 or px <= 0 or side not in ("LONG", "SHORT"):
            return False
        buf = max(px * float(buffer_pct), 0.05)
        if side == "LONG":
            return tp <= px + buf
        return tp >= px - buf

    def _sanitize_open_tps_vs_mark(self, entry, curr_px=None):
        """
        开仓挂 TP 前：穿市价的档禁止挂出（否则开完秒平大半剩蚂蚁仓）。
        优先 entry+ATR 重算；仍穿价则把价格推离市价一侧，绝不挂出即成交的限价。
        """
        side = str(self.current_side or "").strip().upper()
        entry = float(entry or self.watched_entry or 0)
        curr_px = float(
            curr_px
            or binance_client.get_current_price(self.symbol)
            or self.tv_price
            or 0
        )
        atr = float(getattr(self, "open_atr", None) or self.current_atr or 30)
        regime = int(getattr(self, "open_regime", None) or self.regime or 3)
        if side not in ("LONG", "SHORT") or curr_px <= 0:
            return list(self.tv_tps or [])

        tps = list(self.tv_tps or [0.0, 0.0, 0.0])
        while len(tps) < 3:
            tps.append(0.0)

        def _any_marketable(prices):
            return any(
                self._tp_is_marketable(side, p, curr_px)
                for p in prices if float(p or 0) > 0
            )

        if _any_marketable(tps) and entry > 0:
            enriched = enrich_entry_tp_prices(side, entry, atr, regime, {})
            rebuilt = self._sanitize_tp_prices([
                self._safe_float(enriched.get("tv_tp1"), 0),
                self._safe_float(enriched.get("tv_tp2"), 0),
                self._safe_float(enriched.get("tv_tp3"), 0),
            ])
            if validate_tp_prices_for_side(side, entry, rebuilt):
                logger.warning(
                    f"⚠️ [{self.symbol}] 开仓 TP 穿市价 → ATR 重算 "
                    f"{[round(float(x or 0), 2) for x in tps]} → {rebuilt} | "
                    f"mark={curr_px:.2f}"
                )
                tps = list(rebuilt)

        # 仍穿价：强制推离市价（LONG 抬高 / SHORT 压低），保持单调
        min_gap = max(curr_px * 0.0015, atr * 0.15, 0.5)
        fixed = []
        for i, p in enumerate(tps[:3]):
            px = float(p or 0)
            if px <= 0:
                fixed.append(0.0)
                continue
            if self._tp_is_marketable(side, px, curr_px):
                if side == "LONG":
                    px = round(curr_px + min_gap * (i + 1), 2)
                else:
                    px = round(curr_px - min_gap * (i + 1), 2)
                logger.error(
                    f"🚨 [{self.symbol}] 穿价 TP{i + 1} 推离市价 → @{px:.2f} "
                    f"(mark={curr_px:.2f})，禁止挂出秒平"
                )
            fixed.append(px)
        # 单调修正
        if side == "LONG":
            for i in range(1, 3):
                if fixed[i] > 0 and fixed[i - 1] > 0 and fixed[i] <= fixed[i - 1]:
                    fixed[i] = round(fixed[i - 1] + min_gap, 2)
        else:
            for i in range(1, 3):
                if fixed[i] > 0 and fixed[i - 1] > 0 and fixed[i] >= fixed[i - 1]:
                    fixed[i] = round(fixed[i - 1] - min_gap, 2)
        self.tv_tps = self._sanitize_tp_prices(fixed)
        self._save_state()
        return list(self.tv_tps)

    def _place_tp_levels_only(self, live_qty, retries=2):
        """只挂未成交 TP 限价档，绝不触碰止损/雷达"""
        close_side = "SHORT" if self.current_side == "LONG" else "LONG"
        live_qty = self._resolve_live_qty(live_qty)
        if live_qty <= 0:
            return 0
        self._clear_spurious_tp_consumed_if_full_size(
            live_qty, source="place_tp_levels_only",
        )
        curr_px = float(binance_client.get_current_price(self.symbol) or 0)
        placed = 0
        for lv in self._expected_tp_levels(live_qty):
            q, px = float(lv["qty"] or 0), float(lv["price"] or 0)
            if q <= 0 or px <= 0:
                continue
            if self._may_mark_tp_filled_missing_limit(
                int(lv["level"]), live_qty, curr_px, tp_px=px,
            ):
                self._mark_tp_levels_consumed([int(lv["level"])])
                continue
            if self._tp_is_marketable(self.current_side, px, curr_px):
                self._force_tps_unmarketable(curr_px, self.watched_entry or 0)
                tps = list(self.tv_tps or [])
                idx = int(lv["level"]) - 1
                px = float(tps[idx]) if 0 <= idx < len(tps) else 0.0
                if px <= 0 or self._tp_is_marketable(self.current_side, px, curr_px):
                    logger.warning(
                        f"📈 穿价 TP{lv['level']} 再推 mark={curr_px:.2f}"
                    )
                    self._force_tps_unmarketable(curr_px, self.watched_entry or 0)
                    tps = list(self.tv_tps or [])
                    px = float(tps[idx]) if 0 <= idx < len(tps) else 0.0
                    if px <= 0 or self._tp_is_marketable(
                        self.current_side, px, curr_px
                    ):
                        logger.error(
                            f"❌ 跳过穿价 TP{lv['level']}：推离失败 mark={curr_px:.2f}"
                        )
                        continue
                logger.warning(
                    f"📈 穿价 TP{lv['level']} 已推离 → @{px:.2f} mark={curr_px:.2f}"
                )
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
        """
        雷达保本只能在浮盈侧：
        LONG: SL < entry 且 ≤ mark-gap；SHORT: SL > entry 禁止！必须 < entry 且 ≥ mark+gap。
        禁止把 SHORT 保本线抬过开仓价变成近市亏损止损（会秒平）。
        """
        if not sl or curr_px <= 0:
            return None
        entry = float(self.watched_entry or 0)
        gap = self._radar_min_stop_gap(curr_px)
        sl = round(float(sl), 2)
        if self.current_side == "LONG":
            if entry > 0 and sl <= entry + 0.01:
                return None  # 未越过成本，不是合法雷达
            safe_cap = round(curr_px - gap, 2)
            # 不得超过市价安全距，也不得压回成本以下
            floor = round(entry + 0.01, 2) if entry > 0 else 0.0
            if sl >= safe_cap:
                sl = safe_cap
            if floor > 0 and sl < floor:
                return None
            if sl >= safe_cap and safe_cap < floor:
                return None
            return round(float(sl), 2)
        if self.current_side == "SHORT":
            if entry > 0 and sl >= entry - 0.01:
                return None  # 禁止抬到成本及以上（近市亏损侧）
            safe_floor = round(curr_px + gap, 2)
            # 合法 SHORT 雷达：entry 下方、市价上方
            ceiling = round(entry - 0.01, 2) if entry > 0 else sl
            if sl <= safe_floor:
                sl = safe_floor
            if sl >= ceiling:
                return None  # 市价尚未离开成本足够远，无法安全挂保本
            if safe_floor >= ceiling:
                return None
            return round(float(sl), 2)
        return None

    def _can_safely_place_radar_sl(self, curr_px, sl):
        """False = 贴市 / 未在浮盈侧 → 交易所会立刻全平"""
        if curr_px <= 0 or not sl:
            return False
        if not self._is_valid_radar_sl(sl):
            return False
        gap = self._radar_min_stop_gap(curr_px)
        sl = float(sl)
        if self.current_side == "LONG":
            return sl <= curr_px - gap
        if self.current_side == "SHORT":
            return sl >= curr_px + gap
        return False

    def _radar_placement_blocked(self, live_qty=None, curr_px=0.0, reason="", silent=False):
        """
        开仓冷却内默认禁止近市雷达；但现价已达档位激活线 / TP1 已成交时
        解除冷却，允许防回吐交棒（硬止损仍用 closePosition 单槽，不抢 TP 额度）。
        """
        if getattr(self, "_open_in_progress", False):
            return True
        curr_px = float(curr_px or 0)
        if curr_px > 0 and self._radar_ready_to_handoff(curr_px, live_qty):
            return False
        if self._tp_level_consumed(1):
            return False
        if time.time() < float(getattr(self, "_post_open_radar_block_until", 0) or 0):
            if not silent:
                logger.warning(
                    f"📡 [{self.symbol}] 拒绝雷达挂单：开仓后冷却中"
                    + (f" | {reason}" if reason else "")
                )
            return True
        return False

    def _tp1_fill_allows_radar(self, live_qty=None, curr_px=0.0):
        """TP1 已实盘成交（账本消费 + 盘口无 TP1 限价）→ 允许强制交棒防回吐。"""
        if not self._tp_level_consumed(1):
            if not getattr(self, "_ws_tp1_fill_hint", False):
                return False
        tp1 = float(self.tv_tps[0] or 0) if self.tv_tps else 0.0
        if tp1 > 0 and self._has_tp_limit_at_price(tp1):
            return False
        # 有减仓证据或 WS 提示即可；不要求现价仍停在 TP1 区（成交后常回撤）
        live_qty = float(live_qty if live_qty is not None else self.watched_qty or 0)
        initial = self._trusted_initial_qty(live_qty)
        if initial > 0 and live_qty < initial - self._qty_noise_floor(initial):
            return True
        return bool(getattr(self, "_ws_tp1_fill_hint", False) or self._tp_level_consumed(1))

    def _radar_ready_to_handoff(self, curr_px, live_qty=None):
        """
        交棒门槛（按开仓档位）：
        ① 现价达 entry→TP1 的档位激活线（R1=50%/R2=60%/R3=70%/R4=80%）
        ② 或 TP1 已真实成交（价到+限价消失，防回吐）
        """
        curr_px = float(curr_px or 0)
        if curr_px > 0 and self._price_reached_radar_activation(curr_px, live_only=True):
            return True
        return self._tp1_fill_allows_radar(live_qty, curr_px)

    def _resolve_armed_radar_sl(self, live_qty, curr_px, dynamic_sl=None):
        """仅交棒成功后才允许雷达价；否则 None → 只挂 TV 硬止损"""
        if self._radar_placement_blocked(
            live_qty, curr_px, reason="resolve_radar", silent=True,
        ):
            return None
        if not self._radar_legitimately_armed(live_qty, curr_px):
            return None
        cand = dynamic_sl if dynamic_sl and float(dynamic_sl) > 0 else None
        if cand is None and self._is_radar_active():
            cand = self.current_sl
        if cand and self._is_valid_radar_sl(cand):
            return round(float(cand), 2)
        return None

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
        原子雷达交棒：现价达档位激活线或 TP1 已真实成交后，
        挂「理想保本线」并核实 → 再钉钉（只发一次）。
        硬止损与雷达共用 closePosition 单槽（不抢 TP reduceOnly）。
        禁止把止损夹到现价旁；空间不足则延迟，保留TV硬止损。
        """
        real_amt = float(self._resolve_live_qty(real_amt) or 0)
        if real_amt <= 0:
            return False
        if self._radar_placement_blocked(real_amt, curr_px, reason=reason or "handoff"):
            return False
        if getattr(self, "_open_in_progress", False) or getattr(
            self, "_defense_align_in_progress", False
        ):
            logger.info(
                f"📡 [{self.symbol}] 雷达交棒拒绝：开仓/防线重建中 | {reason or ''}"
            )
            return False
        # 主判：档位激活线 或 TP1 已真实成交
        if not self._radar_ready_to_handoff(curr_px, real_amt):
            ratio = self._radar_activation_ratio()
            prog = self._tp1_direction_progress(curr_px)
            logger.info(
                f"📡 [{self.symbol}] 雷达交棒拒绝：未达激活线且TP1未成交 "
                f"(朝TP1 {prog:.0%} < {ratio:.0%}·距TP1剩{(1-ratio)*100:.0f}%) | "
                f"{reason or ''}"
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
                f"（{self._unit()}）→ 保留TV硬止损呼吸空间 | {reason or ''}"
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

        # for_handoff=True：允许在置位 handoff_done 之前挂保本（修交棒死锁）
        sl_placed = self._ensure_radar_sl(safe_sl, real_amt, for_handoff=True)
        sl_verified = sl_placed and self._wait_verify(
            lambda: self._has_stop_sl_near(safe_sl, exclude_shield=False),
            retries=10,
            delay=0.45,
        )
        if not sl_verified:
            logger.warning(
                f"📡 [{self.symbol}] 雷达交棒中止：保本 @ {safe_sl:.2f} 未核实，"
                f"不撤TV硬止损"
            )
            if had_tv_shield and old_tv:
                self._maintain_hard_shield(real_amt, curr_px, force=True, radar_sl=None)
            return False

        # 仅核实成功后锁存
        self._radar_armed_after_tp1 = True
        self._radar_handoff_done = True
        self._radar_stage_last = max(int(getattr(self, "_radar_stage_last", 0) or 0), 1)
        self._post_open_radar_block_until = 0.0  # 交棒成功即解除开仓冷却
        gate = (
            "TP1成交强制交棒" if self._tp1_fill_allows_radar(real_amt, curr_px)
            else f"距TP1剩{(1 - self._radar_activation_ratio()) * 100:.0f}%"
        )
        self._radar_trigger_gate = gate
        # 交棒成功即排队钉钉；即使首次推送失败也由哨兵补发
        self._radar_notify_pending = not bool(
            getattr(self, "_radar_activation_notified", False)
        )
        self._save_state()

        logger.info(
            f"📡 [{self.symbol}] 雷达交棒成功：保本 @ {safe_sl:.2f} | "
            f"闸门={gate} | best={self.best_price:.2f} | "
            f"现价 {float(curr_px or 0):.2f} | {self._unit()} {real_amt}"
        )
        if had_tv_shield and not getattr(self, "_shield_handoff_notified", False):
            self._notify_shield_handoff_to_radar(
                real_amt, curr_px, safe_sl,
                reason=reason or f"{gate} · 雷达交棒",
                sl_verified=True,
                cancelled_hint=1 if old_tv else 0,
            )
        if not getattr(self, "_radar_activation_notified", False):
            self._report_radar_first_activation(
                real_amt, curr_px, safe_sl, sl_placed, trigger_gate=gate,
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
        """价触激活线并交棒成功 → 撤TV硬止损交棒雷达保本"""
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
        三轨并行（互不抢份额）：
        ① TP123 = reduceOnly 限价止盈（价到成交即记账，不重挂已成交档）
        ② 雷达移动保本 = 档位激活线启动+步进追随，closePosition 单槽
        ③ TV 硬止损 = tv_sl，与雷达合并为同一 closePosition 单槽
        """
        # 每轮先按「价到+限价消失」对账，微漂不干扰
        self._reconcile_tp_consumed_from_live_qty(
            real_amt, curr_px, source="哨兵三轨对账", notify=True,
        )
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
        # maintain 常不传 audit：宽限/冷却期内若盘口无保护 STOP → 视为 missing，禁止裸仓空转
        if (
            not missing_shield
            and self.current_side
            and float(self.watched_qty or 0) > 0
            and (
                now < float(getattr(self, "_sentinel_grace_until", 0) or 0)
                or getattr(self, "_shield_fail_streak", 0) > 0
            )
        ):
            try:
                if not binance_client.find_protective_stop_prices(self.symbol):
                    missing_shield = True
            except Exception:
                pass
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
        """同品种多 worker 仅允许一个进程重启接管；ETH/XAU 锁必须隔离。"""
        try:
            os.makedirs("logs", exist_ok=True)
            lock_path = f"logs/.recover_singleton_{self.symbol}.lock"
            # 清理旧共享锁误伤：若本品种锁不存在而旧全局锁存在，不阻塞本品种
            if os.path.exists(lock_path):
                age = time.time() - os.path.getmtime(lock_path)
                try:
                    with open(lock_path, encoding="utf-8") as f:
                        info = f.read().strip()
                except Exception:
                    info = "?"
                holder_alive = self._recover_lock_pid_alive(info)
                if age < RECOVER_LOCK_TTL_SEC and holder_alive:
                    logger.info(
                        f"🔄 [{self.symbol}] 跳过重复重启接管 "
                        f"(进程 {info} 仍存活, {age:.0f}s 前)"
                    )
                    return False
                if age < RECOVER_LOCK_TTL_SEC and not holder_alive:
                    logger.info(
                        f"🔄 [{self.symbol}] 旧接管锁已失效 (原 {info})，重新执行闪电接管"
                    )
            with open(lock_path, "w", encoding="utf-8") as f:
                f.write(
                    f"pid={os.getpid()} symbol={self.symbol} "
                    f"ts={datetime.now().isoformat()}"
                )
            return True
        except Exception as e:
            logger.warning(f"recover singleton lock [{self.symbol}]: {e}")
            return True

    def _ensure_sentinel_running_quiet(self):
        if not self._sentinel_active:
            threading.Thread(
                target=self._sentinel_loop, daemon=True,
                name=f"sentinel-{self.symbol}",
            ).start()

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
            defense_plan = "持有 TP123 + TV硬止损"
        elif favorable > 0.001:
            tp1_prog = self._tp1_direction_progress(curr_px)
            pnl_label = f"浮盈 {favorable:.1%}·朝TP1 {tp1_prog:.0%}(雷达待命)"
            defense_plan = "持有 TP123 + TV硬止损 (现价达激活线后才激活雷达)"
        else:
            pnl_label = "保本附近"
            defense_plan = "持有 TP123 + TV硬止损"

        stop_px = self._shield_stop_price(entry)
        hung_stops = binance_client.find_protective_stop_prices(self.symbol)
        hung_uniq = sorted({round(float(p), 2) for p in hung_stops if float(p) > 0})
        hung_px = hung_uniq[0] if len(hung_uniq) == 1 else None
        if should_radar or radar_active:
            radar_sl = (
                self._clamp_radar_to_tv_floor(self.current_sl)
                if self._is_radar_active() else None
            )
            merged = self._effective_exchange_stop(radar_sl)
            shield_status = (
                f"合并止损 @ {merged:.2f}" if merged
                else f"TV硬止损 @ {stop_px:.2f}" if stop_px else "雷达区·待合并"
            )
        elif shield_ok and hung_px and stop_px and abs(hung_px - stop_px) > SHIELD_STOP_TOLERANCE:
            # 钉钉禁止报「已齐」却仍挂 TV 紧价
            shield_status = (
                f"盘口@{hung_px:.2f}≠VPS@{stop_px:.2f}·纠偏中"
            )
            shield_ok = False
        elif shield_ok:
            shield_status = f"TV硬止损已挂 @ {stop_px:.2f}" if stop_px else "已核实"
        else:
            shield_status = (
                f"TV硬止损待补挂 @ {stop_px:.2f}" if stop_px
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
        重启一次性防线：只挂 TV 硬止损；雷达仅价触激活线交棒后合并。
        """
        actions = []
        radar_sl = None
        if health.get("should_radar") or health.get("radar_active"):
            if not self._is_radar_active():
                self._refresh_radar_state_on_recover(curr_px, self.watched_entry)
            if self._is_radar_active():
                radar_sl = self._clamp_radar_to_vps_floor(self.current_sl)

        self._sanitize_vps_hard_sl_ledger(source="重启防线消毒")
        ok = self._maintain_hard_shield(real_amt, curr_px, force=True, radar_sl=radar_sl)
        stop_px = self._effective_exchange_stop(radar_sl) or self._vps_hard_sl_target()
        vps_note = f"TV硬止损(tv_sl)"
        tag = (
            f"合并止损@{stop_px:.2f}"
            if radar_sl and stop_px and self._is_valid_radar_sl(radar_sl)
            else f"{vps_note}@{stop_px:.2f}" if stop_px else vps_note
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
                        trigger_gate=self._describe_radar_trigger_gate(real_amt, curr_px),
                    )
                elif getattr(self, "_radar_notify_pending", False) or (
                    getattr(self, "_radar_handoff_done", False)
                    and not getattr(self, "_radar_activation_notified", False)
                ):
                    self._flush_pending_radar_notify(real_amt, curr_px)
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
                        else f"雷达进度 {progress:.0%}，现价达激活线后推升止损"
                    )
                ),
            )

    def _place_shield_stops(self, live_qty, entry=None, reason="", force=False,
                            recover_mode=False, suppress_alert=False):
        """兼容旧入口：一律走 TV 硬止损同步。"""
        entry = float(entry or self.watched_entry or 0)
        if entry > 0:
            self.watched_entry = entry
        self._sanitize_vps_hard_sl_ledger(source=reason or "旧盾入口消毒")
        return self._sync_exchange_stop(
            live_qty, radar_sl=None,
            reason=reason or "TV硬止损(旧盾入口)",
            force=True,
        ).get("ok", False)

    def _adopt_exchange_hard_sl(self, source=""):
        """
        实盘已有唯一 STOP 时写回账本；仅当贴近 TV 硬止损（或合法雷达）。
        禁止再用 VPS% 覆盖 TV 价。
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
        if not self._is_exchange_stop_acceptable_as_vps_floor(chosen, entry, side):
            tv = self._tv_hard_sl_target(entry, side)
            logger.warning(
                f"🛡️ 拒采纳盘口异价止损 @{chosen:.2f} | "
                f"TV硬止损应为 @{tv:.2f}"
                + (f" | {source}" if source else "")
            )
            return 0.0
        old = round(float(getattr(self, "tv_sl", 0) or 0), 2)
        tv = self._tv_hard_sl_target(entry, side) or chosen
        # 盘口已贴近 TV → 账本写 TV；若无 TV 则写盘口价
        self.tv_sl = tv
        if float(getattr(self, "tv_sl_ref", 0) or 0) <= 0:
            self.tv_sl_ref = tv
        if not self.current_sl or float(self.current_sl) <= 0:
            self.current_sl = tv
        self.shield_active = True
        self._tv_sl_missing_alerted = False
        self._last_applied_exchange_sl = chosen
        self._save_state()
        logger.info(
            f"🛡️ 盘口硬止损可接受 @{chosen:.2f} → 账本 TV @{tv:.2f}"
            + (f" (原账本 {old:.2f})" if old and abs(old - tv) > 0.01 else "")
            + (f" | {source}" if source else "")
        )
        return tv

    def _ensure_hard_sl_ledger(self, live_qty=0, source=""):
        """账本硬止损必须是 TV tv_sl；缺失则自愈/盘口采纳。"""
        if self._sanitize_vps_hard_sl_ledger(source=source or "账本自愈"):
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
        """维护 TV 硬止损 closePosition；雷达激活时合并为雷达保本"""
        if real_amt <= 0 or not self.watched_entry:
            return False
        curr_px = float(curr_px or 0)
        self._sanitize_vps_hard_sl_ledger(source="维护硬止损消毒")
        if radar_sl is not None and (
            not self._is_valid_radar_sl(radar_sl)
            or self._radar_placement_blocked(real_amt, curr_px, reason="maintain_shield", silent=True)
            or not self._radar_legitimately_armed(real_amt, curr_px)
        ):
            radar_sl = None
        if radar_sl is None and self._radar_legitimately_armed(
            self.watched_qty, curr_px
        ) and not self._radar_placement_blocked(
            real_amt, curr_px, reason="maintain_shield", silent=True,
        ):
            cand = self._clamp_radar_to_vps_floor(self.current_sl)
            if cand and self._is_valid_radar_sl(cand):
                radar_sl = cand

        if float(getattr(self, "tv_sl", 0) or 0) <= 0 and not radar_sl:
            self._ensure_hard_sl_ledger(real_amt, source="维护硬止损自愈")

        if getattr(self, "tv_sl", 0) > 0 or radar_sl:
            if not force and not self._can_maintain_shield_now(force=force):
                return getattr(self, "shield_active", False)
            return self._sync_exchange_stop(
                real_amt,
                radar_sl=radar_sl,
                reason="维护TV硬止损/雷达合并",
                force=force,
            ).get("ok", False)

        # 最终核对：盘口已有 STOP → 对齐 TV；禁止「拒紧止损改挂VPS宽」旧逻辑
        live_stops = binance_client.find_protective_stop_prices(self.symbol)
        if live_stops:
            adopted = self._adopt_exchange_hard_sl(source="维护核对·盘口已有")
            if adopted > 0:
                logger.warning(
                    f"🛡️ 账本缺tv_sl但盘口STOP可接受{live_stops} → 已对齐TV"
                )
                self._tv_sl_missing_alerted = False
                return True
            self._ensure_hard_sl_ledger(real_amt, source="维护核对·补挂TV硬止损")
            return self._sync_exchange_stop(
                real_amt, radar_sl=None, reason="补挂TV硬止损原值", force=True,
            ).get("ok", False)

        if real_amt > 0 and not getattr(self, "_tv_sl_missing_alerted", False):
            logger.error(
                f"维护硬止损失败：持仓 {real_amt} ETH | entry={self.watched_entry} "
                f"| side={self.current_side} | regime={self.regime} | 盘口无STOP"
            )
            dingtalk.report_system_alert(
                "TV硬止损缺失",
                f"持仓 {real_amt} ETH · 账本与盘口均无硬止损 "
                f"(entry={self.watched_entry or '空'} side={self.current_side or '空'} "
                f"R{int(getattr(self, 'open_regime', None) or self.regime or 0)})",
                suggestion="哨兵将按 TV tv_sl 原值补挂；若盘口已有请忽略本条",
            )
            self._tv_sl_missing_alerted = True
        return False

    def _process_adverse_shield(self, real_amt, curr_px):
        """兼容旧调用 → 维护硬止损"""
        return self._maintain_hard_shield(real_amt, curr_px)

    def _is_radar_active(self):
        """
        雷达移动保本已武装：必须 stage≥1（价触激活线交棒后），且止损已越过成本。
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
        """头寸对应的 TP123 价位+数量均已正确挂好，且雷达/VPS 止损（若需要）也在"""
        tp_pxs = self.tv_tps
        expected = self._expected_tp_count(tp_pxs)
        if expected == 0:
            # 价位缺失 ≠ 防线齐；仍须 VPS 硬止损在盘
            stop_need = float(dynamic_sl or 0) or float(
                self._vps_hard_sl_target() or getattr(self, "tv_sl", 0) or 0
            )
            if stop_need <= 0:
                return False
            return self._has_stop_sl_near(
                stop_need, tolerance, exclude_shield=False,
            )

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

        if dynamic_sl and not self._has_stop_sl_near(
            dynamic_sl, tolerance, exclude_shield=False,
        ):
            return False
        return True

    def _patch_missing_tp_levels(self, live_qty, tolerance=1.0, qty_tol=0.005):
        """
        只补「真正漏挂」的剩余档。
        价到+限价消失 = 已成交 → 记账后绝不补挂，耐心等 TP23。
        TP=reduceOnly；雷达/TV硬止损=closePosition 单槽，互不抢份额。
        """
        live_qty = self._resolve_live_qty(live_qty)
        curr_px = float(binance_client.get_current_price(self.symbol) or 0)
        note = self._block_rehang_filled_tps_note(live_qty, curr_px)
        if note:
            logger.warning(f"🧩 [{self.symbol}] {note}")
        audit = self._audit_tp_levels(live_qty, tolerance, qty_tol)
        if self._defense_anomaly_is_severe(audit):
            logger.warning("补挂跳过：检测到重复/偏差/孤儿，改走核武对齐")
            return 0
        if audit.get("expected", 0) <= 0:
            logger.info(
                f"🧩 [{self.symbol}] 无剩余应挂 TP（已成交记账）→ 耐心等收网/雷达锁利"
            )
            return 0
        close_side = "SHORT" if self.current_side == "LONG" else "LONG"
        placed = 0

        for lv in self._expected_tp_levels(live_qty):
            q, px = lv["qty"], lv["price"]
            level = int(lv["level"])
            if q <= 0 or px <= 0:
                continue
            if self._tp_level_consumed(level):
                continue
            # 铁律：价到 + 限价没了 + 减仓证据 = 成交，禁止再挂
            if self._may_mark_tp_filled_missing_limit(level, live_qty, curr_px, tp_px=px):
                logger.warning(
                    f"🧩 拒绝补挂 TP{level} @{px:.2f}：价到+限价消失+减仓=已成交 "
                    f"→ 记账后耐心等剩余TP（不与雷达/TV硬止损抢份额）"
                )
                self._mark_tp_levels_consumed([level])
                continue
            # 持仓期铁律：现价已达该档 → 永远不补挂（防 TP1 死循环吃光仓）
            # 仅开仓瞬间允许推离后挂；开仓结束后一律记账跳过
            if (
                not getattr(self, "_open_in_progress", False)
                and self._price_reached_tp_zone(level, curr_px, px, live_only=True)
            ):
                logger.warning(
                    f"🧩 接管跳过补挂/拒绝补挂 TP{level} @{px:.2f}：现价已达 "
                    f"(mark={curr_px:.2f}) → 记账跳过，只挂更远档，禁 TP1 反复成交"
                )
                self._mark_tp_levels_consumed([level])
                continue
            # 开仓路径：现价已达但无减仓证据 → 推离后仍要挂
            if self._price_reached_tp_zone(level, curr_px, px):
                if self._has_tp_limit_at_price(px):
                    continue
                if not getattr(self, "_open_in_progress", False):
                    # 双保险：非开仓不得推离补挂
                    self._mark_tp_levels_consumed([level])
                    continue
                logger.warning(
                    f"🧩 TP{level} @{px:.2f} 现价已近但无减仓证据 "
                    f"(mark={curr_px:.2f}) → 开仓推离后补挂，禁止假吃"
                )
                self._force_tps_unmarketable(curr_px, self.watched_entry or 0)
                tps = list(self.tv_tps or [])
                px = float(tps[level - 1]) if level - 1 < len(tps) else 0.0
                if px <= 0:
                    continue
            # 穿价：推离后再挂，禁止挂出即成交把仓位在 TP1 削光
            if self._tp_is_marketable(self.current_side, px, curr_px):
                self._force_tps_unmarketable(curr_px, self.watched_entry or 0)
                tps = list(self.tv_tps or [])
                px = float(tps[level - 1]) if level - 1 < len(tps) else 0.0
                q = float(lv["qty"] or 0)
                if px <= 0 or self._tp_is_marketable(self.current_side, px, curr_px):
                    logger.error(
                        f"🚨 补挂仍穿价 TP{level} mark={curr_px:.2f} → 再推一轮"
                    )
                    self._force_tps_unmarketable(curr_px, self.watched_entry or 0)
                    tps = list(self.tv_tps or [])
                    px = float(tps[level - 1]) if level - 1 < len(tps) else 0.0
                    if px <= 0 or self._tp_is_marketable(self.current_side, px, curr_px):
                        logger.error(
                            f"🚨 补挂放弃 TP{level}：多次推离仍穿（请查价源）"
                        )
                        continue
                logger.warning(
                    f"⚠️ 补挂 TP{level} 穿价已推离 → @{px:.2f} qty={q}"
                )
            # 限价消失但价未到：不盲目补挂到未触及价（防异常撤单后贴价成交）
            if not self._has_tp_limit_at_price(px) and not self._price_reached_tp_zone(
                level, curr_px, px
            ):
                # 真正漏挂（开仓后从未挂上/被误撤）才允许补
                logger.info(
                    f"  + 补挂漏档 TP{level} @ {px:.2f} qty={q} "
                    f"(现价未达·限价缺失=真漏挂)"
                )
            orders = self._collect_tp_limit_orders()
            at_px = [o for o in orders if abs(o["price"] - px) <= tolerance]
            if len(at_px) == 1 and abs(at_px[0]["qty"] - q) <= qty_tol:
                logger.info(f"  ✓ TP{level} @ {px:.2f} 已存在 {at_px[0]['qty']} ETH，跳过")
                continue
            for o in at_px:
                if o.get("orderId"):
                    binance_client.cancel_order(self.symbol, order=o)
                    time.sleep(0.25)
            logger.info(f"  + 补挂 TP{level} @ {px:.2f} qty={q} ETH")
            if binance_client.place_limit_order(close_side, q, px, symbol=self.symbol, reduce_only=True):
                placed += 1
            else:
                logger.error(f"  ❌ 补挂 TP{level} @ {px:.2f} 失败")
            time.sleep(0.4)
        return placed

    def _audit_requires_nuclear(self, audit):
        """
        重复/数量偏差/孤儿/总单数超标 → 必须核武清场。
        纯 missing（无盘口叠单）→ 不强制核武，优先补挂，避免秒挂秒撤。
        """
        expected = audit.get("expected", 0)
        if expected <= 0:
            return False
        if audit.get("matched_full", 0) >= expected and not audit.get("orphans"):
            return False
        orders = self._collect_tp_limit_orders()
        if len(orders) > expected:
            return True
        bad = [
            lv for lv in audit.get("levels", [])
            if lv.get("status") in ("duplicate", "qty_mismatch")
        ]
        if bad:
            return True
        if audit.get("orphans"):
            return True
        # 全缺且盘口已有奇怪限价（无法识别为期望档）→ 核武
        if audit.get("matched_full", 0) == 0 and orders and audit.get("issues"):
            return True
        return False

    def _defense_severity_is_severe(self, audit):
        """仅叠单/偏差/孤儿算严重（绕过冷却）；纯缺失走补挂+冷却。"""
        if not audit:
            return False
        if audit.get("orphans"):
            return True
        for lv in audit.get("levels", []) or []:
            if lv.get("status") in ("duplicate", "qty_mismatch"):
                return True
        orders = self._collect_tp_limit_orders()
        expected = int(audit.get("expected", 0) or 0)
        if expected > 0 and len(orders) > expected:
            return True
        return False

    def _nuclear_backoff_remaining(self):
        """核武 thrash 刹车剩余秒数；0=可执行。"""
        now = time.time()
        last = float(getattr(self, "_last_nuclear_realign_ts", 0) or 0)
        streak = int(getattr(self, "_nuclear_fail_streak", 0) or 0)
        wait = NUCLEAR_REALIGN_MIN_INTERVAL_SEC * (1 + min(3, max(0, streak)))
        wait = min(float(NUCLEAR_FAIL_BACKOFF_MAX_SEC), float(wait))
        left = wait - (now - last)
        return max(0.0, left)

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
            # 自撤限价窗口：禁止立刻把「价到+限价消失」当成 TP 成交
            self._tp_purge_ts = time.time()
            logger.info(f"🧹 已撤销限价止盈合计 {total} 张")
        return total

    def _scorched_earth_cancel_for_recover(self):
        """
        重启接管：只撤 TP 限价（含重复档），保留 closePosition 硬止损。
        禁止 cancel_all 撤净 STOP 后裸仓；随后核武只重挂 TP。
        """
        for attempt in range(6):
            self._cancel_all_tp_limit_orders(max_rounds=4)
            time.sleep(0.6)
            remaining = self._collect_tp_limit_orders()
            if not remaining:
                logger.info(
                    f"☢️ 重启撤TP完成，限价止盈已清零 (第 {attempt + 1} 轮) | "
                    f"硬止损保留不撤"
                )
                return True
            remain_txt = ", ".join(f"{o['qty']}@{o['price']}" for o in remaining[:4])
            logger.warning(
                f"⚠️ 撤TP后仍剩 {len(remaining)} 张 ({remain_txt}) "
                f"→ 重试 {attempt + 1}/6"
            )
        logger.error("❌ 重启撤TP未净：请币安 APP 手动撤限价止盈后重启（勿平仓）")
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

    def _ensure_radar_sl(self, dynamic_sl, live_qty=None, for_handoff=False):
        """
        挂雷达保本 STOP（走 closePosition 单槽合并，不占 TP reduceOnly）。
        for_handoff=True：交棒首挂，允许在 _radar_handoff_done 置位前下单（修死锁）。
        """
        if not dynamic_sl:
            return False
        live_qty = float(live_qty or self.watched_qty or 0)
        curr_px = float(binance_client.get_current_price(self.symbol) or 0)
        if self._radar_placement_blocked(live_qty, curr_px, reason="ensure_radar_sl"):
            return False
        if for_handoff:
            if not self._radar_ready_to_handoff(curr_px, live_qty):
                logger.warning(
                    f"📡 [{self.symbol}] 拒绝交棒挂单：未达档位激活线且TP1未成交"
                )
                return False
        elif not self._radar_legitimately_armed(live_qty, curr_px):
            logger.warning(
                f"📡 [{self.symbol}] 拒绝雷达挂单：未交棒/未达激活线 | ensure_radar_sl"
            )
            return False
        if not self._is_valid_radar_sl(dynamic_sl):
            logger.warning(
                f"📡 [{self.symbol}] 拒绝雷达止损 @{float(dynamic_sl):.2f}：不在浮盈侧"
            )
            return False
        clamped = self._clamp_radar_sl_for_market(curr_px, dynamic_sl)
        if not clamped or not self._can_safely_place_radar_sl(curr_px, clamped):
            logger.warning(
                f"📡 [{self.symbol}] 拒绝雷达止损：市价不安全 "
                f"ideal={float(dynamic_sl):.2f} mark={curr_px:.2f}"
            )
            return False
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
            live_qty,
            radar_sl=clamped,
            reason=f"雷达保本 @ {clamped:.2f}",
            force=True if for_handoff else False,
        )
        return result.get("ok", False)

    def _realign_radar_defenses(self, live_qty, entry, new_sl):
        """
        雷达推升：同价已在则跳过；禁止先 scope=radar 撤再挂（易裸仓/死循环）。
        一律走 closePosition 单槽合并总线（雷达∪TV硬止损），不抢 TP reduceOnly。
        """
        new_sl = round(float(new_sl or 0), 2)
        if new_sl <= 0:
            return False
        if self._has_stop_sl_near(new_sl, exclude_shield=False):
            self._last_applied_exchange_sl = new_sl
            logger.info(f"📡 雷达止损已在 @{new_sl:.2f}，跳过撤挂")
            return True
        last = round(float(getattr(self, "_last_applied_exchange_sl", 0) or 0), 2)
        if last > 0 and abs(last - new_sl) <= SHIELD_STOP_TOLERANCE:
            if self._has_stop_sl_near(last, exclude_shield=False):
                return True
        # 仅当 TP 严重异常才纠偏；正常追随只同步单槽 STOP
        audit = self._audit_tp_levels(live_qty)
        if self._defense_needs_immediate_fix(audit):
            self._enforce_defense_alignment(
                live_qty, entry, dynamic_sl=new_sl,
                reason="雷达推升前 TP 纠偏", rounds=2,
            )
        result = self._sync_exchange_stop(
            live_qty, radar_sl=new_sl, reason=f"雷达追随 @{new_sl:.2f}", force=False,
        )
        if result.get("ok") or result.get("skipped"):
            return True
        # 冷却挡住但盘口仍无目标价 → 强制一次（仍走合并总线，不裸挂）
        if not self._has_stop_sl_near(new_sl, exclude_shield=False):
            return self._sync_exchange_stop(
                live_qty, radar_sl=new_sl, reason="雷达追随兜底", force=True,
            ).get("ok", False)
        return True

    def _report_radar_first_activation(self, real_amt, curr_px, new_sl, sl_placed,
                                       trigger_gate=""):
        """
        雷达首次激活钉钉：交棒核实后必须播报。
        失败则 _radar_notify_pending=True，哨兵补发（修「启了雷达无钉钉」）。
        """
        if getattr(self, "_radar_activation_notified", False):
            self._radar_notify_pending = False
            return True

        gate = trigger_gate or self._describe_radar_trigger_gate(real_amt, curr_px)
        self._radar_trigger_gate = gate

        # 交棒已成功：禁止因次要校验跳过钉钉；仅记录警告
        if not self._radar_legitimately_armed(real_amt, curr_px):
            if not getattr(self, "_radar_handoff_done", False):
                logger.warning(
                    f"📡 雷达激活钉钉暂缓：尚未交棒 "
                    f"(entry={self.watched_entry:.2f} sl={new_sl:.2f})"
                )
                self._radar_notify_pending = True
                self._save_state()
                return False
            logger.warning(
                f"📡 雷达激活钉钉：handoff_done 但 armed 标志异常，仍强制播报"
            )

        if self.current_side == "LONG" and float(new_sl or 0) <= float(self.watched_entry or 0):
            logger.warning(
                f"📡 雷达激活钉钉警告：LONG 止损 {new_sl:.2f} 未高于 entry，仍播报"
            )
        if self.current_side == "SHORT" and float(new_sl or 0) >= float(self.watched_entry or 0):
            logger.warning(
                f"📡 雷达激活钉钉警告：SHORT 止损 {new_sl:.2f} 未低于 entry，仍播报"
            )

        verified = bool(sl_placed)
        try:
            verified = verified or self._wait_verify(
                lambda: self._has_stop_sl_near(new_sl, exclude_shield=False),
                retries=6,
                delay=0.35,
            )
        except Exception as e:
            logger.warning(f"📡 雷达激活止损复核异常: {e}")

        progress = self._radar_activation_progress(curr_px) if curr_px > 0 else 1.0
        stage = self._radar_stage(curr_px) if curr_px > 0 else 0
        tv_floor = round(float(getattr(self, "tv_sl", 0) or 0), 2)
        act_px = round(float(self._radar_activation_price() or 0), 2)
        verify_note = (
            f"闸门={gate} | 雷达阶段{stage} {self._radar_stage_label(stage)} | "
            f"进度 {progress:.0%} | 合并止损 @ {new_sl:.2f} | "
            f"激活线@{act_px or '—'} | TV硬止损底线={tv_floor or 'fallback'} | "
            f"持仓 {real_amt} {self._unit()} @ {self.watched_entry:.2f}"
        )
        if not verified:
            verify_note += f" | {dingtalk.VERIFY_DELAY_MARK}"

        try:
            self._call_dingtalk(
                dingtalk.report_radar_activated,
                side=self.current_side,
                qty=real_amt,
                entry=self.watched_entry,
                new_sl=new_sl,
                radar_progress=progress,
                regime=int(getattr(self, "open_regime", None) or self.regime or 3),
                shield_cleared=True,
                verify_note=verify_note,
                verified=verified,
                trigger_gate=gate,
                activation_price=act_px,
            )
            self._radar_activation_notified = True
            self._radar_notify_pending = False
            self._save_state()
            logger.info(
                f"📡 [{self.symbol}] 雷达激活钉钉已发 | 闸门={gate} | SL={new_sl:.2f}"
            )
            return True
        except Exception as e:
            logger.error(f"📡 雷达激活钉钉失败，排队补发: {e}", exc_info=True)
            self._radar_notify_pending = True
            self._save_state()
            return False

    def _flush_pending_radar_notify(self, real_amt, curr_px):
        """哨兵补发：交棒成功但首次钉钉未发出。"""
        if getattr(self, "_radar_activation_notified", False):
            self._radar_notify_pending = False
            return False
        if not (
            getattr(self, "_radar_notify_pending", False)
            or getattr(self, "_radar_handoff_done", False)
        ):
            return False
        real_amt = float(self._resolve_live_qty(real_amt) or 0)
        if real_amt <= 0:
            return False
        sl = float(
            getattr(self, "current_sl", 0)
            or self._radar_sl_to_pass()
            or 0
        )
        if sl <= 0:
            return False
        logger.warning(
            f"📡 [{self.symbol}] 补发雷达激活钉钉 | SL={sl:.2f} | "
            f"闸门={self._describe_radar_trigger_gate(real_amt, curr_px)}"
        )
        return self._report_radar_first_activation(
            real_amt, curr_px, sl,
            sl_placed=self._has_stop_sl_near(sl, exclude_shield=False),
            trigger_gate=self._describe_radar_trigger_gate(real_amt, curr_px),
        )

    def _nuclear_realign_tp(self, live_qty, entry, dynamic_sl=None, rounds=3):
        """
        核武级止盈对齐：只撤限价 TP → 重挂 TP123 → 始终续挂 tv_sl/雷达合并止损。
        带 thrash 刹车：短时间内反复失败则跳过，避免秒挂秒撤。
        例外：盘口 0 档 TP 且仍有仓 → 忽略刹车（禁裸奔）。
        """
        live_qty = self._resolve_live_qty(live_qty)
        self._clear_spurious_tp_consumed_if_full_size(
            live_qty, source="核武前清假成交",
        )
        last_audit = self._audit_tp_levels(live_qty)
        naked_tp = (
            live_qty > 0
            and int(last_audit.get("expected") or 0) > 0
            and int(last_audit.get("matched_full") or 0) <= 0
        )
        backoff = self._nuclear_backoff_remaining()
        if backoff > 0 and not naked_tp:
            logger.warning(
                f"☢️ [{self.symbol}] 核武刹车 {backoff:.0f}s "
                f"(fail_streak={getattr(self, '_nuclear_fail_streak', 0)}) | "
                f"跳过撤挂 | {self._format_audit_summary(last_audit)}"
            )
            return last_audit
        if naked_tp and backoff > 0:
            logger.error(
                f"☢️ [{self.symbol}] TP 全缺有仓 → 无视核武刹车 "
                f"(原剩 {backoff:.0f}s) | {self._format_audit_summary(last_audit)}"
            )
            self._nuclear_fail_streak = 0

        self._last_nuclear_realign_ts = time.time()
        for r in range(rounds):
            logger.warning(
                f"☢️ 核武级止盈清场重挂 {r + 1}/{rounds} | 持仓 {live_qty} ETH | "
                f"当前 {last_audit['matched_full']}/{last_audit['expected']} | "
                f"{self._format_audit_summary(last_audit)}"
            )
            self._cancel_all_tp_limit_orders()
            time.sleep(1.0)
            self._force_tps_unmarketable(
                binance_client.get_current_price(self.symbol), entry,
            )
            placed = self._rebuild_defenses(
                live_qty, entry, dynamic_sl=None, cancel_first=False,
            )
            logger.info(f"☢️ 核武轮 {r + 1} 新挂 {placed} 笔限价止盈")
            # 止损已齐则勿 force 撤挂
            self._maintain_hard_shield(
                live_qty, None, force=False, radar_sl=dynamic_sl,
            )
            time.sleep(1.2)
            last_audit = self._audit_tp_levels(live_qty)
            stop_px = self._resolve_defense_stop_for_audit(dynamic_sl)
            # TP 已齐即可收工；止损另用 exclude_shield=False 核对（勿把 VPS 当缺失）
            if self._tp_audit_ok(last_audit) and (
                not stop_px
                or self._has_stop_sl_near(stop_px, exclude_shield=False)
                or self._defenses_fully_ok(live_qty, stop_px)
            ):
                logger.info(f"☢️ 核武重挂成功: {self._format_audit_summary(last_audit)}")
                self._nuclear_fail_streak = 0
                self._mark_defense_align_ok()
                return last_audit
            logger.warning(
                f"☢️ 核武轮 {r + 1} 仍未对齐: {self._format_audit_summary(last_audit)}"
            )
            time.sleep(1.5)
        self._nuclear_fail_streak = int(getattr(self, "_nuclear_fail_streak", 0) or 0) + 1
        return last_audit

    def _tp_audit_ok(self, audit):
        expected = audit.get("expected", 0)
        if expected <= 0:
            # 有仓且 TP 未吃完时 expected=0 = 价位缺失，禁止假「已齐」跳过挂单
            if self.current_side and float(self.watched_entry or 0) > 0:
                consumed = set(getattr(self, "tp_levels_consumed", []) or [])
                if not all(lv in consumed for lv in (1, 2, 3)):
                    return False
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
        """
        是否需要立刻动盘口：
        - 严重（叠单/偏差/孤儿）→ True
        - 纯缺失 → True（但 guardian 用 severe 区分是否绕过冷却）
        """
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
        entry = float(entry or self.watched_entry or 0)
        # 对齐前强制补全 TP 价：禁止 expected=0 假齐跳过挂单
        if entry > 0 and self.current_side and self._expected_tp_count() <= 0:
            consumed = set(getattr(self, "tp_levels_consumed", []) or [])
            if not all(lv in consumed for lv in (1, 2, 3)):
                self._ensure_tp123_prices_from_tv(entry)
        if reason:
            logger.info(f"🛡️ 防线对齐: {reason} | 持仓 {live_qty} ETH")

        self._defense_align_in_progress = True
        try:
            curr_px = float(binance_client.get_current_price(self.symbol) or 0)
            # 对齐前：先按头寸减仓记账已成交档，禁止核武/补挂把 TP1 再挂回现价旁
            self._reconcile_tp_consumed_from_live_qty(
                live_qty, curr_px, source=f"防线对齐·{reason or ''}", notify=True,
            )
            # 开仓/TP1前：dynamic_sl 一律丢弃，只挂 TV 硬止损
            radar_sl = self._resolve_armed_radar_sl(live_qty, curr_px, dynamic_sl)
            dynamic_sl = radar_sl
            audit = self._audit_tp_levels(live_qty)

            if recover_mode and self._tp_audit_ok(audit):
                logger.info(
                    f"✅ 重启接管：盘口 TP 已齐，跳过核武撤挂 | "
                    f"{self._format_audit_summary(audit)}"
                )
                if radar_sl and not self._has_stop_sl_near(radar_sl):
                    self._ensure_radar_sl(radar_sl, live_qty)
                else:
                    self._maintain_hard_shield(live_qty, curr_px, force=True, radar_sl=None)
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
                    if radar_sl and not self._has_stop_sl_near(radar_sl):
                        self._ensure_radar_sl(radar_sl, live_qty)
                    else:
                        self._maintain_hard_shield(
                            live_qty, curr_px, force=True, radar_sl=None,
                        )
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
                if radar_sl and not self._has_stop_sl_near(radar_sl):
                    self._ensure_radar_sl(radar_sl, live_qty)
                else:
                    self._maintain_hard_shield(live_qty, curr_px, force=False, radar_sl=None)
                self._mark_defense_align_ok()
                return {
                    "matched": audit["matched_full"],
                    "expected": audit["expected"],
                    "pending_prices": audit["pending_prices"],
                    "rebuilt": False,
                    "audit": audit,
                    "nuclear": False,
                }

            # 非严重（纯缺失）：优先增量补挂，禁止先撤再挂
            if not recover_mode and not self._defense_anomaly_is_severe(audit):
                placed = self._patch_missing_tp_levels(live_qty)
                time.sleep(0.6)
                audit = self._audit_tp_levels(live_qty)
                if self._tp_audit_ok(audit):
                    logger.info(
                        f"✅ 增量补挂成功 {placed} 档: {self._format_audit_summary(audit)}"
                    )
                    self._maintain_hard_shield(
                        live_qty, curr_px, force=False, radar_sl=radar_sl,
                    )
                    self._mark_defense_align_ok()
                    return {
                        "matched": audit["matched_full"],
                        "expected": audit["expected"],
                        "pending_prices": audit["pending_prices"],
                        "rebuilt": placed > 0,
                        "audit": audit,
                        "nuclear": False,
                    }
                if self._nuclear_backoff_remaining() > 0:
                    logger.warning(
                        f"⚠️ 补挂后仍不齐且核武刹车中 → 保留现有挂单 | "
                        f"{self._format_audit_summary(audit)}"
                    )
                    self._maintain_hard_shield(
                        live_qty, curr_px, force=False, radar_sl=radar_sl,
                    )
                    return {
                        "matched": audit["matched_full"],
                        "expected": audit["expected"],
                        "pending_prices": audit["pending_prices"],
                        "rebuilt": placed > 0,
                        "audit": audit,
                        "nuclear": False,
                    }

            if recover_mode:
                self._scorched_earth_cancel_for_recover()
            elif self._defense_anomaly_is_severe(audit):
                self._cancel_all_tp_limit_orders()
            time.sleep(0.45)
            audit = self._audit_tp_levels(live_qty)
            if self._tp_audit_ok(audit):
                logger.info(f"✅ 撤单后 TP 已齐: {self._format_audit_summary(audit)}")
                if radar_sl and not self._has_stop_sl_near(radar_sl):
                    self._ensure_radar_sl(radar_sl, live_qty)
                else:
                    self._maintain_hard_shield(live_qty, curr_px, force=False, radar_sl=None)
                self._mark_defense_align_ok()
                return {
                    "matched": audit["matched_full"],
                    "expected": audit["expected"],
                    "pending_prices": audit["pending_prices"],
                    "rebuilt": False,
                    "audit": audit,
                    "nuclear": False,
                }

            audit = self._nuclear_realign_tp(
                live_qty, entry, dynamic_sl=radar_sl, rounds=rounds,
            )
            self._maintain_hard_shield(
                live_qty, curr_px, force=False, radar_sl=radar_sl,
            )
            if (
                audit["matched_full"] < audit["expected"]
                and self._defense_anomaly_is_severe(audit)
                and self._nuclear_backoff_remaining() <= 0
            ):
                logger.warning("☢️ 首轮核武未齐，追加一轮重挂")
                if recover_mode:
                    self._scorched_earth_cancel_for_recover()
                else:
                    self._cancel_all_tp_limit_orders(max_rounds=4)
                time.sleep(0.6)
                audit = self._nuclear_realign_tp(
                    live_qty, entry, dynamic_sl=radar_sl,
                    rounds=max(2, rounds - 1),
                )
                self._maintain_hard_shield(
                    live_qty, curr_px, force=False, radar_sl=radar_sl,
                )
            stop_px = self._resolve_defense_stop_for_audit(radar_sl)
            if stop_px and not self._has_stop_sl_near(stop_px):
                self._maintain_hard_shield(
                    live_qty, curr_px, force=True, radar_sl=radar_sl,
                )
            elif radar_sl and not self._has_stop_sl_near(radar_sl):
                self._ensure_radar_sl(radar_sl, live_qty)
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
        纯缺失不绕过冷却；叠单才算 severe。核武有 thrash 刹车。
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

        # 补挂前先按实盘减仓记账，禁止把已成交 TP 当漏挂
        self._reconcile_tp_consumed_from_live_qty(
            real_amt, curr_px, source="雷达守护对账", notify=True,
        )
        audit = self._audit_tp_levels(real_amt)
        sl = self._radar_sl_to_pass()

        if self._tp_audit_ok(audit):
            self._guardian_bad_streak = 0
            if sl and not self._has_stop_sl_near(sl, exclude_shield=False):
                self._ensure_radar_sl(sl, real_amt)
            return None

        self._guardian_bad_streak += 1
        now = time.time()
        severe = self._defense_severity_is_severe(audit)
        in_grace = now < getattr(self, "_sentinel_grace_until", 0)
        in_cooldown = (
            now - getattr(self, "_last_defense_align_ok_ts", 0)
            < DEFENSE_ALIGN_COOLDOWN_SEC
        )
        # 纯缺失：宽限期/冷却期内最多轻量补挂，禁止核武连环
        # 例外：有仓却 0 档 TP → 必须升级强制对齐（禁裸奔）
        naked_tp = (
            int(audit.get("matched_full") or 0) <= 0
            and int(audit.get("expected") or 0) > 0
        )
        if naked_tp:
            self._clear_spurious_tp_consumed_if_full_size(
                real_amt, source="雷达守护·TP全缺",
            )
            logger.error(
                f"📡 [雷达守护] 有仓 TP 全缺 → 无视宽限/冷却，强制对齐 | "
                f"{self._format_audit_summary(audit)}"
            )
            self._nuclear_fail_streak = 0
            result = self._enforce_defense_alignment(
                real_amt, self.watched_entry,
                dynamic_sl=(sl if self._is_radar_active() else None),
                reason="雷达守护·裸仓强制补TP", rounds=3,
            )
            if not binance_client.find_protective_stop_prices(self.symbol):
                self._sync_exchange_stop(
                    real_amt, radar_sl=None,
                    reason="雷达守护·裸仓强制TV硬止损", force=True,
                )
            return result
        if (in_grace or in_cooldown) and not severe:
            if self._guardian_bad_streak <= 3:
                placed = self._patch_missing_tp_levels(real_amt)
                if placed:
                    logger.info(
                        f"📡 [雷达守护] 冷却/宽限期内轻量补挂 {placed} 档 | "
                        f"{self._format_audit_summary(self._audit_tp_levels(real_amt))}"
                    )
                else:
                    logger.info(
                        f"📡 [雷达守护] TP 审计波动，暂不核武 "
                        f"({'重启宽限期' if in_grace else '冷却期'}) | "
                        f"{self._format_audit_summary(audit)}"
                    )
                return None

        if not severe and self._nuclear_backoff_remaining() > 0:
            placed = self._patch_missing_tp_levels(real_amt)
            logger.warning(
                f"📡 [雷达守护] 核武刹车中 → 仅补挂 {placed} 档 | "
                f"{self._format_audit_summary(audit)}"
            )
            return None

        logger.warning(
            f"📡 [雷达守护] TP 未对齐 → "
            f"{'核武纠偏' if severe else '补挂/对齐'} | "
            f"{self._format_audit_summary(audit)}"
        )
        sl_preserve = sl if self._is_radar_active() else None
        result = self._enforce_defense_alignment(
            real_amt, self.watched_entry, dynamic_sl=sl_preserve,
            reason="雷达守护实时纠偏", rounds=2 if not severe else 3,
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
                self._ensure_radar_sl(dynamic_sl, live_qty)
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
                self._ensure_radar_sl(dynamic_sl, live_qty)
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

    def _has_stop_sl_near(self, sl_price, tolerance=2.0, exclude_shield=False):
        """
        盘口是否已有贴近目标价的 STOP。
        默认 exclude_shield=False：统一 closePosition 硬止损/雷达同槽，
        若排除 shield 会把唯一 VPS 止损当成「缺失」→ 核武永远失败秒挂秒撤。
        """
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

    def _price_reached_tp_zone(self, level, curr_px=0.0, tp_px=None, live_only=False):
        """现价（或 best）是否已触及/越过指定 TP 档。live_only=True 仅用实时价。"""
        level = int(level or 0)
        if level == 1:
            return self._price_reached_tp1_zone(curr_px, tp_px, live_only=live_only)
        idx = level - 1
        tp_px = float(
            tp_px
            if tp_px is not None
            else ((self.tv_tps[idx] if self.tv_tps and 0 <= idx < len(self.tv_tps) else 0) or 0)
        )
        entry = float(self.watched_entry or 0)
        if tp_px <= 0 or entry <= 0:
            return False
        px_tol = max(
            float(getattr(self, "qty_step", 0.001) or 0.001),
            tp_px * TP1_PRICE_ZONE_PCT,
        )
        prices = [float(curr_px or 0)]
        if not live_only:
            prices.append(float(self.best_price or 0))
        for px in prices:
            if px <= 0:
                continue
            if self.current_side == "LONG" and px >= tp_px - px_tol:
                return True
            if self.current_side == "SHORT" and px <= tp_px + px_tol:
                return True
        return False

    def _detect_tp_fills_from_trades(self, old_qty, new_qty, initial=None, lookback_ms=180000):
        """用成交历史核对：可识别连续多档 TP 同时成交（TP1+TP2）。"""
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
        budget = reduced
        step = float(getattr(self, "qty_step", 0.001) or 0.001)
        for sl in sorted(self._tp_slices_for_initial(initial), key=lambda x: x["level"]):
            if sl["level"] in consumed or sl["price"] <= 0 or sl["qty"] <= 0.0005:
                continue
            if budget < step:
                break
            px_tol = max(1.5, float(sl["price"]) * 0.0012)
            matched = sum(
                r["qty"] for r in recent
                if abs(r["price"] - sl["price"]) <= px_tol
            )
            qty_tol = max(step * 2, sl["qty"] * TP_SLICE_MATCH_TOL_PCT)
            # 单档量匹配，或本档限价附近成交量覆盖本档切片
            if matched + step < float(sl["qty"]) - qty_tol and abs(budget - sl["qty"]) > qty_tol:
                # 多档连吃：若累计预算仍够且价到该档，允许按切片记账
                if not self._price_reached_tp_zone(sl["level"], 0.0, sl["price"]):
                    break
                if budget + step < float(sl["qty"]) - qty_tol:
                    break
            fills.append({
                "level": sl["level"],
                "price": sl["price"],
                "qty": round(min(sl["qty"], budget), 3),
                "source": "trades",
            })
            budget = round(budget - float(sl["qty"]), 6)
            if budget < step * 2:
                break
        if fills:
            logger.info(
                f"🎯 成交历史核实 TP{[f['level'] for f in fills]} "
                f"(减仓 {reduced} {self._unit()})"
            )
        return fills

    def _detect_tp_fills_by_order_disappear(self, old_qty, new_qty, initial=None):
        """盘口限价 TP 消失 + 价到 + 减仓量匹配 → 可连续识别多档。"""
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
        noise = max(
            float(getattr(self, "qty_step", 0.001) or 0.001) * 2,
            initial * TP_FILL_NOISE_VS_OPEN_PCT,
        )
        if reduced < noise:
            return []
        consumed = set(getattr(self, "tp_levels_consumed", []) or [])
        curr_px = float(binance_client.get_current_price(self.symbol) or 0)
        fills = []
        budget = reduced
        step = float(getattr(self, "qty_step", 0.001) or 0.001)
        for sl in sorted(self._tp_slices_for_initial(initial), key=lambda x: x["level"]):
            if sl["level"] in consumed or sl["price"] <= 0 or sl["qty"] <= 0.0005:
                continue
            if budget < step:
                break
            if self._has_tp_limit_at_price(sl["price"]):
                break  # 顺序档：前档还在则后续不算成交
            if not self._price_reached_tp_zone(sl["level"], curr_px, sl["price"]):
                break
            tol = max(step * 2, float(sl["qty"]) * TP_SLICE_MATCH_TOL_PCT)
            # 单档精确匹配，或连续多档预算覆盖
            if abs(budget - sl["qty"]) <= tol or budget + step >= float(sl["qty"]) - tol:
                fills.append({
                    "level": sl["level"],
                    "price": sl["price"],
                    "qty": round(min(sl["qty"], budget), 3),
                    "source": "order_gone",
                })
                budget = round(budget - float(sl["qty"]), 6)
                if budget < noise:
                    break
                continue
            break
        if fills:
            logger.info(
                f"🎯 盘口限价消失核实 TP{[f['level'] for f in fills]} "
                f"→ 判止盈成交 (减仓 {reduced} {self._unit()})"
            )
        return fills

    def _detect_tp_fills_by_price_qty_reconcile(self, old_qty, new_qty, curr_px=0.0, initial=None):
        """
        增强对账：现价已过 TP 档 + 该档限价不在 + 减仓覆盖切片
        → 记为止盈（防 REST 成交史延迟误报人工减仓）。
        """
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
        noise = max(
            float(getattr(self, "qty_step", 0.001) or 0.001) * 2,
            initial * TP_FILL_NOISE_VS_OPEN_PCT,
        )
        if reduced < noise:
            return []
        curr_px = float(curr_px or binance_client.get_current_price(self.symbol) or 0)
        consumed = set(getattr(self, "tp_levels_consumed", []) or [])
        fills = []
        budget = reduced
        step = float(getattr(self, "qty_step", 0.001) or 0.001)
        for sl in sorted(self._tp_slices_for_initial(initial), key=lambda x: x["level"]):
            if sl["level"] in consumed or sl["price"] <= 0 or sl["qty"] <= 0.0005:
                continue
            if budget < step:
                break
            if self._has_tp_limit_at_price(sl["price"]):
                break
            if not self._price_reached_tp_zone(sl["level"], curr_px, sl["price"]):
                break
            tol = max(step * 2, float(sl["qty"]) * TP_SLICE_MATCH_TOL_PCT)
            if budget + step < float(sl["qty"]) - tol:
                break
            fills.append({
                "level": sl["level"],
                "price": sl["price"],
                "qty": round(min(sl["qty"], budget), 3),
                "source": "price_qty_reconcile",
            })
            budget = round(budget - float(sl["qty"]), 6)
            if budget < noise:
                break
        return fills

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
        识别 TP 成交：成交史 → 限价消失 → 价量对账（可多档）。
        """
        if new_qty >= old_qty - 0.0005:
            return []
        self._ensure_tv_tps_for_fill_detect()
        baseline = self._tp_baseline_qty(old_qty)
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
        reconcile = self._detect_tp_fills_by_price_qty_reconcile(
            old_qty, new_qty, curr_px, initial,
        )
        if reconcile:
            logger.info(
                f"🎯 价量对账核实 TP{[f['level'] for f in reconcile]} "
                f"({old_qty}→{new_qty} {self._unit()})"
            )
            return reconcile

        soft = self._infer_tp_consumed_sequential(initial, new_qty, curr_px)
        if soft:
            hanging = []
            for lv in soft:
                px = self.tv_tps[lv - 1] if 0 <= lv - 1 < len(self.tv_tps) else 0
                if px > 0 and self._has_tp_limit_at_price(px):
                    hanging.append(lv)
            # 限价已全部消失 → 软推断可作成交证据（修「成交后当漏挂再补」）
            if not hanging:
                fills = []
                slices = {
                    int(s["level"]): s for s in self._tp_slices_for_initial(initial)
                }
                for lv in soft:
                    sl = slices.get(lv) or {}
                    fills.append({
                        "level": int(lv),
                        "price": float(sl.get("price") or 0),
                        "qty": float(sl.get("qty") or 0),
                        "source": "soft_infer",
                    })
                logger.info(
                    f"🧮 软推断 TP{soft} 限价已消失 → 采纳为成交证据 "
                    f"(基线 {initial}→{new_qty})"
                )
                return fills
            logger.info(
                f"🧮 软推断 TP{soft} 暂不采纳 "
                f"(基线 {initial}→{new_qty} | 仍挂限价档={hanging})"
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

    def _merge_tv_hard_sl_on_add(self, old_sl, new_sl):
        """加仓后硬止损：一律采用最新 TV tv_sl，禁止取更宽合并。"""
        new_sl = float(new_sl or 0)
        if new_sl > 0:
            return new_sl
        return float(old_sl or 0)

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
        重启修复：开仓价+现价对账 → 已过档跳过；只重分/补挂剩余档。
        禁止无减仓证据就清空「现价已过」记账后重挂 TP1。
        """
        live_qty = self._resolve_live_qty(live_qty)
        initial_qty = float(initial_qty or live_qty or 0)
        curr_px = float(
            curr_px or binance_client.get_current_price(self.symbol) or 0
        )
        actions = []

        plan = self._apply_takeover_price_progress(
            entry, curr_px, live_qty, source="重启部分止盈修复",
        )
        actions.extend(plan.get("notes") or [])
        self._sanitize_tp_consumed(initial_qty, live_qty, curr_px)
        consumed = getattr(self, "tp_levels_consumed", []) or []
        if consumed and initial_qty <= live_qty + 0.001:
            inferred = self._infer_tp_consumed_sequential(initial_qty, live_qty, curr_px)
            price_past = [
                lv for lv in consumed
                if self._price_reached_tp_zone(lv, curr_px, live_only=True)
            ]
            if not inferred and not price_past:
                logger.warning(
                    f"跳过部分止盈修复：无减仓且现价未过，清除 TP{consumed}"
                )
                self.tp_levels_consumed = []
                self._save_state()
                return {"repaired": False, "actions": actions, "result": None, "consumed": []}
            if price_past and not inferred:
                keep = self._sequential_tp_prefix(price_past)
                self.tp_levels_consumed = keep
                self._save_state()
                consumed = keep
                actions.append(f"现价已过保留 TP{keep}（无减仓也不重挂）")

        stale_levels = self._detect_stale_consumed_tp_levels(
            initial_qty, live_qty, curr_px,
        )
        if stale_levels:
            # 合并现价已过，避免 detect 丢掉
            past = [
                lv for lv in (1, 2, 3)
                if self._price_reached_tp_zone(lv, curr_px, live_only=True)
            ]
            merged = self._sequential_tp_prefix(
                sorted(set(stale_levels) | set(past))
            )
            prev = set(getattr(self, "tp_levels_consumed", []) or [])
            if merged != sorted(prev):
                self.tp_levels_consumed = merged
                self._save_state()
            actions.append(
                f"已成交/已过价档 TP{merged} | 开单 {initial_qty} → 现仓 {live_qty} ETH"
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
            # 仍可能仅需补挂全档；交由后续 enforce
            return {"repaired": False, "actions": actions, "result": None, "consumed": []}

        # 有现仓且仍有未成交档 → 必须 repair（含仅余 TP3 全仓）
        if live_qty > DUST_QTY_ETH and self._expected_tp_count() == 0:
            self._sanitize_tp_consumed(initial_qty, live_qty, curr_px)
            self._apply_takeover_price_progress(
                entry, curr_px, live_qty, source="重启·无待挂档",
            )
            consumed = getattr(self, "tp_levels_consumed", []) or []
            if self._expected_tp_count() == 0 and live_qty > DUST_QTY_ETH:
                # 现价未过任何档却 expected=0 → 异常；若已过1+2则只挂3
                if self._price_reached_tp_zone(2, curr_px, live_only=True):
                    logger.warning(
                        f"⚠️ 仍有 {live_qty} ETH 且现价已过TP2 → 只挂 TP3"
                    )
                    self.tp_levels_consumed = [1, 2]
                    self._save_state()
                elif self._price_reached_tp_zone(1, curr_px, live_only=True):
                    logger.warning(
                        f"⚠️ 仍有 {live_qty} ETH 且现价已过TP1 → 只挂 TP23"
                    )
                    self.tp_levels_consumed = [1]
                    self._save_state()

        n_stale = self._cancel_tp_orders_at_levels(consumed)
        if n_stale:
            actions.append(f"撤多余已成交档 {n_stale} 笔")

        n_mismatch = self._cancel_mismatched_remaining_tps(live_qty)
        if n_mismatch:
            actions.append(f"撤偏差剩余档 {n_mismatch} 笔")

        time.sleep(0.4)

        sl_to_pass = self._radar_sl_to_pass()
        if sl_to_pass is None and curr_px and curr_px > 0:
            top_level = max(consumed) if consumed else 0
            if top_level:
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
            f"对齐 {audit.get('matched_full', 0)}/{audit.get('expected', 0)} 档 | "
            f"应挂 {[lv['level'] for lv in rem_levels]}"
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
        curr_px = float(binance_client.get_current_price(self.symbol) or 0)
        radar_sl = self._resolve_armed_radar_sl(live_qty, curr_px, dynamic_sl)
        if radar_sl and not self._has_stop_sl_near(radar_sl):
            self._ensure_radar_sl(radar_sl, live_qty)
        elif not radar_sl:
            self._maintain_hard_shield(live_qty, curr_px, force=True, radar_sl=None)
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

    def _price_reached_tp1_zone(self, curr_px=0.0, tp1_px=None, live_only=False):
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
        prices = [float(curr_px or 0)]
        if not live_only:
            prices.append(float(self.best_price or 0))
        for px in prices:
            if px <= 0:
                continue
            if self.current_side == "LONG" and px >= tp1_px - px_tol:
                return True
            if self.current_side == "SHORT" and px <= tp1_px + px_tol:
                return True
        return False

    def _tp_fill_ok_to_arm_radar(self, tp_fills, curr_px, old_qty, new_qty):
        """兼容旧名 → 可信 TP 成交过滤（含 TP2/TP3，不再只认 TP1）。"""
        ok, _ = self._filter_credible_tp_fills(tp_fills, curr_px, old_qty, new_qty)
        return bool(ok)

    def _filter_credible_tp_fills(self, tp_fills, curr_px, old_qty, new_qty):
        """
        按档校验 TP 成交可信度：
        - TP1：价到区 + 限价消失 + 减仓覆盖切片（防伪）
        - TP2/TP3：价到该档 + 限价消失（或 trades 来源）；不要求本批含 TP1
        返回 (credible_fills, rejected_reason)
        """
        if getattr(self, "_open_in_progress", False) or getattr(
            self, "_defense_align_in_progress", False
        ):
            return [], "开仓/防线重建中"
        fills = list(tp_fills or [])
        if not fills:
            return [], "无成交"
        curr_px = float(curr_px or 0)
        consumed = set(getattr(self, "tp_levels_consumed", []) or [])
        credible = []
        for f in sorted(fills, key=lambda x: int(x.get("level") or 0)):
            lv = int(f.get("level") or 0)
            if lv not in (1, 2, 3):
                continue
            if lv in consumed:
                continue
            px = float(
                f.get("price")
                or ((self.tv_tps[lv - 1] if self.tv_tps else 0) or 0)
            )
            src = str(f.get("source") or "")
            if px > 0 and self._has_tp_limit_at_price(px):
                logger.warning(
                    f"📡 [{self.symbol}] TP{lv} 拒认：限价仍在盘口 @{px:.2f}"
                )
                # 顺序：前档未吃完则停
                break
            if not self._price_reached_tp_zone(lv, curr_px, px):
                # trades 来源且价贴近切片价 → 仍可信（现价可能已回撤）
                if src == "trades" and px > 0:
                    pass
                else:
                    logger.warning(
                        f"📡 [{self.symbol}] TP{lv} 拒认：现价/best 未达该档 "
                        f"(px={curr_px:.2f} tp={px:.2f})"
                    )
                    break
            if lv == 1:
                # TP1 额外量校验，过滤开仓微漂
                if not self._tp1_qty_matches_baseline(new_qty, old_qty=old_qty):
                    # 若本批同时含 TP2 且总量覆盖 TP1+…，放宽为可信
                    levels = {int(x.get("level") or 0) for x in fills}
                    if 2 not in levels and 3 not in levels:
                        logger.warning(
                            f"📡 [{self.symbol}] TP1 拒认：减仓量不匹配开仓基线 "
                            f"({old_qty}→{new_qty})"
                        )
                        break
            if src and src not in (
                "trades", "order_gone", "price_qty_reconcile",
            ):
                logger.warning(
                    f"📡 [{self.symbol}] TP{lv} 拒认：来源={src}"
                )
                break
            credible.append(f)
        if not credible:
            return [], "证据不足"
        return credible, ""

    def _reconcile_open_qty_vs_tp123(self, live_qty, entry=None, source=""):
        """
        开仓/成交后对账：总头寸 ≈ TP1+TP2+TP3 切片之和；
        硬止损 closePosition 与雷达共用单槽，不占 TP reduceOnly 额度。
        """
        live_qty = float(live_qty or 0)
        if live_qty <= 0:
            return {"ok": False, "note": "无仓"}
        baseline = self._tp_baseline_qty(live_qty)
        if baseline <= 0:
            baseline = live_qty
            self.initial_qty = live_qty
            self._open_settled_qty = live_qty
        slices = self._tp_slices_for_initial(baseline)
        slice_sum = round(sum(float(s.get("qty") or 0) for s in slices), 3)
        step = float(getattr(self, "qty_step", 0.001) or 0.001)
        drift = abs(slice_sum - baseline)
        ok = drift <= max(step * 3, baseline * 0.02)
        note = (
            f"开仓基线 {baseline} {self._unit()} | "
            f"TP切片合计 {slice_sum} "
            f"(TP1={slices[0]['qty']}/TP2={slices[1]['qty']}/TP3={slices[2]['qty']}) | "
            f"硬止损+雷达=closePosition单槽·TP=reduceOnly"
        )
        if not ok:
            logger.warning(
                f"⚠️ [{self.symbol}] [{source or '对账'}] 头寸与TP123偏差 "
                f"drift={drift} | {note}"
            )
        else:
            logger.info(f"✅ [{self.symbol}] [{source or '对账'}] {note}")
        # 盘口：TP 限价张数 vs 未消费档；STOP 应 ≤1（单槽）
        try:
            tp_orders = self._collect_tp_limit_orders()
            stops = binance_client.find_protective_stop_prices(self.symbol)
            expected = self._expected_tp_count()
            if expected > 0 and len(tp_orders) > expected + 1:
                logger.warning(
                    f"⚠️ [{self.symbol}] TP限价偏多 {len(tp_orders)}>{expected} "
                    f"→ 哨兵将纠偏（不撤硬止损）"
                )
            if len(stops) > 1:
                logger.warning(
                    f"⚠️ [{self.symbol}] 保护STOP叠单 {stops} → 合并为单槽 "
                    f"(雷达/硬止损不抢份额)"
                )
                self._maintain_hard_shield(
                    live_qty,
                    binance_client.get_current_price(self.symbol) or 0,
                    force=True,
                    radar_sl=self._radar_sl_to_pass(),
                )
        except Exception as e:
            logger.debug(f"对账盘口跳过: {e}")
        return {
            "ok": ok,
            "baseline": baseline,
            "slice_sum": slice_sum,
            "note": note,
            "slices": slices,
        }

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
        # TP 成交仍刷新 best；雷达交棒由价触激活线驱动，不强制三重
        for f in tp_fills:
            px = f["price"]
            if self.current_side == "LONG":
                self.best_price = max(self.best_price, px, curr_px or 0)
            else:
                bp = curr_px if curr_px and curr_px > 0 else px
                self.best_price = min(self.best_price, px, bp)
        max_level = max(f["level"] for f in tp_fills)
        tp3 = self.tv_tps[2] if len(self.tv_tps) > 2 else 0.0
        note = f"TP{max_level}成交"
        if max_level >= 2 and tp3 > 0:
            note += f" → 交棒后向 TP3({tp3:.2f}) 收紧"
        elif max_level == 1:
            note += " → 强制交棒保本防回吐"
        logger.info(
            f"📈 [{self.symbol}] 雷达推进预备 {note} | "
            f"保持VPS={float(getattr(self, 'tv_sl', 0) or 0):.2f} | "
            f"best={self.best_price:.2f} | "
            f"朝TP1={self._tp1_direction_progress(curr_px):.0%} "
            f"激活线={self._radar_activation_ratio():.0%}"
        )
        self._save_state()
        return None

    def _tp1_triad_ok(self, live_qty=None, curr_px=0.0, require_fresh=False):
        """
        兼容旧名：历史「三重验证」。现仅作 TP1 实盘成交核实（记账/伪TP拦截），
        **不再**作为雷达启动门槛。雷达主判见 `_price_reached_radar_activation`。
        """
        if getattr(self, "_open_in_progress", False) or getattr(
            self, "_defense_align_in_progress", False
        ):
            return False

        live_qty = float(live_qty if live_qty is not None else self.watched_qty or 0)
        tp1_px = float(self.tv_tps[0] or 0) if self.tv_tps else 0.0

        if tp1_px > 0 and self._has_tp_limit_at_price(tp1_px):
            return False
        if not self._tp_filled_verified(1, live_qty, curr_px):
            return False
        if not self._tp1_qty_matches_baseline(live_qty):
            return False
        if not self._price_reached_tp1_zone(curr_px, tp1_px):
            return False
        return True

    def _tp1_filled_verified(self, live_qty=None, curr_px=0.0):
        """兼容旧名 → TP1 实盘成交核实（非雷达启动门槛）。"""
        return self._tp1_triad_ok(live_qty, curr_px, require_fresh=True)

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
            credible, reject_why = self._filter_credible_tp_fills(
                change["tp_fills"], curr_px, old_qty, new_qty,
            )
            if not credible:
                # 仅当「声称含 TP1 却证据全无」才打伪TP1；纯 TP2/TP3 拒认走对账重试
                claimed_tp1 = any(
                    int(f.get("level") or 0) == 1 for f in (change["tp_fills"] or [])
                )
                if claimed_tp1:
                    logger.warning(
                        f"🎯 [智慧大脑] {levels} 疑似伪TP1"
                        f"（{reject_why or '证据不足'}）→ 再对账 | "
                        f"{old_qty}→{new_qty}"
                    )
                else:
                    logger.warning(
                        f"🎯 [智慧大脑] {levels} 证据不足（{reject_why}）→ 价量再对账 | "
                        f"{old_qty}→{new_qty}"
                    )
                retry = self._detect_tp_fills_by_price_qty_reconcile(
                    old_qty, new_qty, curr_px,
                )
                credible, reject_why = self._filter_credible_tp_fills(
                    retry, curr_px, old_qty, new_qty,
                )
            if not credible:
                # 证据不足：先头寸对账记账；禁止立刻按现仓重挂全部 TP123
                newly = self._reconcile_tp_consumed_from_live_qty(
                    new_qty, curr_px, source="TP证据不足·头寸对账", notify=True,
                )
                if newly or list(getattr(self, "tp_levels_consumed", []) or []):
                    logger.warning(
                        f"🎯 [智慧大脑] {levels} 证据不足但头寸对账已记账 "
                        f"TP{getattr(self, 'tp_levels_consumed', [])} → 只挂剩余档"
                    )
                    result = self._realign_remaining_tps_after_fill(
                        new_qty, dynamic_sl=None,
                        reason="头寸对账后静默对齐剩余TP",
                    )
                    if self._radar_ready_to_handoff(curr_px, new_qty):
                        self._perform_radar_handoff(
                            new_qty, curr_px, reason="价触激活线/TP1·防回吐",
                        )
                    elif self._should_activate_shield(curr_px) or getattr(
                        self, "shield_active", False
                    ):
                        self._maintain_hard_shield(new_qty, curr_px, force=True)
                    change = {
                        "kind": "tp_fill" if newly else "reduce_unknown",
                        "tp_fills": [
                            {"level": lv, "price": 0, "qty": 0} for lv in (newly or [])
                        ],
                        "shield_fills": [],
                    }
                    self._save_state()
                    return change, result

                if any(int(f.get("level") or 0) == 1 for f in (change["tp_fills"] or [])):
                    dingtalk.report_system_alert(
                        f"雷达拒启·伪TP1拦截 [{self.symbol}]",
                        f"{self.current_side} {old_qty}→{new_qty} {self._unit()} | {levels} | "
                        f"现价 {float(curr_px or 0):.2f} | {reject_why or '证据不足'} | "
                        f"规则：伪TP1不记账；近TP1禁止补挂TP1",
                    )
                # 近 TP1 且减仓：禁止 smart_realign 把 TP1 再挂回盘口
                if self._price_reached_tp1_zone(curr_px) or self._qty_reduction_looks_like_tp(
                    old_qty, new_qty, curr_px
                ):
                    logger.warning(
                        f"🎯 [{self.symbol}] 减仓近TP区但未核实 → 禁止重挂TP123，"
                        f"保留TV硬止损/已挂剩余档 | {old_qty}→{new_qty}"
                    )
                    dingtalk.report_system_alert(
                        f"止盈对账中·暂禁补挂 [{self.symbol}]",
                        f"{self.current_side} {old_qty}→{new_qty} {self._unit()} | "
                        f"现价 {float(curr_px or 0):.2f} | 疑似TP成交但证据不足 | "
                        f"已禁止按现仓重挂TP1（防TP1附近循环成交）",
                    )
                    if self._should_activate_shield(curr_px) or getattr(
                        self, "shield_active", False
                    ):
                        self._maintain_hard_shield(new_qty, curr_px, force=True)
                    change = {"kind": "reduce_unknown", "tp_fills": [], "shield_fills": []}
                    self._save_state()
                    return change, None

                result = self._smart_realign_defenses(
                    new_qty, self.watched_entry, dynamic_sl=None,
                    reason="TP证据不足·保TV硬止损+重挂剩余TP",
                )
                self._reconcile_open_qty_vs_tp123(new_qty, source="TP证据不足")
                if self._radar_ready_to_handoff(curr_px, new_qty):
                    self._perform_radar_handoff(
                        new_qty, curr_px, reason="价触激活线/TP1·防回吐",
                    )
                elif self._should_activate_shield(curr_px) or getattr(
                    self, "shield_active", False
                ):
                    self._maintain_hard_shield(new_qty, curr_px, force=True)
                change = {"kind": "reduce_unknown", "tp_fills": [], "shield_fills": []}
                self._save_state()
                return change, result

            change = {
                "kind": "tp_fill",
                "tp_fills": credible,
                "shield_fills": [],
            }
            levels = ",".join(f"TP{f['level']}" for f in credible)
            logger.info(
                f"🎯 [智慧大脑] {levels} 成交减仓 {old_qty} ➔ {new_qty} "
                f"→ 记账 + 守剩余TP + 雷达锁利"
            )
            self._mark_tp_levels_consumed([f["level"] for f in credible])
            curr_px_safe = curr_px or binance_client.get_current_price(self.symbol) or 0
            # 用成交价抬升 best，供 TP23 阶段锁利
            self._advance_radar_on_tp_fill(credible, curr_px_safe, new_qty)
            self._reconcile_open_qty_vs_tp123(new_qty, source=f"{levels}成交")
            # 只撤/重挂剩余 TP 限价，绝不撤 closePosition 硬止损/雷达单槽
            result = self._realign_remaining_tps_after_fill(
                new_qty, dynamic_sl=None,
                reason=f"{levels} 成交静默对齐",
            )
            # TP1 已吃或现价达激活线 → 交棒；已交棒则推升锁 TP23
            handed = self._perform_radar_handoff(
                new_qty, curr_px_safe, reason=f"{levels} 成交·雷达防回吐",
            )
            if handed or self._radar_legitimately_armed(new_qty, curr_px_safe):
                self._process_radar_trailing(new_qty, curr_px_safe)
            else:
                self._maintain_hard_shield(
                    new_qty, curr_px_safe, force=True, radar_sl=None,
                )
                logger.info(
                    f"📡 [{self.symbol}] {levels}已记账，交棒条件未齐 → "
                    f"保留 TV硬止损(closePosition单槽)"
                )
        elif kind == "shield_fill":
            f = change["shield_fills"][0]
            logger.warning(
                f"🛡️ [智慧大脑] TV硬止损成交 "
                f"{old_qty} ➔ {new_qty} @ {f['price']:.2f}"
            )
            if new_qty <= 0.0005 or self._is_dust_qty(new_qty):
                near_sl = self._likely_exchange_stop_exit(curr_px)
                if self._radar_was_armed():
                    flat_meta = self._infer_flat_close_meta(
                        curr_px, hint_reason="雷达保本/追踪止损全平",
                    )
                else:
                    reason = (
                        "触碰硬止损平仓（TV硬止损）"
                        if near_sl else
                        "仓位归零（现价未到硬止损·疑似人工/异动/市价强平）"
                    )
                    flat_meta = self._build_close_meta(
                        "CLOSE_STOPLOSS" if near_sl else "CLOSE",
                        self.current_side,
                        self._estimate_pnl_pct(curr_px),
                        reason,
                    )
                    if near_sl:
                        flat_meta["close_type"] = CLOSE_TYPE_VPS_SHIELD
                        flat_meta["exit_source"] = EXIT_SOURCE_VPS_HARD_SL
                        flat_meta["exit_source_label"] = EXIT_SOURCE_LABELS[
                            EXIT_SOURCE_VPS_HARD_SL
                        ]
                    else:
                        flat_meta["exit_source"] = EXIT_SOURCE_MANUAL
                        flat_meta["exit_source_label"] = EXIT_SOURCE_LABELS[
                            EXIT_SOURCE_MANUAL
                        ]
                self._disarm_shield(
                    "雷达/硬止损全平" if self._radar_was_armed() or near_sl else "异动全平",
                    notify=False,
                )
                self._handle_manual_flat_detected(
                    flat_meta.get("tv_reason") or flat_meta.get("exit_source_label"),
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
            # 再查：成交史 / 限价消失 / 价量对账 → 优先改判为止盈（禁误报人工）
            self._ensure_tv_tps_for_fill_detect()
            retry_fills = self._detect_tp_fills(old_qty, new_qty, curr_px)
            if not retry_fills:
                peak = max(float(self.initial_qty or 0), float(old_qty or 0))
                retry_fills = self._detect_tp_fills_from_trades(
                    old_qty, new_qty, initial=peak, lookback_ms=300000,
                )
            if not retry_fills:
                retry_fills = self._detect_tp_fills_by_price_qty_reconcile(
                    old_qty, new_qty, curr_px,
                )
            if retry_fills:
                credible, why = self._filter_credible_tp_fills(
                    retry_fills, curr_px, old_qty, new_qty,
                )
                if credible:
                    change = {
                        "kind": "tp_fill",
                        "tp_fills": credible,
                        "shield_fills": [],
                    }
                    self._save_state()
                    return self._handle_smart_qty_change(old_qty, new_qty, curr_px)
                logger.warning(
                    f"🎯 [智慧大脑] 重试判 TP 仍缺证据 "
                    f"{[f.get('level') for f in retry_fills]} ({why}) → 通用对齐"
                )
            # 价已过 TP 区但仍未匹配切片 → 标注「待核实止盈」而非武断「手动减仓」
            near_tp = any(
                self._price_reached_tp_zone(lv, curr_px)
                for lv in (1, 2, 3)
            )
            action_msg = (
                "手动加仓" if new_qty > old_qty
                else (
                    "限价止盈待核实对账" if near_tp
                    else "仓位减仓（未匹配TP切片）"
                )
            )
            logger.info(
                f"🔄 [智慧大脑] 仓位变化 {old_qty} ➔ {new_qty} ({pct:.1%})，"
                f"{action_msg} → 通用重对齐 + 对账"
            )
            self._bump_best_on_tp_fill(old_qty, new_qty, curr_px)
            self._reconcile_open_qty_vs_tp123(new_qty, source=action_msg)
            self._sync_radar_sl_from_best(curr_px)
            sl_to_pass = self._radar_sl_to_pass()
            result = self._smart_realign_defenses(
                new_qty, self.watched_entry, dynamic_sl=sl_to_pass,
                reason=f"仓位异动: {action_msg}",
            )
            if self._radar_ready_to_handoff(curr_px, new_qty):
                self._perform_radar_handoff(
                    new_qty, curr_px, reason="价触激活线/TP1·防回吐",
                )
                if self._radar_legitimately_armed(new_qty, curr_px):
                    self._process_radar_trailing(new_qty, curr_px)
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
                fills, _ = self._filter_credible_tp_fills(
                    fills, entry_px, old_qty, new_qty,
                )
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
                    f"({fill['qty']} {self._unit()})"
                )
        else:
            near_tp = any(
                self._price_reached_tp_zone(lv, entry_px)
                for lv in (1, 2, 3)
            )
            action_msg = (
                "手动加仓" if new_qty > old_qty
                else (
                    "限价止盈对账中" if near_tp
                    else "仓位减仓（未匹配TP切片）"
                )
            )
            recon = self._reconcile_open_qty_vs_tp123(new_qty, source="钉钉对账")
            if recon.get("note"):
                verify_note = f"{verify_note} | {recon['note']}"
            self._call_dingtalk(
                dingtalk.report_manual_position_change,
                action_type=action_msg,
                old_qty=old_qty,
                new_qty=new_qty,
                new_entry_price=entry_px,
                verify_note=verify_note,
                tp_audit=(realign_result or {}).get("audit"),
                verified=verified,
            )

        if realign_result and realign_result.get("expected", 0) > 0 and (
            realign_result.get("matched", 0) < realign_result.get("expected", 0)
        ):
            dingtalk.report_system_alert(
                "仓位变动后止盈未对齐",
                f"{self._format_audit_summary(realign_result.get('audit') or {})}",
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

    def _radar_was_armed(self):
        """交棒 STOP 核实成功即武装——不以钉钉是否发出为准（修误判硬止损）。"""
        return bool(
            getattr(self, "_radar_handoff_done", False)
            or getattr(self, "_radar_armed_after_tp1", False)
            or self._is_radar_active()
        )

    def _describe_radar_trigger_gate(self, live_qty=None, curr_px=0.0):
        saved = str(getattr(self, "_radar_trigger_gate", "") or "").strip()
        if saved:
            return saved
        if self._tp1_fill_allows_radar(live_qty, curr_px):
            return "TP1成交强制交棒"
        ratio = self._radar_activation_ratio()
        return (
            f"距TP1剩{(1 - ratio) * 100:.0f}%（路程{ratio * 100:.0f}%）"
        )

    def _resolve_exit_source(self, curr_px=0.0, hint_reason=""):
        """
        全平归因（优先级）：
        ① 近 180s TV 全平信号 → tv_*
        ② TP123 全吃完 → tp3
        ③ 雷达已交棒（handoff_done，不看钉钉）→ radar_be
        ④ 贴 TV 硬止损且未交棒 → vps_hard_sl(兼容名)/tv_hard_sl
        ⑤ 其余 → manual
        """
        hint = str(hint_reason or "").strip()
        last = self.last_tv_signal or {}
        last_ts = float(last.get("ts", 0) or 0)
        last_act = str(last.get("action", "") or "").upper()
        if last_act and time.time() - last_ts < 180:
            if last_act == "CLOSE_TP3":
                return EXIT_SOURCE_TP3, last.get("reason") or "TV CLOSE_TP3 · TP3完美收网"
            if last_act == "CLOSE_PROTECT" or last_act.startswith("CLOSE_PROTECT"):
                return (
                    EXIT_SOURCE_TV_PROTECT,
                    last.get("reason") or "TV CLOSE_PROTECT · 风控拦截",
                )
            if last_act == "CLOSE_STOPLOSS" or last_act.startswith("CLOSE_STOP"):
                reason = str(last.get("reason") or "")
                if (
                    "雷达" in reason
                    or "保本" in reason
                    or "防回吐" in reason
                    or self._radar_was_armed()
                ):
                    gate = self._describe_radar_trigger_gate(self.watched_qty, curr_px)
                    return (
                        EXIT_SOURCE_RADAR_BE,
                        (reason or "TV CLOSE_STOPLOSS · 雷达/保本")
                        + f" | 启动闸门={gate}",
                    )
                return (
                    EXIT_SOURCE_TV_CLOSE,
                    reason or "TV CLOSE_STOPLOSS",
                )
            if last_act == "CLOSE" or last_act.startswith("CLOSE"):
                return EXIT_SOURCE_TV_CLOSE, last.get("reason") or last_act

        consumed = set(getattr(self, "tp_levels_consumed", []) or [])
        if consumed >= {1, 2, 3}:
            return EXIT_SOURCE_TP3, "TP123三档全部成交 · 完美收网"

        if self._radar_was_armed():
            gate = self._describe_radar_trigger_gate(self.watched_qty, curr_px)
            sl = float(
                getattr(self, "_last_applied_exchange_sl", 0)
                or getattr(self, "current_sl", 0)
                or 0
            )
            near = self._likely_exchange_stop_exit(curr_px)
            note = (
                f"雷达保本止损触发 @ {sl:.2f}" if sl > 0 else "雷达保本止损触发"
            )
            note += f" | 启动闸门={gate}"
            note += " | 现价贴保本线" if near else " | 现价未贴线(滑点/扫尾可能)"
            if hint:
                note += f" | {hint}"
            return EXIT_SOURCE_RADAR_BE, note

        if self._likely_exchange_stop_exit(curr_px):
            sl = float(
                getattr(self, "_last_applied_exchange_sl", 0)
                or getattr(self, "tv_sl", 0)
                or 0
            )
            return (
                EXIT_SOURCE_VPS_HARD_SL,
                f"TV硬止损触发 @ {sl:.2f}（雷达未交棒）"
                + (f" | {hint}" if hint else ""),
            )

        if getattr(self, "shield_active", False):
            return (
                EXIT_SOURCE_MANUAL,
                hint
                or "仓位归零（现价未到硬止损·疑似人工/异动/市价强平）",
            )
        return EXIT_SOURCE_MANUAL, hint or "仓位归零（来源未明）"

    def _infer_flat_close_meta(self, curr_px=0.0, hint_reason=""):
        """哨兵/重启推断全平类型 + exit_source（雷达 vs TP vs 硬止损一目了然）"""
        exit_src, note = self._resolve_exit_source(curr_px, hint_reason)
        est = self._estimate_pnl_pct(curr_px)
        side = self.current_side

        if exit_src == EXIT_SOURCE_TP3:
            meta = self._build_close_meta("CLOSE_TP3", side, est, note)
            meta["close_type"] = CLOSE_TYPE_TP3
        elif exit_src == EXIT_SOURCE_RADAR_BE:
            meta = self._build_close_meta("CLOSE_STOPLOSS", side, est, note)
            meta["close_type"] = CLOSE_TYPE_BREAKEVEN
        elif exit_src == EXIT_SOURCE_VPS_HARD_SL:
            meta = self._build_close_meta("CLOSE_STOPLOSS", side, est, note)
            meta["close_type"] = CLOSE_TYPE_VPS_SHIELD
        elif exit_src == EXIT_SOURCE_TV_PROTECT:
            meta = self._build_close_meta("CLOSE_PROTECT", side, est, note)
            meta["close_type"] = CLOSE_TYPE_PROTECT
        elif exit_src == EXIT_SOURCE_TV_CLOSE:
            last = self.last_tv_signal or {}
            meta = self._build_close_meta(
                last.get("action") or "CLOSE",
                last.get("side") or side,
                last.get("pnl_pct") if last.get("pnl_pct") is not None else est,
                note,
            )
        else:
            meta = self._build_close_meta("CLOSE", side, est, note)

        meta["exit_source"] = exit_src
        meta["exit_source_label"] = EXIT_SOURCE_LABELS.get(exit_src, exit_src)
        return meta

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

    def _release_tv_seq_after_close(self, payload=None, reason=""):
        """
        同 K 线先平后开：CLOSE(seq小) 后释放 LONG/SHORT 幂等，允许 OPEN(seq大)。
        """
        bi, _sq = extract_seq_meta(payload or {})
        if bi is None and isinstance(self.last_tv_signal, dict):
            bi, _sq = extract_seq_meta(self.last_tv_signal)
        if bi is None:
            return
        try:
            self._last_close_bar_index = int(bi)
            self._last_close_flat_ts = time.time()
            n = self._seq_buffer.release_bar_for_reentry(int(bi))
            if n:
                logger.info(
                    f"📬 [{self.symbol}] 先平后开：已释放 bar={bi} 开仓幂等 "
                    f"({n}键) | {reason or 'CLOSE'} | 随后 OPEN 按 seq 升序"
                )
                # 钉钉：平仓本体已有收网播报；此处仅日志，避免连环刷屏
                logger.info(
                    f"📬 [{self.symbol}] CLOSE 后已释放开仓幂等 {n} 键 | "
                    f"bar={bi} | 待 OPEN 按档位开仓"
                )
        except Exception as e:
            logger.warning(f"先平后开释放幂等失败: {e}")

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
        elif raw_action in ("LONG", "SHORT"):
            # TV 空价：用盘口价占位，否则 enrich TP 无法补全 → 开仓裸奔
            live_px = float(binance_client.get_current_price(self.symbol) or 0)
            if live_px > 0:
                self.tv_price = live_px
                payload = dict(payload)
                payload["price"] = live_px
                payload["_price_source"] = payload.get("_price_source") or "local"
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
            px_for_tp = float(self.tv_price or 0)
            if px_for_tp <= 0:
                px_for_tp = float(binance_client.get_current_price(self.symbol) or 0)
            if px_for_tp > 0 and not validate_tp_prices_for_side(
                raw_action, px_for_tp, self.tv_tps,
            ):
                enriched = enrich_entry_tp_prices(
                    raw_action, px_for_tp, self.current_atr, self.regime, payload,
                )
                self.tv_tps = self._sanitize_tp_prices([
                    self._safe_float(enriched.get("tv_tp1"), 0),
                    self._safe_float(enriched.get("tv_tp2"), 0),
                    self._safe_float(enriched.get("tv_tp3"), 0),
                ])
                if enriched.get("_tp_source"):
                    payload = dict(payload)
                    payload["_tp_source"] = enriched.get("_tp_source")
                logger.info(
                    f"📐 开仓信号 TP123 本地补全 @ {px_for_tp:.2f} → {self.tv_tps} "
                    f"({payload.get('_tp_source', 'local')})"
                )
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
            logger.error(f"⏱️ 锁等待 120s 超时，信号 {raw_action} 重新入队(旁路)")
            self._signal_queue.put(payload)
            return

        try:
            is_close = (
                raw_action in ("CLOSE", "CLOSE_PROTECT", "CLOSE_TP3", "CLOSE_STOPLOSS")
                or raw_action.startswith("CLOSE")
            )
            if is_close:
                self.monitoring = False
                # 同 bar 1-2-1：先释放开仓幂等，再执行平仓，保证随后 OPEN 可入队
                self._release_tv_seq_after_close(payload, reason=raw_action)
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
                # 单独平仓：清仓+撤净挂单+复位，干净等待下次 TV（无开仓则不开）
                pos = self._get_active_position()
                tv_reason = close_reason or "TV单独平仓清场"
                if not pos or pos.get("size", 0) <= 0:
                    logger.info(
                        f"🧹 TV单独平仓但盘口已空 → 撤净挂单复位等待 | {tv_reason}{close_extra}"
                    )
                    self._handle_manual_flat_detected(
                        tv_reason,
                        close_meta=close_meta,
                        curr_px=self.tv_price,
                    )
                else:
                    self._close_all(
                        f"🧹 TV单独平仓清场：{tv_reason}{close_extra}",
                        close_meta=close_meta,
                    )
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
        """
        v13.83：同向筛选已废除「仅刷新 TP」。
        凡 OPEN 一律 FULL_REENTRY（先平后开），禁止新老逻辑打架漏挂防线。
        """
        ref_px = curr_px or self.tv_price or pos["entry_price"]
        live_entry = pos["entry_price"]
        diff_pct = self._entry_price_diff_pct(live_entry, self.tv_price, ref_px)
        open_atr = float(getattr(self, "open_atr", self.current_atr) or self.current_atr)
        tv_atr = float(self.current_atr)
        logger.info(
            f"⚡ 同向 [{action}] → 铁律一律先平后开 "
            f"(价差 {diff_pct:.3f}% | ATR {open_atr:.2f}→{tv_atr:.2f}) | "
            f"禁止仅刷 TP"
        )
        return "FULL_REENTRY", diff_pct, "always_close_then_open", open_atr, tv_atr

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
        """
        v13.83 废弃：旧「仅刷新 TP」会漏挂/与先平后开打架。
        若被误调用 → 强制改走先平后开（挂齐 TP123+TV硬止损+雷达待命）。
        """
        logger.warning(
            f"⚠️ [{self.symbol}] 废弃路径 _same_direction_refresh_tp 被调用 → "
            f"强制先平后开 [{action}] 价差={diff_pct:.3f}%"
        )
        payload = {}
        last = self.last_tv_signal if isinstance(self.last_tv_signal, dict) else {}
        if isinstance(last.get("payload"), dict):
            payload = dict(last["payload"])
        elif last:
            payload = dict(last)
        self._full_reentry(
            action,
            "废弃同向仅刷TP·改走先平后开",
            payload=payload,
        )

    def _ensure_sentinel_running(self):
        if self.monitoring and not self._sentinel_active:
            threading.Thread(
                target=self._sentinel_loop, daemon=True, name="sentinel",
            ).start()

    def _full_reentry(self, action, close_reason, payload=None):
        """
        铁律原子链：先平现有仓（无菌净场）→ 再按 TV 开仓刷新。
        空仓时同样走净场（清残留挂单）再开；钉钉核实终态。
        开仓前快照 TV TP123，净场后强制绑回，杜绝只开仓不挂防线。
        """
        payload = payload or {}
        chain = bool(getattr(self, "_close_open_chain_active", False))
        reason = close_reason or "TV开仓·一律先平后开"
        # 净场前快照：无菌/强平复位不得冲掉本笔 TV 的 TP123
        snap = self._snapshot_tv_open_defenses(payload, action=action)
        self._pending_open_defense_snap = snap
        logger.info(
            f"📌 [{self.symbol}] 开仓前防线快照 TP={snap.get('tv_tps')} "
            f"sl_ref={float(snap.get('tv_sl_ref') or 0):.2f}"
        )
        if not self._sterile_flat_gate(reason_tag=reason, force_close=True):
            logger.error("❌ 先平后开中止：无菌空仓未通过，拒绝叠仓开仓")
            try:
                self._call_dingtalk(
                    dingtalk.report_close_then_open_chain,
                    phase="中止",
                    side=action,
                    reason=reason,
                    bar_index=getattr(self, "_last_close_bar_index", None),
                    chain_same_bar=chain,
                    verify_note="qty/挂单未净 → 已拒绝开仓",
                    ok=False,
                )
            except Exception:
                pass
            self._close_open_chain_active = False
            return
        # 净场后立刻绑回 TV 防线（防 close_all/复位冲掉）
        self._bind_tv_open_defenses(
            snap, entry=snap.get("price") or self.tv_price, side=action,
            source="先平后开·净场后绑回",
        )
        curr_px = binance_client.get_current_price(self.symbol) or self.tv_price
        if curr_px <= 0:
            logger.error("❌ 先平后开中止：无有效市价")
            self._close_open_chain_active = False
            return
        try:
            self._call_dingtalk(
                dingtalk.report_close_then_open_chain,
                phase="执行·先平后开",
                side=action,
                reason=reason,
                bar_index=getattr(self, "_last_close_bar_index", None),
                chain_same_bar=chain,
                verify_note=(
                    f"无菌通过 @ {float(curr_px):.2f} → 开 {action} "
                    f"| TP={snap.get('tv_tps')} | TV硬止损+雷达待命"
                ),
                ok=True,
            )
        except Exception:
            pass
        self._open_position(action, curr_px, payload=payload)
        self._close_open_chain_active = False

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
        self._abnormal_reduce_alert_ts = 0.0
        self._abnormal_reduce_alert_sig = ""
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
        加仓成功后：加权均价(交易所) + 最新 TV tv_sl + 重置雷达 + 替换 TP123。
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
        merged_sl = self._merge_tv_hard_sl_on_add(old_vps_sl, new_vps_sl)
        if merged_sl > 0:
            self.tv_sl = merged_sl
            self._last_applied_exchange_sl = 0.0
            if abs(merged_sl - old_vps_sl) > 0.01:
                logger.info(
                    f"🛡️ 加仓硬止损改挂最新 TV tv_sl: 旧{old_vps_sl:.2f} → {merged_sl:.2f} "
                    f"(禁止取更宽)"
                )
        self.best_price = new_entry
        self.current_sl = merged_sl if merged_sl > 0 else new_vps_sl
        self._radar_stage_last = 0
        self._radar_activation_notified = False
        self._radar_notify_pending = False
        self._radar_trigger_gate = ""
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
        """PYRAMID / PROFIT_ADD：TV 唯一公式 × qty_ratio，并重挂 TP123 + 同步雷达"""
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

        lev = int(round(float(getattr(self, "tv_sizing_leverage", 0) or 0)))
        if lev <= 0:
            logger.error(f"{entry_type} 跳过：TV leverage 无效，禁止 set_leverage 回退固定倍数")
            dingtalk.report_system_alert(
                f"{entry_type} 杠杆无效",
                "缺少 TV leverage，已拒绝加仓（禁止固定 25x）",
            )
            return
        binance_client.set_leverage(self.symbol, leverage=lev)
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
        last_open = self._load_last_journal_entry(None, kind="open")
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

    def _radar_in_progress(self):
        """雷达已交棒/已激活：新 TV OPEN 一律先平后开，禁止在保本线下刷新 TP。"""
        return bool(
            getattr(self, "_radar_handoff_done", False)
            or self._is_radar_active()
        )

    def _snapshot_tv_open_defenses(self, payload=None, action=None):
        """开仓前快照 TV TP123/参考止损，防无菌净场/复位冲掉后再裸仓开单。"""
        payload = dict(payload or {})
        tps = self._sanitize_tp_prices([
            self._safe_float(payload.get("tv_tp1"), 0)
            or (self.tv_tps[0] if self.tv_tps else 0),
            self._safe_float(payload.get("tv_tp2"), 0)
            or (self.tv_tps[1] if self.tv_tps and len(self.tv_tps) > 1 else 0),
            self._safe_float(payload.get("tv_tp3"), 0)
            or (self.tv_tps[2] if self.tv_tps and len(self.tv_tps) > 2 else 0),
        ])
        if sum(1 for t in tps if float(t or 0) > 0) < 3:
            last = self.last_tv_signal if isinstance(self.last_tv_signal, dict) else {}
            last_tps = last.get("tv_tps") or []
            if isinstance(last_tps, (list, tuple)) and sum(
                1 for t in last_tps if float(t or 0) > 0
            ) >= 3:
                tps = self._sanitize_tp_prices(list(last_tps))
            else:
                pl = last.get("payload") if isinstance(last.get("payload"), dict) else {}
                tps = self._sanitize_tp_prices([
                    self._safe_float(pl.get("tv_tp1") or last.get("tv_tp1"), 0),
                    self._safe_float(pl.get("tv_tp2") or last.get("tv_tp2"), 0),
                    self._safe_float(pl.get("tv_tp3") or last.get("tv_tp3"), 0),
                ])
        return {
            "action": str(action or payload.get("action") or self.current_side or "").upper(),
            "tv_tps": list(tps),
            "tv_sl_ref": self._safe_float(
                payload.get("tv_sl") or getattr(self, "tv_sl_ref", 0), 0,
            ),
            "atr": float(
                self._safe_float(payload.get("atr"), 0)
                or getattr(self, "current_atr", 0)
                or 30
            ),
            "regime": int(
                self._safe_int(payload.get("regime"), 0)
                or getattr(self, "regime", 0)
                or 3
            ),
            "price": float(
                self._safe_float(payload.get("price"), 0)
                or getattr(self, "tv_price", 0)
                or 0
            ),
            "payload": payload,
        }

    def _bind_tv_open_defenses(self, snap=None, entry=None, side=None, source="开仓绑定"):
        """
        开仓挂防线前强制绑定 TV TP123（来自本笔快照），禁止接管跳过逻辑污染。
        返回绑定后的 TP 列表。
        """
        self._takeover_price_skip = False
        snap = snap or getattr(self, "_pending_open_defense_snap", None) or {}
        side = str(side or snap.get("action") or self.current_side or "").upper()
        entry = float(entry or snap.get("price") or self.watched_entry or self.tv_price or 0)
        tps = self._sanitize_tp_prices(list(snap.get("tv_tps") or self.tv_tps or []))
        if side in ("LONG", "SHORT") and entry > 0:
            if not validate_tp_prices_for_side(side, entry, tps):
                atr = float(snap.get("atr") or self.current_atr or 30)
                regime = int(snap.get("regime") or self.regime or 3)
                enriched = enrich_entry_tp_prices(
                    side, entry, atr, regime, snap.get("payload") or {},
                )
                tps = self._sanitize_tp_prices([
                    self._safe_float(enriched.get("tv_tp1"), 0),
                    self._safe_float(enriched.get("tv_tp2"), 0),
                    self._safe_float(enriched.get("tv_tp3"), 0),
                ])
                logger.warning(
                    f"📐 [{source}] TV TP 与方向不符 → ATR 重算 {tps}"
                )
        self.tv_tps = list(tps)
        self.tp_levels_consumed = []
        if float(snap.get("atr") or 0) > 0:
            self.current_atr = float(snap["atr"])
            self.open_atr = float(snap["atr"])
        if int(snap.get("regime") or 0) in (1, 2, 3, 4):
            self.regime = int(snap["regime"])
            self.open_regime = int(snap["regime"])
        ref = float(snap.get("tv_sl_ref") or 0)
        if ref > 0:
            self.tv_sl_ref = ref
        self._pending_open_defense_snap = {
            **snap,
            "tv_tps": list(self.tv_tps),
            "action": side,
            "price": entry,
        }
        self._save_state()
        logger.info(
            f"🛡️ [{source}] 绑定开仓防线 TP={self.tv_tps} "
            f"tv_sl_ref={float(getattr(self, 'tv_sl_ref', 0) or 0):.2f} "
            f"R{getattr(self, 'open_regime', self.regime)} "
            f"ATR={float(getattr(self, 'open_atr', self.current_atr) or 0):.2f}"
        )
        return list(self.tv_tps)

    def _handle_smart_entry(self, action, payload=None):
        """
        铁律（清晰）：
        - 带开仓的 TV（OPEN / LONG|SHORT 建仓）→ 一律先平现有仓再开（刷新仓位）
        - 同时收到平仓+开仓 → 缓冲已先平后开；此处开仓仍走先平后开净场
        - PYRAMID / PROFIT_ADD → 加仓（非新开）
        - 单独平仓由 CLOSE* 分支清零等待（不进本函数）
        """
        payload = payload or {}
        entry_type = normalize_entry_type(payload.get("entry_type"))

        if entry_type in (ENTRY_TYPE_PYRAMID, ENTRY_TYPE_PROFIT_ADD):
            self._add_to_position(action, payload)
            self._touch_entry_signal_signature(action)
            return

        curr_px = binance_client.get_current_price(self.symbol) or self.tv_price
        # 空仓短时重复开仓信号：忽略（无仓可刷，耐心等下次有效 TV）
        if self._verify_flat() and self._is_duplicate_flat_entry(action, curr_px):
            logger.info(f"🧠 空仓短时重复开仓 TV [{action}] → 忽略，干净等待下次")
            try:
                self._call_dingtalk(
                    dingtalk.report_smart_same_dir_decision,
                    side=action,
                    decision="skip_duplicate_flat",
                    live_entry=0.0,
                    tv_price=self.tv_price,
                    diff_pct=0.0,
                    threshold_pct=SAME_DIR_MIN_SPREAD_PCT,
                    open_regime=self.regime,
                    tv_regime=self.regime,
                    open_atr=self._last_entry_signal.get("atr", self.current_atr),
                    tv_atr=self.current_atr,
                    qty=0.0,
                    verify_note="空仓重复开仓信号已忽略 | 状态干净等待",
                )
            except Exception:
                pass
            self._touch_entry_signal_signature(action)
            return

        pos = self._get_active_position()
        live_sz = float((pos or {}).get("size", 0) or 0)
        live_side = (pos or {}).get("side")
        logger.info(
            f"⚡ TV开仓 [{action}] entry={entry_type} → 铁律先平后开刷新 "
            f"| 现仓 {live_side or 'FLAT'} {live_sz} {self._unit()} "
            f"| TP快照 {[round(float(x or 0), 2) for x in (self.tv_tps or [])]}"
        )
        self._full_reentry(
            action,
            "TV开仓·一律先平后开刷新仓位（有仓先平；无仓净挂单再开）",
            payload=payload,
        )
        self._touch_entry_signal_signature(action)

    def _open_position(self, action, curr_px, payload=None):
        payload = payload or {}
        if self._open_in_progress:
            logger.error(f"开仓中止：已有开仓流程进行中，拒绝叠仓 [{action}]")
            return
        self._open_in_progress = True
        self._takeover_price_skip = False  # 开仓路径禁止接管「跳过已过TP」逻辑
        try:
            snap = getattr(self, "_pending_open_defense_snap", None) or self._snapshot_tv_open_defenses(
                payload, action=action,
            )
            self._bind_tv_open_defenses(
                snap, entry=curr_px, side=action, source="开仓下单前绑定",
            )
            # 本金快照不单独钉钉（并入开仓播报），避免开一单刷两条
            self._snapshot_sizing_principal(
                f"开仓前 {normalize_entry_type(payload.get('entry_type'))} R{self.regime}",
                notify=False,
            )
            qty, balance, margin_usdt, margin_pct, sizing_meta = self._calc_target_open_qty(
                curr_px, payload=payload,
            )
            if qty <= 0:
                logger.error(f"开仓跳过：目标数量无效 balance={balance:.2f} px={curr_px}")
                return

            lev = int(round(float(
                (sizing_meta or {}).get("leverage")
                or getattr(self, "tv_sizing_leverage", 0)
                or 0
            )))
            if lev <= 0:
                logger.error("开仓跳过：TV leverage 无效，禁止 set_leverage 回退固定 25x")
                dingtalk.report_system_alert(
                    f"开仓中止 · TV杠杆缺失 [{self.symbol}]",
                    "webhook 未带有效 leverage，已拒绝下单（仓位与 API 杠杆同源，禁止固定 25x）",
                )
                return
            binance_client.set_leverage(self.symbol, leverage=lev)
            notional = qty * curr_px
            budget_txt = format_vps_sizing_note(sizing_meta, qty=qty, entry_type=ENTRY_TYPE_OPEN)
            logger.info(
                f"📐 仓位预算 [{self.symbol}]: {budget_txt} "
                f"| set_leverage={lev}x(TV) | 名义 ~{notional:.0f}U"
            )

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
                f"| {self.symbol} | 档位 {self.regime} | 待挂TP={self.tv_tps}"
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
            self.open_regime = int(snap.get("regime") or self.regime or 3)
            self.open_atr = float(snap.get("atr") or self.current_atr or 30)
            self._open_regime_sticky = True
            self.initial_qty = real_qty
            self.base_qty = float(real_qty)
            self.add_count = 0
            # 成交后再绑一次（防无菌/并发冲掉 TV TP）
            self._bind_tv_open_defenses(
                snap, entry=pos["entry_price"], side=action, source="开仓成交后绑定",
            )
            self._protect_and_monitor(
                real_qty, pos["entry_price"],
                budget_note=f"[{self.symbol}] {budget_txt} | ",
                target_qty=qty,
                sizing_meta=sizing_meta,
            )
        finally:
            self._open_in_progress = False
            self._takeover_price_skip = False

    def _protect_and_monitor(self, qty, entry_price, budget_note="", target_qty=0.0, sizing_meta=None):
        """
        开仓后防线铁律（挂一次、挂齐、三轨不抢份额）：
        1) 核实持仓 → 绑回本笔 TV TP123 + tv_sl
        2) 挂 TP123（reduceOnly 限价，按档位比例切片）— 只挂本轮缺失档
        3) 挂 TV 硬止损（closePosition 单槽）— 与雷达共用，不占 TP 额度
        4) 雷达阶段0 待命（价触档位激活线或 TP1 真成交后再交棒）
        5) 实盘核实后钉钉一条（verified=TP齐+硬止损已挂）
        """
        entry_price = float(entry_price or 0)
        # 开仓路径：禁止接管「现价已过跳过TP」污染；强制绑回本笔 TV TP123
        self._takeover_price_skip = False
        snap = getattr(self, "_pending_open_defense_snap", None)
        self._bind_tv_open_defenses(
            snap, entry=entry_price, side=self.current_side, source="开仓保护绑定",
        )
        # 开仓硬闸：TV 空/不全时必须用实盘 entry+ATR 合成 TP123，禁止 expected=0 裸奔
        if not self._ensure_tp123_prices_from_tv(entry_price):
            logger.error(
                f"🚨 [{self.symbol}] 开仓 TP123 补全失败 entry={entry_price} "
                f"tps={self.tv_tps} → 仍强制挂 VPS 硬止损"
            )
            dingtalk.report_system_alert(
                f"开仓 TP123 补全失败 [{self.symbol}]",
                f"{self.current_side} entry={entry_price:.2f} | tps={self.tv_tps} | "
                f"将仅挂 TV 硬止损，哨兵继续补 TP",
            )
        # 若补全后仍空，再从快照硬灌一次
        if sum(1 for t in (self.tv_tps or []) if float(t or 0) > 0) < 3 and snap:
            self._bind_tv_open_defenses(
                snap, entry=entry_price, side=self.current_side, source="开仓保护·快照回灌",
            )
        tp_pxs = list(self.tv_tps or [0.0, 0.0, 0.0])
        # 开仓后 current_sl 必须是 TV 硬止损，绝不能写成成本价（否则会被当成雷达）
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
        self._radar_notify_pending = False
        self._radar_trigger_gate = ""
        self._radar_armed_after_tp1 = False
        self._radar_handoff_done = False
        self._ws_tp1_fill_hint = False
        self._ws_tp_fill_levels = set()
        self._shield_handoff_notified = False
        self._post_open_radar_block_until = time.time() + POST_OPEN_RADAR_BLOCK_SEC
        self._open_settled_qty = float(qty or 0)
        self.initial_qty = float(qty or 0)
        self.watched_qty, self.watched_entry, self.monitoring = qty, entry_price, True
        self._save_state()

        self._ensure_price_ws()

        verified = self._wait_verify(
            lambda: self._verify_position(self.current_side), retries=8, delay=0.7,
        )
        if not verified:
            time.sleep(1.0)
            verified = self._verify_position(self.current_side)
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

            # 开仓后只清 TP 残留；硬止损由后续统一同步挂上（禁先撤净 STOP 裸仓窗口）
            self._cancel_all_tp_limit_orders(max_rounds=3)
            time.sleep(0.4)
            # 再用核实 entry 补一次 TP（防 TV 空价时首轮用错价）
            entry_live = float(verified["entry_price"] or entry_price)
            self._ensure_tp123_prices_from_tv(entry_live)
            # 穿市价 TP 禁止挂出（主因：开完秒平成蚂蚁仓）
            mark_px = float(
                binance_client.get_current_price(self.symbol) or entry_live or 0
            )
            # 开仓瞬间禁止假成交记账；强制推离穿价后再挂
            self.tp_levels_consumed = []
            self._force_tps_unmarketable(mark_px, entry_live)
            tp_pxs = list(self.tv_tps or [0.0, 0.0, 0.0])
            self._enforce_pre_tp1_radar_standby(
                live_qty, verified["entry_price"], source="开仓保护",
            )
            self._nuclear_fail_streak = 0
            # 先挂 TP123，再挂 closePosition 硬止损（不占 reduceOnly 额度）
            # 宽限期在首轮挂单后再开，避免 grace 挡住 force=False 硬止损维护
            self._enforce_defense_alignment(
                live_qty, verified["entry_price"],
                dynamic_sl=None, reason="开仓后防线对齐", rounds=3,
                recover_mode=False,
            )
            # 开仓后硬闸：无论 TP 是否齐，强制 TV 硬止损
            hung = binance_client.find_protective_stop_prices(self.symbol)
            tv_target = self._tv_hard_sl_target(verified["entry_price"])
            bad = [
                p for p in hung
                if (
                    tv_target > 0
                    and abs(float(p) - tv_target) > SHIELD_STOP_TOLERANCE
                    and not self._is_valid_radar_sl(p)
                )
            ]
            self._sync_exchange_stop(
                live_qty, radar_sl=None,
                reason=(
                    "开仓后强制TV硬止损" if (bad or not hung or tv_target <= 0)
                    else "开仓后确认TV硬止损"
                ),
                force=True,
            )
            audit = self._wait_defense_settled(live_qty, retries=10, delay=0.9)
            matched, expected = audit["matched_full"], audit["expected"]
            curr_px = binance_client.get_current_price(self.symbol) or entry_price
            if expected <= 0:
                self._ensure_tp123_prices_from_tv(verified["entry_price"])
                self._clear_spurious_tp_consumed_if_full_size(
                    live_qty, source="开仓后 expected=0",
                )
                expected = self._expected_tp_count()
                logger.warning(
                    f"⚠️ 开仓后 expected TP=0 → 再合成 → expected={expected} tps={self.tv_tps}"
                )
                tp_pxs = list(self.tv_tps or tp_pxs)
            if expected > 0 and matched < expected:
                logger.warning(
                    f"⚠️ 开仓首轮 TP 仅 {matched}/{expected} → 追加补挂/核武"
                )
                # 先补挂；核武受刹车约束（全缺时无视刹车）
                self._patch_missing_tp_levels(live_qty)
                time.sleep(0.8)
                audit = self._audit_tp_levels(live_qty)
                matched, expected = audit["matched_full"], audit["expected"]
                if matched < expected:
                    audit = self._nuclear_realign_tp(
                        live_qty, verified["entry_price"], dynamic_sl=None, rounds=2,
                    )
                self._maintain_hard_shield(live_qty, curr_px, force=True)
                audit = self._wait_defense_settled(live_qty, retries=8, delay=0.8)
                matched, expected = audit["matched_full"], audit["expected"]
            hung_final = binance_client.find_protective_stop_prices(self.symbol)
            if not hung_final:
                logger.error(f"🚨 [{self.symbol}] 开仓终检：盘口无硬止损 → 再强制补挂")
                self._sync_exchange_stop(
                    live_qty, radar_sl=None, reason="开仓终检裸仓补挂", force=True,
                )
                hung_final = binance_client.find_protective_stop_prices(self.symbol)
                if not hung_final:
                    dingtalk.report_system_alert(
                        f"开仓后裸仓无硬止损 [{self.symbol}]",
                        f"{self.current_side} {live_qty} {self.unit_label} @ "
                        f"{verified['entry_price']:.2f} | TP {matched}/{expected} | "
                        f"目标TV硬止损@{(tv_target or 0):.2f} | 将撤销开仓防裸奔",
                    )
                    # 自查 7.6：硬止损失败 → 撤销开仓，不持仓裸奔
                    self._emergency_flatten_naked_open(
                        "硬止损失败·撤销开仓防裸奔",
                    )
                    return
            # 终检：应有 TP 却不齐 / 无硬止损 → 强制闭环挂齐（清假成交+推离+重挂）
            if (expected > 0 and matched < expected) or not hung_final:
                logger.error(
                    f"🚨 [{self.symbol}] 开仓终检未齐 TP {matched}/{expected} "
                    f"stop={hung_final} → 强制闭环挂防线"
                )
                audit, hung_final = self._force_hang_open_defenses(
                    live_qty, verified["entry_price"], rounds=3,
                )
                matched = int(audit.get("matched_full") or 0)
                expected = int(audit.get("expected") or self._expected_tp_count() or 0)
                tp_pxs = list(self.tv_tps or tp_pxs)
                if not hung_final:
                    self._emergency_flatten_naked_open(
                        "硬止损失败·强制闭环后仍无STOP·撤开仓",
                    )
                    return
                try:
                    self._call_dingtalk(
                        dingtalk.report_system_alert,
                        title=f"开仓终检·强制补防线 [{self.symbol}]",
                        detail=(
                            f"{self.current_side} {live_qty} | TP现 {matched}/{expected} "
                            f"| stop={hung_final} | 价 {list(self.tv_tps or [])} | "
                            f"已清假成交+推离穿价+强制挂TP123+TV硬止损"
                        ),
                        level="警告",
                        suggestion="核对盘口 TP123 与 closePosition；勿反复重启核武",
                    )
                except Exception:
                    pass
            # 首轮挂单完成后再开宽限；仅防线齐才标 align_ok
            self._sentinel_grace_until = time.time() + SENTINEL_GRACE_AFTER_OPEN_SEC
            self._reconcile_open_qty_vs_tp123(live_qty, source="开仓终检")
            if expected > 0 and matched >= expected and hung_final:
                self._mark_defense_align_ok()
            else:
                logger.warning(
                    f"⚠️ 开仓防线未齐 TP {matched}/{expected} stop={hung_final} "
                    f"→ 不标 align_ok，哨兵继续补"
                )
            verify_note = (
                f"{budget_note} | " if budget_note else ""
            ) + (
                f"持仓 {live_qty} {self._unit()} @ {verified['entry_price']:.2f} | "
                f"限价止盈 {matched}/{expected} 档 | {self._format_audit_summary(audit)} | "
                f"{self._tv_field_source_note(getattr(self, '_last_tv_field_sources', {}))}"
            )
            hard_sl_px = float(
                self._vps_hard_sl_target(verified["entry_price"]) or vps_sl or 0
            )
            act_px = float(self._radar_activation_price() or 0)
            if hard_sl_px > 0:
                verify_note += f" | TV硬止损@{hard_sl_px:.2f}"
            if act_px > 0:
                ratio = self._radar_activation_ratio()
                verify_note += (
                    f" | 雷达待命激活线@{act_px:.2f}"
                    f"(距TP1剩{(1 - ratio) * 100:.0f}%)"
                )
            if hung_final:
                verify_note += f" | 盘口保护STOP@{[round(float(p), 2) for p in hung_final]}"
            if target_qty > 0 and live_qty > target_qty * OPEN_OVERSIZE_RATIO:
                verify_note += f" | ⚠️ 超标目标 {target_qty} {self._unit()}"
            if self._should_activate_shield(curr_px):
                shield_ok = self._maintain_hard_shield(live_qty, curr_px, force=True)
                stop_px = self._shield_stop_price(verified["entry_price"])
                sl_note = format_vps_hard_sl_note(
                    self.current_side, verified["entry_price"],
                    float(getattr(self, "open_atr", None) or self.current_atr or 30),
                    int(getattr(self, "open_regime", None) or self.regime or 3),
                    tv_sl_ref=getattr(self, "tv_sl_ref", 0),
                ) if getattr(self, "tv_sl", 0) > 0 else "TV硬止损待计算"
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
            open_verified = (
                expected > 0 and matched >= expected and bool(hung_final)
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
                verified=open_verified,
                principal_balance=self.sizing_principal or binance_client.get_principal_wallet_balance(),
                margin_pct=float(
                    (sizing_meta or {}).get("risk_pct")
                    or (sizing_meta or {}).get("effective_risk_pct")
                    or getattr(self, "tv_risk_pct", 0)
                    or 0
                ) / 100.0,
                margin_usdt=float((sizing_meta or {}).get("order_amount", 0) or 0),
                leverage=float(
                    (sizing_meta or {}).get("leverage")
                    or getattr(self, "tv_sizing_leverage", 0)
                    or 0
                ),
                vps_sizing_meta=sizing_meta,
                tv_field_sources=getattr(self, "_last_tv_field_sources", {}),
                symbol=self.symbol,
                unit_label=self.unit_label,
                hard_sl_px=hard_sl_px,
                radar_act_px=act_px,
                radar_act_ratio=self._radar_activation_ratio(),
            )
            if expected > 0 and matched < expected:
                self._open_tp_unconfirmed = True
                hint = (
                    "硬止损已改 closePosition，不占 reduceOnly | 哨兵将接力补挂 TP | "
                    "请查 logs/binance_brain.log"
                )
                dingtalk.report_system_alert(
                    f"开仓后限价止盈未全部挂上 [{self.symbol}]",
                    f"{self.current_side} {live_qty} {self.unit_label} | 仅 {matched}/{expected} 档 | "
                    f"{self._format_audit_summary(audit)} | {hint}",
                )
            if not hung_final:
                self._open_tp_unconfirmed = True
        else:
            logger.warning("开仓钉钉跳过：实盘持仓核查未通过 → 延迟再探并尽量挂防线")
            dingtalk.report_system_alert(
                f"开仓后持仓核查失败 [{self.symbol}]",
                f"{self.current_side} 目标 qty={qty} entry≈{entry_price:.2f} | "
                f"REST 未核实持仓，正在延迟再探并挂 TP123+TV硬止损",
            )
            # 竞态：市价已成但 REST 滞后 → 再探一轮，能探到就挂齐防线，禁止裸奔
            time.sleep(1.2)
            late = self._get_active_position()
            if late and float(late.get("size") or 0) > 0:
                late_qty = float(late["size"])
                late_entry = float(late.get("entry_price") or entry_price or 0)
                self.current_side = late.get("side") or self.current_side
                self.watched_qty = late_qty
                self.watched_entry = late_entry
                self.initial_qty = late_qty
                self._open_settled_qty = late_qty
                self.monitoring = True
                self._save_state()
                try:
                    self._ensure_tp123_prices_from_tv(late_entry)
                    self._refresh_vps_hard_sl(
                        entry=late_entry, side=self.current_side,
                        regime=int(getattr(self, "open_regime", None) or self.regime or 3),
                        atr=float(getattr(self, "open_atr", None) or self.current_atr or 30),
                        tv_sl_ref=getattr(self, "tv_sl_ref", 0) or None,
                        source="开仓滞后核实",
                    )
                    self._enforce_defense_alignment(
                        late_qty, late_entry, dynamic_sl=None,
                        reason="开仓滞后核实·补挂TP123", rounds=3, recover_mode=False,
                    )
                    self._sync_exchange_stop(
                        late_qty, radar_sl=None,
                        reason="开仓滞后核实·强制TV硬止损", force=True,
                    )
                    hung_late = binance_client.find_protective_stop_prices(self.symbol)
                    if not hung_late:
                        dingtalk.report_system_alert(
                            f"开仓滞后核实仍无硬止损 [{self.symbol}]",
                            f"{self.current_side} {late_qty} @ {late_entry:.2f} | 请人工挂止损",
                        )
                    else:
                        logger.warning(
                            f"✅ [{self.symbol}] 开仓滞后核实成功 → "
                            f"已补挂 TP123+TV硬止损 stop={hung_late}"
                        )
                except Exception as e:
                    logger.error(f"开仓滞后核实补挂失败: {e}")
            self._sentinel_grace_until = time.time() + SENTINEL_GRACE_AFTER_OPEN_SEC

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
        激活线前：强制雷达待命，止损仅 TV 硬止损。
        交棒成功 / 雷达已武装：坚决不干预（止损只前进不回撤）。
        """
        curr_px = float(curr_px or binance_client.get_current_price(self.symbol) or 0)
        if (
            self._radar_legitimately_armed(live_qty, curr_px)
            or self._is_radar_active()
            or bool(getattr(self, "_radar_handoff_done", False))
        ):
            return False
        if curr_px > 0 and self._radar_ready_to_handoff(curr_px, live_qty):
            return False

        tv = float(getattr(self, "tv_sl", 0) or 0)
        entry = float(self.watched_entry or 0)
        changed = False

        consumed = list(getattr(self, "tp_levels_consumed", []) or [])
        if consumed:
            fake = [
                lv for lv in consumed
                if not self._tp_filled_verified(lv, live_qty, curr_px)
                and not self._price_reached_tp_zone(lv, curr_px, live_only=True)
            ]
            if fake:
                self.tp_levels_consumed = [lv for lv in consumed if lv not in fake]
                changed = True
                logger.info(
                    f"📡 [{source or '雷达'}] 清除伪 TP{fake} 标记 "
                    f"(未交棒·未实盘成交且现价未过)"
                )

        # 仅未交棒时允许把误挂保本拉回 tv_sl
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
        if getattr(self, "_radar_notify_pending", False):
            self._radar_notify_pending = False
            changed = True
        if getattr(self, "_radar_trigger_gate", ""):
            self._radar_trigger_gate = ""
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
                f"📡 [{source or '雷达'}] 激活线前待命·TV硬止损 | "
                f"tv_sl={tv:.2f} | entry={entry:.2f} | "
                f"{format_radar_activation_ratios_label()}"
            )
        return changed

    def _disarm_premature_radar(self, live_qty=None, curr_px=0.0, source=""):
        """
        激活线前：纠正过早保本线 / 清伪TP标记 → 恢复 TV 硬止损。
        交棒成功或雷达已武装后：坚决只前进不回撤（禁止「雷达解除」）。
        清伪TP 与 回撤止损 解耦：已交棒时只清标记，不动 SL。
        """
        live_qty = float(live_qty or self.watched_qty or 0)
        curr_px = float(curr_px or binance_client.get_current_price(self.symbol) or 0)
        entry = float(self.watched_entry or 0)
        tv = float(getattr(self, "tv_sl", 0) or 0)
        ratio_label = format_radar_activation_ratios_label()

        # 清伪 TP 记账（与是否撤雷达无关）
        stale = list(getattr(self, "tp_levels_consumed", []) or [])
        fake = [
            lv for lv in stale
            if not self._tp_filled_verified(lv, live_qty, curr_px)
            and not self._price_reached_tp_zone(lv, curr_px, live_only=True)
        ]
        if fake:
            self.tp_levels_consumed = [lv for lv in stale if lv not in fake]
            self._save_state()
            logger.info(
                f"📡 [{self.symbol}] [{source or '雷达'}] 清除伪TP标记 {fake} "
                f"（不影响已交棒雷达止损）"
            )

        radar_locked = (
            bool(getattr(self, "_radar_handoff_done", False))
            or self._is_radar_active()
            or self._radar_legitimately_armed(live_qty, curr_px)
        )
        if radar_locked:
            # 铁律：挂上雷达后只能前进，禁止解除/回撤到 tv_sl
            return False
        if curr_px > 0 and self._radar_ready_to_handoff(curr_px, live_qty):
            return False

        # 仅激活线前：若 state 里误塞了保本线，拉回 TV 硬止损
        premature_be = False
        if entry > 0 and self.current_sl:
            if self.current_side == "LONG" and float(self.current_sl) > entry + 0.01:
                premature_be = True
            elif self.current_side == "SHORT" and float(self.current_sl) < entry - 0.01:
                premature_be = True
        if not premature_be:
            return False

        self._radar_activation_notified = False
        self._radar_notify_pending = False
        self._radar_trigger_gate = ""
        self._shield_handoff_notified = False
        self._radar_stage_last = 0
        self._radar_armed_after_tp1 = False
        self._radar_handoff_done = False
        self._ws_tp1_fill_hint = False
        self._sanitize_vps_hard_sl_ledger(source=source or "纠正过早保本")
        if tv > 0:
            self.current_sl = tv
        self._save_state()
        logger.warning(
            f"📡 [{self.symbol}] [{source or '雷达'}] 激活线前过早保本纠正 "
            f"→ 恢复 tv_sl={tv:.2f} | entry={entry:.2f} | 规则：{ratio_label}"
        )
        dingtalk.report_system_alert(
            f"过早保本纠正·恢复TV硬止损 [{self.symbol}]",
            f"{self.current_side} {live_qty} {self._unit()} @ {entry:.2f} | "
            f"激活线前误挂保本已纠正 | tv_sl={tv:.2f} | "
            f"规则：档位激活线({ratio_label})或TP1成交后才启雷达；"
            f"交棒后止损只前进不回撤",
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

    def _radar_regime_locked(self):
        """雷达参数一律用开仓档位锁定，禁止中途 TV UPDATE 改比例。"""
        return int(getattr(self, "open_regime", 0) or self.regime or 3)

    def _radar_trail_step(self):
        return get_radar_trail_step(self._radar_regime_locked())

    def _radar_breath_atr(self):
        return get_radar_breath_atr(self._radar_regime_locked())

    def _radar_stage(self, curr_px):
        """
        雷达 5 阶段（适度追随，按开仓档位步进）：
        0=激活线前硬止损 · 1=激活成本保本 · 2=TP1→TP2 步进 · 3=达TP2 · 4=TP2→TP3 步进 · 5=达TP3
        探针=现价与 best 有利侧；TP 已记账成交也推进阶段（头寸已减，锁剩余利润）。
        """
        live_qty = float(self.watched_qty or 0)
        if not self._radar_legitimately_armed(live_qty, curr_px):
            return 0
        latched = int(getattr(self, "_radar_stage_last", 0) or 0) >= 1
        if curr_px <= 0 or not self.watched_entry:
            return max(1, latched) if latched else 1

        best = float(self.best_price or 0)
        if self.current_side == "LONG":
            probe = max(float(curr_px), best) if best > 0 else float(curr_px)
        else:
            probe = min(float(curr_px), best) if best > 0 else float(curr_px)

        tp1 = float(self.tv_tps[0] or 0) if self.tv_tps else 0.0
        tp2 = float(self.tv_tps[1] or 0) if len(self.tv_tps) > 1 else 0.0
        tp3 = float(self.tv_tps[2] or 0) if len(self.tv_tps) > 2 else 0.0
        is_long = self.current_side == "LONG"
        step = self._radar_trail_step()  # R1=0.35 … R4=0.20（取代旧固定 50%）
        stage = 1

        # TP1 价到或已记账成交 → 可进 TP1→TP2 轨道
        tp1_hit = self._tp_level_consumed(1) or (
            tp1 > 0 and (
                (is_long and probe >= tp1) or (not is_long and probe <= tp1)
            )
        )
        if tp1_hit:
            stage = max(stage, 1)

        if tp1 > 0 and tp2 > 0:
            p12 = self._segment_progress(probe, tp1, tp2)
            if p12 >= step or self._tp_level_consumed(2):
                stage = max(stage, 2)
        if tp2 > 0:
            if (
                self._tp_level_consumed(2)
                or (is_long and probe >= tp2)
                or (not is_long and probe <= tp2)
            ):
                stage = max(stage, 3)
        if tp2 > 0 and tp3 > 0:
            p23 = self._segment_progress(probe, tp2, tp3)
            if p23 >= step or self._tp_level_consumed(3):
                stage = max(stage, 4)
        if tp3 > 0:
            if (
                self._tp_level_consumed(3)
                or (is_long and probe >= tp3)
                or (not is_long and probe <= tp3)
            ):
                stage = max(stage, 5)
        return stage

    def _radar_stage_label(self, stage):
        return RADAR_STAGE_LABELS.get(int(stage or 0), f"阶段{stage}")

    def _radar_segment_progress_probe(self, curr_px):
        """当前所处 TP 段内进度 0~1（供步进门限，防每 tick 撤挂）。"""
        best = float(self.best_price or 0)
        if self.current_side == "LONG":
            probe = max(float(curr_px or 0), best) if best > 0 else float(curr_px or 0)
        else:
            probe = min(float(curr_px or 0), best) if best > 0 else float(curr_px or 0)
        tp1 = float(self.tv_tps[0] or 0) if self.tv_tps else 0.0
        tp2 = float(self.tv_tps[1] or 0) if len(self.tv_tps) > 1 else 0.0
        tp3 = float(self.tv_tps[2] or 0) if len(self.tv_tps) > 2 else 0.0
        stage = int(self._radar_stage(curr_px) or 0)
        if stage <= 1 and tp1 > 0:
            return self._tp1_direction_progress(probe)
        if stage == 2 and tp1 > 0 and tp2 > 0:
            return self._segment_progress(probe, tp1, tp2)
        if stage == 3 and tp2 > 0:
            return 1.0
        if stage == 4 and tp2 > 0 and tp3 > 0:
            return self._segment_progress(probe, tp2, tp3)
        if stage >= 5:
            return 1.0
        return 0.0

    def _compute_radar_sl_for_stage(self, stage, curr_px=0.0):
        """
        适度追随止损：
        阶段1=成本±0.1%；阶段2+= best ± ATR×档位呼吸（R1=1.0 … R4=0.5）。
        已删除旧 RADAR_STAGE_ATR_MULT 紧追（0.3 极限）逻辑。
        """
        stage = int(stage or 0)
        if stage <= 0:
            return None
        entry = float(self.watched_entry or 0)
        atr = float(
            getattr(self, "open_atr", None) or self.current_atr or 30.0
        )
        best = float(self.best_price or entry)
        if stage == 1:
            cushion = entry * RADAR_STAGE_COST_BUFFER_PCT
            if self.current_side == "LONG":
                return round(entry + cushion, 2)
            if self.current_side == "SHORT":
                return round(entry - cushion, 2)
            return None
        breath = self._radar_breath_atr()  # 唯一呼吸源，无旧阶段紧追表
        if self.current_side == "LONG":
            return round(best - atr * breath, 2)
        if self.current_side == "SHORT":
            return round(best + atr * breath, 2)
        return None

    def _refresh_radar_state_on_recover(self, curr_px, entry):
        """
        重启/接管雷达：
        - 现价达激活线 或 TP1 已过价 → 尝试交棒/恢复追随（不要求曾交棒）
        - 否则TV硬止损待命
        - 禁止无缘无故平仓；禁止用陈旧 best 误触
        """
        if curr_px <= 0 or not entry:
            return

        if self.best_price == 0.0:
            self.best_price = entry
        if self.current_side == "LONG":
            self.best_price = max(float(self.best_price or 0), float(curr_px))
        else:
            bp = float(self.best_price or 0)
            self.best_price = min(bp, float(curr_px)) if bp > 0 else float(curr_px)

        live_at_act = self._radar_ready_to_handoff(curr_px, self.watched_qty)
        # 接管：现价已过 TP1 也视为可启雷达
        if not live_at_act and self._price_reached_tp_zone(1, curr_px, live_only=True):
            live_at_act = True
            if 1 not in (getattr(self, "tp_levels_consumed", []) or []):
                self._mark_tp_levels_consumed([1])
        ratio = self._radar_activation_ratio()
        prog = self._tp1_direction_progress(curr_px)

        if not live_at_act:
            # 铁律：已交棒/已武装 → 价格回撤也不撤雷达，止损只前进
            if (
                bool(getattr(self, "_radar_handoff_done", False))
                or self._is_radar_active()
            ):
                logger.info(
                    f"📡 [{self.symbol}] 重启：现价回撤激活线但雷达已交棒 → "
                    f"保持 SL={float(self.current_sl or 0):.2f} 不回撤 "
                    f"(朝TP1 {prog:.0%}/{ratio:.0%})"
                )
                return
            if self.current_sl == 0.0 and float(getattr(self, "tv_sl", 0) or 0) > 0:
                self.current_sl = float(self.tv_sl)
            elif float(getattr(self, "tv_sl", 0) or 0) > 0:
                self.current_sl = float(self.tv_sl)
            self._radar_stage_last = 0
            self._radar_armed_after_tp1 = False
            self._radar_handoff_done = False
            self._ws_tp1_fill_hint = False
            logger.info(
                f"📡 [{self.symbol}] 重启雷达待命: 阶段0 | 保留 TV硬止损 "
                f"(现价未达R{self._radar_regime_locked()}激活线"
                f"{ratio:.0%}且TP1未过价(朝TP1 {prog:.0%}) · "
                f"{format_radar_activation_ratios_label()})"
            )
            return

        stage1 = self._compute_radar_sl_for_stage(1, curr_px)
        if stage1 is None or not self._ideal_radar_sl_is_safe(curr_px, stage1):
            # 已交棒则不因「距市不足」回撤到 tv_sl
            if (
                bool(getattr(self, "_radar_handoff_done", False))
                or self._is_radar_active()
            ):
                logger.info(
                    f"📡 [{self.symbol}] 重启：保本距市不足但已交棒 → 保持现有雷达SL"
                )
                return
            self._radar_handoff_done = False
            self._radar_armed_after_tp1 = False
            self._radar_stage_last = 0
            if float(getattr(self, "tv_sl", 0) or 0) > 0:
                self.current_sl = float(self.tv_sl)
            logger.info(
                f"📡 [{self.symbol}] 重启：现价达雷达门槛，但理想保本距市不足 → "
                f"暂留TV硬止损，哨兵再交棒"
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
            f"📡 [{self.symbol}] 重启/接管雷达激活: 阶段{stage} "
            f"{self._radar_stage_label(stage)} | "
            f"best={self.best_price:.2f} | SL={self.current_sl:.2f} | "
            f"朝TP1 {prog:.0%}/{ratio:.0%} | 已过档 "
            f"{getattr(self, 'tp_levels_consumed', [])}"
        )

    def _ensure_price_ws(self):
        """雷达/哨兵：公开行情 WS（mark@1s）+ 私有 User Data Stream（持仓/订单）"""
        binance_client.start_public_price_ws(
            self.symbol, on_tick=self._on_mark_price_tick,
        )
        binance_client.start_user_data_ws(
            self.symbol, on_event=self._on_user_data_ws_event,
        )

    def _on_mark_price_tick(self, symbol, price):
        """
        WS markPrice@1s（最快盯价）：朝档位激活线接近 / 已达线 / 已交棒 → 脉冲哨兵。
        不在 WS 线程挂单（防竞态）；只置位，由哨兵串行交棒/追随。
        """
        if str(symbol or "").upper() != self.symbol.upper():
            return
        if not self.monitoring or getattr(self, "_open_in_progress", False):
            return
        px = float(price or 0)
        if px <= 0:
            return
        # 更新 best，供阶段锁利（精密追随用）
        if self.current_side == "LONG":
            if px > float(self.best_price or 0):
                self.best_price = px
        elif self.current_side == "SHORT":
            bp = float(self.best_price or 0)
            if bp <= 0 or px < bp:
                self.best_price = px

        armed = self._radar_legitimately_armed(self.watched_qty, px)
        near_act = self._price_reached_radar_activation(px, live_only=True)
        tp1_hint = bool(
            getattr(self, "_ws_tp1_fill_hint", False)
            or self._tp_level_consumed(1)
        )
        # 接近激活线（走过档位比例的 90%）就加速盯，护本金必须快
        ratio = float(self._radar_activation_ratio() or 0)
        prog = float(self._tp1_direction_progress(px) or 0)
        approaching = (
            ratio > 0
            and prog >= ratio * float(RADAR_WS_APPROACH_RATIO)
            and not near_act
        )
        if armed or near_act or tp1_hint or approaching:
            self._ws_defense_pulse = True
            self._ws_fast_poll = True
        if near_act or armed or tp1_hint:
            self._radar_work_urgent = True

    def _on_user_data_ws_event(self, event_type, data):
        """WS 事件脉冲：本品种 LIMIT 成交 → 提示 TP 档并脉冲哨兵对账（禁止仅看漏挂）"""
        et = str(event_type or "")
        if et == "ORDER_TRADE_UPDATE":
            o = (data or {}).get("o") or {}
            sym = str(o.get("s") or "").upper()
            if sym and sym != self.symbol.upper():
                return
        if et in ("ACCOUNT_UPDATE", "ORDER_TRADE_UPDATE", "CONDITIONAL_ORDER_TRIGGER",
                  "listenKeyExpired"):
            self._ws_defense_pulse = True
            self._ws_fast_poll = True
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
                    if reduce_only and px > 0 and self.tv_tps:
                        matched = []
                        for lv in (1, 2, 3):
                            tp_px = float(self.tv_tps[lv - 1] or 0)
                            if tp_px <= 0:
                                continue
                            tol = max(1.5, tp_px * 0.0012)
                            if abs(px - tp_px) <= tol:
                                matched.append(lv)
                                levels = getattr(self, "_ws_tp_fill_levels", None)
                                if not isinstance(levels, set):
                                    levels = set()
                                levels.add(lv)
                                self._ws_tp_fill_levels = levels
                        if matched:
                            if 1 in matched:
                                self._ws_tp1_fill_hint = True
                                self._post_open_radar_block_until = 0.0
                            logger.info(
                                f"📡 [{self.symbol}] UD-WS TP{matched} 限价成交提示 "
                                f"@ {px:.2f} → 脉冲头寸对账（禁当漏挂补挂）"
                            )
                elif otype in ("STOP", "STOP_MARKET") and status in (
                    "NEW", "PARTIALLY_FILLED", "FILLED",
                ):
                    self._tv_sl_missing_alerted = False
            logger.debug(f"📡 [{self.symbol}] UD-WS 脉冲 {et}")

    def _tp1_distance(self):
        entry = float(self.watched_entry or 0)
        if self.tv_tps and float(self.tv_tps[0] or 0) > 0 and entry > 0:
            return abs(float(self.tv_tps[0]) - entry)
        atr = float(
            getattr(self, "open_atr", None) or self.current_atr or 30.0
        )
        # 禁止 dist=0（激活线=成本）→ 开仓即误触雷达交棒
        return max(atr * 1.5, entry * 0.005 if entry > 0 else atr * 1.5)

    def _radar_activation_ratio(self):
        """开仓档位锁定的雷达启动比例（相对 entry→TP1）。"""
        regime = int(getattr(self, "open_regime", 0) or self.regime or 3)
        return get_radar_activation_ratio(regime)

    def _radar_activation_price(self):
        """价触此价 → 可交棒雷达保本。"""
        entry = float(self.watched_entry or 0)
        if entry <= 0:
            return 0.0
        tp1_dist = self._tp1_distance()
        ratio = self._radar_activation_ratio()
        if self.current_side == "LONG":
            return entry + tp1_dist * ratio
        if self.current_side == "SHORT":
            return entry - tp1_dist * ratio
        return 0.0

    def _price_reached_radar_activation(self, curr_px, live_only=False):
        """
        主判：是否达档位激活线。
        live_only=True（交棒/重启/待命闸）：只用现价，禁止历史 best 误触保本。
        live_only=False：现价或 best（仅用于进度展示等非挂单路径）。
        """
        curr_px = float(curr_px or 0)
        if curr_px <= 0 or not self.watched_entry:
            return False
        act = self._radar_activation_price()
        if act <= 0:
            return False
        if self.current_side == "LONG":
            if curr_px >= act:
                return True
            if live_only:
                return False
            best = float(self.best_price or 0)
            return best > 0 and best >= act
        if self.current_side == "SHORT":
            if curr_px <= act:
                return True
            if live_only:
                return False
            best = float(self.best_price or 0)
            return best > 0 and best <= act
        return False

    def _tp1_direction_progress(self, curr_px):
        """0~1：现价朝 TP1 价位的推进比例"""
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
        """交棒 STOP 核实成功后才允许雷达追踪（不再要求三重）。"""
        if getattr(self, "_open_in_progress", False):
            return False
        return bool(getattr(self, "_radar_handoff_done", False))

    def _effective_radar_stage(self, curr_px):
        """雷达阶段：已武装后只升不降"""
        stage = self._radar_stage(curr_px)
        if not self._radar_legitimately_armed(self.watched_qty, curr_px):
            return 0
        latched = int(getattr(self, "_radar_stage_last", 0) or 0)
        return max(stage, latched, 1)

    def _radar_activation_progress(self, curr_px):
        """0~1：激活线前=朝激活线推进；交棒后=5阶段进度"""
        if self._radar_legitimately_armed(self.watched_qty, curr_px) or self._is_radar_active():
            return min(1.0, self._effective_radar_stage(curr_px) / 5.0)
        ratio = self._radar_activation_ratio()
        if ratio <= 0:
            return 0.0
        return max(0.0, min(1.0, self._tp1_direction_progress(curr_px) / ratio))

    def _should_radar_trail(self, curr_px):
        """现价达档位激活线 / TP1已成交 / 已交棒 → 适度追随；否则只保留 VPS TV硬止损。"""
        if getattr(self, "_open_in_progress", False):
            return False
        if self._radar_legitimately_armed(self.watched_qty, curr_px):
            return True
        if self._is_radar_active():
            return True
        return self._radar_ready_to_handoff(curr_px, self.watched_qty)

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
        elif self.current_side == "SHORT":
            cur = float(self.current_sl or 0)
            # 空单：无止损或新止损更低（前进）才更新；禁止抬高回撤
            if cur <= 0 or new_sl < cur:
                logger.info(
                    f"📉 雷达止损预算刷新: {cur:.2f} → {new_sl:.2f} "
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
        """
        适度追随雷达：档位激活线交棒 → 按步进+呼吸 ATR 推升。
        推升前先对账 TP（头寸减+价到=成交）；closePosition 单槽不抢 TP。
        同价已挂 / 未跨步进 / 冷却中 → 禁止撤挂死循环。
        """
        if self._radar_placement_blocked(real_amt, curr_px, reason="trailing", silent=True):
            return False
        if not self._should_radar_trail(curr_px):
            return False
        real_amt = float(self._resolve_live_qty(real_amt) or 0)
        if real_amt <= 0:
            return False

        # 雷达移动伴随 TP 可能真实成交：先价到对账，再按剩余头寸追随
        try:
            self._reconcile_tp_consumed_from_live_qty(
                real_amt, curr_px, source="雷达移动前对账", notify=True,
            )
            pos = self._get_active_position()
            if pos and float(pos.get("size") or 0) > 0:
                real_amt = float(pos["size"])
                self.watched_qty = real_amt
        except Exception as e:
            logger.debug(f"雷达前TP对账跳过: {e}")

        if not self._is_radar_active():
            ratio = self._radar_activation_ratio()
            reg = self._radar_regime_locked()
            return self._perform_radar_handoff(
                real_amt, curr_px,
                reason=(
                    f"R{reg}激活线{ratio:.0%}"
                    f"(距TP1剩{(1 - ratio) * 100:.0f}%)·适度保本"
                ),
            )

        new_sl = self._compute_radar_sl()
        if new_sl is None:
            return False
        new_sl = self._clamp_radar_sl_for_market(curr_px, new_sl)
        if not new_sl or not self._can_safely_place_radar_sl(curr_px, new_sl):
            return False
        if not self._is_valid_radar_sl(new_sl):
            return False

        stage = self._effective_radar_stage(curr_px)
        seg_prog = float(self._radar_segment_progress_probe(curr_px) or 0)
        step = self._radar_trail_step()
        last_stage = int(getattr(self, "_last_radar_trail_stage", 0) or 0)
        last_prog = float(getattr(self, "_last_radar_trail_progress", 0) or 0)
        now = time.time()
        # 门限：阶段升级，或段内进度再跨一个步进；否则不动盘口
        stage_up = stage > last_stage
        prog_up = (seg_prog - last_prog) >= max(0.08, step * 0.45)
        if not stage_up and not prog_up and last_stage > 0:
            return False
        if (
            now - float(getattr(self, "_last_radar_trail_ts", 0) or 0)
            < RADAR_TRAIL_MIN_INTERVAL_SEC
            and not stage_up
        ):
            return False

        # 盘口已是目标价 → 只同步账本，禁止撤了再挂
        if self._has_stop_sl_near(new_sl, exclude_shield=False):
            self.current_sl = new_sl
            self._last_applied_exchange_sl = round(float(new_sl), 2)
            self._last_radar_trail_stage = stage
            self._last_radar_trail_progress = seg_prog
            self._radar_stage_last = max(
                int(getattr(self, "_radar_stage_last", 0) or 0), stage
            )
            return False

        min_step = max(0.3, float(curr_px or self.watched_entry or 0) * 0.00025)
        reg = self._radar_regime_locked()
        breath = self._radar_breath_atr()
        moved = False

        if self.current_side == "LONG":
            if new_sl > float(self.current_sl or 0) + min_step:
                old_sl = float(self.current_sl or 0)
                self.current_sl = new_sl
                self._save_state()
                sl_placed = self._realign_radar_defenses(
                    real_amt, self.watched_entry, new_sl,
                )
                self._log_radar_update(stage, old_sl, new_sl, "适度追随推升", curr_px)
                self._cancel_stale_tp_beyond_radar(new_sl, real_amt)
                self._report_radar_intervention(
                    real_amt, new_sl,
                    f"🚀 R{reg} 阶段{stage} {self._radar_stage_label(stage)} "
                    f"呼吸{breath:.2f}ATR 步进{step:.0%} → @{new_sl:.2f} "
                    f"(剩仓 {real_amt} {self._unit()})",
                    sl_placed=sl_placed,
                )
                moved = True
        else:
            if (
                float(self.current_sl or 0) >= float(self.watched_entry or 0)
                or new_sl < float(self.current_sl or 0) - min_step
            ):
                old_sl = float(self.current_sl or 0)
                self.current_sl = new_sl
                self._save_state()
                sl_placed = self._realign_radar_defenses(
                    real_amt, self.watched_entry, new_sl,
                )
                self._log_radar_update(stage, old_sl, new_sl, "适度追随下压", curr_px)
                self._cancel_stale_tp_beyond_radar(new_sl, real_amt)
                self._report_radar_intervention(
                    real_amt, new_sl,
                    f"🚀 R{reg} 阶段{stage} {self._radar_stage_label(stage)} "
                    f"呼吸{breath:.2f}ATR 步进{step:.0%} → @{new_sl:.2f} "
                    f"(剩仓 {real_amt} {self._unit()})",
                    sl_placed=sl_placed,
                )
                moved = True

        if moved:
            self._last_radar_trail_ts = now
            self._last_radar_trail_stage = stage
            self._last_radar_trail_progress = seg_prog
            self._radar_stage_last = max(
                int(getattr(self, "_radar_stage_last", 0) or 0), stage
            )
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
                            old_qty = float(self.watched_qty or 0)
                            material = self._is_material_qty_change(old_qty, real_amt)
                            # R4 TP1≈5% < 10% 门槛：仍必须走成交检测，禁止静默同步后当漏挂补挂
                            tp_like = (
                                real_amt < old_qty - 0.0005
                                and self._qty_reduction_looks_like_tp(
                                    old_qty, real_amt, curr_px,
                                )
                            )
                            if material or tp_like:
                                qty_changed = True
                                if real_amt <= DUST_QTY_ETH:
                                    self._purge_all_defense_orders_on_flat(
                                        "仓位归零·抢先撤TP123",
                                    )
                                self.watched_qty = real_amt
                                self.watched_entry = pos["entry_price"]
                                if tp_like and not material:
                                    logger.warning(
                                        f"🎯 [哨兵] 小额减仓疑似TP成交 "
                                        f"{old_qty}→{real_amt} "
                                        f"({self._qty_change_ratio(old_qty, real_amt):.1%} "
                                        f"< {QTY_ALIGN_MIN_PCT:.0%}门槛) → 强制成交对账"
                                    )
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
                                # 即使微漂也尝试减仓记账（防漏检后守护补挂）
                                if real_amt < old_qty - 0.0005:
                                    self._reconcile_tp_consumed_from_live_qty(
                                        real_amt, curr_px, source="哨兵微漂对账",
                                        notify=False,
                                    )
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
                            # 交棒成功但钉钉未发 → 每轮补发（修实盘「雷达启了无播报」）
                            self._flush_pending_radar_notify(real_amt, curr_px)
                            if (
                                self._price_reached_radar_activation(curr_px, live_only=True)
                                and not self._is_radar_active()
                                and self._scan_ticks % 5 == 0
                            ):
                                logger.info(
                                    f"📡 雷达待交棒: 现价已达激活线 "
                                    f"(朝TP1 {self._tp1_direction_progress(curr_px):.0%} "
                                    f"/ {self._radar_activation_ratio():.0%}) | "
                                    f"现价 {curr_px:.2f} | 轮询 {SENTINEL_POLL_RADAR}s"
                                )
                        elif curr_px <= 0:
                            continue
                    finally:
                        self._lock.release()
                except Exception as e:
                    logger.error(f"哨兵异常: {e}")
                if self.monitoring:
                    # WS 达激活线/交棒：几乎立即再跑；接近线：1.5s；否则 5~8s
                    if getattr(self, "_radar_work_urgent", False):
                        self._radar_work_urgent = False
                        self._ws_fast_poll = False
                        time.sleep(float(RADAR_WS_URGENT_SLEEP_SEC))
                    elif getattr(self, "_ws_fast_poll", False):
                        self._ws_fast_poll = False
                        time.sleep(1.5)
                    else:
                        time.sleep(self._sentinel_poll_sec(last_px))
        finally:
            self._sentinel_active = False

    def _rebuild_defenses(self, qty, entry, dynamic_sl=None, cancel_first=True):
        close_side = "SHORT" if self.current_side == "LONG" else "LONG"

        live_qty = self._resolve_live_qty(qty)
        if live_qty <= 0:
            logger.warning(f"重建防线跳过：交易所无可用持仓 (传入 {qty} ETH)")
            return 0

        if cancel_first:
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

        curr_px = float(binance_client.get_current_price(self.symbol) or 0)
        for lv in self._expected_tp_levels(live_qty):
            q, px = lv["qty"], lv["price"]
            if q > 0 and px > 0:
                # 已存在则跳过，避免重复叠单
                if self._has_tp_limit_at_price(px, tolerance=1.0):
                    logger.info(f"  ✓ TP{lv['level']} @ {px:.2f} 已在盘口，跳过")
                    placed += 1
                    continue
                # 接管/重启：现价已过 → 跳过不挂（禁 TP1 死循环）；开仓中禁止
                if (
                    not getattr(self, "_open_in_progress", False)
                    and (
                        getattr(self, "_takeover_price_skip", False)
                        or getattr(self, "_recover_in_progress", False)
                    )
                    and self._price_reached_tp_zone(
                        int(lv["level"]), curr_px, px, live_only=True,
                    )
                ):
                    logger.warning(
                        f"🧩 重建跳过 TP{lv['level']}：接管现价已达 "
                        f"(mark={curr_px:.2f}) → 只挂更远档"
                    )
                    self._mark_tp_levels_consumed([int(lv["level"])])
                    continue
                if self._tp_is_marketable(self.current_side, px, curr_px):
                    self._force_tps_unmarketable(curr_px, entry or self.watched_entry or 0)
                    tps = list(self.tv_tps or [])
                    idx = int(lv["level"]) - 1
                    px = float(tps[idx]) if 0 <= idx < len(tps) else 0.0
                    if px <= 0 or self._tp_is_marketable(self.current_side, px, curr_px):
                        logger.error(
                            f"🚨 重建仍穿价 TP{lv['level']} mark={curr_px:.2f} "
                            f"→ 再强制推离"
                        )
                        self._force_tps_unmarketable(
                            curr_px, entry or self.watched_entry or 0,
                        )
                        tps = list(self.tv_tps or [])
                        px = float(tps[idx]) if 0 <= idx < len(tps) else 0.0
                        if px <= 0 or self._tp_is_marketable(
                            self.current_side, px, curr_px
                        ):
                            logger.error(
                                f"🚨 拒绝挂穿价 TP{lv['level']}：多次推离失败"
                            )
                            continue
                    logger.warning(
                        f"⚠️ 重建 TP{lv['level']} 穿价已推离 → @{px:.2f}"
                    )
                # 现价已达该档：有减仓证据才记账；开仓路径推离后仍挂
                if self._may_mark_tp_filled_missing_limit(
                    int(lv["level"]), live_qty, curr_px, tp_px=px,
                ):
                    logger.warning(
                        f"🧩 重建跳过 TP{lv['level']}：价到+限价无+减仓=已成交"
                    )
                    self._mark_tp_levels_consumed([int(lv["level"])])
                    continue
                if (
                    self._price_reached_tp_zone(int(lv["level"]), curr_px, px)
                    and not self._has_tp_limit_at_price(px)
                    and not getattr(self, "_takeover_price_skip", False)
                    and not getattr(self, "_recover_in_progress", False)
                ):
                    logger.warning(
                        f"🧩 重建 TP{lv['level']} 现价已近但无减仓 → 推离后挂"
                    )
                    self._force_tps_unmarketable(
                        curr_px, entry or self.watched_entry or 0,
                    )
                    tps = list(self.tv_tps or [])
                    idx = int(lv["level"]) - 1
                    px = float(tps[idx]) if 0 <= idx < len(tps) else 0.0
                    if px <= 0:
                        continue
                res = binance_client.place_limit_order(
                    close_side, q, px, symbol=self.symbol, reduce_only=True,
                )
                if res:
                    placed += 1
                else:
                    logger.error(
                        f"❌ 挂 TP{lv['level']} 失败 {q} @ {px:.2f} {self.symbol}"
                    )
                time.sleep(0.35)

        self._maintain_hard_shield(
            live_qty, None, force=False, radar_sl=dynamic_sl,
        )
        return placed

    def _emergency_flatten_naked_open(self, reason="硬止损失败·撤销开仓防裸奔"):
        """
        开仓后硬止损挂不上 → 立即市价平掉，禁止裸仓持有。
        （自查清单 7.6：硬止损挂单失败，开仓单自动撤销）
        """
        logger.error(f"🚨 [{self.symbol}] {reason} → 强制市价撤仓")
        try:
            self._call_dingtalk(
                dingtalk.report_system_alert,
                title=f"硬止损失败·已撤开仓 [{self.symbol}]",
                detail=(
                    f"{self.current_side} 硬止损未挂上 → 已市价平仓防裸奔 | {reason}"
                ),
                level="紧急",
                suggestion="检查 TV tv_sl 是否有效；勿人工重开裸仓",
            )
        except Exception:
            pass
        ok = self._close_all(
            reason=reason,
            reset_state=True,
            close_meta={"tv_reason": reason, "exit_source": "naked_sl_abort"},
        )
        return bool(ok)

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
            # 同品种其它 worker 已接管：本进程仍须启动哨兵，禁止漏盯实盘
            logger.warning(
                f"🔄 [{self.symbol}] 跳过重复接管进程，仍启动哨兵巡检实盘"
            )
            self.monitoring = True
            self._ensure_sentinel_running_quiet()
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
                    self._radar_activation_notified = bool(
                        s.get("radar_activation_notified", False)
                    )
                    self._radar_notify_pending = bool(
                        s.get("radar_notify_pending", False)
                    )
                    self._radar_trigger_gate = str(
                        s.get("radar_trigger_gate", "") or ""
                    )
                    self._shield_handoff_notified = bool(
                        s.get("shield_handoff_notified", False)
                    )
                    # 交棒已成功但钉钉未记 → 强制补发队列（修实盘「雷达启了无钉钉」）
                    if (
                        self._radar_handoff_done
                        and not self._radar_activation_notified
                    ):
                        self._radar_notify_pending = True
                    self._open_settled_qty = float(
                        s.get("open_settled_qty", s.get("initial_qty", 0)) or 0
                    )
                    self._last_applied_exchange_sl = float(
                        s.get("last_applied_exchange_sl", 0) or 0
                    )
                    self._open_regime_sticky = bool(
                        s.get("open_regime_sticky", bool(s.get("open_regime")))
                    )
                    self.tv_risk_pct = float(s.get("tv_risk_pct", 0) or 0)
                    self.tv_qty_ratio = float(s.get("tv_qty_ratio", 1.0) or 1.0)
                    self.tv_entry_type = s.get("tv_entry_type", ENTRY_TYPE_OPEN)
                    self.tv_sizing_leverage = float(
                        s.get("tv_sizing_leverage", s.get("leverage", 0)) or 0
                    )
                    self.leverage = float(getattr(self, "tv_sizing_leverage", 0) or 0)
                    self.base_qty = float(s.get("base_qty", 0) or 0)
                    self.add_count = int(s.get("add_count", 0) or 0)
                    if self.sizing_principal <= 0:
                        eq = binance_client.get_principal_wallet_balance()
                        if eq > 0:
                            self.sizing_principal = eq

            if self.base_qty <= 0 and os.path.exists(self.state_file):
                last_open = self._load_last_journal_entry(None, kind="open")
                if last_open:
                    jq = float(last_open.get("qty", 0) or 0)
                    if jq > 0:
                        self.base_qty = jq
                        logger.info(
                            f"📖 恢复 base_qty 取自开仓日志 {jq} {self.unit_label}"
                        )

            if self._scan_and_sweep_dust_on_startup(was_monitoring=saved_monitoring):
                return

            if self._recover_missed_flat_on_startup(was_monitoring=saved_monitoring):
                return

            # 强制 REST 多轮探测，禁止 WS 空缓存 / 共享锁导致漏接实盘
            pos = self._probe_position_for_recover()
            if pos == "AMBIGUOUS":
                dingtalk.report_system_alert(
                    f"重启仓位探测冲突 [{self.symbol}]",
                    "REST 报空仓但盘口仍有挂单 → 禁止清挂单/禁止报空仓待命；"
                    "已启动哨兵接力核对 TP123 + TV硬止损（雷达仅现价达激活线后）",
                    suggestion="请在币安核对持仓；勿手动乱撤，等待哨兵对齐",
                )
                self.monitoring = True
                self._ensure_sentinel_running_quiet()
                self._last_idle_takeover_ts = 0.0
                return

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
                    # 持仓存在：先锁定开仓档位，再消毒/挂 TV 硬止损
                    self.watched_entry = float(
                        pos.get("entry_price") or self.watched_entry or 0
                    )
                    self.current_side = pos.get("side") or self.current_side
                    self._lock_open_regime_from_sources()
                    self._sanitize_vps_hard_sl_ledger(source="重启接管消毒")
                    self._sync_exchange_stop(
                        float(pos.get("size") or 0),
                        radar_sl=None,
                        reason="重启强制TV硬止损",
                        force=True,
                    )

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
                    elif self._strict_tv_opposite_side(side):
                        # 重启禁止自动平仓：只告警并接管补挂 TP123+TV硬止损
                        opp = self._strict_tv_opposite_side(side)
                        logger.error(
                            f"🚨 [重启] 实盘 {side} vs TV {opp} 反向 → "
                            f"保留持仓接管，禁止自动强平"
                        )
                        dingtalk.report_system_alert(
                            f"重启方向背离·保留持仓 [{self.symbol}]",
                            f"实盘 {side} {pos['size']} {self.unit_label} vs 最新TV {opp} | "
                            f"重启不自动平仓，改为挂齐 TP123 + TV硬止损",
                            suggestion="若确需反向，请 TV 发 OPEN 反向信号走先平后开",
                        )
                        self.last_tv_side = side

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
                    self._lock_open_regime_from_sources()
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
                        f"雷达={'已激活' if radar_active else '待命(价触激活线)'} | "
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
                    vps_sl = float(self._vps_hard_sl_target(entry_px) or 0)
                    verify_note = (
                        f"接管 {real_amt} ETH @ {entry_px:.2f} | "
                        f"开单 {saved_initial} ETH | "
                        f"已成交 TP{getattr(self, 'tp_levels_consumed', []) or '无'} | "
                        f"TV方向 {self.last_tv_side} | "
                        f"TV硬止损@{vps_sl:.2f}"
                        f"(开仓R{self._resolve_hard_sl_regime()}) | "
                        f"TV参考tv_sl={float(getattr(self, 'tv_sl_ref', 0) or 0) or '—'} | "
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
                        regime=int(
                            getattr(self, "open_regime", None)
                            or self._resolve_hard_sl_regime()
                        ),
                        radar_active=radar_active,
                        sl_price=vps_sl or float(self._vps_hard_sl_target(entry_px) or 0),
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
                        tv_regime=(
                            self._resolve_tv_open_regime_for_position(
                                self.current_side, entry_px,
                            )
                            or int((self.last_tv_signal or {}).get("regime") or 0)
                            or None
                        ),
                        hard_sl_pct=get_vps_hard_sl_params(
                            int(
                                getattr(self, "open_regime", None)
                                or self._resolve_hard_sl_regime()
                            )
                        ).get("pct"),
                        radar_act_pct=get_radar_activation_ratio(
                            int(
                                getattr(self, "open_regime", None)
                                or self._resolve_hard_sl_regime()
                            )
                        ),
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
                    logger.error(f"❌ [{self.symbol}] 重启接管步骤异常: {recover_err}")
                    # 有仓绝不平仓：尽力补挂 TP123+TV硬止损，哨兵接力
                    try:
                        live = self._get_active_position(prefer_ws=False) or pos
                        if live and float(live.get("size") or 0) > 0:
                            self.current_side = live.get("side") or self.current_side
                            self.watched_entry = float(
                                live.get("entry_price") or self.watched_entry or 0
                            )
                            self.watched_qty = float(live.get("size") or 0)
                            self.monitoring = True
                            self._open_regime_sticky = True
                            curr_px = binance_client.get_current_price(self.symbol) or 0
                            self._ensure_full_defense_stack(
                                self.watched_qty, self.watched_entry, curr_px,
                                source=f"{self.symbol}重启异常兜底",
                                manual_fresh=True,
                            )
                            self._save_state()
                    except Exception as e2:
                        logger.error(f"❌ [{self.symbol}] 异常兜底防线也失败: {e2}")
                    self.monitoring = True
                    self._save_state()
                    dingtalk.report_system_alert(
                        f"重启接管部分失败 [{self.symbol}]",
                        f"实盘仍有仓，已尽力挂 TP123+TV硬止损并启动哨兵 | "
                        f"禁止自动平仓 | {recover_err}",
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
                # 确认空仓：禁止误平仓；仅清理本品种孤儿挂单
                logger.info(
                    f"🔄 [{self.symbol}] 系统重启点火：REST确认无持仓，账本复位为空仓待命。"
                )
                self.monitoring = False
                self.watched_qty = 0.0
                self.base_qty = 0.0
                self.add_count = 0
                self.current_side = None
                self._open_regime_sticky = False
                self._save_state()
                flat_ok = self._wait_verify(
                    lambda: self._get_active_position(prefer_ws=False) is None,
                    retries=6,
                    delay=0.5,
                )
                # 空仓后再清挂单；若清场前又冒出持仓 → 立刻改接管
                resurfaced = self._get_active_position(prefer_ws=False)
                if resurfaced:
                    logger.error(
                        f"🚨 [{self.symbol}] 空仓确认后持仓复现 → 改闪电接管，禁止清场"
                    )
                    self._perform_live_takeover(
                        resurfaced, source="VPS重启·空仓复现", manual_open=True,
                    )
                    self._ensure_sentinel_running_quiet()
                    return
                binance_client.cancel_all_open_orders(self.symbol)
                standby_note = (
                    f"[{self.symbol}] 重启完成 | 盘口无持仓 | 挂单已清空 | "
                    f"{BINANCE_VPS_VERSION}"
                )
                if not flat_ok:
                    standby_note += " | REST 同步略延迟"
                dingtalk.report_recover_standby(
                    verify_note=standby_note,
                    version=BINANCE_VPS_VERSION,
                    symbol=self.symbol,
                )
                self._ensure_sentinel_running_quiet()
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
    """启动全部活动品种军师并逐一恢复状态（ETH/XAU 独立核查，互不跳过）。"""
    from symbol_config import active_binance_symbols
    global position_supervisor
    symbols = active_binance_symbols()
    logger.info(f"🔄 多品种启动恢复清单: {symbols}")
    for sym in symbols:
        get_supervisor(sym)
    position_supervisor = SUPERVISORS.get("ETHUSDT") or next(iter(SUPERVISORS.values()), None)
    if __name__ != "__main__":
        summaries = []
        for sym, sup in SUPERVISORS.items():
            try:
                logger.info(f"🔄 启动恢复 [{sym}] …")
                before = None
                try:
                    before = sup._get_active_position(prefer_ws=False)
                except Exception:
                    before = None
                sup.recover_state_on_startup()
                after = None
                try:
                    after = sup._get_active_position(prefer_ws=False)
                except Exception:
                    after = None
                if after:
                    summaries.append(
                        f"{sym}:有仓 {after.get('side')} {after.get('size')} "
                        f"@ {after.get('entry_price')} monitoring={sup.monitoring}"
                    )
                elif before:
                    summaries.append(f"{sym}:探测曾有仓但恢复后REST空 → 哨兵巡检")
                else:
                    summaries.append(f"{sym}:空仓待命")
            except Exception as e:
                logger.error(f"启动恢复失败 [{sym}]: {e}")
                summaries.append(f"{sym}:恢复异常 {e}")
                try:
                    # 单品种失败不影响其它品种；本品种仍启哨兵
                    if hasattr(sup, "_ensure_sentinel_running_quiet"):
                        sup.monitoring = True
                        sup._ensure_sentinel_running_quiet()
                except Exception:
                    pass
        try:
            dingtalk.report_system_alert(
                "多品种重启核查汇总",
                " | ".join(summaries) if summaries else "无品种",
                suggestion="有仓品种应挂齐 TP123+TV硬止损；雷达仅现价达激活线后；重启禁止无故平仓",
            )
        except Exception as e:
            logger.warning(f"多品种重启汇总钉钉跳过: {e}")
    return SUPERVISORS


bootstrap_supervisors()
