#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# position_supervisor_binance.py — 与深币 VPS 逻辑对齐（仓位/杠杆一律跟 TV）
import logging
import time
import threading
import os
import json
import math
import queue
import inspect
import random
from datetime import datetime
from logging.handlers import TimedRotatingFileHandler
from binance_client import (
    binance_client,
    is_orders_query_failed,
    is_position_query_failed,
    ORDERS_QUERY_FAILED,
)
from position_manager import position_manager
import dingtalk
from webhook_parser import (
    enrich_signal_fields,
    format_tv_field_sources,
    classify_tv_close,
    compute_vps_open_qty,
    compute_tv_order_qty,
    compute_fixed_order_qty,
    check_total_notional_cap,
    MAX_TOTAL_NOTIONAL_MULT,
    HARD_NOTIONAL_CAP,
    FIXED_LEVERAGE,
    FIXED_MARGIN_PCT,
    FIXED_RISK_PCT,
    FIXED_NOTIONAL_MULT,
    SIZING_MODE,
    LEG_TP_RATIOS,
    PLACE_TP_LEVELS,
    ATR_UPDATE_SEC,
    ORDER_TIMEOUT_SEC,
    SIGNAL_DEDUP_SEC as WP_SIGNAL_DEDUP_SEC,
    compute_vps_hard_sl,
    compute_vps_hard_sl_distance,
    format_vps_hard_sl_note,
    format_tv_vps_sl_compare,
    get_vps_hard_sl_params,
    format_vps_sizing_note,
    enrich_entry_tp_prices,
    get_regime_tp_ratios,
    get_leg_tp_ratios,
    format_regime_tp_ratios_label,
    format_radar_activation_ratios_label,
    validate_tp_prices_for_side,
    normalize_entry_type,
    is_reconcile_action,
    is_flatten_action,
    RECONCILE_ACTIONS,
    FLATTEN_ACTIONS,
    ENTRY_TYPE_OPEN,
    CLOSE_TYPE_TP3,
    CLOSE_TYPE_BREAKEVEN,
    CLOSE_TYPE_VPS_SHIELD,
    CLOSE_TYPE_PROTECT,
    CLOSE_TYPE_HARD_SL,
    CLOSE_TYPE_QUICK,
    CLOSE_TYPE_RSI,
    CLOSE_TYPE_RECONCILE,
    EXIT_SOURCE_RADAR_BE,
    EXIT_SOURCE_VPS_HARD_SL,
    EXIT_SOURCE_SL_INITIAL,
    EXIT_SOURCE_SL_BREAKEVEN,
    EXIT_SOURCE_TP3,
    EXIT_SOURCE_TV_CLOSE,
    EXIT_SOURCE_TV_PROTECT,
    EXIT_SOURCE_MANUAL,
    EXIT_SOURCE_QUICK,
    EXIT_SOURCE_RSI,
    EXIT_SOURCE_LABELS,
    RADAR_STAGE_COST_BUFFER_PCT,
    RADAR_STAGE_LABELS,
    RADAR_ACTIVATE_TP1_FRAC,
    RADAR_STEP_ATR,
    RADAR_LOCK_ATR,
    RADAR_TP1_FLOOR_ATR,
    RADAR_TP2_FLOOR_ATR,
    get_radar_activation_ratio,
    get_radar_trail_step,
    get_radar_breath_atr,
    radar_activation_price,
)
from breath_stop import (
    INITIAL_SL_ATR,
    ADX_FALLBACK,
    STOP_EXEC_BUFFER_USD,
    initial_stop_price,
    order_stop_price,
    calculate_breath_stop,
    get_breathing_coefficient,
    trail_distance_by_adx,
    BREAKEVEN_TRIGGER_ATR,
    STEP_TRIGGER_ATR,
    STEP_ADVANCE_ATR,
)
from atr_1h import get_atr_1h_engine
from atr_scenario import (
    SCENARIO_TV,
    SCENARIO_VPS,
    compute_hard_stop_distance,
    hard_stop_price,
    place_tp_levels_for_scenario,
    resolve_atr_scenario,
    scenario_notice,
    temp_hard_stop_price,
)
from breath_profiles import LockedInitialAtr, cold_start_multiplier
from market_engine import (
    get_market_engine,
    atr_divergence_pct,
    resolve_tv_atr_for_compare,
    evaluate_atr_emergency_degrade,
    tv_implied_atr_for_degrade,
    ATR_COMPARE_ALERT_PCT,
    ATR_ANOMALY_RATIO,
    ATR_MEDIAN_LOOKBACK,
    TV_HARD_SL_ATR_MULT,
)
from tv_seq import (
    TVSeqBuffer,
    extract_seq_meta,
    is_close_action,
    is_open_action,
    reorder_batch_close_then_open,
    collapse_batch_for_execution,
)

if not os.path.exists('logs'):
    os.makedirs('logs')
_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_LOG_DIR = os.path.join(_BASE_DIR, 'logs')
os.makedirs(_LOG_DIR, exist_ok=True)
_BRAIN_LOG = os.path.join(_LOG_DIR, 'binance_brain.log')
# 按自然日滚动，保留 30 天
handler = TimedRotatingFileHandler(
    _BRAIN_LOG, when="midnight", interval=1, backupCount=30, encoding="utf-8",
)
handler.suffix = "%Y-%m-%d"
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] Brain: %(message)s',
    handlers=[handler, logging.StreamHandler()],
)
logger = logging.getLogger(__name__)

BINANCE_VPS_VERSION = "v15.7.9-hard-unify"

# 白皮书：OPEN 成交后 15s 内迟到 CLOSE 直接丢弃（OPEN 先到场景）
LATE_CLOSE_SUPPRESS_SEC = 15.0

# 双雷达：每 symbol 独立哨兵；REST 降到 ≥1s + 抖动，成交优先靠 User Data WS
SENTINEL_POLL_NORMAL = 1.0
SENTINEL_POLL_ARMING = 1.0
SENTINEL_POLL_RADAR = 1.0
SENTINEL_POLL_JITTER_SEC = 0.2
IDLE_PATROL_INTERVAL_SEC = 45  # 双品种空闲轮询；过密会触发 -1003 IP ban
IDLE_PATROL_BACKOFF_SEC = 120  # 查仓 QUERY_FAILED / 限流后拉长间隔
IDLE_TAKEOVER_COOLDOWN_SEC = 30
DUST_QTY_ETH = 0.004
TP_COMPLETE_RESIDUAL_RATIO = 0.12
OPEN_OVERSIZE_RATIO = 1.10  # 与 QTY_ALIGN_MIN_PCT 一致：偏离 ≥10% 才裁减
# 60s 去重键 = action+symbol+price（见 _signal_fingerprint 注释：意图/风险）
SIGNAL_DEDUP_SEC = int(WP_SIGNAL_DEDUP_SEC or 60)
DEFENSE_ALIGN_COOLDOWN_SEC = 60
SENTINEL_GRACE_AFTER_RECOVER_SEC = 45
SENTINEL_GRACE_AFTER_OPEN_SEC = 90
# 呼吸止损开仓即运行（无旧「TP1前禁雷达」窗）
POST_OPEN_RADAR_BLOCK_SEC = 0
RADAR_TRAIL_MIN_INTERVAL_SEC = 5  # 呼吸止损改单最短间隔
RADAR_WS_APPROACH_RATIO = 0.90
RADAR_WS_URGENT_SLEEP_SEC = 0.25
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
        from breath_profiles import get_breath_profile
        meta = resolve_binance_symbol(symbol)
        self.symbol = meta["symbol"]
        self.unit_label = meta.get("unit") or "ETH"
        self.tag = str(meta.get("tag") or self.unit_label or "ETH").upper()
        self.qty_step = float(meta.get("qty_step") or 0.001)
        self.min_qty = float(meta.get("min_qty") or 0.001)
        self.dust_qty = float(meta.get("dust_qty") or DUST_QTY_ETH)
        self.atr_fallback_symbol = meta.get("atr_fallback_symbol") or self.symbol
        self.breath_profile = meta.get("breath_profile") or get_breath_profile(
            self.symbol, "binance",
        )
        self.price_precision = int(meta.get("price_precision") or 2)
        # profile.tick_size 与价格精度对齐
        if self.breath_profile and self.price_precision >= 0:
            self.breath_profile = dict(self.breath_profile)
            self.breath_profile["tick_size"] = 10 ** (-self.price_precision)
        self.monitoring = False
        self._lock = threading.Lock()

        # 固定分腿 30/30/40；只挂 TP1+TP2 限价（余仓40%交呼吸止损阶段二）
        _leg = list(LEG_TP_RATIOS)
        self.regime_settings = {
            1: {"margin": 0.0, "ratios": list(_leg)},
            2: {"margin": 0.0, "ratios": list(_leg)},
            3: {"margin": 0.0, "ratios": list(_leg)},
            4: {"margin": 0.0, "ratios": list(_leg)},
        }
        self.leverage = float(FIXED_LEVERAGE)  # 固定 5x
        self.tv_sizing_leverage = float(FIXED_LEVERAGE)

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
        self._sentinel_start_lock = threading.Lock()
        self.open_regime = 3
        self.open_atr = 30.0  # 展示默认；真开仓后由 LockedInitialAtr 锁定
        self._locked_initial_atr = LockedInitialAtr(strict=False)
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
        # TP 超时撤单移交雷达后禁止再挂（与假成交清理隔离，防 Gemini 叠单循环）
        self.tp_levels_radar_handoff = []
        self._stop_write_blocked = False
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
        self.tv_suggested_qty = 0.0  # TV webhook qty 上限
        self.tv_qty1 = 0.0  # TV TP1 数量意图（按实开仓缩放后挂限价）
        self.tv_qty2 = 0.0
        self.tv_qty3 = 0.0  # 不挂限价，余仓交阶段二
        self.radar_step_count = 0
        self.radar_activated = False
        self.breakeven_phase = False  # 呼吸止损阶段二
        self.initial_stop = 0.0       # entry±1.5×initialAtr，阶梯基准（理论，不含0.3缓冲）
        self.last_adx = float(ADX_FALLBACK)  # 兼容旧状态；阶段二已不依赖 ADX
        self.breathing_coefficient = cold_start_multiplier(
            getattr(self, "breath_profile", None)
        )
        self.early_be_done = False
        self._breath_ratio_history = []
        self._tv_signal_atr = 0.0  # 本笔 webhook atr → initial_atr
        self._last_open_exec_ts = 0.0
        self._last_open_bar_index = None
        self._last_open_bar_time_ms = 0
        self.remaining_qty_pct = 1.0
        self._breath_tick_paused = False  # TP成交收缩止损数量时暂停价格tick
        self._atr_last_update_ts = 0.0
        self._tp_order_placed_ts = {}  # level(1/2/3) → unix ts
        self._breath_coeff_meta = {}
        # TP1/TP2/TP3/硬止损/雷达止损 交易所订单 ID
        self.frozen_hard_sl_px = 0.0
        self._defense_order_ids = {
            "tp1": "", "tp2": "", "tp3": "",
            "hard_stop": "", "radar_stop": "", "stop": "",
        }
        self.trading_paused = False
        self.trading_pause_reason = ""
        self._state_old_schema = False
        self._last_bar_time_ms = 0  # 最近处理过的 bar_time（乱序/过期兜底）
        self._atr_div_streak = 0
        self.atr_source = "tv"  # 场景二=tv；场景一接管后=vps
        self.atr_degraded = False  # 兼容旧 state；两场景定稿不再因 ATR 暂停
        self._pending_atr_degrade = None
        # 两场景：0=未决 · 1=VPS真实ATR · 2=TV理论ATR(+TP3兜底)
        self._atr_scenario = 0
        self._temp_stop_active = False
        self._tp3_fallback_active = False

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
            f"🧠 币安 VPS [{BINANCE_VPS_VERSION}] [{self._tag()}] {self.symbol} 军师已加载："
            f"sizing={SIZING_MODE} · breath={(self.breath_profile or {}).get('name') or '?'} "
            f"· leverage={FIXED_LEVERAGE}x · {self.unit_label}"
        )
        self._start_signal_worker()
        self._start_idle_flat_patrol()
        # 启动即订阅行情/私有流，避免开仓前钉钉与盘口不同步
        try:
            self._ensure_price_ws()
        except Exception as e:
            logger.warning(f"启动 WS 订阅跳过: {e}")

    def _start_idle_flat_patrol(self):
        """空仓待命时实盘巡检：反向强平 / 同向接管 / 人工异动 / 漏报全平 / 蚂蚁扫尾。
        间隔默认 45s；QUERY_FAILED/限流后退避 120s，避免 -1003 雪崩。"""
        def loop():
            while True:
                now = time.time()
                backoff_until = float(
                    getattr(self, "_idle_patrol_backoff_until", 0.0) or 0.0
                )
                sleep_for = float(IDLE_PATROL_INTERVAL_SEC)
                if now < backoff_until:
                    sleep_for = max(sleep_for, backoff_until - now)
                time.sleep(max(1.0, sleep_for))
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

    def _mark_idle_patrol_backoff(self, reason="QUERY_FAILED"):
        """限流/查仓失败后拉长空闲巡检间隔，保护共享 IP 配额。"""
        until = time.time() + float(IDLE_PATROL_BACKOFF_SEC)
        self._idle_patrol_backoff_until = until
        logger.warning(
            f"⏳ [{self.symbol}] 空闲巡检退避 {IDLE_PATROL_BACKOFF_SEC:.0f}s "
            f"| {reason}"
        )

    def _book_thinks_active(self):
        return (
            float(self.watched_qty or 0) > 0
            or self.current_side in ("LONG", "SHORT")
        )

    def _live_position_qty(self):
        """返回实盘数量；查询失败返回 None（禁止当 0/空仓）。"""
        pos = self._get_active_position()
        if pos == "QUERY_FAILED":
            return None
        if not pos:
            return 0.0
        return float(pos.get("size", 0) or 0)

    def _confirm_position_flat(self, retries=None, delay=None):
        """REST 延迟/重启抖动时多次复核，避免误报空仓触发常规清场。
        查询失败 → False（fail-closed，禁止清账本）。"""
        retries = retries if retries is not None else FLAT_CONFIRM_RETRIES
        delay = delay if delay is not None else FLAT_CONFIRM_DELAY_SEC
        for i in range(max(1, int(retries))):
            qty = self._live_position_qty()
            if qty is None:
                return False
            if qty > DUST_QTY_ETH:
                return False
            if i + 1 < retries:
                time.sleep(delay)
        final = self._live_position_qty()
        if final is None:
            return False
        return final <= DUST_QTY_ETH

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
            handoff = set(getattr(self, "tp_levels_radar_handoff", []) or [])
            keep = [lv for lv in consumed if lv in handoff]
            drop = [lv for lv in consumed if lv not in handoff]
            if not drop:
                return False
            logger.warning(
                f"⚠️ 清除陈旧 tp_levels_consumed={drop} "
                f"(开单 {initial_qty}≈现仓 {live_qty}，无减仓且现价未过)"
                + (f" | 保留雷达移交{keep}" if keep else "")
            )
            self.tp_levels_consumed = keep
            self._save_state()
            return True
        if 1 in consumed and self.tv_tps and self.tv_tps[0] > 0:
            if 1 in (getattr(self, "tp_levels_radar_handoff", []) or []):
                return False
            if (
                1 not in inferred
                and not self._has_tp_limit_at_price(self.tv_tps[0])
                and not self._price_reached_tp_zone(1, curr_px, live_only=True)
            ):
                logger.warning(
                    f"⚠️ TP1 已标记成交但无减仓/无挂单/现价未过 → 重置 {consumed}"
                )
                handoff = set(getattr(self, "tp_levels_radar_handoff", []) or [])
                self.tp_levels_consumed = [lv for lv in consumed if lv in handoff]
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

    def _perform_live_takeover(self, pos, source="巡检", manual_open=False, qty_change=None,
                               link_historical_tv=None):
        """
        实盘有仓但 VPS 未监控 / 防线缺失 → 补挂 TP123+硬止损，启动雷达哨兵。
        link_historical_tv:
          None → 自动：manual_open 默认 False（不把历史 TV 当成这笔仓的来源）
          True/False → 显式覆盖
        """
        if link_historical_tv is None:
            link_historical_tv = not bool(manual_open)
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

        provenance = self._probe_position_provenance(pos)
        reconcile_notes = self._hydrate_tv_defense_context(
            pos, link_historical_tv=bool(link_historical_tv),
        )
        if provenance.get("note"):
            reconcile_notes.insert(0, provenance["note"])
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
            f"开单 {saved_initial} ETH | "
            f"来源={provenance.get('label') or ('历史TV关联' if link_historical_tv else '待核实')} | "
            f"止盈 {matched}/{expected} 档 | "
            f"呼吸止损@{float(self._tv_hard_sl_target(entry_px) or 0):.2f} | "
            f"TV参考tv_sl="
            f"{(float(getattr(self, 'tv_sl_ref', 0) or 0) if link_historical_tv else 0) or '不适用'} | "
            f"雷达={'已激活' if radar_active else '待命'} | "
            f"{self._format_audit_summary(audit)}{extra_txt}{reconcile_txt}"
        )
        if not verified:
            verify_note += " | REST 同步略延迟"

        if manual_open:
            self._call_dingtalk(
                dingtalk.report_manual_position_change,
                action_type="检测到未登记来源的仓位，来源待核实",
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
        if pos == "QUERY_FAILED":
            self._on_position_query_failed("空闲巡检")
            self._mark_idle_patrol_backoff("空闲巡检·QUERY_FAILED")
            return
        live_qty = float(pos["size"]) if pos else 0.0

        if live_qty <= 0:
            if self._book_thinks_active():
                if not self._confirm_position_flat():
                    logger.warning(
                        "📭 [空闲巡检] 首次无仓但复核仍有持仓/查询失败 → 跳过误清场"
                    )
                    return
                curr_px = binance_client.get_current_price(self.symbol)
                logger.warning("📭 [空闲巡检] 账本有仓且复核空仓 → 补发收网钉钉")
                try:
                    flat_meta = self._infer_flat_close_meta(
                        curr_px=curr_px, hint_reason="",
                    )
                    src_lab = flat_meta.get("exit_source_label") or ""
                    note = flat_meta.get("tv_reason") or ""
                    if src_lab and src_lab not in str(note):
                        flat_meta["tv_reason"] = (
                            f"{src_lab} · {note}" if note else src_lab
                        )
                    elif not note:
                        flat_meta["tv_reason"] = (
                            src_lab or "仓位归零（空闲巡检·来源未明·请查交易所成交）"
                        )
                except Exception as e:
                    logger.error(f"空闲巡检归因失败: {e}")
                    flat_meta = {
                        "tv_reason": "仓位归零（空闲巡检·归因异常·请查交易所成交）",
                    }
                self._handle_manual_flat_detected(
                    flat_meta.get("tv_reason"),
                    close_meta=flat_meta,
                    curr_px=curr_px,
                )
                return
            # 账本空 + 仓位空：仍必须扫残留限价/条件单（防幽灵 TP 空仓挂着）
            try:
                n = self._remaining_open_order_count()
                if n is not None and n > 0:
                    logger.warning(
                        f"🧹 [空闲巡检] {self.symbol} 空仓但仍有挂单 {n} 笔 "
                        f"→ 强制净场（杜绝幽灵限价）"
                    )
                    self._purge_all_defense_orders_on_flat(
                        "空闲巡检·空仓残留挂单", max_rounds=6,
                    )
            except Exception as e:
                logger.debug(f"空闲巡检空仓净场跳过: {e}")
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
                f"(TV={tv_side}) → 未登记来源接管+挂防线"
            )
            self._perform_live_takeover(
                pos,
                source="空闲巡检·未登记来源",
                manual_open=True,
                link_historical_tv=False,
            )
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
            # 按函数签名过滤未知 kwargs，避免未来新增字段再次 TypeError
            try:
                sig = inspect.signature(fn)
                params = sig.parameters
                if any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values()):
                    call_kwargs = dict(kwargs)
                else:
                    call_kwargs = {k: v for k, v in kwargs.items() if k in params}
            except (TypeError, ValueError):
                call_kwargs = dict(kwargs)
            try:
                return fn(**call_kwargs)
            except TypeError as exc:
                if "unexpected keyword argument" not in str(exc):
                    raise
                legacy = {
                    k: v for k, v in call_kwargs.items()
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

    def _call_dingtalk(self, fn, *args, **kwargs):
        """兼容旧调用名 → _dingtalk（自动注入 symbol/unit_label）

        同时兼容历史位置参数：
          _call_dingtalk(report_system_alert, title, detail)
        """
        if args and "title" not in kwargs:
            if len(args) >= 1:
                kwargs["title"] = args[0]
            if len(args) >= 2 and "detail" not in kwargs:
                kwargs["detail"] = args[1]
            if len(args) >= 3 and "level" not in kwargs:
                kwargs["level"] = args[2]
            if len(args) >= 4 and "suggestion" not in kwargs:
                kwargs["suggestion"] = args[3]
        elif args:
            logger.warning(
                f"_call_dingtalk 收到多余位置参数 args={args!r}，已忽略并改用 kwargs"
            )
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
            # 白皮书：先按到达序折叠（OPEN先到→丢CLOSE）；再对残留开平强制先平后开
            before_n = len(batch)
            batch = collapse_batch_for_execution(batch)
            if len(batch) != before_n:
                logger.info(
                    f"📬 [{self.symbol}] 缓存折叠 {before_n}→{len(batch)} | "
                    + " → ".join(
                        str((p or {}).get("action", "")).upper() for p in batch
                    )
                )
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
        """
        去重键：action + symbol + price（60s 内同键忽略）。

        设计意图：TV 偶发重复推送同一条警报（同动作、同价）时防双开/双平；
        故意把 price 纳入键，使「同向但价格已变」的新信号（例如快速再入场）不被误杀。

        风险：若 TV 在 60s 内对同一 action+symbol 推送不同 price 的重复噪音，
        去重不会拦截，可能连开/连平。产品若要求「同 action+symbol 一律忽略」，
        应去掉 price 分量（会误伤合法换价再入场）。
        """
        action = str(payload.get("action", "")).strip().upper()
        sym = str(
            payload.get("symbol") or payload.get("ticker") or self.symbol or ""
        ).strip().upper()
        px = round(self._safe_float(payload.get("price"), 0), 2)
        return (action, sym, px)

    def enqueue_signal(self, payload):
        payload = dict(payload or {})
        bi, sq = extract_seq_meta(payload)
        action = str(payload.get("action", "")).strip().upper() or "?"
        # bar_time 乱序/过期兜底（中优先级）：早于已处理最新 bar_time → 只记日志不交易
        bar_time = self._extract_bar_time_ms(payload)
        if bar_time > 0 and int(getattr(self, "_last_bar_time_ms", 0) or 0) > 0:
            if bar_time < int(self._last_bar_time_ms):
                logger.warning(
                    f"📬 [{self.symbol}] 过期/乱序 webhook 已忽略 | action={action} "
                    f"bar_time={bar_time} < last={self._last_bar_time_ms}"
                )
                try:
                    dingtalk.report_system_alert(
                        f"过期webhook已忽略 [{self.symbol}]",
                        f"action={action} bar_time={bar_time} < 已处理={self._last_bar_time_ms} | "
                        f"不执行交易（先平后开/幂等仍兜底主路径）",
                        level="提示",
                    )
                except Exception:
                    pass
                return
        # 60s 去重：所有路径统一（action+symbol+price）；平/开成功后清指纹
        fp = self._signal_fingerprint(payload)
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
            logger.warning(
                f"📬 开仓进行中，忽略重复建仓信号 {action}"
                + (f" bar={bi} seq={sq}" if bi is not None else "")
            )
            return
        self._last_signal_fp = fp
        self._last_signal_fp_ts = now
        if bar_time > 0:
            self._last_bar_time_ms = max(int(getattr(self, "_last_bar_time_ms", 0) or 0), bar_time)
        # 有 bar_index+seq：幂等键去重 + 有序缓冲
        if bi is not None and sq is not None:
            status = self._seq_buffer.add(payload)
            logger.info(
                f"📬 TV时序入队: {action} bar={bi} seq={sq} "
                f"bar_time={bar_time or '-'} → {status} | "
                f"缓冲深度 {self._seq_buffer.depth()} 旁路 {self._signal_queue.qsize()}"
            )
            return
        status = self._seq_buffer.add(payload)  # legacy：1.0s 缓存窗口后再冲刷
        logger.info(
            f"📬 TV信号入队(无时序·缓存窗口): {action} "
            f"bar_time={bar_time or '-'} → {status} | "
            f"缓冲深度 {self._seq_buffer.depth()}"
        )

    @staticmethod
    def _extract_bar_time_ms(payload) -> int:
        """TV K线收盘/开盘时间戳（ms）。支持 bar_time / time / bar_time_ms。"""
        if not isinstance(payload, dict):
            return 0
        for key in ("bar_time", "bar_time_ms", "time", "barTime"):
            raw = payload.get(key)
            if raw is None or raw == "":
                continue
            try:
                v = int(float(raw))
            except (TypeError, ValueError):
                continue
            # 秒级时间戳 → ms
            if 0 < v < 10_000_000_000:
                v *= 1000
            if v > 0:
                return v
        return 0

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
            # 铁律：信号播报 sizing 必须与本笔 payload 同步，禁止沿用上笔残留 tv_suggested_qty
            # （2026-07-22 事故：钉钉显示 0.02，真实下单却用巨大 TV.qty→notional=4.445）
            try:
                self._apply_tv_sl_from_payload(payload, source="信号预览sizing")
                self._apply_tv_sizing_params(payload)
            except Exception as e:
                logger.warning(f"信号预览 sizing 参数绑定跳过: {e}")
            _, open_sizing_meta = self._calc_vps_open_qty(self.tv_price)
            sizing_note = " | " + format_vps_sizing_note(open_sizing_meta, entry_type=ENTRY_TYPE_OPEN)
        logger.info(
            f"📡 TV日志: {raw_action}{seq_tag} RISK20 @ {self.tv_price:.2f} "
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
            tv_sl=(
                payload.get("tv_sl")
                or payload.get("stop_loss")
                or getattr(self, "tv_sl_ref", 0)
                or None
            ),
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
                    tv_sl_ref=(
                        payload.get("tv_sl")
                        or payload.get("stop_loss")
                        or getattr(self, "tv_sl_ref", 0)
                    ),
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
                    f"🔒 [{self.symbol}] 内部参数档锁定 R{tv_reg} (tv_open_signal)"
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
                f"🔒 [{self.symbol}] 内部参数档锁定 R{best} ({best_src})"
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
        """TV 方向为准：实盘与 TV 明确反向 → 强制市价全平 + 钉钉。"""
        if not pos or float(pos.get("size", 0) or 0) <= 0:
            return False
        live_side = self._live_position_side(pos)
        if self._live_aligns_with_credible_tv(live_side):
            logger.info(
                f"✅ [{source}] 实盘 {live_side} 与可信 TV 信源同向 → 跳过强平"
            )
            return False
        tv_opposite = self._strict_tv_opposite_side(live_side)
        if not tv_opposite or not live_side:
            return False
        reason = (
            f"TV方向为准·强制平仓：实盘({live_side}) ≠ 最新TV({tv_opposite}) [{source}]"
        )
        logger.error(f"🚨 {reason}")
        verify_note = (
            f"触发源: {source} | 最新TV {tv_opposite} | 实盘反向 {live_side} | "
            "已强制全平对齐 TV，账本归零待命"
        )
        self.trading_paused = False
        self.trading_pause_reason = ""
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

    def _probe_position_provenance(self, pos):
        """
        尝试溯源未登记仓位（无 clientOrderId 时尽力而为）。
        返回 {label, matched_tv, matched_test, note} —— 不做确定性「人工开仓」结论。
        """
        entry = float((pos or {}).get("entry_price") or 0)
        side = str((pos or {}).get("side") or "").upper()
        size = float((pos or {}).get("size") or 0)
        out = {
            "label": "来源待核实",
            "matched_tv": False,
            "matched_test": False,
            "note": "溯源：本系统未写 clientOrderId，无法交易所侧硬归因",
        }
        try:
            open_j = self._load_last_journal_entry(None, kind="open") or {}
            src = str(open_j.get("source") or open_j.get("reason") or "")
            je = float(open_j.get("entry") or open_j.get("price") or 0)
            jq = float(open_j.get("qty") or open_j.get("size") or 0)
            js = str(open_j.get("side") or open_j.get("action") or "").upper()
            if je > 0 and entry > 0 and abs(je - entry) <= max(1.0, entry * 0.0015):
                if (not js) or js == side or js in ("LONG", "SHORT"):
                    if any(
                        k in src.upper()
                        for k in (
                            "LIVE_TEST", "TP1_RESIZE", "RESIZE_TEST",
                            "ACCEPT", "VERIFY", "TEST",
                        )
                    ):
                        out["matched_test"] = True
                        out["label"] = "疑似实盘验证脚本"
                        out["note"] = f"溯源：开仓日志 source≈{src[:48]} 入场贴近"
                        return out
                    if jq > 0 and abs(jq - size) <= max(0.002, size * 0.15):
                        out["matched_test"] = True
                        out["label"] = "疑似本地开仓日志"
                        out["note"] = f"溯源：开仓日志 qty/entry 贴近 source={src[:32] or '—'}"
                        return out
            tv = self._load_last_tv_open_signal() or {}
            tv_side = str(tv.get("action") or tv.get("side") or "").upper()
            tv_px = float(tv.get("price") or 0)
            if (
                tv_side == side
                and tv_px > 0
                and entry > 0
                and abs(tv_px - entry) <= max(2.0, entry * 0.003)
            ):
                out["matched_tv"] = True
                out["label"] = "疑似TV开仓信号附近"
                out["note"] = (
                    f"溯源：最近TV {tv_side}@{tv_px:.2f} 与入场接近（仍非硬证据）"
                )
                return out
        except Exception as e:
            out["note"] = f"溯源探测失败: {e}"
        return out

    def _hydrate_tv_defense_context(self, pos, link_historical_tv=True):
        """
        接管补全防线上下文。
        link_historical_tv=False：未登记来源仓位 —— 禁止把缓存/日志里无关的旧 TV
        tv_sl、regime、TP 当成「这笔仓」的来源；仅用行情 ATR 计算呼吸止损与TP。
        """
        notes = []
        side = pos.get("side") or self.current_side
        entry = float(pos.get("entry_price", 0) or self.watched_entry or 0)
        if not side:
            return notes

        self.current_side = side
        if not self.last_tv_side:
            self.last_tv_side = side

        if not link_historical_tv:
            notes.append("来源未核实·未关联历史TV信号")
            try:
                self._refresh_market_metrics(force=False)
            except Exception:
                pass
            # 不锁「TV开仓档」叙事；保留内部默认仅用于 TP 比例兼容
            if float(getattr(self, "open_atr", 0) or 0) <= 0:
                self.open_atr = float(getattr(self, "current_atr", 0) or 0)
            if sum(1 for t in (self.tv_tps or []) if t > 0) < 2 and entry > 0:
                atr = float(getattr(self, "open_atr", 0) or self.current_atr or 0)
                if atr > 0:
                    payload = enrich_entry_tp_prices(
                        side, entry, atr, 3, {},
                    )
                    tps = self._sanitize_tp_prices([
                        payload.get("tv_tp1"), payload.get("tv_tp2"), payload.get("tv_tp3"),
                    ])
                    if self._tp_prices_valid_for_side(side, entry, tps):
                        self.tv_tps = tps
                        notes.append(f"ATR本地补全TP(无TV关联) {tps}")
            # 禁止写入历史 tv_sl_ref 冒充本仓 TV 硬止损
            self.tv_sl_ref = 0.0
            if entry > 0 and side in ("LONG", "SHORT"):
                atr_lock = float(
                    getattr(self, "open_atr", 0)
                    or getattr(self, "current_atr", 0)
                    or 0
                )
                if self._refresh_vps_hard_sl(
                    entry=entry, side=side,
                    atr=atr_lock, tv_sl_ref=None,
                    source="接管补全·无TV关联",
                ):
                    notes.append(
                        f"呼吸止损@{float(getattr(self, 'tv_sl', 0) or 0):.2f}"
                        f"(ATR={atr_lock:.2f}·无历史TV)"
                    )
                else:
                    adopted = self._adopt_exchange_hard_sl(source="接管盘口采纳")
                    if adopted:
                        notes.append(f"盘口采纳硬止损@{adopted:.2f}")
            live_stops = binance_client.find_protective_stop_prices(self.symbol)
            target = float(getattr(self, "tv_sl", 0) or getattr(self, "current_sl", 0) or 0)
            if (
                target > 0
                and live_stops
                and any(abs(float(p) - target) <= SHIELD_STOP_TOLERANCE for p in live_stops)
            ):
                notes.append(f"盘口止损已齐@{target:.2f}·跳过改挂")
            return notes

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
                # 日志 ATR 仅展示；权威值由行情引擎刷新
                pass
            if float(self.tv_price or 0) <= 0 and float(src.get("price", 0) or 0) > 0:
                self.tv_price = float(src["price"])
        try:
            self._refresh_market_metrics(force=False)
        except Exception:
            pass

        # 内部参数档锁定（不影响 RISK20 算仓；仅兼容旧字段）
        hard_regime = self._lock_open_regime_from_sources(force=True)
        notes.append(f"内部参数档 R{hard_regime}（不影响算仓）")
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
                    notes.append(f"TV参考止损tv_sl_ref={sl:.2f}（非必然本仓）")
                    break

        # 呼吸止损按 ATR 武装；TV tv_sl 仅参考
        if entry > 0 and side in ("LONG", "SHORT"):
            atr_lock = float(
                getattr(self, "open_atr", 0)
                or getattr(self, "current_atr", 0)
                or 0
            )
            if self._refresh_vps_hard_sl(
                entry=entry, side=side,
                regime=hard_regime, atr=atr_lock,
                tv_sl_ref=getattr(self, "tv_sl_ref", 0) or None,
                source="接管补全",
            ):
                notes.append(
                    f"呼吸止损@{float(getattr(self, 'tv_sl', 0) or 0):.2f}"
                )
            else:
                adopted = self._adopt_exchange_hard_sl(source="接管盘口采纳")
                if adopted:
                    notes.append(f"盘口采纳硬止损@{adopted:.2f}")

            # 重启：盘口已有贴近账本的唯一 STOP → 禁止 force 撤挂
            live_stops = binance_client.find_protective_stop_prices(self.symbol)
            if live_stops is None:
                notes.append("挂单查询失败·跳过强制改挂")
            else:
                uniq = sorted({round(float(p), 2) for p in live_stops if float(p) > 0})
                target = round(float(self._tv_hard_sl_target(entry, side) or 0), 2)
                if target > 0 and len(uniq) == 1 and abs(uniq[0] - target) <= SHIELD_STOP_TOLERANCE:
                    self._last_applied_exchange_sl = uniq[0]
                    notes.append(f"盘口止损已齐@{uniq[0]:.2f}·跳过改挂")
                elif target > 0 and (
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
                            qty, radar_sl=None, reason="接管强制呼吸止损", force=True,
                        )
                        if sync.get("ok"):
                            notes.append(
                                f"呼吸止损@{sync.get('target'):.2f}"
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
        全链防线：TP1+TP2 限价 + 呼吸止损单槽（唯一止损写入）。
        开仓即跑 breath_stop；TP1/TP2 成交只通知引擎缩量，不独立强制改价。
        重启禁止用历史 best 误触保本。
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
        # hung is None = 查询失败，禁止当成裸仓
        hung = binance_client.find_protective_stop_prices(self.symbol)
        stop_missing = hung is not None and not hung
        tp_incomplete = (
            int(audit.get("expected") or 0) > 0
            and int(audit.get("matched_full") or 0) < int(audit.get("expected") or 0)
        )
        if live_qty > 0 and (stop_missing or tp_incomplete):
            logger.error(
                f"🚨 [{source}] 终检防线未齐 TP "
                f"{audit.get('matched_full', 0)}/{audit.get('expected', 0)} "
                f"stop={hung} 已过={getattr(self, 'tp_levels_consumed', [])} → 强制闭环"
            )
            audit, hung = self._force_hang_open_defenses(
                live_qty, entry, rounds=2, takeover_mode=True,
            )
            shield_ok = bool(hung)
            if hung is not None and not hung:
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
                "手动增仓" if real_amt > saved_watched
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
                    "tp_levels_radar_handoff": list(
                        getattr(self, "tp_levels_radar_handoff", []) or []
                    ),
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
                    "tv_suggested_qty": float(getattr(self, "tv_suggested_qty", 0) or 0),
                    "tv_qty1": float(getattr(self, "tv_qty1", 0) or 0),
                    "tv_qty2": float(getattr(self, "tv_qty2", 0) or 0),
                    "tv_qty3": float(getattr(self, "tv_qty3", 0) or 0),
                    "radar_step_count": int(getattr(self, "radar_step_count", 0) or 0),
                    "radar_activated": bool(getattr(self, "radar_activated", False)),
                    "breakeven_phase": bool(getattr(self, "breakeven_phase", False)),
                    "initial_stop": float(getattr(self, "initial_stop", 0) or 0),
                    "last_adx": float(getattr(self, "last_adx", ADX_FALLBACK) or ADX_FALLBACK),
                    "remaining_qty_pct": float(getattr(self, "remaining_qty_pct", 1.0) or 1.0),
                    "breathing_coefficient": float(
                        getattr(self, "breathing_coefficient", 1.0) or 1.0
                    ),
                    "early_be_done": bool(getattr(self, "early_be_done", False)),
                    "breath_profile_name": str(
                        (getattr(self, "breath_profile", None) or {}).get("name") or ""
                    ),
                    "atr_1h_ratio_history": list(
                        getattr(self, "_breath_ratio_history", None) or []
                    ),
                    "last_open_exec_ts": float(
                        getattr(self, "_last_open_exec_ts", 0) or 0
                    ),
                    "atr_last_update_ts": float(getattr(self, "_atr_last_update_ts", 0) or 0),
                    "tp_order_placed_ts": dict(getattr(self, "_tp_order_placed_ts", {}) or {}),
                    "defense_order_ids": dict(getattr(self, "_defense_order_ids", {}) or {}),
                    "frozen_hard_sl_px": float(getattr(self, "frozen_hard_sl_px", 0) or 0),
                    "trading_paused": bool(getattr(self, "trading_paused", False)),
                    "trading_pause_reason": str(getattr(self, "trading_pause_reason", "") or ""),
                    "atr_div_streak": int(getattr(self, "_atr_div_streak", 0) or 0),
                    "atr_source": str(getattr(self, "atr_source", "vps") or "vps"),
                    "atr_degraded": bool(getattr(self, "atr_degraded", False)),
                    "atr_scenario": int(getattr(self, "_atr_scenario", 0) or 0),
                    "tp3_fallback_active": bool(
                        getattr(self, "_tp3_fallback_active", False)
                    ),
                    "temp_stop_active": bool(getattr(self, "_temp_stop_active", False)),
                    "last_bar_time_ms": int(getattr(self, "_last_bar_time_ms", 0) or 0),
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
        from binance_client import is_position_query_failed
        pos = binance_client.get_position(self.symbol, prefer_ws=prefer_ws)
        if is_position_query_failed(pos):
            return "QUERY_FAILED"
        if not pos or float(pos.get("positionAmt", 0) or 0) == 0:
            return None
        amt = float(pos["positionAmt"])
        return {
            "size": abs(amt),
            "entry_price": round(float(pos.get("entryPrice", 0)), 2),
            "side": "LONG" if amt > 0 else "SHORT",
        }

    def _on_position_query_failed(self, source=""):
        """查询失败：保留账本，禁止清场/平仓归因，钉钉限频告警。"""
        now = time.time()
        last = float(getattr(self, "_pos_query_fail_alert_ts", 0) or 0)
        logger.error(
            f"🚨 [{self.symbol}] 持仓查询失败 → 保留账本/跳过空仓判定 | {source}"
        )
        if now - last < 60:
            return
        self._pos_query_fail_alert_ts = now
        try:
            self._call_dingtalk(
                dingtalk.report_system_alert,
                title=f"持仓查询失败·保留账本 [{self.symbol}]",
                detail=(
                    f"source={source or '—'} | watched_qty="
                    f"{float(getattr(self, 'watched_qty', 0) or 0)} "
                    f"side={getattr(self, 'current_side', None)} | "
                    f"REST/WS 均不可用且无有效缓存 → 禁止当空仓清账本"
                ),
                level="紧急",
                suggestion="检查币安 API/网络；哨兵将跳过空仓判定直至查询恢复",
            )
        except Exception as e:
            logger.warning(f"持仓查询失败钉钉跳过: {e}")

    def _probe_position_for_recover(self):
        """
        重启专用持仓探测：强制 REST 多轮；若仓位空但挂单仍在 → 禁止报空仓/清挂单。
        返回: dict 持仓 | None 确认空仓 | "AMBIGUOUS" 查询与挂单矛盾
        """
        last = None
        saw_query_fail = False
        for i in range(6):
            last = self._get_active_position(prefer_ws=False)
            if last == "QUERY_FAILED":
                saw_query_fail = True
                time.sleep(0.55)
                continue
            if last and float(last.get("size") or 0) > 0:
                if i > 0:
                    logger.info(
                        f"🔄 [{self.symbol}] 重启持仓探测第{i + 1}轮命中 "
                        f"{last['side']} {last['size']}"
                    )
                return last
            time.sleep(0.55)
        if saw_query_fail:
            logger.error(
                f"🚨 [{self.symbol}] 重启持仓探测全程 QUERY_FAILED "
                f"→ 禁止空仓清场，交哨兵接力"
            )
            return "AMBIGUOUS"
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
        if pos == "QUERY_FAILED":
            return False
        return pos is None

    def _count_open_limits_and_stops(self):
        """
        统计盘口 LIMIT + STOP（含 Algo）。
        成功返回 (n_limit, n_stop, all_orders)；查单失败返回 None。
        开仓前净场：任意 LIMIT/STOP >0 都算未净（含非 reduceOnly 幽灵限价）。
        """
        orders = binance_client.get_open_orders(self.symbol, include_algo=True)
        if is_orders_query_failed(orders):
            return None
        n_limit = 0
        n_stop = 0
        for o in orders or []:
            ot = str(o.get("type") or o.get("orderType") or "").upper()
            if ot == "LIMIT":
                n_limit += 1
            elif ot in ("STOP", "STOP_MARKET") or "STOP" in ot:
                n_stop += 1
        return n_limit, n_stop, list(orders or [])

    def _verify_sterile_flat(self):
        """
        无菌空仓铁律：持仓=0 且 挂单=0（含全部限价/止损/Algo）。
        查仓/查单失败 → False（禁止当成已净场去开仓）。
        """
        if not self._verify_flat():
            return False
        counted = self._count_open_limits_and_stops()
        if counted is None:
            logger.warning(
                f"🛡️ [{self.symbol}] 无菌核查：挂单不可读 → 判未净（禁开仓）"
            )
            return False
        n_limit, n_stop, orders = counted
        if n_limit > 0 or n_stop > 0 or len(orders) > 0:
            return False
        # 双保险：TP 分类收集也必须可读且为空
        tp_left = self._collect_tp_limit_orders()
        if is_orders_query_failed(tp_left):
            return False
        return len(tp_left) == 0

    def _sterile_flat_gate(self, reason_tag="开仓前", force_close=True, notify=True):
        """
        先平后开无菌闸：撤单 → 平仓 → 再撤单 → 扫孤儿 → 验 qty=0+orders=0。
        「平」= 无持仓 + 无限价 + 无止损/Algo，清爽后再开。
        TV 同K线 / 同秒开+平：永远先平后开；开仓前必须净场。
        即使 TV 把 OPEN 标成更小 seq，缓冲层已强制重排。
        notify=False 时仅记日志（供外层重试汇总告警，避免刷屏）。
        """
        tag = reason_tag or "无菌清场"
        prev_side = self.current_side
        # 1) 先撤一切防御单，避免平仓过程中 TP 成交反向开仓
        self._purge_all_defense_orders_on_flat(f"{tag}·开仓前抢先撤单")
        time.sleep(0.35)
        # 2) 查仓失败 → fail-closed 拒开（禁止把 QUERY_FAILED 当残留仓去强平）
        pos_now = self._get_active_position()
        if pos_now == "QUERY_FAILED":
            detail = "持仓=QUERY_FAILED | REST不可读·禁当残留强平"
            logger.error(f"❌ [{tag}] {detail} → 拒绝开仓")
            self._last_sterile_flat_fail_detail = detail
            return False
        # 3) 有仓则阶梯强平（含平后撤单）
        if pos_now is not None:
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
        # 4) 平后再撤一轮（CLOSE→OPEN 间隔极短时残留 Algo/限价/幽灵单）
        purge = self._purge_all_defense_orders_on_flat(f"{tag}·平后净挂单", max_rounds=8)
        # 5) 扫孤儿反向（残留 TP 在空仓成交）
        self._sweep_orphan_reverse_after_flat(prev_side=prev_side, reason=tag)
        time.sleep(0.35)
        # 6) 终检：仓+单皆零（含任意 LIMIT 幽灵单）
        if self._wait_verify(self._verify_sterile_flat, retries=8, delay=0.45):
            logger.info(
                f"🧹 [{tag}] 无菌空仓通过 | qty=0 limits=0 stops=0 | "
                f"撤轮={purge.get('rounds')} TP撤={purge.get('tp_cancelled', 0)}"
            )
            self._last_close_flat_ts = time.time()
            return True
        remaining = self._remaining_open_order_count()
        counted = self._count_open_limits_and_stops()
        if counted is None:
            n_limit, n_stop = -1, -1
            tp_left = []
        else:
            n_limit, n_stop, _ = counted
            tp_left = self._collect_tp_limit_orders()
            if is_orders_query_failed(tp_left):
                tp_left = []
        pos = self._get_active_position()
        if pos == "QUERY_FAILED":
            pos_txt = "QUERY_FAILED"
        elif not pos:
            pos_txt = "无"
        else:
            pos_txt = f"{pos.get('side')} {pos.get('size')}"
        detail = (
            f"持仓={pos_txt} | 挂单={remaining} | "
            f"LIMIT={n_limit} STOP={n_stop} | TP残留={len(tp_left)}"
        )
        logger.error(f"❌ [{tag}] 无菌空仓失败 → 拒绝开仓 | {detail}")
        if notify:
            try:
                self._call_dingtalk(
                    dingtalk.report_system_alert,
                    title=f"无菌空仓失败·拒绝开仓 [{self.symbol}]",
                    detail=f"{tag} | {detail} | 防残留限价/幽灵单成交导致反手/超档位",
                    level="紧急",
                    suggestion="币安 APP 手动全部撤单+平仓后，等下一根 TV 信号",
                )
            except Exception:
                pass
        self._last_sterile_flat_fail_detail = detail
        return False

    def _ensure_flat_before_open(self, reason_tag="开仓前"):
        """
        开仓前一律无菌净场（有仓强平+撤单；空仓也清残留挂单）。
        平仓/净场失败：重试 3 次，间隔 1s/3s/6s；仍失败 → CLOSE_THEN_OPEN_FAIL_ABORT
        （放弃本笔开仓 + 高优钉钉 + 暂停该 symbol 自动开仓，需人工 /admin/resume）。
        """
        tag = reason_tag or "开仓前"
        delays = (1.0, 3.0, 6.0)
        n = len(delays)
        last_detail = ""
        for attempt in range(1, n + 1):
            ok = self._sterile_flat_gate(
                reason_tag=f"{tag}·尝试{attempt}/{n}",
                force_close=True,
                notify=False,
            )
            if ok:
                return True
            last_detail = str(getattr(self, "_last_sterile_flat_fail_detail", "") or "")
            wait = float(delays[attempt - 1])
            logger.warning(
                f"⚠️ [{self.symbol}] 先平后开净场失败，间隔 {wait:.0f}s 后"
                f"{'继续重试' if attempt < n else '宣告中止'} "
                f"({attempt}/{n}) | {last_detail}"
            )
            time.sleep(wait)
        # 3 次仍失败：放弃开仓 + 暂停自动开仓（平仓结果不明时严禁继续开新仓）
        self.trading_paused = True
        self.trading_pause_reason = f"CLOSE_THEN_OPEN_FAIL_ABORT|{tag}"
        try:
            self._save_state()
        except Exception:
            pass
        logger.error(
            f"🚨 [{self.symbol}] CLOSE_THEN_OPEN_FAIL_ABORT | {tag} | "
            f"重试{n}次仍未净场 → 已暂停自动开仓 | {last_detail}"
        )
        try:
            self._call_dingtalk(
                dingtalk.report_close_then_open_fail_abort,
                symbol=self.symbol,
                attempts=n,
                reason=tag,
                detail=last_detail or "qty/挂单未净，平仓结果不明",
            )
        except Exception:
            try:
                self._call_dingtalk(
                    dingtalk.report_system_alert,
                    title=f"清仓失败·需人工介入 [{self.symbol}]",
                    detail=(
                        f"CLOSE_THEN_OPEN_FAIL_ABORT | {tag} | 重试{n}次"
                        f"(间隔 1s/3s/6s) 仍失败 | {last_detail}\n"
                        f"本笔开仓已放弃，该品种自动开仓已暂停"
                    ),
                    level="紧急",
                    suggestion=(
                        "1) 币安 APP 核对真实持仓与挂单并手动清净；"
                        "2) POST /admin/resume/" + str(self.symbol) + " 恢复自动开仓"
                    ),
                    immediate=True,
                )
            except Exception:
                pass
        return False

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

    def _apply_tv_sizing_params(self, payload):
        """v15.5.1：RISK20 + TV止损距调整系数；分腿仅 TP1+TP2。"""
        self.tv_entry_type = ENTRY_TYPE_OPEN
        self.tv_risk_pct = 0.0
        self.tv_qty_ratio = 1.0
        self.tv_sizing_leverage = float(FIXED_LEVERAGE)
        self.leverage = float(FIXED_LEVERAGE)
        # 每次绑定覆盖（含 <=0）：禁止沿用上笔残留 TV.qty / SL 参考
        tv_qty = self._safe_float((payload or {}).get("qty"), 0)
        self.tv_suggested_qty = float(tv_qty) if tv_qty > 0 else 0.0
        # TV 分腿数量意图（qty3 不挂；qty1/qty2 按实开仓缩放后挂限价）
        self.tv_qty1 = self._safe_float((payload or {}).get("qty1"), 0)
        self.tv_qty2 = self._safe_float((payload or {}).get("qty2"), 0)
        self.tv_qty3 = self._safe_float((payload or {}).get("qty3"), 0)
        # TV stop_loss 仅作 sizing 调整系数输入（不挂盘）
        tv_sl = self._safe_float(
            (payload or {}).get("stop_loss")
            or (payload or {}).get("tv_sl")
            or (payload or {}).get("sl"),
            0,
        )
        self.tv_sl_ref = float(tv_sl) if tv_sl > 0 else 0.0
        ratios = get_leg_tp_ratios(payload)
        for k in self.regime_settings:
            self.regime_settings[k]["ratios"] = list(ratios)
        self._leg_ratios = list(ratios)
        self._save_state()
        logger.info(
            f"📐 仓位参数: 风险{FIXED_RISK_PCT * 100:.0f}%/VPS止损距 · "
            f"名义=本金×{FIXED_RISK_PCT * 100:.0f}%×{FIXED_NOTIONAL_MULT:.0f}"
            f"(=本金×{FIXED_RISK_PCT * FIXED_NOTIONAL_MULT:.0f}) "
            f"| TV.qty={self.tv_suggested_qty or '-'} "
            f"| TV.qty1/2/3={self.tv_qty1 or '-'}/{self.tv_qty2 or '-'}/{self.tv_qty3 or '-'} "
            f"| TV.sl_ref={float(getattr(self, 'tv_sl_ref', 0) or 0) or '-'} "
            f"| 挂TP1+TP2(价格=tv tp；数量=qty1/qty2按实开缩放) | sizing={SIZING_MODE}"
        )

    def _tag(self):
        """日志/钉钉品种短标签：[ETH] / [XAU]。"""
        return str(getattr(self, "tag", None) or self.unit_label or self.symbol or "?").upper()

    def _resolve_open_atr_with_degrade(self, px, tv_sl_ref=None):
        """
        开仓 ATR：必须用 TV webhook.atr 作为 initial_atr（缺则拒绝）。
        1h ATR 只喂呼吸系数，不覆盖已锁定 initial_atr。
        """
        locked = float(getattr(self, "open_atr", 0) or 0)
        if float(getattr(self, "watched_qty", 0) or 0) > 0:
            atr = locked if locked > 0 else float(getattr(self, "current_atr", 0) or 0)
            return atr, {
                "vps_atr": 0.0,
                "tv_implied_atr": 0.0,
                "div_pct": 0.0,
                "div_streak": 0,
                "reason": "hold_locked_tv_atr",
                "degrade": False,
                "source": "tv_locked" if locked > 0 else "missing",
                "klines_ok": True,
                "atr": atr,
            }

        tv_atr = float(getattr(self, "_tv_signal_atr", 0) or 0)
        meta = {
            "vps_atr": 0.0,
            "tv_implied_atr": 0.0,
            "div_pct": 0.0,
            "div_streak": 0,
            "reason": "tv_atr",
            "degrade": False,
            "source": "tv",
            "klines_ok": True,
            "atr": float(tv_atr or 0),
        }
        if tv_atr > 0:
            self.atr_source = "tv"
            self.atr_degraded = False
            self._pending_atr_degrade = None
            return float(tv_atr), meta

        meta["reason"] = "missing_tv_atr"
        meta["source"] = "reject"
        self.atr_source = "reject"
        self.atr_degraded = False
        try:
            dingtalk.report_system_alert(
                f"[{self._tag()}] 开仓拒绝·缺TV atr",
                f"{self.symbol} webhook 无 atr 或 atr≤0 → 拒绝开仓（禁止用1h/90m冒充 initial_atr）",
                level="紧急",
                suggestion="检查 TV get_entry_json 是否传 atr 字段",
            )
        except Exception:
            pass
        return 0.0, meta

    def _atr_1h_engine(self):
        return get_atr_1h_engine(self.symbol, binance_client.fetch_klines)

    def _effective_place_tp_levels(self):
        """场景一=2；场景二=3（TP3兜底）。未决时按场景一不挂TP3。"""
        sc = int(getattr(self, "_atr_scenario", 0) or 0)
        if sc == SCENARIO_TV or bool(getattr(self, "_tp3_fallback_active", False)):
            return 3
        if sc == SCENARIO_VPS:
            return 2
        return place_tp_levels_for_scenario(SCENARIO_VPS)

    def _temp_hard_stop_from_tv(self, entry=None, side=None, tv_sl=None):
        """
        永久硬止损价（v15.7.8）：
          基础 = max(|TV价−TV.SL|×1.2, 1.5×initial_atr×1.05)
          + 滑点缓冲 |成交价−TV价|×2
          挂在成交价外侧。
        entry 参数 = 交易所成交价；TV 理论开仓价取 self.tv_price。
        """
        fill = float(
            entry if entry is not None else (self.watched_entry or self.tv_price or 0)
        )
        side = str(side or self.current_side or "").strip().upper()
        tv_sl = float(
            tv_sl if tv_sl is not None else (
                getattr(self, "tv_sl_ref", 0) or 0
            )
        )
        tv_entry = float(getattr(self, "tv_price", 0) or 0)
        if tv_entry <= 0:
            # 无 TV 信号价时退化为成交价（滑点=0）
            tv_entry = fill
        atr = float(
            self._get_locked_initial_atr()
            or getattr(self, "open_atr", 0)
            or getattr(self, "_tv_signal_atr", 0)
            or 0
        )
        return hard_stop_price(
            side,
            fill,
            tv_sl,
            tv_entry=tv_entry,
            initial_atr=atr,
            fill_entry=fill,
        )

    def _hard_stop_distance_meta(self, fill=None, tv_sl=None, tv_entry=None, atr=None):
        """调试/钉钉：硬止损距离拆解。"""
        fill = float(fill if fill is not None else (self.watched_entry or 0))
        tv_entry = float(
            tv_entry if tv_entry is not None else (getattr(self, "tv_price", 0) or fill)
        )
        tv_sl = float(
            tv_sl if tv_sl is not None else (getattr(self, "tv_sl_ref", 0) or 0)
        )
        atr = float(
            atr if atr is not None else (
                self._get_locked_initial_atr()
                or getattr(self, "open_atr", 0)
                or 0
            )
        )
        return compute_hard_stop_distance(tv_entry, tv_sl, fill, atr)

    def _remount_frozen_hard_sl_wider(self, reason="硬止损加宽重挂·v15.7.8"):
        """
        公式升级：仅当新硬止损比旧更宽（更多缓冲）时，撤 closePosition 旧硬止损并重挂。
        不触碰雷达 reduceOnly 定量腿。
        """
        side = str(self.current_side or "").strip().upper()
        fill = float(self.watched_entry or 0)
        live_qty = float(self._resolve_live_qty(self.watched_qty) or 0)
        if side not in ("LONG", "SHORT") or fill <= 0 or live_qty <= 0:
            logger.warning(f"🛡️ [{self.symbol}] {reason} 跳过：无持仓")
            return False

        # 尽力恢复 tv_sl_ref（曾被雷达价覆盖）
        tv_sl = float(getattr(self, "tv_sl_ref", 0) or 0)
        if tv_sl <= 0:
            last = self.last_tv_signal if isinstance(self.last_tv_signal, dict) else {}
            pl = last.get("payload") if isinstance(last.get("payload"), dict) else {}
            tv_sl = float(
                self._safe_float(pl.get("stop_loss") or pl.get("tv_sl") or last.get("stop_loss"), 0)
                or 0
            )
            if tv_sl > 0:
                self.tv_sl_ref = tv_sl
        tv_entry = float(getattr(self, "tv_price", 0) or 0)
        if tv_entry <= 0:
            last = self.last_tv_signal if isinstance(self.last_tv_signal, dict) else {}
            tv_entry = float(self._safe_float(last.get("price") or last.get("tv_price"), 0) or 0)
            if tv_entry > 0:
                self.tv_price = tv_entry

        new_hard = float(self._temp_hard_stop_from_tv(fill, side, tv_sl=tv_sl) or 0)
        old_hard = self._frozen_hard_px()
        if new_hard <= 0:
            logger.error(f"🛡️ [{self.symbol}] {reason} 失败：无法计算新硬止损")
            return False

        # SHORT：更宽=更高；LONG：更宽=更低
        wider = (
            (side == "SHORT" and new_hard > old_hard + 0.05)
            or (side == "LONG" and (old_hard <= 0 or new_hard < old_hard - 0.05))
        )
        meta = self._hard_stop_distance_meta(fill=fill, tv_sl=tv_sl, tv_entry=tv_entry)
        if not wider and old_hard > 0:
            logger.info(
                f"🛡️ [{self.symbol}] {reason} 跳过：新@{new_hard:.2f} 未宽于旧@{old_hard:.2f} "
                f"| dist={meta}"
            )
            return True

        if not self._orders_book_readable():
            logger.error(f"🛡️ [{self.symbol}] {reason} 中止：挂单不可读")
            return False

        cancelled = self._purge_all_close_position_stops()
        if cancelled < 0:
            logger.error(f"🛡️ [{self.symbol}] {reason} 中止：无法撤旧 closePosition")
            return False
        self.frozen_hard_sl_px = float(new_hard)
        self._save_state()
        time.sleep(0.35)
        ok = self._ensure_frozen_hard_sl(live_qty, reason=reason)
        logger.warning(
            f"🛡️ [{self.symbol}] {reason}: 旧@{old_hard:.2f} → 新@{new_hard:.2f} "
            f"撤closePos={cancelled} ok={ok} | {meta}"
        )
        try:
            dingtalk.report_system_alert(
                f"硬止损加宽重挂 [{self.symbol}]",
                f"{side} fill={fill:.2f} | 旧@{old_hard:.2f} → 新@{new_hard:.2f} | "
                f"base={meta.get('base'):.2f} slip={meta.get('slip'):.2f} "
                f"final={meta.get('final'):.2f} | radar不动",
            )
        except Exception:
            pass
        return bool(ok)

    def _lock_initial_atr_value(self, atr, *, upgrade=False):
        """写入/锁定 initial_atr；upgrade=True 允许场景二→一覆盖。"""
        atr = float(atr or 0)
        if atr <= 0:
            return 0.0
        try:
            if upgrade and getattr(self, "_locked_initial_atr", None) is not None:
                self._locked_initial_atr.upgrade_to_vps(atr)
            elif getattr(self, "_locked_initial_atr", None) is not None:
                if self._locked_initial_atr.locked:
                    if abs(self._locked_initial_atr.value - atr) > 1e-6 and not upgrade:
                        atr = float(self._locked_initial_atr.value)
                    elif upgrade:
                        self._locked_initial_atr.upgrade_to_vps(atr)
                    else:
                        atr = float(self._locked_initial_atr.value)
                else:
                    self._locked_initial_atr.set_on_open(atr)
        except Exception as e:
            logger.warning(f"[{self.symbol}] initial_atr lock: {e}")
        self.open_atr = float(atr)
        self.current_atr = float(atr)
        return float(atr)

    def _arm_temp_stop_and_tp12(self, live_qty, entry, side, source="开仓共同第一步"):
        """
        两场景共同第一步：永久硬止损(max(TV×1.2,1.5×ATR×1.05)+滑点×2) + TP1/TP2，不挂TP3。
        frozen_hard_sl_px 挂出后直至 flat 才清零（公式升级重挂除外）。
        """
        live_qty = float(live_qty or 0)
        entry = float(entry or 0)
        side = str(side or "").strip().upper()
        if live_qty <= 0 or entry <= 0 or side not in ("LONG", "SHORT"):
            return False
        temp_sl = self._temp_hard_stop_from_tv(entry, side)
        if temp_sl <= 0:
            # 无 TV.stop_loss 时用 TV atr 的 1.5× 作临时保护（仍先保命）
            tv_atr = float(getattr(self, "_tv_signal_atr", 0) or self.open_atr or 0)
            if tv_atr > 0:
                temp_sl = float(
                    initial_stop_price(
                        side, entry, tv_atr,
                        profile=getattr(self, "breath_profile", None),
                    ) or 0
                )
        if temp_sl <= 0:
            logger.error(f"🚨 [{self.symbol}] {source} 无法计算永久硬止损")
            return False

        meta = self._hard_stop_distance_meta(fill=entry)
        logger.info(
            f"🛡️ [{self.symbol}] {source} 硬止损算距: "
            f"tv_implied={meta.get('tv_implied'):.2f} "
            f"radar_floor={meta.get('radar_floor'):.2f} "
            f"slip={meta.get('slip'):.2f} final={meta.get('final'):.2f} "
            f"→ @{temp_sl:.2f}"
        )
        self._atr_scenario = 0
        self._temp_stop_active = True
        self._tp3_fallback_active = False
        self.frozen_hard_sl_px = float(temp_sl)
        self.initial_stop = float(temp_sl)
        self.current_sl = float(temp_sl)
        self.tv_sl = float(temp_sl)
        self._save_state()

        hard_ok = self._ensure_frozen_hard_sl(live_qty, reason=f"{source}·永久硬止损")
        placed_tp = self._place_tp_levels_only(live_qty, retries=2)
        logger.info(
            f"🛡️ [{self.symbol}] {source}: 永久硬止损@{temp_sl:.2f} "
            f"hard={bool(hard_ok)} TP挂出={placed_tp} (未挂TP3)"
        )
        return bool(hard_ok) or placed_tp > 0

    def _enter_atr_scenario_1(self, entry, side, live_qty, vps_atr, *, recovered=False):
        """场景一：VPS真实ATR接管，撤TP3，呼吸引擎用真实ATR。"""
        entry = float(entry or 0)
        side = str(side or "").strip().upper()
        live_qty = float(live_qty or 0)
        vps_atr = float(vps_atr or 0)
        if entry <= 0 or side not in ("LONG", "SHORT") or vps_atr <= 0:
            return False
        atr = self._lock_initial_atr_value(vps_atr, upgrade=True)
        profile = getattr(self, "breath_profile", None)
        init = initial_stop_price(side, entry, atr, profile=profile)
        self.initial_stop = float(init or 0)
        self.current_sl = float(init or 0)
        self.tv_sl = float(init or 0)
        self.atr_source = "vps"
        self.atr_degraded = False
        self._pending_atr_degrade = None
        self._atr_scenario = SCENARIO_VPS
        self._temp_stop_active = False
        had_tp3 = bool(getattr(self, "_tp3_fallback_active", False))
        self._tp3_fallback_active = False
        try:
            self._breath_ratio_history = []
            self._atr_1h_engine().reset_ratio_history()
        except Exception:
            pass
        self._refresh_breathing_coefficient(force=True)

        if had_tp3 or recovered:
            try:
                self._cancel_tp_orders_at_levels([3])
            except Exception as e:
                logger.warning(f"[{self.symbol}] 撤TP3失败: {e}")

        if live_qty > 0 and self.initial_stop > 0:
            self._sync_exchange_stop(
                live_qty, float(self.initial_stop), force=True,
                reason=("场景一恢复·雷达止损" if recovered else "场景一·雷达止损"),
            )
        self._save_state()
        msg = scenario_notice(
            SCENARIO_VPS, vps_atr=atr, recovered=recovered,
        )
        if msg:
            logger.info(f"✅ [{self.symbol}] {msg}")
            try:
                dingtalk.report_system_alert(
                    f"[{self._tag()}] VPS真实ATR已接管",
                    msg,
                    level="提示",
                )
            except Exception:
                pass
        else:
            logger.info(
                f"✅ [{self.symbol}] 场景一: VPS真实ATR={atr:.4f} "
                f"initialStop={self.initial_stop:.2f}（不挂TP3）"
            )
        return True

    def _enter_atr_scenario_2(self, entry, side, live_qty, tv_atr):
        """场景二：TV atr 降级；挂TP3；不暂停交易。"""
        entry = float(entry or 0)
        side = str(side or "").strip().upper()
        live_qty = float(live_qty or 0)
        tv_atr = float(tv_atr or 0)
        if entry <= 0 or side not in ("LONG", "SHORT") or tv_atr <= 0:
            return False
        atr = self._lock_initial_atr_value(tv_atr, upgrade=False)
        profile = getattr(self, "breath_profile", None)
        init = initial_stop_price(side, entry, atr, profile=profile)
        # 若已有临时止损，正式 initial_stop 用 TV atr 公式；盘口立刻对齐
        self.initial_stop = float(init or self.initial_stop or 0)
        self.current_sl = float(self.initial_stop or 0)
        self.tv_sl = float(self.current_sl or 0)
        self.atr_source = "tv"
        self.atr_degraded = False
        self._pending_atr_degrade = None
        self._atr_scenario = SCENARIO_TV
        self._temp_stop_active = False
        self._tp3_fallback_active = True
        try:
            self._breath_ratio_history = []
            self._atr_1h_engine().reset_ratio_history()
        except Exception:
            pass
        self._refresh_breathing_coefficient(force=True)

        if live_qty > 0 and self.initial_stop > 0:
            self._sync_exchange_stop(
                live_qty, float(self.initial_stop), force=True,
                reason="场景二·雷达止损",
            )
        # 挂 TP3（受既有同价去重/查簿防护约束）
        try:
            placed = self._place_tp_levels_only(live_qty, retries=2)
            logger.info(f"📈 [{self.symbol}] 场景二 TP 对齐(含TP3) placed={placed}")
        except Exception as e:
            logger.error(f"[{self.symbol}] 场景二挂TP3失败: {e}")

        self._save_state()
        msg = scenario_notice(SCENARIO_TV, tv_atr=atr) or ""
        logger.warning(f"⚠️ [{self.symbol}] {msg}")
        try:
            dingtalk.report_system_alert(
                f"[{self._tag()}] VPS真实ATR获取失败·已用TV理论ATR",
                msg,
                level="提示",
                suggestion="系统将持续尝试恢复真实ATR并自动撤销TP3",
            )
        except Exception:
            pass
        return True

    def _resolve_atr_scenario_after_open(self, entry, side, live_qty):
        """开仓流程内同步完成：拉原生1h ATR → 场景一或场景二。"""
        entry = float(entry or self.watched_entry or 0)
        side = str(side or self.current_side or "").strip().upper()
        live_qty = float(live_qty or self.watched_qty or 0)
        tv_atr = float(
            getattr(self, "_tv_signal_atr", 0)
            or getattr(self, "open_atr", 0)
            or 0
        )
        vps_atr = 0.0
        try:
            vps_atr = float(self._atr_1h_engine().refresh(force=True) or 0)
        except Exception as e:
            logger.warning(f"[{self.symbol}] 开仓拉取1h ATR失败: {e}")
            vps_atr = 0.0
        sc, atr, src = resolve_atr_scenario(vps_atr, tv_atr)
        if sc == SCENARIO_VPS:
            return self._enter_atr_scenario_1(entry, side, live_qty, atr)
        if sc == SCENARIO_TV:
            return self._enter_atr_scenario_2(entry, side, live_qty, atr)
        logger.error(
            f"🚨 [{self.symbol}] ATR场景无法决议 vps={vps_atr} tv={tv_atr} "
            f"→ 维持临时止损，不暂停交易"
        )
        return False

    def _maybe_recover_atr_scenario(self, entry=None, side=None, live_qty=None):
        """场景二 tick：持续尝试恢复 VPS 真实 ATR → 切场景一并撤TP3。"""
        if int(getattr(self, "_atr_scenario", 0) or 0) != SCENARIO_TV:
            return False
        if not bool(getattr(self, "_tp3_fallback_active", False)):
            # 仍允许场景二恢复
            pass
        entry = float(entry if entry is not None else (self.watched_entry or 0))
        side = str(side or self.current_side or "").strip().upper()
        live_qty = float(
            live_qty if live_qty is not None else (self.watched_qty or 0)
        )
        try:
            vps_atr = float(self._atr_1h_engine().refresh(force=False) or 0)
        except Exception:
            return False
        if vps_atr <= 0:
            return False
        return self._enter_atr_scenario_1(
            entry, side, live_qty, vps_atr, recovered=True,
        )

    def _refresh_breathing_coefficient(self, force=False):
        """用币安 1h ATR / initial_atr 更新呼吸系数（3 次平滑 · 按品种档位表）。"""
        init = float(getattr(self, "open_atr", 0) or 0)
        if init <= 0:
            return float(getattr(self, "breathing_coefficient", 1.0) or 1.0)
        eng = self._atr_1h_engine()
        eng.ratio_history = list(getattr(self, "_breath_ratio_history", None) or [])
        profile = getattr(self, "breath_profile", None)
        coeff, meta = eng.breathing_coefficient(
            init, force_refresh=force, profile=profile,
        )
        self.breathing_coefficient = float(coeff or 1.0)
        self._breath_ratio_history = list(meta.get("ratio_history") or [])
        self._breath_coeff_meta = meta
        # current_atr 仅展示实时 1h，不改 open_atr
        if float(meta.get("atr_1h") or 0) > 0:
            self.current_atr = float(meta["atr_1h"])
        self._atr_last_update_ts = time.time()
        return self.breathing_coefficient

    def _stop_buffer_usd(self):
        p = getattr(self, "breath_profile", None) or {}
        try:
            return abs(float(p.get("stop_exec_buffer") or STOP_EXEC_BUFFER_USD))
        except (TypeError, ValueError):
            return float(STOP_EXEC_BUFFER_USD)

    def _circuit_breaker_blocks_open(self):
        """日亏 ≥5.5% 本金 / 连续亏 / 次数 / 回撤 → 拒开仓。"""
        try:
            from risk_manager import risk_manager
            equity = float(self._resolve_cap_sizing_base() or 0)
            if hasattr(risk_manager, "is_trading_allowed_for_equity"):
                ok = risk_manager.is_trading_allowed_for_equity(equity)
            else:
                # 兼容：用权益换算日亏阈值
                risk_manager._reset_daily_if_needed()
                if equity > 0 and float(risk_manager.daily_pnl or 0) <= -0.055 * equity:
                    ok = False
                else:
                    ok = risk_manager.is_trading_allowed()
            if not ok:
                st = risk_manager.get_status() if hasattr(risk_manager, "get_status") else {}
                logger.warning(
                    f"🚫 [{self._tag()}] 熔断拒开仓 | daily_pnl={st.get('daily_pnl')} "
                    f"consec={st.get('consecutive_losses')} trades={st.get('today_trade_count')}"
                )
                try:
                    dingtalk.report_system_alert(
                        f"[{self._tag()}] 开仓拒绝·熔断",
                        f"{self.symbol} 触发日亏/连续亏/次数/回撤熔断 | {st}",
                        level="紧急",
                        suggestion="等待次日重置或人工确认后恢复",
                    )
                except Exception:
                    pass
                return True
        except Exception as e:
            logger.warning(f"[{self._tag()}] 熔断检查异常(放行): {e}")
        return False

    def _should_ignore_late_close(self, payload=None):
        """
        开仓成交后 LATE_CLOSE_SUPPRESS_SEC 内的迟到 CLOSE → 忽略，保护刚开仓。
        同窗先平后开链（_close_open_chain_active）不忽略。
        """
        if getattr(self, "_close_open_chain_active", False):
            return False
        last_ts = float(getattr(self, "_last_open_exec_ts", 0) or 0)
        if last_ts <= 0:
            return False
        age = time.time() - last_ts
        if age < 0 or age > float(LATE_CLOSE_SUPPRESS_SEC):
            return False
        # 有实盘仓才保护
        pos = self._get_active_position()
        if not pos or float(pos.get("size") or 0) <= 0:
            return False
        return True

    def _finalize_atr_degrade_after_open(self, entry, qty, side):
        """
        旧逻辑：ATR 降级后暂停 symbol —— 已废除（两场景定稿）。
        现仅清 pending；场景二由 _enter_atr_scenario_2 处理（不暂停、挂TP3）。
        """
        self._pending_atr_degrade = None
        self.atr_degraded = False
        logger.info(
            f"ℹ️ [{self.symbol}] ATR降级暂停已废除 | side={side} qty={qty} "
            f"entry={float(entry or 0):.2f} → 走两场景路径，不暂停交易"
        )

    def _calc_vps_open_qty(self, curr_px, regime=None):
        """
        无状态独立计算（仅开仓时一次）：
          qty = (principal × 0.20 × 5 × NOTIONAL_MARGIN_HAIRCUT) / price
          stop_loss 可选收紧；TV.qty 可选 soft-cap（非必须）
        ATR/stop 仍用于雷达 initialStop 账本；ATR 缺失时仍允许纯名义 sizing。
        """
        principal = self._resolve_cap_sizing_base()
        px = float(curr_px or self.tv_price or 0)
        tv_qty = float(getattr(self, "tv_suggested_qty", 0) or 0)
        tv_sl_ref = float(getattr(self, "tv_sl_ref", 0) or 0)
        atr, atr_meta = self._resolve_open_atr_with_degrade(px, tv_sl_ref=tv_sl_ref)
        atr = float(atr or 0)
        side = str(
            getattr(self, "last_tv_side", None)
            or getattr(self, "current_side", None)
            or "LONG"
        ).upper()
        stop = float(initial_stop_price(
            side, px, atr, profile=getattr(self, "breath_profile", None),
        ) or 0) if atr > 0 else 0.0
        if principal <= 0 or px <= 0:
            logger.error(f"🚫 开仓 sizing 拒绝：权益={principal} price={px}")
            return 0.0, {
                "error": "missing_equity_or_price",
                "principal": principal,
                "leverage": FIXED_LEVERAGE,
                "sizing_mode": SIZING_MODE,
            }
        if atr <= 0 or stop <= 0:
            logger.warning(
                f"⚠️ [{self.symbol}] ATR不可用 atr={atr} stop={stop} "
                f"→ 纯名义 sizing（无 stop 收紧） degrade_meta={atr_meta}"
            )
        # 降级仅作用于「本笔即将成交」的临时 ATR；禁止在 sizing 试算/拒绝路径覆盖已锁定 open_atr
        if atr_meta.get("degrade"):
            self.current_atr = atr
            # 仅无仓/未锁定时才写入 open_atr；持仓中锁定不变
            if float(getattr(self, "watched_qty", 0) or 0) <= 0 and float(getattr(self, "open_atr", 0) or 0) <= 0:
                self.open_atr = atr
            elif float(getattr(self, "watched_qty", 0) or 0) > 0:
                now = time.time()
                last = float(getattr(self, "_open_atr_lock_warn_ts", 0) or 0)
                if now - last > 120:
                    self._open_atr_lock_warn_ts = now
                    logger.warning(
                        f"🛡️ [{self.symbol}] 持仓中拒覆盖 open_atr="
                        f"{float(getattr(self, 'open_atr', 0) or 0):.4f} "
                        f"← degrade ATR={float(atr or 0):.4f} (同类告警120s内去重)"
                    )

        qty, meta = compute_fixed_order_qty(
            principal=principal,
            price=px,
            stop_loss=stop if stop > 0 else None,
            tv_qty=tv_qty if tv_qty > 0 else None,
            tv_sl=tv_sl_ref if tv_sl_ref > 0 else None,
            tv_price=float(getattr(self, "tv_price", 0) or px),
            qty_step=float(getattr(self, "qty_step", 0.001) or 0.001),
            min_qty=float(getattr(self, "min_qty", 0.001) or 0.001),
        )
        meta["principal"] = principal
        meta["symbol"] = self.symbol
        meta["initial_stop"] = stop
        meta["atr"] = atr
        meta["atr_source"] = atr_meta.get("source") or "vps"
        meta["atr_degraded"] = bool(atr_meta.get("degrade"))
        meta["atr_degrade_reason"] = atr_meta.get("reason") or ""
        meta["atr_meta"] = atr_meta
        # 天文 TV.qty（Pine equity 膨胀）→ 上限失效，记录明确；真实生效约束见 binding
        try:
            q_notional = float(meta.get("qty_by_notional") or 0)
            q_risk = float(meta.get("qty_by_risk") or 0)
            sane_ref = max(q_notional, q_risk, 1.0)
            if tv_qty > sane_ref * 50:
                meta["tv_qty_absurd"] = True
                logger.warning(
                    f"⚠️ [{self.symbol}] TV.qty 异常巨大 {tv_qty} "
                    f"(≫ VPS候选 max={sane_ref:.4f}) → 仅作失效上限，"
                    f"生效约束={meta.get('binding')} qty={float(qty or 0):.4f}"
                )
        except Exception:
            pass
        # 保证金安全网：名义按「本金×20%×5(=本金×1)」算出后，
        # 再用 available×20%×5×0.92 裁一次，杜绝 -2019。
        try:
            summary = binance_client.get_futures_account_summary() or {}
            avail = float(summary.get("available_balance") or 0)
            if avail <= 0:
                avail = float(binance_client.get_available_balance() or 0)
            if avail > 0 and px > 0 and float(qty or 0) > 0:
                margin_cap = (
                    avail * float(FIXED_RISK_PCT) * float(FIXED_LEVERAGE) * 0.92
                ) / px
                step = float(getattr(self, "qty_step", 0.001) or 0.001)
                min_q = float(getattr(self, "min_qty", 0.001) or 0.001)
                if margin_cap > 0 and float(qty) > margin_cap:
                    clipped = math.floor(margin_cap / step) * step if step > 0 else margin_cap
                    if clipped < min_q:
                        clipped = 0.0
                    logger.warning(
                        f"🛡️ [{self.symbol}] 保证金裁剪 "
                        f"qty {float(qty):.4f} → {clipped:.4f} "
                        f"(available={avail:.2f}U · {FIXED_RISK_PCT*100:.0f}%×{FIXED_LEVERAGE:.0f}x · 92%)"
                    )
                    meta["qty_before_margin_cap"] = float(qty)
                    meta["margin_cap_qty"] = round(float(clipped), 6)
                    meta["available_balance"] = round(avail, 4)
                    meta["binding"] = f"{meta.get('binding')}+margin_cap"
                    qty = float(clipped)
                    meta["qty"] = float(qty)
                    meta["notional"] = round(float(qty) * px, 2)
                    meta["order_amount"] = meta["notional"]
        except Exception as e:
            logger.warning(f"保证金裁剪跳过: {e}")
        self.leverage = float(FIXED_LEVERAGE)
        self.tv_sizing_leverage = float(FIXED_LEVERAGE)
        logger.info(
            f"📐 [{self.symbol}] 开仓qty核算 | "
            f"atr_source={meta['atr_source']} atr={atr:.4f} | "
            f"sl_adj={float(meta.get('sl_adj') or 1):.4f} "
            f"(TV距={float(meta.get('tv_implied_dist') or 0):.2f}/"
            f"VPS距={float(meta.get('vps_stop_dist') or 0):.2f}) | "
            f"候选 risk={float(meta.get('qty_by_risk') or 0):.4f} "
            f"notional={float(meta.get('qty_by_notional') or 0):.4f} "
            f"tv′={float(meta.get('adjusted_tv_qty') or 0):.4f} | "
            f"生效={meta.get('binding')} → qty={float(qty or 0):.4f} | "
            f"TV.qty={tv_qty} TV.sl={tv_sl_ref or '—'} VPS.sl={stop:.2f}"
        )
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
        """已废除 CAP_ALIGN：禁止 reduceOnly 主动减仓；返回当前实盘数量。"""
        pos = self._get_active_position()
        if pos == "QUERY_FAILED" or not isinstance(pos, dict):
            live = 0.0
        else:
            live = float(pos.get("size") or 0)
        logger.warning(
            f"🚫 [{self.symbol}] CAP_ALIGN/_trim 已废除 | {reason_tag} | "
            f"目标 {target_qty} 实盘 {live} → 不减仓"
        )
        return live

    def _radar_enforce_regime_cap(self, live_qty, curr_px, force=False):
        """
        已废除 CAP_ALIGN：新架构禁止 VPS 自主减仓（非 TV / 非呼吸止损）。

        哨兵/雷达守护路径必须纯 no-op：禁止再调用 _is_oversize_for_regime /
        _calc_vps_open_qty。持仓中用 mark−sl 反推「TV隐含ATR」会假偏差刷屏，
        并污染 atr_div_streak（2026-07-22 实盘事故）。
        """
        return None

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
        # regime 仅内部逻辑使用，禁止拼进用户可见 verify_note
        if meta.get("atr") and float(meta.get("atr") or 0) > 0:
            base_note += f" | ATR {float(meta['atr']):.2f}"
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
        # 必须完整清零呼吸账本（禁止半清理残留 entry/sl/atr）
        self._reset_breath_ledger_on_flat(source=f"蚂蚁仓扫尾·{reason}")
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
        if pos == "QUERY_FAILED":
            self._on_position_query_failed("重启蚂蚁扫描")
            return False
        if not isinstance(pos, dict) or float(pos.get("size") or 0) <= 0:
            return False
        if not self.current_side:
            self.current_side = pos["side"]
        real_amt = float(pos["size"])
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
            reason = "重启扫描：残量扫尾（疑似止盈余量/灰尘仓）"
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
        if pos == "QUERY_FAILED":
            self._on_position_query_failed("重启对账补发")
            return False
        if isinstance(pos, dict) and float(pos.get("size") or 0) > 0:
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
        # 完整清零：禁止只清 qty 留下 entry/sl/atr（此前半清理污染 HARD_SL）
        self._reset_breath_ledger_on_flat(source="重启对账补发收网")
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
        # 空仓净场：无持仓方向时，任意 LIMIT 都视为须撤的幽灵/残留单
        if not self.current_side:
            return True
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

    def _tp_slices_for_initial(self, initial_qty):
        """
        返回可挂限价的 TP 切片。
        数量：固定 LEG_TP_RATIOS 30/30/40（相对 VPS 实开总仓）；忽略 webhook qty。
        价格：TV tp1/tp2/tp3。
        实际挂出档数由 _effective_place_tp_levels（场景一=2，场景二=3）决定。
        """
        initial_qty = float(initial_qty or 0)
        ratios = list(
            getattr(self, "_leg_ratios", None)
            or LEG_TP_RATIOS
            or self.regime_settings[self._tp_split_regime()]["ratios"]
        )
        o1, o2, o3 = self._split_tp_quantities(initial_qty, ratios)
        logger.info(
            f"📐 [{self.symbol}] TP切片比例 {ratios} "
            f"→ TP1={o1} TP2={o2} TP3={o3} "
            f"(场景挂档={self._effective_place_tp_levels()})"
        )

        tps = list(self.tv_tps or [])
        while len(tps) < 3:
            tps.append(0.0)
        slices = [
            {"level": 1, "price": float(tps[0] or 0), "qty": o1},
            {"level": 2, "price": float(tps[1] or 0), "qty": o2},
            {"level": 3, "price": float(tps[2] or 0), "qty": o3},
        ]
        return [
            s for s in slices[: int(self._effective_place_tp_levels() or 3)]
            if float(s.get("price") or 0) > 0 and float(s.get("qty") or 0) > 0
        ]

    def _tp_split_regime(self):
        """止盈比例以开仓档位为准（open_regime），避免 TV 档位变化导致比例算错"""
        if self.watched_qty and self.watched_qty > 0:
            return int(getattr(self, "open_regime", self.regime) or self.regime)
        return int(self.regime)

    def _expected_tp_count(self, tp_pxs=None):
        tp_pxs = tp_pxs if tp_pxs is not None else self.tv_tps
        consumed = set(getattr(self, "tp_levels_consumed", []) or [])
        n = min(int(self._effective_place_tp_levels() or 3), len(tp_pxs or []))
        return sum(
            1 for i in range(n)
            if float((tp_pxs or [0])[i] or 0) > 0 and (i + 1) not in consumed
        )

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
                        current_stop=float(getattr(self, "current_sl", 0) or 0),
                    )
                except Exception as e:
                    logger.warning(f"TP成交对账钉钉失败: {e}")
                self._ws_tp_fill_levels = set()
                if 1 in inferred:
                    self._ws_tp1_fill_hint = False
            # TP1 成交后：止损数量收缩（不在此强制改止损价）
            if 1 in newly and live_qty > 0 and curr_px > 0:
                try:
                    self._breath_resize_stop_on_tp(
                        live_qty, reason="TP1价到成交·止损数量收缩",
                    )
                except Exception as e:
                    logger.warning(f"TP1记账后止损收缩异常: {e}")
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
        handoff = set(getattr(self, "tp_levels_radar_handoff", []) or [])
        self.tp_levels_consumed = [lv for lv in consumed if lv in handoff]
        self._save_state()
        return True

    def _apply_takeover_price_progress(self, entry, curr_px, live_qty, source="接管"):
        """
        重启/接管铁律（开仓价 + 实时价两头对账）：
        - 现价已达/越过 TPn → 记账跳过，禁止再挂该档（防 TP1 反复补挂秒成）
        - 只挂尚未达价的剩余档（TP1过→只挂23；TP2过→只挂3）
        - 达阶段二激活条件或 TP1 已过 → 呼吸止损进入动态追随
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
            place_n = max(1, min(3, int(self._effective_place_tp_levels() or 3)))
            return {
                "consumed": list(getattr(self, "tp_levels_consumed", []) or []),
                "hang_levels": list(range(1, place_n + 1)),
                "should_radar": False,
                "notes": ["缺开仓价或现价"],
            }

        self._ensure_tp123_prices_from_tv(entry)
        place_n = max(1, min(3, int(self._effective_place_tp_levels() or 3)))
        past = []
        for lv in range(1, place_n + 1):
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
        for lv in range(1, place_n + 1):
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
        default_hang = list(range(1, place_n + 1))
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
                f"现价未过TP1 | 应挂{hang or default_hang} | "
                f"entry={entry:.2f} mark={curr_px:.2f} | "
                f"雷达={'应激活' if should_radar else '待命'}"
            )
            logger.info(
                f"🧭 [{source}] [{self.symbol}] 开仓价/现价对账: "
                f"未过TP1 → 可挂 {hang or default_hang} | "
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
        """
        已成交档跳过；仅对 PLACE_TP_LEVELS 内档位拆量。
        无成交时用绝对比例（TP1/TP2 各 30%，余仓 40% 不挂）；
        有成交后仅在仍应挂档间分配剩余仓位。
        """
        ratios = list(
            ratios or self.regime_settings[self._tp_split_regime()]["ratios"]
        )
        while len(ratios) < 3:
            ratios.append(0.0)
        place_n = max(1, min(3, int(self._effective_place_tp_levels() or 3)))
        consumed = set(getattr(self, "tp_levels_consumed", []) or [])
        remaining = [i for i in range(place_n) if (i + 1) not in consumed]
        if not remaining or live_qty <= 0:
            return {}
        if len(remaining) == 1:
            return {remaining[0] + 1: round(float(live_qty), 3)}
        # 全档未成交：按开仓绝对比例挂 PLACE_TP_LEVELS，禁止把 TP3 余量并进 TP2
        if not consumed and place_n < 3:
            q1, q2, q3 = self._split_tp_quantities(float(live_qty), ratios)
            raw = {1: q1, 2: q2, 3: q3}
            return {
                lv: round(float(raw[lv]), 3)
                for lv in range(1, place_n + 1)
                if float(raw.get(lv) or 0) > 0
            }
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
        只返回 PLACE_TP_LEVELS 内档位（默认 TP1+TP2，不挂 TP3）。
        """
        place_n = max(1, min(3, int(self._effective_place_tp_levels() or 3)))
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
        for level in range(1, place_n + 1):
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
        expected = self._expected_tp_count()
        if is_orders_query_failed(orders):
            return {
                "matched_full": 0,
                "expected": expected,
                "levels": [],
                "issues": ["orders_unreadable"],
                "orphans": [],
                "pending_prices": [],
                "live_qty": live_qty,
                "orders_unreadable": True,
            }
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

        pending_prices = sorted({o["price"] for o in orders})
        return {
            "matched_full": matched_full,
            "expected": expected,
            "levels": levels,
            "issues": issues,
            "orphans": orphans,
            "pending_prices": pending_prices,
            "live_qty": live_qty,
            "orders_unreadable": False,
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
        # -1003/查单失败时禁止当「全缺失」盲补（曾刷出同价 50+ 限价）
        if not self._orders_book_readable():
            logger.error(
                f"🛡️ [{self.symbol}] 智能修复中止：挂单不可读 → 禁止补/撤 TP"
            )
            audit = self._audit_tp_levels(live_qty, tolerance, qty_tol)
            return audit, 0

        close_side = "SHORT" if self.current_side == "LONG" else "LONG"
        actions = 0
        audit = self._audit_tp_levels(live_qty, tolerance, qty_tol)
        if audit.get("orders_unreadable"):
            return audit, 0

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
                        self._mark_tp_order_placed(lv['level'], order_res=res)
                        logger.info(
                            f"🔧 重启纠偏 TP{lv['level']} @{price:.2f} → {target_q} ETH"
                        )
                    time.sleep(0.35)
                continue

            res = binance_client.place_limit_order(close_side, target_q, price, symbol=self.symbol, reduce_only=True,
            )
            if res:
                actions += 1
                self._mark_tp_order_placed(lv['level'], order_res=res)
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
        """已废弃"""
        return None

    def _shield_stop_price(self, entry=None):
        """实盘保护止损 = 呼吸止损 currentStop。"""
        return self._tv_hard_sl_target(entry) or None

    def _resolve_hard_sl_regime(self):
        """开仓档位锁定（TP 比例用）。"""
        return int(getattr(self, "open_regime", None) or self.regime or 3)

    def _market_engine(self):
        return get_market_engine(
            self.symbol,
            fetch_klines=lambda s, iv, lim: binance_client.fetch_klines(s, iv, lim),
        )

    def _refresh_market_metrics(self, force=False):
        """
        VPS 行情引擎：30m 合成 90m → ATR(14)/ADX(14)。
        返回 (atr, adx)。止损距离只用 open_atr(initialAtr)；ADX 可刷新。
        """
        atr, adx = self._market_engine().refresh(force=bool(force))
        if atr > 0:
            self.current_atr = float(atr)
        if adx > 0:
            self.last_adx = float(adx)
        return float(self.current_atr or 0), float(self.last_adx or 0)

    def _debug_compare_tv_implied_atr(self, entry, tv_sl_ref, vps_atr, tv_atr=None):
        """
        纯调试：VPS ATR vs TV ATR；不参与交易决策、不挡开仓。
        优先用 webhook atr；否则按 TV 硬止损≈1.0×ATR 反推（禁止误用 VPS 1.5，
        否则会系统性报出 ~33% 假偏差——与 2026-07-22 实盘误告警同源）。
        """
        vps = float(vps_atr or 0)
        if vps <= 0:
            return
        ref, source = resolve_tv_atr_for_compare(
            vps,
            tv_atr=float(tv_atr or 0),
            entry=entry,
            stop_loss=tv_sl_ref,
            tv_sl_mult=TV_HARD_SL_ATR_MULT,
        )
        if ref <= 0:
            return
        div = atr_divergence_pct(vps, ref)
        msg = (
            f"🔍 [{self.symbol}] ATR核对(仅日志) VPS={vps:.4f} "
            f"TV={ref:.4f}({source}) 差={div:.1%} | "
            f"止损仍只用 VPS ATR；TV硬止损距仅影响 sizing sl_adj"
        )
        if div >= ATR_COMPARE_ALERT_PCT:
            logger.warning(msg + " → 超阈值，请人工核 90m 周期/数据源")
            try:
                dingtalk.report_system_alert(
                    f"ATR核对差异 [{self.symbol}]",
                    msg + " | 请核对 TV 90m 与 VPS 合成是否一致（本告警不拦截开仓）",
                    level="提示",
                )
            except Exception:
                pass
        else:
            logger.info(msg)

    def _get_locked_initial_atr(self):
        """
        读取开仓锁定 ATR（全程固定，禁止用实时 ATR 重算止损距离）。
        注意：实例属性 `_locked_initial_atr` 是 LockedInitialAtr 对象，禁止同名方法，
        否则属性遮蔽方法 → TypeError: LockedInitialAtr object is not callable。
        """
        lock = getattr(self, "_locked_initial_atr", None)
        if lock is not None and getattr(lock, "value", 0):
            try:
                v = float(lock.value or 0)
                if v > 0:
                    return v
            except Exception:
                pass
        atr = float(getattr(self, "open_atr", 0) or 0)
        if atr > 0:
            return atr
        atr = float(getattr(self, "current_atr", 0) or 0)
        return atr if atr > 0 else 0.0

    def _default_sizing_atr_fallback(self):
        """仅用于开仓 sizing 兜底；禁止用于持仓止损发明。"""
        atr = float(getattr(self, "open_atr", 0) or 0)
        if atr > 0:
            return atr
        atr = float(getattr(self, "current_atr", 0) or 0)
        return atr if atr > 0 else 30.0

    def _tv_hard_sl_target(self, entry=None, side=None, regime=None, allow_atr_invent=False):
        """
        盘口保护止损唯一来源：呼吸 currentStop → initialStop →（可选）entry±1.5×ATR。
        持仓维护默认禁止用 ATR/默认30 发明止损（重启窗口曾因此把 1910 改成 1886）。
        """
        cs = round(float(getattr(self, "current_sl", 0) or 0), 2)
        if cs > 0:
            return cs
        init = round(float(getattr(self, "initial_stop", 0) or 0), 2)
        if init > 0:
            return init
        if not allow_atr_invent:
            return 0.0
        entry = float(
            entry if entry is not None else (self.watched_entry or self.tv_price or 0)
        )
        side = str(side or self.current_side or "").strip().upper()
        atr = self._get_locked_initial_atr()
        if atr <= 0:
            return 0.0
        return initial_stop_price(
            side, entry, atr, profile=getattr(self, "breath_profile", None),
        )

    def _vps_hard_sl_target(self, entry=None, side=None, regime=None):
        """兼容旧名 → 呼吸止损。"""
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
        """
        呼吸止损合法：只要正价即可（阶段一可低于入场价）。
        贴市安全由 _can_safely_place_radar_sl / clamp 另行保证。
        """
        sl = round(float(sl or 0), 2)
        return sl > 0

    def _is_exchange_stop_acceptable_as_vps_floor(self, stop_px, entry=None, side=None):
        """盘口 STOP 贴近呼吸止损目标即可写回。"""
        stop_px = round(float(stop_px or 0), 2)
        if stop_px <= 0:
            return False
        target = self._tv_hard_sl_target(entry, side)
        tol = max(float(SHIELD_STOP_TOLERANCE), stop_px * 0.002)
        if target > 0 and abs(stop_px - target) <= tol:
            return True
        return self._is_valid_radar_sl(stop_px, entry, side)

    def _sanitize_vps_hard_sl_ledger(self, source=""):
        """
        强制账本硬止损 = 已有 currentStop / initialStop / 盘口 STOP。
        禁止在账本为空时用默认 ATR=30 发明宽止损（曾导致 1910↔1886 振荡）。
        """
        entry = float(self.watched_entry or 0)
        side = str(self.current_side or "").strip().upper()
        target = self._tv_hard_sl_target(entry, side, allow_atr_invent=False)
        if target <= 0:
            adopted = self._adopt_exchange_hard_sl(source=source or "消毒·盘口优先")
            if adopted > 0:
                return True
            # 仅当已锁定真实 open_atr（非空账本默认）时才允许 ATR 补算
            locked = float(getattr(self, "open_atr", 0) or 0)
            if locked > 0 and entry > 0 and side in ("LONG", "SHORT"):
                target = round(float(initial_stop_price(
                    side, entry, locked,
                    profile=getattr(self, "breath_profile", None),
                ) or 0), 2)
            if target <= 0:
                logger.error(
                    f"🚨 [{self.symbol}] 呼吸止损账本消毒失败：无账本/盘口/锁定ATR "
                    f"| {source}（拒绝默认ATR=30发明）"
                )
                return False
        cur = round(float(getattr(self, "tv_sl", 0) or 0), 2)
        if abs(cur - target) > SHIELD_STOP_TOLERANCE or cur <= 0:
            old = cur
            self.tv_sl = target
            self.current_sl = target
            if float(getattr(self, "initial_stop", 0) or 0) <= 0:
                self.initial_stop = target
            self._last_applied_exchange_sl = 0.0
            self._save_state()
            logger.info(
                f"🫁 呼吸止损账本对齐 @{target:.2f} "
                f"(原 {old or 0:.2f}) | {source or '消毒'}"
            )
        return True

    def _refresh_vps_hard_sl(self, entry=None, side=None, regime=None, atr=None,
                             tv_sl_ref=None, source=""):
        """
        呼吸止损刷新：entry±1.5×initialAtr 写入 initial_stop / current_sl。
        TV stop_loss 仅记入 tv_sl_ref 作日志参考，不挂盘。
        """
        entry = float(entry or self.watched_entry or self.tv_price or 0)
        side = (side or self.current_side or "").strip().upper()
        atr_in = float(atr or 0)
        locked = float(getattr(self, "open_atr", 0) or 0)
        src = str(source or "")
        # 仅「真开仓/保护绑定」允许重锁 ATR；重启/接管禁止用实时 ATR 覆盖已锁定 open_atr
        force_open = any(
            k in src for k in ("开仓绑定", "保护绑定", "开仓保护", "LIVE_TEST开仓", "开仓武装")
        ) or src.endswith("开仓") or src.startswith("开仓")

        if atr_in > 0 and locked <= 0:
            self.open_atr = atr_in
            locked = atr_in
        elif atr_in > 0 and force_open:
            self.open_atr = atr_in
            locked = atr_in
        elif atr_in > 0 and locked > 0 and force_open is False:
            # 接管/重启：保留锁定 ATR，忽略传入的实时 ATR
            atr_in = locked
        if locked <= 0:
            # 禁止静默落成默认 30：优先盘口止损；否则失败（由上层告警）
            adopted = self._adopt_exchange_hard_sl(source=src or "刷新·无ATR盘口")
            if adopted > 0:
                return True
            live_atr = float(getattr(self, "current_atr", 0) or 0)
            if live_atr > 0 and force_open:
                locked = live_atr
                self.open_atr = locked
            else:
                logger.error(
                    f"🚨 [{self.symbol}] 呼吸止损无法计算：缺锁定 open_atr | {source} "
                    f"entry={entry} side={side}（拒绝默认ATR=30）"
                )
                return False

        init = initial_stop_price(
            side, entry, locked, profile=getattr(self, "breath_profile", None),
        )
        if init <= 0 or entry <= 0 or side not in ("LONG", "SHORT"):
            logger.error(
                f"🚨 [{self.symbol}] 呼吸止损无法计算 | {source} "
                f"entry={entry} side={side} atr={locked}"
            )
            return False

        # TV 原值仅作参考
        if tv_sl_ref is not None:
            ref = round(self._safe_float(tv_sl_ref, 0), 2)
            if ref > 0:
                self.tv_sl_ref = ref

        old = round(float(getattr(self, "current_sl", 0) or 0), 2)
        old_init = round(float(getattr(self, "initial_stop", 0) or 0), 2)
        # 接管/重启：若已有 initial_stop，禁止用新 ATR 重算覆盖
        if (not force_open) and old_init > 0:
            init = old_init
        self.initial_stop = init
        if force_open or old <= 0:
            self.current_sl = init
            self.breakeven_phase = False
            self.radar_activated = True
            self._radar_stage_last = max(int(getattr(self, "_radar_stage_last", 0) or 0), 1)
            if entry > 0 and force_open:
                self.best_price = entry
        else:
            # 只允许向有利方向与 initial 合并
            if side == "LONG":
                self.current_sl = max(old, init)
            else:
                self.current_sl = min(old, init) if old > 0 else init

        self.tv_sl = float(self.current_sl)
        if abs(float(self.current_sl) - old) > SHIELD_STOP_TOLERANCE:
            self._last_applied_exchange_sl = 0.0
        self._save_state()
        logger.info(
            f"🫁 呼吸止损 @{float(self.current_sl):.2f} "
            f"(initial={init:.2f}·{INITIAL_SL_ATR}×ATR={locked:.2f}) | "
            f"{side or '?'} entry={entry:.2f}"
            + (f" ({source})" if source else "")
            + (
                f" | TV参考@{float(getattr(self, 'tv_sl_ref', 0) or 0):.2f}"
                if float(getattr(self, "tv_sl_ref", 0) or 0) > 0 else ""
            )
        )
        return True

    def _apply_tv_sl_from_payload(self, payload, source=""):
        """
        开仓/更新：用 ATR 武装呼吸止损；TV tv_sl 仅作参考字段。
        """
        entry = float(
            payload.get("price")
            or self.tv_price
            or self.watched_entry
            or 0
        )
        side = str(
            payload.get("action") or payload.get("side") or self.current_side or ""
        ).upper()
        if side not in ("LONG", "SHORT"):
            side = self.current_side
        atr = float(
            getattr(self, "open_atr", 0)
            or getattr(self, "_tv_signal_atr", 0)
            or self.current_atr
            or 0
        )
        # 若开仓尚未锁定 ATR：优先 webhook atr，再降级链
        if atr <= 0:
            self._tv_signal_atr = self._safe_float(
                payload.get("atr") or payload.get("ATR"), 0,
            )
            atr, _meta = self._resolve_open_atr_with_degrade(entry, tv_sl_ref=None)
        tv_ref = payload.get("tv_sl") or payload.get("stop_loss")
        ref_px = round(self._safe_float(tv_ref, 0), 2) if tv_ref not in (None, "") else 0.0
        # ADX 仅日志；呼吸系数用 1h ATR
        self._refresh_market_metrics(force=False)
        self._refresh_breathing_coefficient(force=False)
        ok = self._refresh_vps_hard_sl(
            entry=entry, side=side,
            regime=self._resolve_hard_sl_regime(), atr=atr,
            tv_sl_ref=ref_px if ref_px > 0 else None,
            source=source or "呼吸止损",
        )
        if not ok:
            dingtalk.report_system_alert(
                f"呼吸止损失败 [{self.symbol}]",
                f"{source or '信号'} 无法计算 entry±{INITIAL_SL_ATR}×ATR 止损",
            )
        return ok

    def _effective_exchange_stop(self, radar_sl=None):
        """雷达账本止损价（不含永久硬止损腿）。"""
        if radar_sl and float(radar_sl) > 0 and self._is_valid_radar_sl(float(radar_sl)):
            return round(float(radar_sl), 2)
        cur = float(getattr(self, "current_sl", 0) or 0)
        if cur > 0:
            return round(cur, 2)
        init = float(getattr(self, "initial_stop", 0) or 0)
        return round(init, 2) if init > 0 else None

    def _clamp_radar_to_vps_floor(self, radar_sl):
        """兼容：非法 → 回退呼吸止损目标。"""
        if not radar_sl:
            return self._tv_hard_sl_target() or radar_sl
        if self._is_valid_radar_sl(radar_sl):
            return round(float(radar_sl), 2)
        return self._tv_hard_sl_target() or None

    def _clamp_radar_to_tv_floor(self, radar_sl):
        """兼容旧名"""
        return self._clamp_radar_to_vps_floor(radar_sl)

    def _purge_all_close_position_stops(self):
        """撤净所有 closePosition 止损（硬止损腿；雷达为 reduceOnly 定量，不在此撤）。"""
        cancelled = 0
        orders = binance_client.get_open_orders(self.symbol)
        if is_orders_query_failed(orders):
            logger.error(
                f"🛡️ [{self.symbol}] 撤 closePosition 中止：挂单不可读"
            )
            return -1
        for o in orders or []:
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

    def _purge_all_protective_stops(
        self, keep_near=None, tolerance=None,
        also_keep_near=None, preserve_hard=True,
    ):
        """
        撤净全部保护性 STOP / STOP_MARKET（含 Stop-Limit reduceOnly + Algo closePosition）。
        keep_near: 若给出目标价，保留触发价贴近该价的单；其余一律撤。
        also_keep_near: float 或 list，贴近这些价的 STOP 不撤。
        preserve_hard=True（默认）时保留 frozen 硬止损价位，禁止触碰永久防线。
        preserve_hard=False 仅允许在仓位归零清场时使用。
        """
        keep_near = float(keep_near or 0)
        tol = float(tolerance if tolerance is not None else SHIELD_STOP_TOLERANCE)
        side = str(self.current_side or "").strip().upper()
        keep_prices = []
        if keep_near > 0:
            keep_prices.append(keep_near)
        if also_keep_near is not None:
            if isinstance(also_keep_near, (list, tuple)):
                keep_prices.extend(
                    float(x) for x in also_keep_near if float(x or 0) > 0
                )
            elif float(also_keep_near or 0) > 0:
                keep_prices.append(float(also_keep_near))
        elif preserve_hard:
            hard = self._frozen_hard_px()
            if hard > 0:
                keep_prices.append(hard)
                hard_ex = round(float(order_stop_price(
                    self.current_side, hard,
                    buffer_usd=self._stop_buffer_usd(),
                    profile=getattr(self, "breath_profile", None),
                ) or hard), 2)
                if abs(hard_ex - hard) > 1e-9:
                    keep_prices.append(hard_ex)

        def _near_keep(px):
            if px is None:
                return False
            fp = float(px)
            for kp in keep_prices:
                if abs(fp - float(kp)) <= tol:
                    return True
            return False

        cancelled = 0
        for o in binance_client.get_open_orders(self.symbol, include_algo=True):
            order_type = str(o.get("type") or o.get("orderType") or "").upper()
            if order_type not in ("STOP", "STOP_MARKET"):
                continue
            px = self._order_stop_price(o)
            if _near_keep(px):
                continue
            if keep_near > 0 and px is not None:
                if side == "LONG" and float(px) > keep_near + tol:
                    logger.warning(
                        f"🛡️ [{self.symbol}] 拒撤更紧LONG止损 @{float(px):.2f} "
                        f"(keep={keep_near:.2f})"
                    )
                    continue
                if side == "SHORT" and float(px) < keep_near - tol:
                    logger.warning(
                        f"🛡️ [{self.symbol}] 拒撤更紧SHORT止损 @{float(px):.2f} "
                        f"(keep={keep_near:.2f})"
                    )
                    continue
            oid = o.get("orderId") or o.get("algoId")
            if not oid:
                continue
            binance_client.cancel_order(self.symbol, order=o)
            cancelled += 1
            time.sleep(0.12)
        return cancelled

    def _count_protective_stops(self):
        """成功返回价位 list；查询失败返回 None（勿当空列表）。"""
        return binance_client.find_protective_stop_prices(self.symbol)

    def _orders_book_readable(self):
        """盘口挂单 REST 是否可读；失败时禁止撤挂/补挂。"""
        orders = binance_client.get_open_orders(self.symbol, include_algo=True)
        if is_orders_query_failed(orders):
            logger.error(
                f"🛡️ [{self.symbol}] 挂单查询失败 → 盘口不可读，禁止撤/补挂"
            )
            return False
        return True

    def _place_vps_hard_sl_order(self, live_qty, trigger_px, use_stop_limit=False):
        """
        呼吸止损写入：STOP_MARKET + reduceOnly + 明确 quantity（跟随剩余仓位）。
        若贴市导致交易所拒单 → 返回 None，由上层紧急平仓（禁止改价保活）。
        """
        live_qty = self._resolve_live_qty(live_qty)
        trigger_px = round(float(trigger_px or 0), 2)
        if live_qty <= 0 or trigger_px <= 0 or not self.current_side:
            return None
        curr_px = float(binance_client.get_current_price(self.symbol) or 0)
        if curr_px > 0:
            if self.current_side == "LONG" and trigger_px >= curr_px:
                logger.error(
                    f"🚨 [{self.symbol}] LONG 止损 @{trigger_px:.2f} 已穿/贴市 "
                    f"{curr_px:.2f} → 禁止推宽，交紧急平仓"
                )
                return None
            if self.current_side == "SHORT" and trigger_px <= curr_px:
                logger.error(
                    f"🚨 [{self.symbol}] SHORT 止损 @{trigger_px:.2f} 已穿/贴市 "
                    f"{curr_px:.2f} → 禁止推宽，交紧急平仓"
                )
                return None
        close_side = "SHORT" if self.current_side == "LONG" else "LONG"
        if use_stop_limit:
            return binance_client.place_stop_limit_order(
                close_side, live_qty, trigger_px, symbol=self.symbol, limit_price=trigger_px,
            )
        return binance_client.place_stop_market_order(
            close_side, trigger_px, symbol=self.symbol, quantity=live_qty,
        )

    def _frozen_hard_px(self):
        return round(float(getattr(self, "frozen_hard_sl_px", 0) or 0), 2)

    def _radar_live_stops(self):
        """Protective stop prices excluding frozen hard leg."""
        all_px = self._count_protective_stops()
        if all_px is None:
            return None
        hard = self._frozen_hard_px()
        tol = SHIELD_STOP_TOLERANCE
        if hard <= 0:
            return list(all_px)
        hard_ex = round(float(order_stop_price(
            self.current_side, hard,
            buffer_usd=self._stop_buffer_usd(),
            profile=getattr(self, "breath_profile", None),
        ) or hard), 2)
        return [
            p for p in all_px
            if abs(float(p) - hard) > tol and abs(float(p) - hard_ex) > tol
        ]

    def _ensure_frozen_hard_sl(self, live_qty, reason="永久硬止损"):
        """
        永久硬止损：仅按 frozen_hard_sl_px 挂出/确认存在；永不改价、永不替换。
        使用 closePosition（quantity=None），TP 减仓后自动覆盖剩余仓位，无需撤单改量。
        挂单不可读且无本地刚挂缓存 → 返回 False（禁止谎称已挂）。
        """
        hard = self._frozen_hard_px()
        live_qty = self._resolve_live_qty(live_qty)
        if hard <= 0 or live_qty <= 0:
            return False
        exchange_target = round(float(order_stop_price(
            self.current_side, hard,
            buffer_usd=self._stop_buffer_usd(),
            profile=getattr(self, "breath_profile", None),
        ) or hard), 2)
        if self._has_stop_sl_near(exchange_target, exclude_shield=False):
            return True
        if not self._orders_book_readable():
            logger.error(
                f"🛡️ [{self.symbol}] {reason} 中止：挂单不可读且无本地缓存 "
                f"@{exchange_target:.2f} → 禁止谎称已挂"
            )
            return False
        close_side = "SHORT" if self.current_side == "LONG" else "LONG"
        # closePosition：仓位归零前数量自动匹配剩余头寸，禁止为改量而撤硬止损
        order = binance_client.place_stop_market_order(
            close_side, exchange_target, symbol=self.symbol, quantity=None,
        )
        if order:
            self._set_defense_order_id("hard_stop", order, save=False)
            logger.info(
                f"🛡️ [{self.symbol}] {reason} @{exchange_target:.2f} "
                f"closePosition (账本硬止损{hard:.2f})"
            )
            return True
        return False

    def _breath_resize_stop_on_tp(self, live_qty, reason=""):
        """
        TP1/TP2 成交后：两笔止损独立收缩数量；硬止损价格只读、绝不撤销。
        硬止损用 closePosition 自动覆盖剩余仓；雷达撤旧挂新（仅触碰雷达腿）。
        """
        self._breath_tick_paused = True
        try:
            live_qty = float(self._resolve_live_qty(live_qty) or 0)
            stop = round(float(getattr(self, "current_sl", 0) or getattr(self, "initial_stop", 0) or 0), 2)
            init_q = float(getattr(self, "initial_qty", 0) or 0)
            if init_q > 0 and live_qty > 0:
                self.remaining_qty_pct = max(0.0, min(1.0, live_qty / init_q))
            if live_qty <= 0 or stop <= 0 or not self.current_side:
                logger.warning(
                    f"🫁 [{self.symbol}] 止损数量收缩跳过 | qty={live_qty} stop={stop} | {reason}"
                )
                return False
            exchange_target = round(
                float(order_stop_price(
                    self.current_side, stop,
                    buffer_usd=self._stop_buffer_usd(),
                    profile=getattr(self, "breath_profile", None),
                ) or stop),
                2,
            )
            logger.info(
                f"🫁 [{self.symbol}] 止损数量收缩 | {reason} | "
                f"qty={live_qty} ({float(getattr(self, 'remaining_qty_pct', 1) or 1):.0%}) "
                f"radar@{stop:.2f} | 硬止损保留只读"
            )
            # 永久防线：只确认仍挂着，禁止 purge hard
            if not self._ensure_frozen_hard_sl(live_qty, reason="TP后确认永久硬止损"):
                logger.error(f"🫁 [{self._tag()}] TP后永久硬止损缺失且补挂失败")
                return False
            # 仅撤雷达腿（preserve_hard=True），再按剩余 qty 重挂雷达
            self._purge_all_protective_stops(preserve_hard=True)
            time.sleep(0.35)
            order = self._place_vps_hard_sl_order(live_qty, exchange_target)
            if not order:
                # 雷达暂缺时硬止损仍在 → 非裸奔；继续告警由哨兵补雷达
                logger.error(
                    f"🫁 [{self._tag()}] 雷达止损数量收缩重挂失败 @{stop:.2f} "
                    f"| 硬止损仍在保护"
                )
                self._save_state()
                return False
            self.shield_sized_qty = live_qty
            self._last_applied_exchange_sl = exchange_target
            self.shield_active = True
            self._set_defense_order_id("radar_stop", order, save=False)
            self._set_defense_order_id("stop", order, save=False)
            self._save_state()
            return True
        except Exception as e:
            logger.error(f"🫁 [{self.symbol}] 止损数量收缩异常: {e}")
            return False
        finally:
            self._breath_tick_paused = False

    def _sync_exchange_stop(self, live_qty, radar_sl=None, reason="", force=False):
        """
        雷达止损唯一写入：按 currentStop 挂/改 STOP_MARKET（quantity=剩余仓位）。
        永久硬止损由 _ensure_frozen_hard_sl 独立维护，本函数不触碰。
        失败重试 3 次 → HARD_SL_FAIL_ABORT：钉钉告警，保持现状，不自主平仓。
        """
        if getattr(self, "_stop_write_blocked", False):
            logger.warning(
                f"🛡️ [{self.symbol}] 止损写入已阻断（账本未热加载完整）| {reason}"
            )
            return {"ok": False, "skipped": True, "reason": "stop_write_blocked"}
        live_qty = self._resolve_live_qty(live_qty)
        if live_qty <= 0 or not self.current_side or not self.watched_entry:
            return {"ok": False, "skipped": True, "reason": "no_position"}

        self._lock_open_regime_from_sources(force=False)
        self._sanitize_vps_hard_sl_ledger(source=reason or "同步止损消毒")
        target = self._effective_exchange_stop(radar_sl)
        if not target or target <= 0:
            logger.error(
                f"🚨 [{self.symbol}] 同步呼吸止损失败：无有效 currentStop | {reason}"
            )
            try:
                self._call_dingtalk(
                    dingtalk.report_system_alert,
                    title=f"呼吸止损缺失·无法挂单 [{self.symbol}]",
                    detail=(
                        f"{self.current_side} qty={live_qty} | {reason or '同步'} | "
                        f"请核对 open_atr/initial_stop/current_sl"
                    ),
                    level="紧急",
                    suggestion="等待行情引擎补 ATR 或人工挂止损；勿按 TV stop_loss 改挂",
                )
            except Exception:
                pass
            return {"ok": False, "skipped": True, "reason": "no_stop"}
        target = round(float(target), 2)
        # 账本理论止损；盘口挂单再 ±0.3 执行缓冲（多减空加）
        ledger_target = target
        exchange_target = round(
            float(order_stop_price(
                self.current_side, ledger_target,
                buffer_usd=self._stop_buffer_usd(),
                profile=getattr(self, "breath_profile", None),
            ) or ledger_target),
            2,
        )
        if exchange_target <= 0:
            exchange_target = ledger_target

        live_stops = self._radar_live_stops()
        if live_stops is None:
            logger.error(
                f"🛡️ [{self.symbol}] 挂单查询失败 → 禁止补/改呼吸止损 | {reason}"
            )
            return {
                "ok": False, "skipped": True, "reason": "orders_query_failed",
                "target": ledger_target, "exchange_target": exchange_target,
            }
        # 安全铁律：禁止把已挂止损改宽（LONG 下调 / SHORT 上调）——重启误用默认ATR=30 曾把 1910 改成 1886
        # 比较用盘口价 vs 本次拟挂 exchange_target（含缓冲），避免 0.3 缓冲误判改宽
        if live_stops and self.current_side in ("LONG", "SHORT"):
            if self.current_side == "LONG":
                best_live = max(float(p) for p in live_stops)
                if best_live > exchange_target + SHIELD_STOP_TOLERANCE:
                    logger.warning(
                        f"🛡️ [{self.symbol}] 拒改宽止损：盘口@{best_live:.2f} > 拟挂@{exchange_target:.2f} "
                        f"(账本@{ledger_target:.2f}) | 保留盘口 | {reason}"
                    )
                    # 盘口更紧：反推账本（去掉缓冲）保持一致
                    self.current_sl = round(
                        best_live + self._stop_buffer_usd(), 2,
                    )
                    self.tv_sl = float(self.current_sl)
                    if float(getattr(self, "initial_stop", 0) or 0) <= 0:
                        self.initial_stop = float(self.current_sl)
                    ledger_target = float(self.current_sl)
                    exchange_target = round(best_live, 2)
            else:
                best_live = min(float(p) for p in live_stops)
                if best_live < exchange_target - SHIELD_STOP_TOLERANCE:
                    logger.warning(
                        f"🛡️ [{self.symbol}] 拒改宽止损：盘口@{best_live:.2f} < 拟挂@{exchange_target:.2f} "
                        f"(账本@{ledger_target:.2f}) | 保留盘口 | {reason}"
                    )
                    self.current_sl = round(
                        best_live - self._stop_buffer_usd(), 2,
                    )
                    self.tv_sl = float(self.current_sl)
                    if float(getattr(self, "initial_stop", 0) or 0) <= 0:
                        self.initial_stop = float(self.current_sl)
                    ledger_target = float(self.current_sl)
                    exchange_target = round(best_live, 2)

        near = [p for p in live_stops if abs(p - exchange_target) <= SHIELD_STOP_TOLERANCE]
        orphans = [p for p in live_stops if abs(p - exchange_target) > SHIELD_STOP_TOLERANCE]

        last = round(float(getattr(self, "_last_applied_exchange_sl", 0) or 0), 2)
        now = time.time()
        if not orphans and len(near) == 1:
            self._last_applied_exchange_sl = exchange_target
            self._last_hard_sl_sync_ts = now
            self.shield_active = True
            self.shield_sized_qty = live_qty
            self._tv_sl_missing_alerted = False
            if abs(last - exchange_target) > SHIELD_STOP_TOLERANCE:
                self._save_state()
            return {
                "ok": True, "skipped": True, "target": ledger_target,
                "exchange_target": exchange_target,
                "reason": "idempotent_unified",
            }

        if (
            not force
            and last > 0
            and abs(last - exchange_target) <= SHIELD_STOP_TOLERANCE
            and (now - float(getattr(self, "_last_hard_sl_sync_ts", 0) or 0))
            < HARD_SL_SYNC_COOLDOWN_SEC
        ):
            if not orphans and (
                near or self._has_stop_sl_near(exchange_target, exclude_shield=False)
            ):
                return {
                    "ok": True, "skipped": True, "target": ledger_target,
                    "exchange_target": exchange_target,
                    "reason": "cooldown_same_target",
                }

        purged = 0
        ok = False
        res = None
        had_old_stops = bool(live_stops)
        for attempt in range(3):
            if self._has_stop_sl_near(exchange_target, exclude_shield=False):
                ok = True
                break
            res = self._place_vps_hard_sl_order(
                live_qty, exchange_target, use_stop_limit=False,
            )
            time.sleep(0.45 if attempt == 0 else 0.7)
            if res is not None:
                # 交易所已接受 → 立刻记账；核实失败也不在同轮再挂（曾叠 25×@1895.42）
                self._last_applied_exchange_sl = exchange_target
                self._last_hard_sl_sync_ts = time.time()
                if self._has_stop_sl_near(exchange_target, exclude_shield=False):
                    ok = True
                    break
                logger.warning(
                    f"🛡️ [{self.symbol}] 止损 API 已成功但核实延迟 "
                    f"@{exchange_target:.2f} → 本轮不再重挂（防叠单）| {reason}"
                )
                ok = True
                break
            logger.warning(
                f"🛡️ [{self.symbol}] 呼吸止损挂单未核实 "
                f"@{exchange_target:.2f}(账本{ledger_target:.2f}) "
                f"重试 {attempt + 1}/3 | {reason}"
            )

        if ok:
            purged = self._purge_all_protective_stops(keep_near=exchange_target)
            if purged or orphans:
                logger.warning(
                    f"🛡️ 统一呼吸止损：新挂已核实 @{exchange_target:.2f}，清孤儿 {purged} 笔 "
                    f"(原盘口{live_stops})"
                )
                time.sleep(0.35)
                if not self._has_stop_sl_near(exchange_target, exclude_shield=False):
                    res = self._place_vps_hard_sl_order(
                        live_qty, exchange_target, use_stop_limit=False,
                    )
                    time.sleep(0.45)
                    ok = res is not None and self._has_stop_sl_near(
                        exchange_target, exclude_shield=False,
                    )
        elif had_old_stops:
            # HARD_SL_FAIL_ABORT：保留原盘口止损，不撤净裸仓，不自主平仓
            logger.error(
                f"❌ [{self.symbol}] HARD_SL_FAIL_ABORT 新挂失败 @{exchange_target:.2f}，"
                f"保留原盘口 STOP {live_stops} | {reason}"
            )
            try:
                self._call_dingtalk(
                    dingtalk.report_hard_sl_fail_abort,
                    side=self.current_side,
                    qty=live_qty,
                    target_sl=exchange_target,
                    attempts=3,
                    reason=reason or "呼吸止损改单",
                    detail=f"原盘口 STOP={live_stops} 已保留，禁止撤净裸仓",
                )
            except Exception:
                pass
            self._record_shield_maintain(success=True)
            return {
                "ok": True, "skipped": False, "target": ledger_target,
                "exchange_target": exchange_target, "purged": 0,
                "reason": "HARD_SL_FAIL_ABORT_keep_old",
            }
        else:
            logger.error(
                f"❌ [{self.symbol}] HARD_SL_FAIL_ABORT 新挂失败且盘口无 STOP → 裸仓 | {reason}"
            )
            try:
                self._call_dingtalk(
                    dingtalk.report_hard_sl_fail_abort,
                    side=self.current_side,
                    qty=live_qty,
                    target_sl=exchange_target,
                    attempts=3,
                    reason=reason or "呼吸止损挂单",
                    detail="盘口无 STOP → 裸仓风险；请人工按 currentStop 挂止损",
                )
            except Exception:
                pass
            self._record_shield_maintain(success=False)
            return {
                "ok": False, "skipped": False, "target": ledger_target,
                "exchange_target": exchange_target, "purged": 0,
                "reason": "HARD_SL_FAIL_ABORT_naked",
            }

        leftovers = [
            p for p in (self._radar_live_stops() or [])
            if abs(float(p) - exchange_target) > SHIELD_STOP_TOLERANCE
        ]
        if leftovers and ok:
            extra = self._purge_all_protective_stops(keep_near=exchange_target)
            purged += extra
            logger.warning(f"🛡️ 二次清孤儿 STOP{leftovers} 撤 {extra} 笔")
            time.sleep(0.3)
            if not self._has_stop_sl_near(exchange_target, exclude_shield=False):
                self._place_vps_hard_sl_order(
                    live_qty, exchange_target, use_stop_limit=False,
                )
                time.sleep(0.4)
                ok = self._has_stop_sl_near(exchange_target, exclude_shield=False)

        if ok:
            self._last_applied_exchange_sl = exchange_target
            self._last_hard_sl_sync_ts = time.time()
            self.shield_active = True
            self.shield_sized_qty = live_qty
            self._shield_fail_streak = 0
            self._tv_sl_missing_alerted = False
            self.current_sl = ledger_target
            if res is not None:
                self._set_defense_order_id("radar_stop", res, save=False)
                self._set_defense_order_id("stop", res, save=False)
            self._save_state()
            self._record_shield_maintain(success=True)
            logger.info(
                f"✅ [{self.symbol}] 呼吸止损已挂 @{exchange_target:.2f}"
                f"(账本{ledger_target:.2f}±{self._stop_buffer_usd()}) | {reason} | "
                f"current_sl={float(getattr(self, 'current_sl', 0) or 0):.2f} | "
                f"撤孤儿 {purged} 笔"
            )
        else:
            # 二次清理后仍失败 → HARD_SL_FAIL_ABORT
            logger.error(
                f"❌ [{self.symbol}] HARD_SL_FAIL_ABORT 核实失败 "
                f"@{exchange_target:.2f} | {reason}"
            )
            try:
                self._call_dingtalk(
                    dingtalk.report_hard_sl_fail_abort,
                    side=self.current_side,
                    qty=live_qty,
                    target_sl=exchange_target,
                    attempts=3,
                    reason=reason or "呼吸止损核实",
                    detail="重试后仍未核实到目标止损，保持现状",
                )
            except Exception:
                pass
            self._record_shield_maintain(success=False)
        return {
            "ok": ok, "skipped": False, "target": ledger_target,
            "exchange_target": exchange_target, "purged": purged,
        }

    def _handle_tv_sl_update(self, payload):
        """已废除：UPDATE_SL 不再改挂盘口止损（webhook 亦不接受该 action）。"""
        logger.warning(
            f"[{self.symbol}] UPDATE_SL 已废除，忽略 | "
            f"keys={list((payload or {}).keys())[:8]}"
        )
        return

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
        if not self._orders_book_readable():
            logger.error(
                f"🛡️ [{self.symbol}] place_tp_levels_only 中止：挂单不可读 → 禁止盲补"
            )
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
            last_res = None
            for attempt in range(max(1, retries + 1)):
                res = binance_client.place_limit_order(close_side, q, px, symbol=self.symbol, reduce_only=True,
                )
                if res:
                    ok = True
                    last_res = res
                    break
                time.sleep(0.2)
            if ok:
                placed += 1
                self._mark_tp_order_placed(int(lv["level"]), order_res=last_res)
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
        """开仓进行中禁止改单；呼吸止损无冷却窗。"""
        if getattr(self, "_open_in_progress", False):
            return True
        return False

    def _tp1_fill_allows_radar(self, live_qty=None, curr_px=0.0):
        """兼容旧调用：TP1 成交不再作为交棒门槛（呼吸止损开仓即跑）。"""
        return True

    def _radar_ready_to_handoff(self, curr_px, live_qty=None):
        """兼容旧调用：呼吸止损无需交棒门槛。"""
        return True

    def _resolve_armed_radar_sl(self, live_qty, curr_px, dynamic_sl=None):
        """返回当前呼吸止损价。"""
        if self._radar_placement_blocked(
            live_qty, curr_px, reason="resolve_radar", silent=True,
        ):
            return None
        cand = dynamic_sl if dynamic_sl and float(dynamic_sl) > 0 else None
        if cand is None:
            cand = self.current_sl or self._tv_hard_sl_target()
        if cand and self._is_valid_radar_sl(cand):
            return round(float(cand), 2)
        return None

    def _notify_shield_handoff_to_radar(self, real_amt, curr_px, new_sl, reason="",
                                        sl_verified=False, cancelled_hint=0):
        """旧「交棒撤硬止损」钉钉已废除；呼吸止损开仓即单槽，不再发本条。"""
        self._shield_handoff_notified = True
        return

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
        兼容旧交棒入口 → 呼吸止损同步（开仓即运行，无激活线门槛）。
        禁止发旧「转雷达/撤硬止损」钉钉。
        """
        real_amt = float(self._resolve_live_qty(real_amt) or 0)
        if real_amt <= 0:
            return False
        if self._radar_placement_blocked(real_amt, curr_px, reason=reason or "handoff"):
            return False
        if getattr(self, "_open_in_progress", False) or getattr(
            self, "_defense_align_in_progress", False
        ):
            return False

        tick = self._apply_breath_stop_tick(curr_px)
        new_sl = float((tick or {}).get("stop") or self.current_sl or 0)
        if new_sl <= 0:
            new_sl = float(self._tv_hard_sl_target() or 0)
        if new_sl <= 0:
            return False
        safe_sl = self._clamp_radar_sl_for_market(curr_px, new_sl) or new_sl
        if not self._can_safely_place_radar_sl(curr_px, safe_sl):
            logger.info(
                f"🫁 [{self.symbol}] 呼吸止损暂缓挂单：@{safe_sl:.2f} 距市不足 | "
                f"{reason or ''}"
            )
            return False

        self.current_sl = float(safe_sl)
        self.tv_sl = float(safe_sl)
        self.radar_activated = True
        self._radar_handoff_done = True
        self._radar_armed_after_tp1 = True
        self._radar_trigger_gate = "开仓即呼吸止损"
        self._shield_handoff_notified = True
        self._post_open_radar_block_until = 0.0
        self._save_state()

        ok = self._ensure_radar_sl(safe_sl, live_qty=real_amt, for_handoff=True)
        if ok and bool((tick or {}).get("phase_entered")):
            self._report_breath_phase2(real_amt, curr_px, safe_sl, sl_placed=True)
        logger.info(
            f"🫁 [{self.symbol}] 呼吸止损同步 @{safe_sl:.2f} | {reason or '兼容交棒入口'}"
        )
        return bool(ok)

    def _radar_handoff_min_gap(self, curr_px=0.0):
        px = float(curr_px or 0)
        base = self._radar_min_stop_gap(px)
        if px <= 0:
            return base
        return max(base, px * RADAR_HANDOFF_EXTRA_GAP_PCT)

    def _ideal_radar_sl_is_safe(self, curr_px, sl):
        """
        呼吸止损距市价缓冲：允许阶段一低于入场价（initial_stop）。
        仅检查相对现价的安全间距，禁止贴市毛刺。
        """
        curr_px = float(curr_px or 0)
        sl = float(sl or 0)
        if curr_px <= 0 or sl <= 0:
            return False
        gap = self._radar_handoff_min_gap(curr_px)
        if self.current_side == "LONG":
            return sl <= curr_px - gap
        if self.current_side == "SHORT":
            return sl >= curr_px + gap
        return False

    def _force_disarm_shield_before_radar(self, curr_px, reason="", notify=True):
        """兼容旧调用 → 呼吸止损同步（禁止先撤 STOP 裸仓）。"""
        real_amt = self._resolve_live_qty(self.watched_qty or 0)
        if real_amt <= 0:
            return {"cancelled": 0, "cleared": True, "verified": True}
        ok = self._perform_radar_handoff(
            real_amt, curr_px, reason=reason or "呼吸止损同步",
        )
        return {"cancelled": 1 if ok else 0, "cleared": ok, "verified": ok}

    def _should_disarm_shield_for_favorable(self, curr_px):
        """呼吸止损已单槽合并，无需再「撤硬止损交棒」。"""
        return False

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
        双轨并行（互不抢份额）：
        ① TP123 = reduceOnly 限价止盈
        ② 呼吸止损 = 硬止损+雷达合并 closePosition 单槽（阶段一阶梯 / 阶段二ADX）
        """
        # 每轮先按「价到+限价消失」对账，微漂不干扰
        self._reconcile_tp_consumed_from_live_qty(
            real_amt, curr_px, source="哨兵双轨对账", notify=True,
        )
        self._disarm_premature_radar(real_amt, curr_px, source="哨兵防线")
        radar_sl = None
        if self._resolve_defense_regime(curr_px) == "FAVORABLE":
            if self._should_radar_trail(curr_px) or self._is_radar_active():
                self._process_radar_trailing(real_amt, curr_px)
                if self.current_sl and (
                    self._is_radar_active() or self._should_radar_trail(curr_px)
                ):
                    radar_sl = float(self.current_sl)
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
                _stops = binance_client.find_protective_stop_prices(self.symbol)
                # None=查询失败：不当「缺失」，避免 REST 风暴期连环补挂
                if _stops is not None and not _stops:
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
        if live_stops is None:
            live_stops = []
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

    def _hydrate_ledger_from_state_file(self, source=""):
        """
        从磁盘状态热加载止损/仓位关键字段。
        跳过重复接管的 worker 必须调用，禁止空账本+默认ATR=30发明止损。
        """
        if not os.path.exists(self.state_file):
            logger.warning(
                f"⚠️ [{self.symbol}] 热加载失败：无状态文件 | {source}"
            )
            return False
        try:
            with open(self.state_file, "r", encoding="utf-8") as f:
                s = json.load(f)
        except Exception as e:
            logger.warning(
                f"⚠️ [{self.symbol}] 热加载读盘失败: {e} | {source}"
            )
            return False
        try:
            self.current_side = s.get("current_side") or self.current_side
            self.watched_qty = float(s.get("watched_qty", self.watched_qty) or 0)
            self.watched_entry = float(s.get("watched_entry", self.watched_entry) or 0)
            self.initial_qty = float(s.get("initial_qty", self.initial_qty) or 0)
            self.current_sl = float(s.get("current_sl", 0) or 0)
            self.initial_stop = float(s.get("initial_stop", 0) or 0)
            self.tv_sl = float(s.get("tv_sl", 0) or 0)
            self.tv_sl_ref = float(s.get("tv_sl_ref", 0) or 0)
            self.open_atr = float(s.get("open_atr", 0) or 0)
            self.current_atr = float(s.get("current_atr", self.current_atr) or 0)
            self.tv_tps = self._sanitize_tp_prices(s.get("tv_tps", self.tv_tps))
            self.tp_levels_consumed = list(s.get("tp_levels_consumed", []) or [])
            self.tp_levels_radar_handoff = list(
                s.get("tp_levels_radar_handoff", []) or []
            )
            self.radar_activated = bool(s.get("radar_activated", False))
            self.breakeven_phase = bool(s.get("breakeven_phase", False))
            self.best_price = float(s.get("best_price", 0) or 0)
            self.breathing_coefficient = float(
                s.get("breathing_coefficient", getattr(self, "breathing_coefficient", 1.0))
                or 1.0
            )
            self.early_be_done = bool(s.get("early_be_done", False))
            self._breath_ratio_history = list(
                s.get("atr_1h_ratio_history", getattr(self, "_breath_ratio_history", []))
                or []
            )
            self._last_open_exec_ts = float(
                s.get("last_open_exec_ts", getattr(self, "_last_open_exec_ts", 0)) or 0
            )
            self.open_regime = int(s.get("open_regime", s.get("regime", 3)) or 3)
            self._last_applied_exchange_sl = float(
                s.get("last_applied_exchange_sl", 0) or 0
            )
            raw_tp_ts = s.get("tp_order_placed_ts") or {}
            self._tp_order_placed_ts = {
                str(k): float(v) for k, v in dict(raw_tp_ts).items()
            }
            ok = (
                float(self.watched_entry or 0) > 0
                and (
                    float(self.current_sl or 0) > 0
                    or float(self.initial_stop or 0) > 0
                    or float(self.open_atr or 0) > 0
                )
            )
            logger.info(
                f"💧 [{self.symbol}] 账本热加载 "
                f"side={self.current_side} entry={float(self.watched_entry or 0):.2f} "
                f"sl={float(self.current_sl or 0):.2f} "
                f"init={float(self.initial_stop or 0):.2f} "
                f"open_atr={float(self.open_atr or 0):.2f} | {source}"
            )
            self._stop_write_blocked = not ok
            return ok
        except Exception as e:
            logger.warning(f"⚠️ [{self.symbol}] 热加载解析失败: {e} | {source}")
            return False

    def _ensure_sentinel_running_quiet(self):
        """单一哨兵（静默）：与 _ensure_sentinel_running 同锁，禁止并行多雷达。"""
        with self._sentinel_start_lock:
            if self._sentinel_active:
                return
            self._sentinel_active = True
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
        if hung_stops is None:
            hung_uniq = []
            hung_px = None
        else:
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
                actions.append(f"阶段二·进度{health.get('radar_progress', 0):.0%}")

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
        实盘已有唯一 STOP 时写回账本。
        账本为空时直接采纳盘口价；禁止用默认ATR发明价覆盖盘口。
        """
        entry = float(self.watched_entry or 0)
        side = (self.current_side or "").upper()
        stops = binance_client.find_protective_stop_prices(self.symbol)
        if stops is None or not stops:
            return 0.0
        uniq = sorted({round(float(p), 2) for p in stops if float(p) > 0})
        if not uniq:
            return 0.0
        if len(uniq) > 1:
            # 多笔并存：LONG 取最高（更紧）、SHORT 取最低
            if side == "LONG":
                chosen = max(uniq)
            elif side == "SHORT":
                chosen = min(uniq)
            else:
                logger.warning(
                    f"🛡️ 盘口多笔硬止损 STOP{uniq} → 拒单笔采纳，强制统一"
                    + (f" | {source}" if source else "")
                )
                return 0.0
            logger.warning(
                f"🛡️ 盘口多笔 STOP{uniq} → 采纳最紧 @{chosen:.2f}"
                + (f" | {source}" if source else "")
            )
        else:
            chosen = uniq[0]
        if side == "LONG" and entry > 0 and chosen >= entry - 0.01:
            return 0.0
        if side == "SHORT" and entry > 0 and chosen <= entry + 0.01:
            return 0.0
        ledger = self._tv_hard_sl_target(entry, side, allow_atr_invent=False)
        tol = max(float(SHIELD_STOP_TOLERANCE), chosen * 0.002)
        if ledger > 0 and abs(ledger - chosen) > tol:
            # 账本更紧则保留账本意图，但仍接受盘口价写回若盘口更紧
            if side == "LONG" and chosen > ledger + tol:
                pass  # 盘口更紧，采纳
            elif side == "SHORT" and chosen < ledger - tol:
                pass
            elif side == "LONG" and chosen < ledger - tol:
                logger.warning(
                    f"🛡️ 拒采纳盘口更宽止损 @{chosen:.2f} < 账本@{ledger:.2f}"
                    + (f" | {source}" if source else "")
                )
                return 0.0
            elif side == "SHORT" and chosen > ledger + tol:
                logger.warning(
                    f"🛡️ 拒采纳盘口更宽止损 @{chosen:.2f} > 账本@{ledger:.2f}"
                    + (f" | {source}" if source else "")
                )
                return 0.0
        old = round(float(getattr(self, "tv_sl", 0) or 0), 2)
        # 写盘口实价，绝不写 ATR 发明价
        write_px = chosen
        if ledger > 0 and abs(ledger - chosen) <= tol:
            write_px = ledger
        self.tv_sl = write_px
        if float(getattr(self, "tv_sl_ref", 0) or 0) <= 0:
            self.tv_sl_ref = write_px
        if not self.current_sl or float(self.current_sl) <= 0:
            self.current_sl = write_px
        elif side == "LONG":
            self.current_sl = max(float(self.current_sl), write_px)
        elif side == "SHORT":
            cur = float(self.current_sl)
            self.current_sl = min(cur, write_px) if cur > 0 else write_px
        if float(getattr(self, "initial_stop", 0) or 0) <= 0:
            self.initial_stop = write_px
        self.shield_active = True
        if abs(old - write_px) > SHIELD_STOP_TOLERANCE:
            self._save_state()
            logger.info(
                f"🛡️ 盘口止损写回账本 @{write_px:.2f} (原 {old or 0:.2f})"
                + (f" | {source}" if source else "")
            )
        self._tv_sl_missing_alerted = False
        self._last_applied_exchange_sl = write_px
        return write_px

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
        """维护双 STOP：永久硬止损只读确认 + 雷达止损独立同步（禁止单槽合并）。"""
        if real_amt <= 0 or not self.watched_entry:
            return False
        if getattr(self, "_stop_write_blocked", False):
            logger.debug(
                f"🛡️ [{self.symbol}] 维护硬止损跳过：stop_write_blocked"
            )
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

        # 永久硬止损与雷达独立维护；硬止损绝不因雷达改单而撤销/改价
        hard_ok = self._ensure_frozen_hard_sl(real_amt, reason="维护永久硬止损")
        if getattr(self, "tv_sl", 0) > 0 or radar_sl or self._frozen_hard_px() > 0:
            if not force and not self._can_maintain_shield_now(force=force):
                return bool(hard_ok or getattr(self, "shield_active", False))
            radar_ok = self._sync_exchange_stop(
                real_amt,
                radar_sl=radar_sl,
                reason="维护独立雷达止损",
                force=force,
            ).get("ok", False)
            return bool(hard_ok or radar_ok)

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
        """呼吸止损开仓即运行（阶段一/二）；有 initial_stop 或 current_sl 即视为活跃。"""
        if float(getattr(self, "initial_stop", 0) or 0) > 0 and float(self.current_sl or 0) > 0:
            return True
        if bool(getattr(self, "radar_activated", False)) and float(self.current_sl or 0) > 0:
            return True
        return False

    def _radar_sl_to_pass(self):
        return self.current_sl if self._is_radar_active() else None

    def _collect_tp_limit_orders(self):
        """reduceOnly / 平仓方向限价止盈单明细。
        查询失败返回 ORDERS_QUERY_FAILED（for 安全空转；调用方须 is_orders_query_failed）。
        """
        raw = binance_client.get_open_orders(self.symbol)
        if is_orders_query_failed(raw):
            logger.warning(
                f"🛡️ [{self.symbol}] TP挂单查询失败 → ORDERS_QUERY_FAILED"
                f"（禁止据此当空盘补挂/核武）"
            )
            return ORDERS_QUERY_FAILED
        orders = []
        for o in raw or []:
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
        if is_orders_query_failed(orders):
            return False
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

    def _prune_duplicate_tp_limits(self, tolerance=1.0):
        """
        同价多张 LIMIT：每价只留 1 张（最早 orderId），其余撤销。
        挂单不可读 → 0（禁止盲撤）。用于巡检轻量去重，避免核武连环撤挂。
        """
        orders = self._collect_tp_limit_orders()
        if is_orders_query_failed(orders) or not orders:
            return 0
        clusters = []
        for o in orders:
            px = float(o.get("price") or 0)
            if px <= 0:
                continue
            placed = False
            for cluster in clusters:
                if abs(cluster[0]["price"] - px) <= tolerance:
                    cluster.append(o)
                    placed = True
                    break
            if not placed:
                clusters.append([o])
        cancelled = 0
        for cluster in clusters:
            if len(cluster) <= 1:
                continue
            keep = sorted(
                cluster,
                key=lambda x: int(x.get("orderId") or 0),
            )[0]
            for o in cluster:
                if o is keep:
                    continue
                oid = o.get("orderId")
                if not oid:
                    continue
                if binance_client.cancel_order(self.symbol, oid):
                    cancelled += 1
                    logger.warning(
                        f"🧹 [{self.symbol}] 同价叠单去重 撤 id={oid} "
                        f"@{o.get('price')}（保留 {keep.get('orderId')}）"
                    )
                time.sleep(0.12)
        return cancelled

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
        if not self._orders_book_readable():
            logger.error(
                f"🛡️ [{self.symbol}] 补挂中止：挂单不可读 → 禁止盲补 TP"
            )
            return 0
        curr_px = float(binance_client.get_current_price(self.symbol) or 0)
        note = self._block_rehang_filled_tps_note(live_qty, curr_px)
        if note:
            logger.warning(f"🧩 [{self.symbol}] {note}")
        audit = self._audit_tp_levels(live_qty, tolerance, qty_tol)
        if audit.get("orders_unreadable"):
            return 0
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
            if is_orders_query_failed(orders):
                logger.error(
                    f"🛡️ [{self.symbol}] 补挂 TP{level} 中止：中途挂单不可读"
                )
                break
            at_px = [o for o in orders if abs(o["price"] - px) <= tolerance]
            if len(at_px) == 1 and abs(at_px[0]["qty"] - q) <= qty_tol:
                logger.info(f"  ✓ TP{level} @ {px:.2f} 已存在 {at_px[0]['qty']} ETH，跳过")
                continue
            for o in at_px:
                if o.get("orderId"):
                    binance_client.cancel_order(self.symbol, order=o)
                    time.sleep(0.25)
            logger.info(f"  + 补挂 TP{level} @ {px:.2f} qty={q} ETH")
            res = binance_client.place_limit_order(close_side, q, px, symbol=self.symbol, reduce_only=True)
            if res:
                placed += 1
                self._mark_tp_order_placed(level, order_res=res)
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

    def _defense_anomaly_is_severe(self, audit):
        """仅叠单/偏差/孤儿算严重（绕过冷却）；纯缺失走补挂+冷却。"""
        if not audit:
            return False
        if audit.get("orders_unreadable"):
            return False
        if audit.get("orphans"):
            return True
        for lv in audit.get("levels", []) or []:
            if lv.get("status") in ("duplicate", "qty_mismatch"):
                return True
        orders = self._collect_tp_limit_orders()
        if is_orders_query_failed(orders):
            return False
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
        """撤销全部限价止盈（不动 STOP）；多轮直到盘口无残留 TP。
        查单失败：立即中止（禁止 cancel_all 误伤硬止损/雷达，并加剧 -1003）。
        """
        total = 0
        for round_i in range(max_rounds):
            raw = binance_client.get_open_orders(self.symbol)
            if is_orders_query_failed(raw):
                logger.error(
                    f"🧹 撤限价止盈第{round_i + 1}轮：挂单不可读 → 中止撤单 "
                    f"（禁止 cancel_all 误伤 STOP / 叠单风暴）"
                )
                return total
            orders = [o for o in (raw or []) if self._is_tp_limit_order(o)]
            # 空仓净场：非 reduceOnly 的 LIMIT 幽灵单也撤（_is_tp_limit_order 在无 side 时可能漏）
            if not self.current_side:
                ghost = [
                    o for o in (raw or [])
                    if str(o.get("type") or "").upper() == "LIMIT"
                    and o not in orders
                ]
                orders = orders + ghost
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
            if is_orders_query_failed(remaining):
                logger.warning(
                    f"⚠️ 撤TP后挂单不可读 → 重试 {attempt + 1}/6（不按已净）"
                )
                continue
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
            orders = binance_client.get_open_orders(self.symbol)
            if is_orders_query_failed(orders):
                return -1
            return len(orders or [])
        except Exception:
            return -1

    def _purge_all_defense_orders_on_flat(self, reason="", max_rounds=6):
        """
        全平/人工平仓后：多轮撤净 TP123 + tv_sl/雷达 STOP + Algo 条件单。
        防止残留 reduceOnly 止盈在空仓后成交 → 反向开 orphan 仓。
        查单失败绝不当成「已清零」。
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
            counted = self._count_open_limits_and_stops()
            if counted is None or remaining < 0:
                logger.warning(
                    f"⚠️ [{tag}] 第 {attempt + 1}/{max_rounds} 轮后挂单不可读 "
                    f"→ 继续撤（禁当已净）| stops_purged={purged_stops}"
                )
                continue
            n_limit, n_stop, _ = counted
            tp_left = self._collect_tp_limit_orders()
            if is_orders_query_failed(tp_left):
                logger.warning(
                    f"⚠️ [{tag}] 第 {attempt + 1}/{max_rounds} 轮 TP 列表不可读 → 继续撤"
                )
                continue
            if remaining == 0 and n_limit == 0 and n_stop == 0 and not tp_left:
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
            if tp_left or n_limit:
                sample = tp_left[:4] if tp_left else []
                remain_txt = ", ".join(
                    f"{o['qty']}@{o['price']}" for o in sample
                ) or f"LIMIT={n_limit}"
                logger.warning(
                    f"⚠️ [{tag}] 第 {attempt + 1}/{max_rounds} 轮后仍剩 "
                    f"LIMIT={n_limit} STOP={n_stop} TP样例({remain_txt}) | "
                    f"全盘 {remaining} 单"
                )
            else:
                logger.warning(
                    f"⚠️ [{tag}] 第 {attempt + 1}/{max_rounds} 轮后仍剩 "
                    f"{remaining} 张挂单 (STOP={n_stop})"
                )
        counted = self._count_open_limits_and_stops()
        remaining = self._remaining_open_order_count()
        if counted is None or remaining < 0:
            ok = False
            n_limit, n_stop = -1, -1
            tp_n = -1
        else:
            n_limit, n_stop, _ = counted
            tp_left = self._collect_tp_limit_orders()
            tp_n = -1 if is_orders_query_failed(tp_left) else len(tp_left)
            ok = remaining == 0 and n_limit == 0 and n_stop == 0 and tp_n == 0
        if not ok:
            logger.error(
                f"❌ [{tag}] 全平后挂单未净：剩余 {remaining} 单 | "
                f"LIMIT={n_limit} STOP={n_stop} TP={tp_n}"
            )
        return {
            "ok": ok,
            "rounds": max_rounds,
            "tp_cancelled": tp_cancelled,
            "remaining": remaining,
            "tp_remaining": tp_n,
        }

    def _ensure_radar_sl(self, dynamic_sl, live_qty=None, for_handoff=False):
        """
        挂呼吸止损 STOP（closePosition 单槽，不占 TP reduceOnly）。
        开仓即允许；无旧激活线/交棒门槛。
        """
        if not dynamic_sl:
            return False
        live_qty = float(live_qty or self.watched_qty or 0)
        curr_px = float(binance_client.get_current_price(self.symbol) or 0)
        if self._radar_placement_blocked(live_qty, curr_px, reason="ensure_radar_sl"):
            return False
        if not self._is_valid_radar_sl(dynamic_sl):
            logger.warning(
                f"🫁 [{self.symbol}] 拒绝呼吸止损 @{float(dynamic_sl):.2f}：无效价"
            )
            return False
        clamped = self._clamp_radar_sl_for_market(curr_px, dynamic_sl)
        if not clamped or not self._can_safely_place_radar_sl(curr_px, clamped):
            logger.warning(
                f"🫁 [{self.symbol}] 拒绝呼吸止损：市价不安全 "
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
            reason=f"呼吸止损 @ {clamped:.2f}",
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
            logger.info(f"📡 呼吸止损已在 @{new_sl:.2f}，跳过撤挂")
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
        """兼容旧入口：仅阶段二才发钉钉，阶段一静默。"""
        if getattr(self, "_radar_activation_notified", False):
            self._radar_notify_pending = False
            return True
        if not bool(getattr(self, "breakeven_phase", False)):
            # 开仓/阶段一禁止发旧「雷达交棒」钉钉
            return False
        self._report_breath_phase2(
            real_amt, curr_px, new_sl, sl_placed=bool(sl_placed),
        )
        return bool(getattr(self, "_radar_activation_notified", False))

    def _flush_pending_radar_notify(self, real_amt, curr_px):
        """哨兵补发：仅阶段二切入钉钉失败时重试（禁止因 handoff_done 误发）。"""
        if getattr(self, "_radar_activation_notified", False):
            self._radar_notify_pending = False
            return False
        if not getattr(self, "_radar_notify_pending", False):
            return False
        # 阶段二才补发；阶段一禁止伪装成「雷达交棒」钉钉
        if not bool(getattr(self, "breakeven_phase", False)):
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
            f"🫁 [{self.symbol}] 补发阶段二钉钉 | SL={sl:.2f} | "
            f"ADX={float(getattr(self, 'last_adx', 0) or 0):.1f}"
        )
        self._report_breath_phase2(
            real_amt, curr_px, sl,
            sl_placed=self._has_stop_sl_near(sl, exclude_shield=False),
        )
        return bool(getattr(self, "_radar_activation_notified", False))

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

        if not self._orders_book_readable():
            logger.error(
                f"☢️ [{self.symbol}] 核武中止：挂单查询失败 → 禁止撤挂防裸奔"
            )
            return last_audit

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
        # 挂单不可读 ≠ 缺失：禁止触发补挂/核武
        if audit.get("orders_unreadable"):
            return True
        expected = audit.get("expected", 0)
        place_n = max(1, min(3, int(self._effective_place_tp_levels() or 3)))
        if expected <= 0:
            # 有仓且 TP 未吃完时 expected=0 = 价位缺失，禁止假「已齐」跳过挂单
            if self.current_side and float(self.watched_entry or 0) > 0:
                consumed = set(getattr(self, "tp_levels_consumed", []) or [])
                need = set(range(1, place_n + 1))
                if not need.issubset(consumed):
                    return False
            return True
        # PLACE_TP_LEVELS=2 时 expected=2 是正确态；禁止因账本仍有 TP3 价而误判不齐
        tp_prices = sum(1 for t in (self.tv_tps or [])[:place_n] if float(t or 0) > 0)
        if (
            tp_prices >= place_n
            and not self._tp_level_consumed(1)
            and expected < place_n
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
        if audit.get("orders_unreadable"):
            return False
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
            place_n = max(1, min(3, int(self._effective_place_tp_levels() or 3)))
            if not set(range(1, place_n + 1)).issubset(consumed):
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

            # PLACE_TP_LEVELS=2 时盘口残留 TP3 会成孤儿：只撤孤儿，禁止核武连环撤挂
            if (
                not recover_mode
                and int(audit.get("matched_full") or 0) >= int(audit.get("expected") or 0)
                and audit.get("orphans")
            ):
                n_orphan = self._cancel_orphan_tp_orders(live_qty)
                time.sleep(0.45)
                audit = self._audit_tp_levels(live_qty)
                if self._tp_audit_ok(audit):
                    logger.info(
                        f"✅ 仅清孤儿止盈 {n_orphan} 张后已齐，跳过核武 | "
                        f"{self._format_audit_summary(audit)}"
                    )
                    self._maintain_hard_shield(
                        live_qty, curr_px, force=False, radar_sl=radar_sl,
                    )
                    self._mark_defense_align_ok()
                    return {
                        "matched": audit["matched_full"],
                        "expected": audit["expected"],
                        "pending_prices": audit["pending_prices"],
                        "rebuilt": n_orphan > 0,
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
                if not self._orders_book_readable():
                    logger.error(
                        f"🛡️ [{self.symbol}] 重启对齐中止：挂单查询失败 → 禁止焦土撤单"
                    )
                    return {
                        "matched": audit["matched_full"],
                        "expected": audit["expected"],
                        "pending_prices": audit["pending_prices"],
                        "rebuilt": False,
                        "audit": audit,
                        "nuclear": False,
                    }
                self._scorched_earth_cancel_for_recover()
            elif self._defense_anomaly_is_severe(audit):
                if not self._orders_book_readable():
                    logger.error(
                        f"🛡️ [{self.symbol}] 严重异常但挂单查询失败 → 禁止撤TP"
                    )
                    return {
                        "matched": audit["matched_full"],
                        "expected": audit["expected"],
                        "pending_prices": audit["pending_prices"],
                        "rebuilt": False,
                        "audit": audit,
                        "nuclear": False,
                    }
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
        severe = self._defense_anomaly_is_severe(audit)
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
            _stops = binance_client.find_protective_stop_prices(self.symbol)
            if _stops is not None and not _stops:
                self._sync_exchange_stop(
                    real_amt, radar_sl=None,
                    reason="雷达守护·裸仓强制TV硬止损", force=True,
                )
            elif _stops is None:
                logger.error(
                    f"📡 [雷达守护] 挂单查询失败 → 禁止裸仓强制补止损 | {self.symbol}"
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
                title="雷达守护：止盈仍未对齐",
                detail=(
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

        if self._has_duplicate_tp_orders():
            pruned = self._prune_duplicate_tp_limits()
            if pruned:
                logger.warning(
                    f"🧹 [{self.symbol}] 同价叠单轻量去重 {pruned} 张 → 再审计"
                )
                time.sleep(0.4)
                audit = self._audit_tp_levels(live_qty)
                matched = audit["matched_full"]
                pending_prices = audit["pending_prices"]
                expected = audit["expected"]

        if self._audit_requires_nuclear(audit) or self._has_duplicate_tp_orders():
            logger.warning(
                f"☢️ 审计触发核武级重挂: {len(self._collect_tp_limit_orders() or [])} 张止盈 | "
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
        硬止损与雷达可同价共存检测：任一带量/closePosition STOP 贴近即 True。
        挂单查询失败：仅当本地 120s 内刚挂同价才 True；否则 False
        （禁止谎称「已有保护」导致裸仓开仓；place_stop 本身仍 fail-closed 防叠单）。
        """
        target = round(float(sl_price), 2)
        shield_prices = self._shield_tier_prices() if exclude_shield else []
        orders = binance_client.get_open_orders(self.symbol)
        if is_orders_query_failed(orders):
            close_side = "BUY" if self.current_side == "SHORT" else "SELL"
            key = (self.symbol, close_side, target)
            cached = getattr(binance_client, "_recent_stop_place", {}).get(key)
            if cached and (time.time() - float(cached[0])) < 120.0:
                logger.warning(
                    f"🛡️ [{self.symbol}] 挂单不可读但本地刚挂 Stop "
                    f"@{target:.2f} → 按已有"
                )
                return True
            logger.warning(
                f"🛡️ [{self.symbol}] 挂单查询失败 → _has_stop_sl_near "
                f"@{target:.2f} 不可确认（禁止谎称已有）"
            )
            return False
        for o in orders or []:
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
        orders = self._collect_tp_limit_orders()
        # 查不到：保守视为「已有」→ 禁止上层补挂（防叠单击穿）
        if is_orders_query_failed(orders):
            close_side = "BUY" if self.current_side == "SHORT" else "SELL"
            key = (self.symbol, close_side, round(float(price), 2))
            cached = getattr(binance_client, "_recent_limit_place", {}).get(key)
            if cached and (time.time() - float(cached[0])) < 120.0:
                return True
            logger.warning(
                f"🛡️ [{self.symbol}] TP查单失败 @{float(price):.2f} "
                f"→ 保守当作已有，禁止补挂"
            )
            return True
        for o in orders:
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
        呼吸止损已越过 TP1/TP2 → 撤销无意义的限价止盈（防孤儿单干扰）。
        多头：雷达价 ≥ TP 价；空头：雷达价 ≤ TP 价。
        """
        radar_sl = float(radar_sl or 0)
        if radar_sl <= 0 or not self.current_side:
            return 0
        live_qty = float(live_qty if live_qty is not None else self.watched_qty or 0)
        cancelled = 0
        stale_levels = []
        for level in (1, 2, 3):
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
                        f"⚠️ 仍有 {live_qty} ETH 且现价已过TP2 → 余仓交阶段二"
                        f"（不挂 TP3 限价）"
                    )
                    self.tp_levels_consumed = [1, 2]
                    self._save_state()
                elif self._price_reached_tp_zone(1, curr_px, live_only=True):
                    logger.warning(
                        f"⚠️ 仍有 {live_qty} ETH 且现价已过TP1 → 仅余 TP2 限价"
                        f"（PLACE_TP_LEVELS=2）"
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
        TP 成交后：只维护剩余 TP2（不挂 TP3）；同步呼吸止损数量。
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
        # 止损数量收缩收拢在呼吸引擎（价格不在此强制改）
        self._breath_resize_stop_on_tp(live_qty, reason=reason or "TP成交后对齐")
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
        开仓/成交后对账：总头寸 ≈ TP1+TP2(+TP3场景二) 切片之和；
        硬止损与雷达为双 STOP 永久共存（非单槽），不占 TP reduceOnly 额度。
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
        by_lv = {int(s.get("level") or 0): float(s.get("qty") or 0) for s in slices}
        q1 = by_lv.get(1, 0.0)
        q2 = by_lv.get(2, 0.0)
        q3 = by_lv.get(3, 0.0)
        slice_sum = round(sum(float(s.get("qty") or 0) for s in slices), 3)
        step = float(getattr(self, "qty_step", 0.001) or 0.001)
        drift = abs(slice_sum - baseline)
        ok = drift <= max(step * 3, baseline * 0.02)
        note = (
            f"开仓基线 {baseline} {self._unit()} | "
            f"TP切片合计 {slice_sum} "
            f"(TP1={q1}/TP2={q2}/TP3={q3}·余仓视场景) | "
            f"硬止损+雷达=双STOP共存·TP=reduceOnly"
        )
        if not ok:
            logger.warning(
                f"⚠️ [{self.symbol}] [{source or '对账'}] 头寸与TP123偏差 "
                f"drift={drift} | {note}"
            )
        else:
            logger.info(f"✅ [{self.symbol}] [{source or '对账'}] {note}")
        # 盘口：TP 限价张数 vs 未消费档；STOP 允许 1~2（硬+雷达），>2 才视为叠单垃圾
        try:
            tp_orders = self._collect_tp_limit_orders()
            stops = binance_client.find_protective_stop_prices(self.symbol)
            expected = self._expected_tp_count()
            if expected > 0 and len(tp_orders) > expected + 1:
                logger.warning(
                    f"⚠️ [{self.symbol}] TP限价偏多 {len(tp_orders)}>{expected} "
                    f"→ 哨兵将纠偏（不撤硬止损）"
                )
            if stops is None:
                pass
            elif len(stops) == 2:
                hard = self._frozen_hard_px()
                logger.info(
                    f"✅ [{self.symbol}] 双STOP共存确认 {stops} "
                    f"(硬止损账本@{hard:.2f} + 雷达；非叠单)"
                )
            elif len(stops) > 2:
                logger.warning(
                    f"⚠️ [{self.symbol}] 保护STOP过多 {stops} "
                    f"(期望≤2：硬止损+雷达) → 保留硬止损、清理多余雷达腿"
                )
                self._maintain_hard_shield(
                    live_qty,
                    binance_client.get_current_price(self.symbol) or 0,
                    force=True,
                    radar_sl=self._radar_sl_to_pass(),
                )
            elif len(stops) < 1:
                logger.warning(
                    f"⚠️ [{self.symbol}] 盘口无保护STOP → 补挂双防线"
                )
                self._ensure_frozen_hard_sl(live_qty, reason="对账补永久硬止损")
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
                self._breath_resize_stop_on_tp(new_qty, reason="减仓近TP区·止损数量同步")
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
            self._advance_radar_on_tp_fill(credible, curr_px_safe, new_qty)
            self._reconcile_open_qty_vs_tp123(new_qty, source=f"{levels}成交")
            # 只撤/重挂剩余 TP1+TP2；止损数量由呼吸引擎原子收缩
            result = self._realign_remaining_tps_after_fill(
                new_qty, dynamic_sl=None,
                reason=f"{levels} 成交静默对齐",
            )
            # 止损数量收缩已在 _realign_remaining_tps_after_fill → _breath_resize_stop_on_tp
            if self._is_radar_active() or float(getattr(self, "current_sl", 0) or 0) > 0:
                self._process_radar_trailing(new_qty, curr_px_safe)
            else:
                logger.info(
                    f"📡 [{self.symbol}] {levels}已记账，止损数量已同步 | "
                    f"剩余 {new_qty} {self._unit()}"
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
                "手动增仓" if new_qty > old_qty
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
                    current_stop=float(getattr(self, "current_sl", 0) or 0),
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
                "手动增仓" if new_qty > old_qty
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

    def _report_radar_intervention(self, real_amt, new_sl, action_msg, sl_placed=True,
                                   extreme=None, profit_pct=None):
        """止损移动钉钉：同价位冷却期内不重复播报"""
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
            f"持仓 {real_amt} {self._unit()}"
        )
        if not sl_placed and not verified:
            logger.warning(f"止损钉钉跳过：止损 @ {new_sl:.2f} 提交失败且盘口未核查到")
            return
        if verified:
            verify_note = base_note
        else:
            verify_note = f"{base_note} | 止损已提交，REST 同步略延迟"
            logger.info(f"止损钉钉：止损已挂 REST 延迟，仍推送 @{new_sl:.2f}")
        self._call_dingtalk(
            dingtalk.report_intervention,
            qty=real_amt,
            entry_px=self.watched_entry,
            new_sl=new_sl,
            action_msg=action_msg,
            verify_note=verify_note,
            verified=verified,
            extreme=extreme,
            profit_pct=profit_pct,
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
        if pos == "QUERY_FAILED":
            return float(fallback_qty or 0)
        if pos and float(pos.get("size") or 0) > 0:
            live = round(float(pos["size"]), 3)
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
        """用户可见平仓附注：禁止拼接内部 regime/R1-R4 编号。"""
        parts = []
        if close_side:
            parts.append(f"TV方向 {close_side}")
        if atr and float(atr) > 0:
            parts.append(f"ATR {float(atr):.2f}")
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
        if bool(getattr(self, "breakeven_phase", False)):
            return f"保本触发{BREAKEVEN_TRIGGER_ATR}×ATR→ADX追踪"
        return "开仓即呼吸止损·阶段一阶梯"

    def _signal_ts_epoch(self, signal_or_ts):
        """兼容 float epoch / 日期字符串，解析失败返回 0。"""
        if isinstance(signal_or_ts, dict):
            raw = signal_or_ts.get("ts", 0)
        else:
            raw = signal_or_ts
        if raw is None or raw == "":
            return 0.0
        try:
            return float(raw)
        except (TypeError, ValueError):
            pass
        try:
            txt = str(raw).strip()
            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%SZ"):
                try:
                    return datetime.strptime(txt.replace("+00:00", ""), fmt).timestamp()
                except ValueError:
                    continue
        except Exception:
            pass
        return 0.0

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
        last_ts = self._signal_ts_epoch(last)
        last_act = str(last.get("action", "") or "").upper()
        if last_act and last_ts > 0 and time.time() - last_ts < 180:
            if last_act == "CLOSE_TP3":
                return EXIT_SOURCE_TP3, last.get("reason") or "TV CLOSE_TP3 · TP3完美收网"
            if last_act == "CLOSE_QUICK_EXIT":
                return (
                    EXIT_SOURCE_QUICK,
                    last.get("reason") or "TV CLOSE_QUICK_EXIT · 反转保护",
                )
            if last_act == "CLOSE_RSI_EXIT":
                return (
                    EXIT_SOURCE_RSI,
                    last.get("reason") or "TV CLOSE_RSI_EXIT · 反转保护(RSI)",
                )
            if last_act == "CLOSE_PROTECT" or last_act.startswith("CLOSE_PROTECT"):
                return (
                    EXIT_SOURCE_TV_PROTECT,
                    last.get("reason") or "TV CLOSE_PROTECT · 反转保护",
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

        # 铁律：仅当现价贴近挂出的止损价，才可归因「止损平仓」。
        # 呼吸引擎开仓即 armed，不能单凭 armed 就把市价/脚本主动平仓贴成止损。
        near_stop = self._likely_exchange_stop_exit(curr_px)
        if near_stop:
            sl = float(
                getattr(self, "_last_applied_exchange_sl", 0)
                or getattr(self, "current_sl", 0)
                or getattr(self, "tv_sl", 0)
                or 0
            )
            phase = (
                "阶段二/趋势追踪"
                if getattr(self, "breakeven_phase", False)
                else "阶段一"
            )
            note = (
                f"止损平仓({phase}) @ {sl:.2f} | 现价贴止损线"
                if sl > 0
                else f"止损平仓({phase}) | 现价贴止损线"
            )
            if self._radar_was_armed():
                gate = self._describe_radar_trigger_gate(self.watched_qty, curr_px)
                note += f" | 闸门={gate}"
            if hint:
                note += f" | {hint}"
            if getattr(self, "breakeven_phase", False):
                return EXIT_SOURCE_SL_BREAKEVEN, note
            return EXIT_SOURCE_SL_INITIAL, note

        if getattr(self, "shield_active", False) or self._radar_was_armed():
            return (
                EXIT_SOURCE_MANUAL,
                hint
                or "仓位归零（现价未贴止损线·非止损触发，疑似主动/脚本/异动市价平仓）",
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
        elif exit_src in (EXIT_SOURCE_RADAR_BE, EXIT_SOURCE_SL_BREAKEVEN):
            meta = self._build_close_meta("CLOSE_SL_BREAKEVEN", side, est, note)
            meta["close_type"] = CLOSE_TYPE_BREAKEVEN
        elif exit_src in (EXIT_SOURCE_VPS_HARD_SL, EXIT_SOURCE_SL_INITIAL):
            meta = self._build_close_meta("CLOSE_SL_INITIAL", side, est, note)
            meta["close_type"] = CLOSE_TYPE_HARD_SL if exit_src == EXIT_SOURCE_SL_INITIAL else CLOSE_TYPE_VPS_SHIELD
        elif exit_src in (EXIT_SOURCE_TV_PROTECT, EXIT_SOURCE_QUICK, EXIT_SOURCE_RSI):
            act = (
                "CLOSE_QUICK_EXIT"
                if exit_src == EXIT_SOURCE_QUICK
                else (
                    "CLOSE_RSI_EXIT"
                    if exit_src == EXIT_SOURCE_RSI
                    else "CLOSE_PROTECT"
                )
            )
            meta = self._build_close_meta(act, side, est, note)
            if exit_src == EXIT_SOURCE_RSI:
                meta["close_type"] = CLOSE_TYPE_RSI
            elif exit_src == EXIT_SOURCE_QUICK:
                meta["close_type"] = CLOSE_TYPE_QUICK
            else:
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
        is_tp_sl_update = False  # v6.5.6 已废除 UPDATE_SL/TP

        # 暂停交易闸：CLOSE_THEN_OPEN_FAIL_ABORT / restart_* 需人工恢复，空仓也不自动解除
        if (
            raw_action in ("LONG", "SHORT")
            and bool(getattr(self, "trading_paused", False))
        ):
            reason = getattr(self, "trading_pause_reason", "") or "trading_paused"
            needs_manual = (
                reason.startswith("CLOSE_THEN_OPEN_FAIL")
                or reason.startswith("restart_")
                or reason.startswith("ATR_DEGRADE")
                or reason.startswith("INCIDENT_")
                or "PENDING_RESUME" in reason
            )
            live = self._get_active_position()
            live_qty = float((live or {}).get("size") or 0)
            if needs_manual or live_qty > float(getattr(self, "min_qty", 0.001) or 0.001):
                logger.error(
                    f"🚫 [{self.symbol}] 交易已暂停，拒绝开仓 {raw_action} | {reason}"
                )
                try:
                    dingtalk.report_system_alert(
                        f"开仓拒绝·交易暂停 [{self.symbol}]",
                        f"信号 {raw_action} 被拦截 | {reason} | "
                        f"实盘仓位 {live_qty if live_qty > 0 else 0}",
                        suggestion=(
                            "核对交易所持仓/挂单后 POST /admin/resume/"
                            + str(self.symbol)
                            + " 恢复"
                        ),
                    )
                except Exception:
                    pass
                return
            logger.warning(
                f"✅ [{self.symbol}] 空仓状态下解除交易暂停，允许 {raw_action}"
            )
            self.trading_paused = False
            self.trading_pause_reason = ""
            self._save_state()

        # 90m ATR 仅作展示/ADX；开仓 initial_atr 只认 TV atr（见 _resolve_open_atr）
        if raw_action in ("LONG", "SHORT"):
            try:
                vps_atr, _adx = self._refresh_market_metrics(force=False)
                if float(vps_atr or 0) > 0:
                    self.current_atr = float(vps_atr)
            except Exception:
                pass

        px_in = self._safe_float(payload.get("price"), 0.0)
        if px_in > 0:
            self.tv_price = px_in
        elif raw_action in ("LONG", "SHORT"):
            live_px = float(binance_client.get_current_price(self.symbol) or 0)
            if live_px > 0:
                self.tv_price = live_px
                payload = dict(payload)
                payload["price"] = live_px
                payload["_price_source"] = payload.get("_price_source") or "local"

        new_tps = self._sanitize_tp_prices([
            self._safe_float(payload.get("tv_tp1") or payload.get("tp1"), 0),
            self._safe_float(payload.get("tv_tp2") or payload.get("tp2"), 0),
            self._safe_float(payload.get("tv_tp3") or payload.get("tp3"), 0),
        ])
        if raw_action in ("LONG", "SHORT"):
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
                    f"📐 开仓信号 TP 本地补全 @ {px_for_tp:.2f} → {self.tv_tps} "
                    f"({payload.get('_tp_source', 'local')})"
                )
        elif sum(1 for t in new_tps if t > 0) >= 2:
            self.tv_tps = new_tps

        self._last_tv_field_sources = {
            "atr": payload.get("_atr_source", "tv"),
            "tp": payload.get("_tp_source", "tv"),
            "price": payload.get("_price_source", "tv"),
        }
        close_reason = str(payload.get("reason") or "").strip()
        close_side = str(payload.get("side") or "").strip().upper()
        pnl_pct = payload.get("pnl_pct")
        close_meta = self._build_close_meta(raw_action, close_side, pnl_pct, close_reason)
        close_extra = self._format_close_extra(
            close_side, pnl_pct, self.tv_price, self.regime, self.current_atr,
        )
        leg = str(payload.get("leg") or "").strip()

        if not raw_action:
            logger.warning("TV 信号缺少 action，已忽略")
            return
        if (
            raw_action in ("LONG", "SHORT")
            or raw_action in RECONCILE_ACTIONS
            or raw_action in FLATTEN_ACTIONS
            or raw_action.startswith("CLOSE")
        ):
            self._record_tv_signal(payload, raw_action)

        if not self._lock.acquire(timeout=120.0):
            logger.error(f"⏱️ 锁等待 120s 超时，信号 {raw_action} 重新入队(旁路)")
            self._signal_queue.put(payload)
            return

        try:
            # ── 已废除对账类 CLOSE_*：一律忽略 ──
            if (
                is_reconcile_action(raw_action)
                or (
                    raw_action.startswith("CLOSE")
                    and raw_action not in FLATTEN_ACTIONS
                    and raw_action not in ("CLOSE_QUICK_EXIT", "CLOSE_RSI_EXIT")
                )
            ):
                logger.warning(
                    f"🚫 [{self.symbol}] 忽略已废除 webhook action={raw_action} "
                    f"(仅接受 LONG/SHORT/CLOSE_QUICK_EXIT/CLOSE_RSI_EXIT)"
                )
                try:
                    dingtalk.report_system_alert(
                        f"忽略废弃webhook [{self.symbol}]",
                        f"action={raw_action} 已不在最终架构，已忽略不下单",
                        level="提示",
                    )
                except Exception:
                    pass
                return

            # ── 主动全平（反转保护）──
            if is_flatten_action(raw_action):
                if self._should_ignore_late_close(payload):
                    age = time.time() - float(
                        getattr(self, "_last_open_exec_ts", 0) or 0
                    )
                    logger.warning(
                        f"🛡️ [{self.symbol}] 迟到平仓已忽略 | {raw_action} "
                        f"开仓后 {age:.2f}s < {LATE_CLOSE_SUPPRESS_SEC}s · 保持开仓"
                    )
                    try:
                        dingtalk.report_system_alert(
                            f"迟到平仓已忽略·保持开仓 [{self.symbol}]",
                            f"{raw_action} 距开仓成交仅 {age:.2f}s "
                            f"(窗={LATE_CLOSE_SUPPRESS_SEC}s) → 不执行平仓，"
                            f"防刚开又平；同窗先平后开链不受影响",
                            level="警告",
                            suggestion="若确需平仓请人工或等待抑制窗结束后再发 CLOSE",
                        )
                    except Exception:
                        pass
                    return
                self.monitoring = False
                self._release_tv_seq_after_close(payload, reason=raw_action)
                pos = self._get_active_position()
                tv_reason = close_reason or raw_action
                tag = (
                    "反转保护"
                    if raw_action == "CLOSE_QUICK_EXIT"
                    else "反转保护(RSI)"
                )
                if not pos or pos.get("size", 0) <= 0:
                    # 盘口已空：只复位账本，钉钉标明「非本次新平仓」，避免误读成刚平完却不知原因
                    already_reason = (
                        f"已空仓复位·{tag}信号到达时盘口无仓 | {tv_reason}{close_extra}"
                    )
                    logger.info(
                        f"🧹 {tag}但盘口已空 → 撤净挂单复位 | {already_reason}"
                    )
                    flat_meta = dict(close_meta or {})
                    flat_meta["already_flat"] = True
                    flat_meta["tv_reason"] = already_reason
                    flat_meta["exit_source"] = (
                        EXIT_SOURCE_QUICK
                        if raw_action == "CLOSE_QUICK_EXIT"
                        else EXIT_SOURCE_RSI
                    )
                    flat_meta["exit_source_label"] = EXIT_SOURCE_LABELS.get(
                        flat_meta["exit_source"], tag
                    )
                    self._handle_manual_flat_detected(
                        already_reason,
                        close_meta=flat_meta,
                        curr_px=self.tv_price,
                    )
                else:
                    self._close_all(
                        f"🧹 {tag}：{tv_reason}{close_extra}",
                        close_meta=close_meta,
                    )
                return

            if raw_action in ("LONG", "SHORT"):
                # 锁定本笔 TV atr → initial_atr
                self._tv_signal_atr = self._safe_float(
                    payload.get("atr") or payload.get("ATR"), 0,
                )
                self._apply_tv_sl_from_payload(payload, source=f"{raw_action}开仓")
                self._apply_tv_sizing_params(payload)
                self.last_tv_side = raw_action
                self._save_state()
                self._handle_smart_entry(raw_action, payload)
            else:
                logger.warning(f"未识别的 TV action: {raw_action}")
        finally:
            self._lock.release()

    def _handle_tv_reconcile(self, payload, raw_action, leg, close_reason, close_meta):
        """
        已废除：CLOSE_TP/CLOSE_TRAIL/CLOSE_SL_* 对账改止损路径。
        webhook VALID_ACTIONS 已拒绝旧 action；本方法不可达，保留空壳防误调用。
        """
        logger.warning(
            f"[{self.symbol}] 旧对账路径已废除，忽略 {raw_action} "
            f"leg={leg or '-'} reason={close_reason or '-'}"
        )
        return

    def _notify_tv_reconcile(self, raw_action, leg, reason, qty, px, live_qty):
        """已废除：旧对账钉钉不再发送。"""
        return

    def _cancel_tp_level_if_still_open(self, level, handoff_radar=True):
        """
        若 TP 限价仍挂着 → 取消。
        仅在盘口确认已无该档后才 handoff + 钉钉。
        返回 True=已确认盘口无该档；False=仍在或无法确认。
        """
        level = int(level or 0)
        if level not in (1, 2, 3):
            return False

        def _tp_still_on_book():
            tps = list(self.tv_tps or [0, 0, 0])
            target_px = float(tps[level - 1] or 0) if level <= len(tps) else 0.0
            try:
                orders = binance_client.get_open_orders(self.symbol) or []
            except Exception:
                return True  # 查询失败：保守视为仍在
            for o in orders:
                otype = str(o.get("type") or o.get("origType") or "").upper()
                if "LIMIT" not in otype and otype not in ("LIMIT", "LIMIT_MAKER"):
                    continue
                opx = float(o.get("price") or o.get("stopPrice") or 0)
                if target_px > 0 and abs(opx - target_px) <= max(0.5, target_px * 0.001):
                    return True
            return False

        stored_oid = str(
            (getattr(self, "_defense_order_ids", {}) or {}).get(f"tp{level}") or ""
        ).strip()
        if stored_oid:
            try:
                binance_client.cancel_order(self.symbol, order_id=stored_oid)
                self._clear_defense_order_ids(f"tp{level}", save=False)
                logger.info(
                    f"📋 [{self.symbol}] 按持久化ID取消 TP{level} orderId={stored_oid}"
                )
            except Exception as e:
                logger.debug(f"按ID取消 TP{level} 跳过: {e}")
        try:
            orders = binance_client.get_open_orders(self.symbol) or []
        except Exception:
            orders = []
        tps = list(self.tv_tps or [0, 0, 0])
        target_px = float(tps[level - 1] or 0) if level <= len(tps) else 0
        for o in orders:
            try:
                otype = str(o.get("type") or o.get("origType") or "").upper()
                if "LIMIT" not in otype and otype not in ("LIMIT", "LIMIT_MAKER"):
                    continue
                opx = float(o.get("price") or o.get("stopPrice") or 0)
                if target_px > 0 and abs(opx - target_px) <= max(0.5, target_px * 0.001):
                    binance_client.cancel_order(self.symbol, order=o)
                    logger.info(
                        f"📋 [{self.symbol}] 取消未成交 TP{level} @{opx}"
                    )
            except Exception as e:
                logger.debug(f"取消 TP{level} 单跳过: {e}")

        placed = dict(getattr(self, "_tp_order_placed_ts", {}) or {})
        placed.pop(str(level), None)
        placed.pop(level, None)
        self._tp_order_placed_ts = placed
        self._clear_defense_order_ids(f"tp{level}", save=False)

        if _tp_still_on_book():
            self._save_state()
            logger.warning(
                f"⚠️ [{self.symbol}] TP{level} 撤单后盘口仍有限价 → 不宣称移交成功"
            )
            try:
                dingtalk.report_system_alert(
                    f"TP超时撤单未净 [{self.symbol}]",
                    f"TP{level} 已尝试撤单，但盘口仍可见该档限价；"
                    f"未标记移交。这不是「已转雷达」。",
                    level="提示",
                    suggestion="核对币安该档 LIMIT；哨兵将重试",
                )
            except Exception:
                pass
            return False

        self._save_state()
        if handoff_radar:
            self.radar_activated = True
            self._mark_tp_radar_handoff([level], source=f"取消TP{level}移交")
            try:
                dingtalk.report_system_alert(
                    f"TP超时已撤单·改由呼吸止损 [{self.symbol}]",
                    f"TP{level} 限价已确认从盘口撤销；该档禁止重挂；"
                    f"剩余仓位止盈改由呼吸止损管理。",
                    level="提示",
                )
            except Exception:
                pass
        return True

    def _reset_after_flat(self, source=""):
        """持仓清零后重置雷达/挂单状态（统一走完整呼吸账本清零）。"""
        try:
            self._purge_all_defense_orders_on_flat(source or "flat_reset")
        except Exception as e:
            logger.debug(f"flat_reset 撤单跳过: {e}")
        self._reset_breath_ledger_on_flat(source=source or "flat_reset")
        self._radar_handoff_done = False
        self._radar_armed_after_tp1 = False
        self._tp_order_placed_ts = {}
        self._save_state()
        logger.info(f"🧹 [{self.symbol}] 状态已重置 | {source}")

    # 交接规格命名别名：所有归零路径应走完整清零
    def _clear_position_local_state(self, source="平仓清零"):
        return self._reset_breath_ledger_on_flat(source=source)

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
            "regime_changed": "参数刷新 → 刷新仓位",
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
        """单一哨兵：先占位 _sentinel_active 再起线程，禁止竞态多启。"""
        if not self.monitoring:
            return
        with self._sentinel_start_lock:
            if self._sentinel_active:
                return
            self._sentinel_active = True
            threading.Thread(
                target=self._sentinel_loop, daemon=True,
                name=f"sentinel-{self.symbol}",
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
        if not self._ensure_flat_before_open(reason_tag=reason):
            logger.error("❌ 先平后开中止：无菌空仓未通过，拒绝叠仓开仓")
            try:
                self._call_dingtalk(
                    dingtalk.report_close_then_open_chain,
                    phase="中止",
                    side=action,
                    reason=reason,
                    bar_index=getattr(self, "_last_close_bar_index", None),
                    chain_same_bar=chain,
                    verify_note="qty/挂单未净 → 已拒绝开仓（CLOSE_THEN_OPEN_FAIL_ABORT）",
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

    def _reset_breath_ledger_on_flat(self, source="平仓清零"):
        """
        平仓确认后立刻清零呼吸止损/防线账本。
        禁止旧 entry/side/currentStop 残留污染下一笔或 HARD_SL 误报。
        """
        self.monitoring = False
        self.watched_qty = 0.0
        self.watched_entry = 0.0
        self.initial_qty = 0.0
        self._open_settled_qty = 0.0
        self.base_qty = 0.0
        self.current_side = None
        self.best_price = 0.0
        self.current_sl = 0.0
        self.tv_sl = 0.0
        self.tv_sl_ref = 0.0
        self.initial_stop = 0.0
        self.frozen_hard_sl_px = 0.0
        self.open_atr = 0.0
        try:
            self._locked_initial_atr.clear_on_flat()
        except Exception:
            pass
        self._atr_scenario = 0
        self._temp_stop_active = False
        self._tp3_fallback_active = False
        self.atr_source = "tv"
        self.atr_degraded = False
        self._pending_atr_degrade = None
        self.breakeven_phase = False
        self.radar_activated = False
        self.radar_step_count = 0
        self.remaining_qty_pct = 1.0
        self.tp_levels_consumed = []
        self.tp_levels_radar_handoff = []
        self.shield_active = False
        self.shield_tiers_consumed = []
        self.shield_sized_qty = 0.0
        self.tv_tps = [0.0, 0.0, 0.0]
        self._last_applied_exchange_sl = 0.0
        self._last_hard_sl_sync_ts = 0.0
        self._tv_sl_missing_alerted = False
        self._radar_activation_notified = False
        self._radar_notify_pending = False
        self._radar_trigger_gate = ""
        self._shield_handoff_notified = False
        self._radar_stage_last = 0
        self._ladder_meta_last = {}
        self._ladder_label_last = ""
        self._stop_write_blocked = False
        self._clear_defense_order_ids(save=False)
        self._clear_signal_fingerprint()
        logger.info(f"🧹 [{self.symbol}] 呼吸/防线账本已清零 | {source}")

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
        self._abnormal_reduce_alert_ts = 0.0
        self._abnormal_reduce_alert_sig = ""
        self._reset_breath_ledger_on_flat(source=meta.get("tv_reason") or reason or "感知空仓")
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

    def _add_to_position(self, action, payload):
        """已删除：单仓位 pyramiding=1，禁止任何追加仓位路径。"""
        logger.warning(
            f"🚫 [{self.symbol}] _add_to_position 已废除 | action={action} → 忽略"
        )
        return


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
        """开仓前快照：TP 价格用 TV；initial_atr 优先 webhook.atr（锁定基准）。"""
        payload = dict(payload or {})
        tps = self._sanitize_tp_prices([
            self._safe_float(payload.get("tv_tp1"), 0)
            or (self.tv_tps[0] if self.tv_tps else 0),
            self._safe_float(payload.get("tv_tp2"), 0)
            or (self.tv_tps[1] if self.tv_tps and len(self.tv_tps) > 1 else 0),
            self._safe_float(payload.get("tv_tp3"), 0)
            or (self.tv_tps[2] if self.tv_tps and len(self.tv_tps) > 2 else 0),
        ])
        # TP3 不挂限价，但仍可保留 TV 价作日志；缺 TP1/TP2 时再回落日志
        if sum(1 for t in tps[:2] if float(t or 0) > 0) < 2:
            last = self.last_tv_signal if isinstance(self.last_tv_signal, dict) else {}
            last_tps = last.get("tv_tps") or []
            if isinstance(last_tps, (list, tuple)) and sum(
                1 for t in last_tps[:2] if float(t or 0) > 0
            ) >= 2:
                tps = self._sanitize_tp_prices(list(last_tps))
            else:
                pl = last.get("payload") if isinstance(last.get("payload"), dict) else {}
                tps = self._sanitize_tp_prices([
                    self._safe_float(pl.get("tv_tp1") or last.get("tv_tp1"), 0),
                    self._safe_float(pl.get("tv_tp2") or last.get("tv_tp2"), 0),
                    self._safe_float(pl.get("tv_tp3") or last.get("tv_tp3"), 0),
                ])

        entry_px = float(
            self._safe_float(payload.get("price"), 0)
            or getattr(self, "tv_price", 0)
            or 0
        )
        tv_sl_ref = self._safe_float(
            payload.get("tv_sl") or payload.get("stop_loss")
            or getattr(self, "tv_sl_ref", 0), 0,
        )
        tv_atr = self._safe_float(payload.get("atr") or payload.get("ATR"), 0)
        self._tv_signal_atr = float(tv_atr or 0)

        # 权威 initial_atr：仅 TV atr；缺则拒绝（不经 1h/90m 冒充）
        init_atr, atr_meta = self._resolve_open_atr_with_degrade(
            entry_px, tv_sl_ref=tv_sl_ref,
        )
        init_atr = float(init_atr or 0)
        if init_atr <= 0:
            logger.error(
                f"🚨 [{self._tag()}] 开仓快照缺 TV atr → 拒开 | meta={atr_meta}"
            )

        # 90m 仅作对比/ADX 日志，不覆盖 initial_atr
        vps_atr, vps_adx = self._refresh_market_metrics(force=False)
        self._debug_compare_tv_implied_atr(
            entry_px,
            tv_sl_ref,
            float(vps_atr or 0),
            tv_atr=tv_atr if tv_atr > 0 else init_atr,
        )

        return {
            "action": str(action or payload.get("action") or self.current_side or "").upper(),
            "tv_tps": list(tps),
            "tv_sl_ref": tv_sl_ref,
            "atr": float(init_atr),  # 权威：仅 TV atr
            "atr_source": str(atr_meta.get("source") or "tv"),
            "adx": float(vps_adx or getattr(self, "last_adx", ADX_FALLBACK) or ADX_FALLBACK),
            "regime": int(
                self._safe_int(payload.get("regime"), 0)
                or getattr(self, "regime", 0)
                or 3
            ),
            "price": entry_px,
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
            try:
                # 开仓绑定：写入并锁定 initial_atr（持仓期禁止再改）
                if self._locked_initial_atr.locked:
                    if abs(self._locked_initial_atr.value - float(snap["atr"])) > 1e-6:
                        logger.warning(
                            f"🛡️ [{self.symbol}] initial_atr 已锁定 "
                            f"{self._locked_initial_atr.value:.4f}，忽略 snap atr={snap['atr']}"
                        )
                        self.open_atr = float(self._locked_initial_atr.value)
                else:
                    self._locked_initial_atr.set_on_open(float(snap["atr"]))
                    self.open_atr = float(self._locked_initial_atr.value)
            except Exception as e:
                logger.warning(f"[{self.symbol}] LockedInitialAtr 绑定跳过: {e}")
        if float(snap.get("adx") or 0) > 0:
            self.last_adx = float(snap["adx"])
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
            f"RISK20 "
            f"ATR={float(getattr(self, 'open_atr', self.current_atr) or 0):.2f}"
        )
        return list(self.tv_tps)

    def _handle_smart_entry(self, action, payload=None):
        """
        铁律（清晰）：
        - 带开仓的 TV（OPEN / LONG|SHORT 建仓）→ 一律先平现有仓再开（刷新仓位）
        - 同时收到平仓+开仓 → 缓冲已先平后开；此处开仓仍走先平后开净场
        - 单独平仓由 CLOSE* 分支清零等待（不进本函数）
        - 加仓/PYRAMID 已废除（normalize_entry_type 恒为 OPEN）
        """
        payload = payload or {}
        if self._circuit_breaker_blocks_open():
            return
        # 必须有 TV atr
        tv_atr = self._safe_float(
            payload.get("atr") or payload.get("ATR")
            or getattr(self, "_tv_signal_atr", 0), 0,
        )
        self._tv_signal_atr = float(tv_atr or 0)
        if self._tv_signal_atr <= 0:
            logger.error(f"🚫 [{self._tag()}] 开仓拒绝·缺 TV atr")
            try:
                dingtalk.report_system_alert(
                    f"[{self._tag()}] 开仓拒绝·缺TV atr",
                    f"{self.symbol} action={action} 无 atr → 拒开",
                    level="紧急",
                )
            except Exception:
                pass
            return
        entry_type = normalize_entry_type(payload.get("entry_type"))

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
                f"开仓前 {normalize_entry_type(payload.get('entry_type'))} RISK20",
                notify=False,
            )
            qty, balance, margin_usdt, margin_pct, sizing_meta = self._calc_target_open_qty(
                curr_px, payload=payload,
            )
            if qty <= 0:
                err = (sizing_meta or {}).get("error") or "qty_zero"
                logger.error(
                    f"开仓跳过：目标数量无效 balance={balance:.2f} px={curr_px} "
                    f"err={err} meta={sizing_meta}"
                )
                try:
                    dingtalk.report_system_alert(
                        f"开仓中止·数量无效 [{self.symbol}]",
                        f"TV {action} @ {curr_px} | balance={balance:.2f} | "
                        f"err={err} | 无菌净场已通过但未下单",
                        level="紧急",
                        suggestion="查权益/ATR/TV.qty；勿重复盲目发信号",
                    )
                except Exception:
                    pass
                return

            # 成功进入开仓路径 → 解除非人工中止类暂停
            if getattr(self, "trading_paused", False):
                pause_r = str(getattr(self, "trading_pause_reason", "") or "")
                sticky = (
                    pause_r.startswith("CLOSE_THEN_OPEN_FAIL")
                    or pause_r.startswith("ATR_DEGRADE")
                    or pause_r.startswith("restart_")
                    or pause_r.startswith("INCIDENT_")
                    or "PENDING_RESUME" in pause_r
                )
                if sticky:
                    logger.error(
                        f"🚫 [{self.symbol}] 人工恢复类暂停中，拒绝下单 | {pause_r}"
                    )
                    return
                logger.info(
                    f"✅ [{self.symbol}] 新开仓解除交易暂停 | {pause_r}"
                )
                self.trading_paused = False
                self.trading_pause_reason = ""

            lev = int(FIXED_LEVERAGE)
            self.leverage = float(FIXED_LEVERAGE)
            self.tv_sizing_leverage = float(FIXED_LEVERAGE)
            binance_client.set_leverage(self.symbol, leverage=lev)
            notional = qty * curr_px
            budget_txt = format_vps_sizing_note(sizing_meta, qty=qty, entry_type=ENTRY_TYPE_OPEN)
            logger.info(
                f"📐 仓位预算 [{self.symbol}]: {budget_txt} "
                f"| set_leverage={lev}x(固定) | 名义 ~{notional:.0f}U"
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

            # 下单前最后一道：必须无持仓 + 无限价/止损（防幽灵单残留）
            if not self._verify_sterile_flat():
                logger.warning(
                    f"⚠️ [{self.symbol}] 下单前挂单未净 → 再撤一轮后复检"
                )
                self._purge_all_defense_orders_on_flat(
                    f"开仓下单前·补清幽灵挂单", max_rounds=4,
                )
                if not self._wait_verify(self._verify_sterile_flat, retries=6, delay=0.4):
                    counted = self._count_open_limits_and_stops()
                    detail = (
                        f"LIMIT/STOP 不可读"
                        if counted is None
                        else f"LIMIT={counted[0]} STOP={counted[1]} total={len(counted[2])}"
                    )
                    logger.error(
                        f"❌ 开仓中止：市价下单前挂单未净 | {detail}"
                    )
                    dingtalk.report_system_alert(
                        f"开仓中止 · 下单前挂单未净 [{self.symbol}]",
                        f"TV {action} 目标 {qty} {self.unit_label} | {detail} | "
                        f"拒绝在幽灵限价未清时开仓",
                        level="紧急",
                        suggestion="币安 APP 全部撤单后再发信号",
                    )
                    return

            open_side = "BUY" if action == "LONG" else "SELL"
            logger.info(
                f"🚀 [唯一主仓] 极速开仓: {open_side} {qty} {self.unit_label} "
                f"| {self.symbol} | RISK20 | 待挂TP={self.tv_tps}"
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
                # CAP_ALIGN 已废除：禁止 reduceOnly 自主减仓，仅告警并以实盘为准继续挂防
                logger.error(
                    f"🚨 持仓偏离目标: 目标 {qty} {self.unit_label}，实盘 {real_qty} "
                    f"(>{qty * OPEN_OVERSIZE_RATIO:.3f}) → 不减仓(CAP_ALIGN已删除)"
                )
                dingtalk.report_system_alert(
                    f"持仓偏离目标·不减仓 [{self.symbol}]",
                    f"目标 {qty} {self.unit_label} (保证金 {margin_usdt:.0f}U)，"
                    f"实盘 {real_qty} @ {pos['entry_price']:.2f} | "
                    f"CAP_ALIGN已废除，以实盘为准挂TP+呼吸止损",
                    level="紧急",
                )

            self.current_side = action
            self.open_regime = int(snap.get("regime") or self.regime or 3)
            # 锁定本笔 provisional atr（TV）；场景决议后可升级为 VPS 真实 ATR
            self.open_atr = float(snap.get("atr") or self.current_atr or 0)
            self.atr_source = str(snap.get("atr_source") or "tv")
            self.atr_degraded = False
            self._pending_atr_degrade = None
            self._open_regime_sticky = True
            self.initial_qty = real_qty
            self._last_open_exec_ts = time.time()
            # 新仓重置呼吸系数采样 / 早保本（场景决议后再 force refresh）
            self._breath_ratio_history = []
            self.breathing_coefficient = 1.0
            self.early_be_done = False
            self.breakeven_phase = False
            try:
                self._atr_1h_engine().reset_ratio_history()
            except Exception:
                pass
            self.base_qty = float(real_qty)
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
            # 旧 ATR_DEGRADE 暂停已废除；两场景路径不暂停
        finally:
            self._open_in_progress = False
            self._takeover_price_skip = False
            # 开仓未成交则丢弃降级挂起，避免误暂停
            if not getattr(self, "monitoring", False):
                self._pending_atr_degrade = None

    def _protect_and_monitor(self, qty, entry_price, budget_note="", target_qty=0.0, sizing_meta=None):
        """
        开仓后防线（两场景定稿）：
        1) 核实持仓 → 绑回本笔 TV TP1/TP2/TP3 价
        2) 共同第一步：永久硬止损(max(TV×1.2,1.5×ATR×1.05)+滑点×2) + TP1/TP2(30%/30%)，不挂TP3
        3) 同步拉原生1h ATR：
           · 场景一：真实ATR重算 initialStop，撤临时止损，不挂TP3
           · 场景二：TV atr 运作雷达，挂TP3(40%)兜底，不暂停；tick可持续恢复场景一
        4) 呼吸止损开仓即接管
        5) 实盘核实后钉钉一条
        """
        entry_price = float(entry_price or 0)
        # 开仓路径：禁止接管「现价已过跳过TP」污染；强制绑回本笔 TV TP
        self._takeover_price_skip = False
        snap = getattr(self, "_pending_open_defense_snap", None)
        self._bind_tv_open_defenses(
            snap, entry=entry_price, side=self.current_side, source="开仓保护绑定",
        )
        # 开仓硬闸：TV 空/不全时必须用实盘 entry+ATR 合成 TP 价，禁止 expected=0 裸奔
        if not self._ensure_tp123_prices_from_tv(entry_price):
            logger.error(
                f"🚨 [{self.symbol}] 开仓 TP1/TP2 补全失败 entry={entry_price} "
                f"tps={self.tv_tps} → 仍强制挂呼吸止损"
            )
            dingtalk.report_system_alert(
                f"开仓 TP 补全失败 [{self.symbol}]",
                f"{self.current_side} entry={entry_price:.2f} | tps={self.tv_tps} | "
                f"将仅挂呼吸止损，哨兵继续补 TP1/TP2",
            )
        # 若补全后仍空，再从快照硬灌一次
        if sum(1 for t in (self.tv_tps or []) if float(t or 0) > 0) < 3 and snap:
            self._bind_tv_open_defenses(
                snap, entry=entry_price, side=self.current_side, source="开仓保护·快照回灌",
            )
        tp_pxs = list(self.tv_tps or [0.0, 0.0, 0.0])
        # 两场景定稿：先记账，核实仓位后再挂「临时止损+TP1/TP2」，再同步决议 ATR 场景
        # （禁止在核实前用虚构 ATR 发明止损）
        self.best_price = entry_price
        self.breakeven_phase = False
        self.radar_activated = True
        self._radar_stage_last = 1
        self.shield_active = False
        self.shield_tiers_consumed = []
        self.tp_levels_consumed = []
        self._radar_activation_notified = False
        self._radar_notify_pending = False
        self._radar_trigger_gate = "开仓即呼吸止损·两场景"
        self._radar_armed_after_tp1 = True
        self._radar_handoff_done = True
        self._ws_tp1_fill_hint = False
        self._ws_tp_fill_levels = set()
        self._shield_handoff_notified = True  # 不再发「交棒」旧钉钉
        self._post_open_radar_block_until = 0.0
        self.remaining_qty_pct = 1.0
        self._open_settled_qty = float(qty or 0)
        self.initial_qty = float(qty or 0)
        self._atr_scenario = 0
        self._temp_stop_active = False
        self._tp3_fallback_active = False
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
            self._clear_signal_fingerprint()
            if target_qty > 0 and live_qty > target_qty * OPEN_OVERSIZE_RATIO:
                logger.warning(
                    f"🚫 [{self.symbol}] 开仓后实盘 {live_qty} > 目标 "
                    f"{target_qty}×{OPEN_OVERSIZE_RATIO} → CAP_ALIGN已废除，不减仓"
                )
            # 以实盘核实仓为开仓基线（禁止 CAP_ALIGN 自主减仓）
            self.watched_qty = live_qty
            self.initial_qty = float(live_qty)
            self._open_settled_qty = float(live_qty)
            self._save_state()

            # 开仓后只清 TP 残留；硬止损由共同第一步统一挂上（禁先撤净 STOP 裸仓窗口）
            self._cancel_all_tp_limit_orders(max_rounds=3)
            time.sleep(0.4)
            # 再用核实 entry 补一次 TP（防 TV 空价时首轮用错价）
            entry_live = float(verified["entry_price"] or entry_price)
            self.watched_entry = entry_live
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

            # ① 共同第一步：临时止损 + TP1/TP2（不挂TP3）
            self._arm_temp_stop_and_tp12(
                live_qty, entry_live, self.current_side, source="开仓共同第一步",
            )
            # ② 同流程内同步拉原生1h ATR → 场景一或场景二
            self._resolve_atr_scenario_after_open(
                entry_live, self.current_side, live_qty,
            )
            vps_sl = float(getattr(self, "current_sl", 0) or 0)

            # 开仓后硬闸：雷达 + 永久硬止损双防线对齐
            hung = binance_client.find_protective_stop_prices(self.symbol)
            frozen = self._frozen_hard_px()
            hard_ex = round(float(order_stop_price(
                self.current_side, frozen,
                buffer_usd=self._stop_buffer_usd(),
                profile=getattr(self, "breath_profile", None),
            ) or frozen), 2) if frozen > 0 else 0.0
            if hung is None:
                logger.error(
                    f"🚨 [{self.symbol}] 开仓后挂单查询失败 → 禁止盲补止损"
                )
            else:
                self._sync_exchange_stop(
                    live_qty, radar_sl=self.current_sl,
                    reason="开仓后雷达止损对齐",
                    force=True,
                )
                self._ensure_frozen_hard_sl(live_qty, reason="开仓后永久硬止损对齐")
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
            has_hard = (
                frozen > 0 and hard_ex > 0
                and self._has_stop_sl_near(hard_ex, exclude_shield=False)
            )
            if hung_final is None:
                logger.error(
                    f"🚨 [{self.symbol}] 开仓终检：挂单查询失败 → "
                    f"禁止当裸仓强平/连环补挂"
                )
            elif not hung_final:
                logger.error(f"🚨 [{self.symbol}] 开仓终检：盘口无止损 → 再强制补挂双防线")
                self._ensure_frozen_hard_sl(live_qty, reason="开仓终检硬止损补挂")
                self._sync_exchange_stop(
                    live_qty, radar_sl=None, reason="开仓终检雷达补挂", force=True,
                )
                hung_final = binance_client.find_protective_stop_prices(self.symbol)
                has_hard = (
                    frozen > 0 and hard_ex > 0
                    and self._has_stop_sl_near(hard_ex, exclude_shield=False)
                )
                if hung_final is not None and not hung_final and not has_hard:
                    dingtalk.report_system_alert(
                        f"开仓后裸仓无硬止损 [{self.symbol}]",
                        f"{self.current_side} {live_qty} {self.unit_label} @ "
                        f"{verified['entry_price']:.2f} | TP {matched}/{expected} | "
                        f"永久硬止损@{frozen:.2f} | 将撤销开仓防裸奔",
                    )
                    self._emergency_flatten_naked_open(
                        "硬止损失败·撤销开仓防裸奔",
                    )
                    return
            elif frozen > 0 and not has_hard:
                logger.error(
                    f"🚨 [{self.symbol}] 开仓终检：永久硬止损缺失 frozen@{frozen:.2f} → 补挂"
                )
                self._ensure_frozen_hard_sl(live_qty, reason="开仓终检硬止损补挂")
            # 终检：应有 TP 却不齐 / 无硬止损 → 强制闭环挂齐（清假成交+推离+重挂）
            # hung_final is None：查询失败，勿当裸仓
            if (expected > 0 and matched < expected) or (
                hung_final is not None and not hung_final
            ):
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
                if hung_final is not None and not hung_final:
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
            if hung_final is not None and not hung_final:
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
                    if hung_late is None:
                        logger.error(
                            f"🚨 [{self.symbol}] 开仓滞后核实：挂单查询失败 → 禁止撤开仓"
                        )
                    elif not hung_late:
                        dingtalk.report_system_alert(
                            f"开仓滞后核实仍无硬止损 [{self.symbol}]",
                            f"{self.current_side} {late_qty} @ {late_entry:.2f} | "
                            f"将撤销开仓防裸奔（自查7.6）",
                        )
                        self._emergency_flatten_naked_open(
                            "开仓滞后核实·硬止损失败·撤开仓防裸奔",
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
        level = int(level or 0)
        if level in (getattr(self, "tp_levels_consumed", []) or []):
            return True
        # 超时移交雷达：视同已消费，禁止补挂/核武重挂
        return level in (getattr(self, "tp_levels_radar_handoff", []) or [])

    def _mark_tp_radar_handoff(self, levels, source=""):
        """TP 超时撤单后永久禁止再挂该档（隔离于假成交清理）。"""
        handoff = set(int(x) for x in (getattr(self, "tp_levels_radar_handoff", []) or []))
        before = set(handoff)
        for lv in levels or []:
            try:
                handoff.add(int(lv))
            except (TypeError, ValueError):
                continue
        if handoff == before:
            return False
        self.tp_levels_radar_handoff = sorted(handoff)
        logger.warning(
            f"📡 [{self.symbol}] TP移交雷达禁重挂 {sorted(handoff - before)} "
            f"→ 累计{self.tp_levels_radar_handoff} | {source or 'timeout'}"
        )
        self._save_state()
        return True

    def _tp_filled_verified(self, level, live_qty=None, curr_px=0.0):
        """账本标记 + 减仓证据 + 该档限价已不在盘口 → 才认定 TP 真正成交"""
        level = int(level)
        if level in (getattr(self, "tp_levels_radar_handoff", []) or []):
            # 超时移交不是成交核实，但禁止当假记账清掉
            return True
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
        """已废弃：呼吸止损开仓即运行，禁止再把止损拉回旧 TV 价。"""
        return False

    def _disarm_premature_radar(self, live_qty=None, curr_px=0.0, source=""):
        """
        已废弃回撤逻辑：呼吸止损只前进不回撤。
        仅清理伪 TP 记账，绝不把 SL 拉回旧价。
        """
        live_qty = float(live_qty or self.watched_qty or 0)
        curr_px = float(curr_px or binance_client.get_current_price(self.symbol) or 0)

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
                f"🫁 [{self.symbol}] [{source or '呼吸'}] 清除伪TP标记 {fake}"
            )
        return False

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
        进度展示用：1=阶段一阶梯，2=阶段二呼吸追踪。
        （旧 5 阶段 TP 梯子已废除，不再驱动挂单。）
        """
        if bool(getattr(self, "breakeven_phase", False)):
            return 2
        if float(getattr(self, "current_sl", 0) or 0) > 0 or getattr(
            self, "radar_activated", False
        ):
            return 1
        return 0

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
        """已废除旧阶段呼吸表 → 连续阶梯追踪。"""
        return self._compute_ladder_sl(curr_px)

    def _refresh_radar_state_on_recover(self, curr_px, entry):
        """
        重启/接管：呼吸止损开仓即运行。
        用 breath tick 对齐 current_sl；禁止拉回旧 TV 价；禁止回撤。
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

        # 确保 initial_stop 存在
        atr = self._get_locked_initial_atr()
        side = str(self.current_side or "").strip().upper()
        if float(getattr(self, "initial_stop", 0) or 0) <= 0 and atr > 0:
            self.initial_stop = initial_stop_price(
                side, entry, atr, profile=getattr(self, "breath_profile", None),
            )
        if float(getattr(self, "current_sl", 0) or 0) <= 0:
            self.current_sl = float(self.initial_stop or 0)

        # 过价 TP1 仅记账，不作为止损门槛
        if self._price_reached_tp_zone(1, curr_px, live_only=True):
            if 1 not in (getattr(self, "tp_levels_consumed", []) or []):
                self._mark_tp_levels_consumed([1])

        tick = self._apply_breath_stop_tick(curr_px)
        new_sl = float((tick or {}).get("stop") or self.current_sl or 0)
        if new_sl > 0 and self._ideal_radar_sl_is_safe(curr_px, new_sl):
            clamped = self._clamp_radar_sl_for_market(curr_px, new_sl) or new_sl
            if side == "LONG":
                self.current_sl = max(float(self.current_sl or 0), float(clamped))
            else:
                cur = float(self.current_sl or 0)
                self.current_sl = min(cur, float(clamped)) if cur > 0 else float(clamped)
            self.tv_sl = float(self.current_sl)

        self.radar_activated = True
        self._radar_armed_after_tp1 = True
        self._radar_handoff_done = True
        self._radar_trigger_gate = "开仓即呼吸止损"
        self._radar_stage_last = 2 if getattr(self, "breakeven_phase", False) else 1
        logger.info(
            f"🫁 [{self.symbol}] 重启呼吸止损对齐: "
            f"阶段{'二·ADX' if getattr(self, 'breakeven_phase', False) else '一·阶梯'} | "
            f"SL={float(self.current_sl or 0):.2f} best={float(self.best_price or 0):.2f} | "
            f"initial={float(getattr(self, 'initial_stop', 0) or 0):.2f} | "
            f"ADX={float(getattr(self, 'last_adx', 0) or 0):.1f} | "
            f"已过档 {getattr(self, 'tp_levels_consumed', [])}"
        )
        self._save_state()

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
        """连续阶梯：entry→TP1 路程 85% 激活。"""
        return float(RADAR_ACTIVATE_TP1_FRAC)

    def _radar_activation_price(self):
        """价触此价 → 首次激活保本。"""
        entry = float(self.watched_entry or 0)
        tp1 = float((self.tv_tps or [0])[0] or 0) if self.tv_tps else 0.0
        if tp1 <= 0:
            tp1 = entry + self._tp1_distance() if self.current_side == "LONG" else entry - self._tp1_distance()
        return radar_activation_price(self.current_side, entry, tp1)

    def _compute_radar_sl_for_stage(self, stage, curr_px=0.0):
        """兼容旧调用：一律走呼吸止损。"""
        return self._compute_ladder_sl(curr_px)

    def _apply_breath_stop_tick(self, curr_px=0.0):
        """
        每个 tick 更新呼吸止损状态（按品种 breath_profile）。
        返回 dict(stop, best, breakeven_phase, meta) 或 None。
        """
        entry = float(self.watched_entry or 0)
        side = str(self.current_side or "").strip().upper()
        if entry <= 0 or side not in ("LONG", "SHORT"):
            return None
        atr = self._get_locked_initial_atr()
        profile = getattr(self, "breath_profile", None)
        init = float(getattr(self, "initial_stop", 0) or 0)
        if init <= 0:
            init = initial_stop_price(side, entry, atr, profile=profile)
            self.initial_stop = init
        cur = float(getattr(self, "current_sl", 0) or 0) or init
        best = float(getattr(self, "best_price", 0) or 0) or entry
        px = float(curr_px or 0) or best
        phase = bool(getattr(self, "breakeven_phase", False))
        early = bool(getattr(self, "early_be_done", False))
        coeff = float(self._refresh_breathing_coefficient(force=False) or 1.0)
        if coeff <= 0:
            coeff = 1.0

        out = calculate_breath_stop(
            side,
            px,
            entry,
            atr,
            init,
            cur,
            best,
            phase,
            breathing_coefficient=coeff,
            profile=profile,
            early_be_done=early,
        )
        new_stop = float(out["stop"] or 0)
        new_best = float(out["best"] or best)
        new_phase = bool(out["breakeven_phase"])
        self.early_be_done = bool(out.get("early_be_done") or early)
        meta = out.get("meta") or {}
        meta["breathing_coefficient"] = coeff
        breath_meta = getattr(self, "_breath_coeff_meta", None) or {}
        if breath_meta:
            meta["atr_1h"] = breath_meta.get("atr_1h")
            meta["smooth_ratio"] = breath_meta.get("smooth_ratio")

        if new_best > 0:
            self.best_price = new_best
        if new_stop > 0:
            if side == "LONG":
                self.current_sl = max(cur, new_stop) if cur > 0 else new_stop
            else:
                self.current_sl = min(cur, new_stop) if cur > 0 else new_stop
            self.tv_sl = float(self.current_sl)
        was_phase = phase
        self.breakeven_phase = new_phase
        self.radar_activated = True
        steps = int(meta.get("step_count") or 0)
        if steps > int(getattr(self, "radar_step_count", 0) or 0):
            self.radar_step_count = steps
        self._radar_stage_last = 2 if new_phase else 1
        self._ladder_meta_last = meta
        self._ladder_label_last = (
            f"呼吸追踪·coeff={coeff:.2f}·{meta.get('trail_distance', 0):.2f}"
            if new_phase else f"阶梯锁本·step{steps}·coeff={coeff:.2f}"
        )
        if new_phase and not was_phase:
            logger.info(
                f"🫁 [{self._tag()}] 呼吸止损切入阶段二 | "
                f"SL={self.current_sl:.2f} best={self.best_price:.2f} "
                f"coeff={coeff:.2f} trail={meta.get('trail_distance', 0):.2f} "
                f"profile={meta.get('profile')}"
            )
        return {
            "stop": float(self.current_sl or 0),
            "best": float(self.best_price or 0),
            "breakeven_phase": new_phase,
            "meta": meta,
            "phase_entered": bool(new_phase and not was_phase),
            "early_be_done": bool(self.early_be_done),
        }

    def _compute_ladder_sl(self, curr_px=0.0):
        """呼吸止损（替代旧阶梯雷达）。未就绪返回 None。"""
        out = self._apply_breath_stop_tick(curr_px)
        if not out:
            return None
        sl = float(out.get("stop") or 0)
        return sl if sl > 0 else None

    def _compute_radar_sl(self):
        if not self.watched_entry:
            return None
        curr_px = float(binance_client.get_current_price(self.symbol) or 0)
        raw = self._compute_ladder_sl(curr_px)
        if raw is None:
            return None
        label = getattr(self, "_ladder_label_last", "") or "呼吸止损"
        phase = bool(getattr(self, "breakeven_phase", False))
        logger.debug(
            f"🫁 呼吸止损 {label} | SL→{raw:.2f} | best={self.best_price:.2f} | "
            f"阶段二={phase}"
        )
        return round(float(raw), 2)

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
        """呼吸止损开仓即武装。"""
        if getattr(self, "_open_in_progress", False):
            return False
        return self._is_radar_active() or bool(
            getattr(self, "_radar_handoff_done", False)
        )

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
        """呼吸止损：有监控仓位即追踪。"""
        if getattr(self, "_open_in_progress", False):
            return False
        return bool(self.watched_entry and self.current_side)

    def _sync_radar_sl_from_best(self, curr_px):
        if not self._should_radar_trail(curr_px):
            return self.current_sl
        new_sl = self._compute_radar_sl()
        if new_sl is None:
            return self.current_sl
        if self.current_side == "LONG" and new_sl > float(self.current_sl or 0):
            logger.info(
                f"📈 呼吸止损刷新: {float(self.current_sl or 0):.2f} → {new_sl:.2f} "
                f"(best={self.best_price:.2f})"
            )
            self.current_sl = new_sl
            self.tv_sl = new_sl
            self._save_state()
        elif self.current_side == "SHORT":
            cur = float(self.current_sl or 0)
            if cur <= 0 or new_sl < cur:
                logger.info(
                    f"📉 呼吸止损刷新: {cur:.2f} → {new_sl:.2f} "
                    f"(best={self.best_price:.2f})"
                )
                self.current_sl = new_sl
                self.tv_sl = new_sl
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
        """
        REST 哨兵间隔：常态/雷达均 ≥1.0s，再加 0~200ms 抖动，避免双币种整秒撞峰。
        成交感知优先 User Data WS；markPrice WS 驱动呼吸改单。
        """
        if self._is_radar_active() or self._radar_legitimately_armed(self.watched_qty, curr_px):
            base = float(SENTINEL_POLL_RADAR)
        else:
            base = float(SENTINEL_POLL_NORMAL)
        jitter = random.uniform(0.0, float(SENTINEL_POLL_JITTER_SEC))
        return base + jitter

    def _maybe_refresh_atr(self):
        """
        刷新 current_atr + last_adx（90m 合成）；open_atr(initialAtr) 锁定不变。
        止损距离全程用 initialAtr；ADX 仅影响阶段二追踪倍数。
        """
        now = time.time()
        last = float(getattr(self, "_atr_last_update_ts", 0) or 0)
        # 90m：约每 3 分钟探测一次是否有新合成 bar
        if last > 0 and (now - last) < 180.0:
            return False
        try:
            atr, adx = self._refresh_market_metrics(force=True)
        except Exception as e:
            logger.debug(f"行情引擎刷新失败: {e}")
            return False
        if atr <= 0 and adx <= 0:
            return False
        old_a = float(self.current_atr or 0)
        old_x = float(getattr(self, "last_adx", 0) or 0)
        if atr > 0:
            self.current_atr = atr
        if adx > 0:
            self.last_adx = adx
        if float(getattr(self, "open_atr", 0) or 0) <= 0 and atr > 0:
            self.open_atr = atr
        self._atr_last_update_ts = now
        if abs(atr - old_a) > 1e-6 or abs(adx - old_x) > 1e-6:
            logger.info(
                f"📐 [{self.symbol}] 行情刷新 ATR {old_a:.2f}→{float(self.current_atr):.2f} "
                f"ADX {old_x:.1f}→{float(self.last_adx):.1f} "
                f"(open_atr锁定={float(getattr(self, 'open_atr', 0) or 0):.2f})"
            )
            self._save_state()
        return True

    def _extract_exchange_order_id(self, res):
        """从下单响应提取 orderId / algoId。"""
        if not isinstance(res, dict):
            return ""
        oid = res.get("algoId") or res.get("orderId") or res.get("clientOrderId")
        return str(oid or "").strip()

    def _set_defense_order_id(self, key, res_or_id, save=True):
        """key: tp1 | tp2 | tp3 | hard_stop | radar_stop | stop"""
        key = str(key or "").strip().lower()
        if key not in ("tp1", "tp2", "tp3", "hard_stop", "radar_stop", "stop"):
            return
        if res_or_id is None:
            return
        if isinstance(res_or_id, dict):
            oid = self._extract_exchange_order_id(res_or_id)
        else:
            oid = str(res_or_id or "").strip()
        if not oid:
            return
        ids = dict(getattr(self, "_defense_order_ids", None) or {})
        for k in ("tp1", "tp2", "tp3", "hard_stop", "radar_stop", "stop"):
            ids.setdefault(k, "")
        ids[key] = oid
        if key == "radar_stop":
            ids["stop"] = oid
        self._defense_order_ids = ids
        if save:
            self._save_state()
        logger.info(f"📎 [{self.symbol}] 持久化订单ID {key}={oid}")

    def _clear_defense_order_ids(self, *keys, save=True):
        ids = dict(getattr(self, "_defense_order_ids", None) or {})
        for k in ("tp1", "tp2", "tp3", "hard_stop", "radar_stop", "stop"):
            ids.setdefault(k, "")
        targets = keys or ("tp1", "tp2", "tp3", "hard_stop", "radar_stop", "stop")
        for k in targets:
            kk = str(k or "").strip().lower()
            if kk in ids:
                ids[kk] = ""
        self._defense_order_ids = ids
        if save:
            self._save_state()

    def _clear_signal_fingerprint(self):
        """平仓/开仓成功后清指纹，避免 60s 误杀同价再入场。"""
        self._last_signal_fp = None
        self._last_signal_fp_ts = 0.0

    def _mark_tp_order_placed(self, level, order_res=None):
        level = int(level or 0)
        if level not in (1, 2, 3):
            return
        placed = dict(getattr(self, "_tp_order_placed_ts", {}) or {})
        placed[str(level)] = time.time()
        self._tp_order_placed_ts = placed
        if order_res is not None:
            self._set_defense_order_id(f"tp{level}", order_res, save=False)
        self._save_state()

    def _check_tp_order_timeouts(self, curr_px=0.0):
        """
        TP 限价超时策略（防误伤正常等待）：
        - 现价从未进入该档触及区 → 正常等待，不撤不告警（即使已挂满 timeout 秒）
        - 现价已进入触及区但仍未成交且超时 → 撤单并移交呼吸止损；仅确认盘口已无该档后才标记
        """
        placed = dict(getattr(self, "_tp_order_placed_ts", {}) or {})
        if not placed:
            return
        now = time.time()
        timeout = float(ORDER_TIMEOUT_SEC or 300)
        changed = False
        for level_s, ts in list(placed.items()):
            try:
                level = int(level_s)
                ts = float(ts or 0)
            except (TypeError, ValueError):
                continue
            if level not in (1, 2, 3) or ts <= 0:
                continue
            if now - ts < timeout:
                continue
            if self._tp_level_consumed(level):
                placed.pop(str(level), None)
                changed = True
                continue
            # 价未到 → 正常等待，推迟复查，禁止「未成交转雷达」误报
            if not self._price_reached_tp_zone(level, curr_px, live_only=True):
                logger.debug(
                    f"⏳ [{self.symbol}] TP{level} 已挂 {now - ts:.0f}s 但现价未进触及区 "
                    f"→ 正常等待，不撤单"
                )
                placed[str(level)] = now - timeout + 60.0
                changed = True
                continue
            logger.warning(
                f"⏰ [{self.symbol}] TP{level} 价已触及却超时 {timeout:.0f}s 未成交 "
                f"→ 撤单移交呼吸止损"
            )
            ok = self._cancel_tp_level_if_still_open(level, handoff_radar=True)
            if ok:
                consumed = list(getattr(self, "tp_levels_consumed", []) or [])
                if level not in consumed:
                    consumed.append(level)
                    self.tp_levels_consumed = sorted(set(consumed))
                placed.pop(str(level), None)
                changed = True
            else:
                placed[str(level)] = now - timeout + 30.0
                changed = True
        if changed:
            self._tp_order_placed_ts = placed
            self._save_state()

    def _process_radar_trailing(self, real_amt, curr_px):
        """
        呼吸止损追踪：开仓即运行，只向有利方向改挂止损价（数量由 TP 成交路径收缩）。
        同价已挂 / 未达最小步进 / 冷却中 → 禁止撤挂死循环。
        """
        if getattr(self, "_breath_tick_paused", False):
            return False
        if self._radar_placement_blocked(real_amt, curr_px, reason="trailing", silent=True):
            return False
        if not self._should_radar_trail(curr_px):
            return False
        real_amt = float(self._resolve_live_qty(real_amt) or 0)
        if real_amt <= 0:
            return False

        try:
            self._reconcile_tp_consumed_from_live_qty(
                real_amt, curr_px, source="呼吸止损前对账", notify=True,
            )
            pos = self._get_active_position()
            if pos and float(pos.get("size") or 0) > 0:
                real_amt = float(pos["size"])
                self.watched_qty = real_amt
            init_q = float(getattr(self, "initial_qty", 0) or 0)
            if init_q > 0:
                self.remaining_qty_pct = max(0.0, min(1.0, real_amt / init_q))
        except Exception as e:
            logger.debug(f"呼吸止损前TP对账跳过: {e}")

        tick = self._apply_breath_stop_tick(curr_px)
        if not tick:
            return False
        # 场景二：每次呼吸 tick 尝试恢复 VPS 真实 ATR
        try:
            if self._maybe_recover_atr_scenario(
                entry=self.watched_entry, side=self.current_side, live_qty=real_amt,
            ):
                tick = self._apply_breath_stop_tick(curr_px) or tick
        except Exception as e:
            logger.debug(f"场景二ATR恢复跳过: {e}")
        new_sl = float(tick.get("stop") or 0)
        if new_sl <= 0:
            return False
        new_sl = self._clamp_radar_sl_for_market(curr_px, new_sl)
        if not new_sl or not self._can_safely_place_radar_sl(curr_px, new_sl):
            return False

        now = time.time()
        phase_up = bool(tick.get("phase_entered"))
        stage = 2 if tick.get("breakeven_phase") else 1
        last_sl = float(getattr(self, "_last_applied_exchange_sl", 0) or 0)
        min_step = max(0.3, float(curr_px or self.watched_entry or 0) * 0.00025)
        cooled = (
            now - float(getattr(self, "_last_radar_trail_ts", 0) or 0)
            < RADAR_TRAIL_MIN_INTERVAL_SEC
            and not phase_up
        )

        if self._has_stop_sl_near(
            order_stop_price(
                self.current_side, new_sl,
                buffer_usd=self._stop_buffer_usd(),
                profile=getattr(self, "breath_profile", None),
            ) or new_sl,
            exclude_shield=False,
        ):
            self.current_sl = new_sl
            self.tv_sl = new_sl
            self._last_applied_exchange_sl = round(
                float(order_stop_price(
                    self.current_side, new_sl,
                    buffer_usd=self._stop_buffer_usd(),
                    profile=getattr(self, "breath_profile", None),
                ) or new_sl), 2,
            )
            self._radar_stage_last = stage
            if phase_up and not getattr(self, "_radar_activation_notified", False):
                self._report_breath_phase2(
                    real_amt, curr_px, new_sl, sl_placed=True,
                )
            return False

        moved_enough = False
        exch_last = float(getattr(self, "_last_applied_exchange_sl", 0) or 0)
        exch_new = float(order_stop_price(
            self.current_side, new_sl,
            buffer_usd=self._stop_buffer_usd(),
            profile=getattr(self, "breath_profile", None),
        ) or new_sl)
        if self.current_side == "LONG":
            moved_enough = exch_new > max(exch_last, float(
                order_stop_price(
                    self.current_side, self.current_sl,
                    buffer_usd=self._stop_buffer_usd(),
                    profile=getattr(self, "breath_profile", None),
                ) or self.current_sl or 0
            )) + min_step
        else:
            ref = exch_last if exch_last > 0 else float(
                order_stop_price(
                    self.current_side, self.current_sl,
                    buffer_usd=self._stop_buffer_usd(),
                    profile=getattr(self, "breath_profile", None),
                ) or self.current_sl or 0
            )
            moved_enough = (ref <= 0) or (exch_new < ref - min_step)

        if not moved_enough and not phase_up:
            return False
        if cooled and not moved_enough:
            return False

        old_sl = float(self.current_sl or 0)
        self.current_sl = new_sl
        self.tv_sl = new_sl
        self._save_state()
        sl_placed = self._realign_radar_defenses(
            real_amt, self.watched_entry, new_sl,
        )
        label = getattr(self, "_ladder_label_last", "") or "呼吸止损"
        self._log_radar_update(stage, old_sl, new_sl, label, curr_px)
        self._cancel_stale_tp_beyond_radar(new_sl, real_amt)
        meta = tick.get("meta") or {}
        trail = float(meta.get("trail_atr") or 0)
        entry = float(self.watched_entry or 0)
        extreme = float(getattr(self, "best_price", 0) or curr_px or 0)
        if self.current_side == "LONG":
            move_word = "上移" if new_sl >= old_sl else "下移"
            profit_pct = ((float(curr_px) - entry) / entry * 100.0) if entry > 0 else 0.0
        else:
            move_word = "下移" if (old_sl <= 0 or new_sl <= old_sl) else "上移"
            profit_pct = ((entry - float(curr_px)) / entry * 100.0) if entry > 0 else 0.0
        self._report_radar_intervention(
            real_amt, new_sl,
            f"止损{move_word}至 {new_sl:.2f}，当前最高/最低价 {extreme:.2f}，浮盈 {profit_pct:.2f}%",
            sl_placed=sl_placed,
            extreme=extreme,
            profit_pct=profit_pct,
        )
        if phase_up:
            self._report_breath_phase2(real_amt, curr_px, new_sl, sl_placed=sl_placed)
        self._last_radar_trail_ts = now
        self._last_radar_trail_stage = stage
        self._radar_stage_last = stage
        self._radar_handoff_done = True
        self._radar_armed_after_tp1 = True
        return True

    def _report_breath_phase2(self, real_amt, curr_px, new_sl, sl_placed=True):
        """阶段二切入钉钉（呼吸系数追踪）。"""
        if getattr(self, "_radar_activation_notified", False):
            return
        coeff = float(getattr(self, "breathing_coefficient", 1.0) or 1.0)
        atr = float(getattr(self, "open_atr", 0) or 0)
        trail_dist = atr * coeff if atr > 0 else 0.0
        breath_meta = getattr(self, "_breath_coeff_meta", None) or {}
        try:
            self._call_dingtalk(
                dingtalk.report_radar_activated,
                side=self.current_side,
                qty=real_amt,
                entry=self.watched_entry,
                new_sl=new_sl,
                radar_progress=1.0,
                regime=int(getattr(self, "open_regime", None) or self.regime or 3),
                shield_cleared=True,
                verify_note=(
                    f"浮盈≥{BREAKEVEN_TRIGGER_ATR}×ATR → 阶段二呼吸追踪 | "
                    f"止损@{new_sl:.2f} | coeff={coeff:.2f} | "
                    f"trail={trail_dist:.2f} | "
                    f"1hATR={float(breath_meta.get('atr_1h') or 0):.2f} | "
                    f"持仓 {real_amt} {self._unit()}"
                ),
                verified=bool(sl_placed),
                trigger_gate=f"保本触发{BREAKEVEN_TRIGGER_ATR}×ATR",
                activation_price=round(float(curr_px or 0), 2),
                adx=0.0,
                trail_dist=trail_dist,
                breathing_coefficient=coeff,
            )
            self._radar_activation_notified = True
            self._radar_notify_pending = False
            self._shield_handoff_notified = True
            self._save_state()
        except Exception as e:
            logger.warning(f"🫁 阶段二钉钉失败: {e}")
            self._radar_notify_pending = True

    def _sentinel_loop(self):
        """哨兵：持仓/TP 防线 + 呼吸止损追踪（WS推送优先，轮询兜底）。
        启动前调用方已置 _sentinel_active=True（占位防双启）。
        """
        self._ensure_price_ws()
        last_px = 0.0
        try:
            while self.monitoring:
                try:
                    if not self._lock.acquire(timeout=2.0):
                        time.sleep(0.5)
                        continue
                    try:
                        ws_pulse = bool(getattr(self, "_ws_defense_pulse", False))
                        if ws_pulse:
                            self._ws_defense_pulse = False
                            self._ws_fast_poll = True
                        pos = self._get_active_position()
                        if pos == "QUERY_FAILED":
                            self._on_position_query_failed("哨兵")
                            self._mark_idle_patrol_backoff("哨兵·QUERY_FAILED")
                            # 必须在 continue 前休眠，否则跳过底部 sleep → REST 雪崩
                            time.sleep(float(IDLE_PATROL_BACKOFF_SEC))
                            continue
                        real_amt = pos["size"] if pos else 0.0
                        actual_side = pos["side"] if pos else None

                        if not pos or real_amt == 0:
                            if time.time() < getattr(self, "_sentinel_grace_until", 0):
                                logger.debug(
                                    "哨兵宽限期：跳过空仓判定（防重启误清场）"
                                )
                                continue
                            if float(self.watched_qty or 0) <= 0:
                                logger.info(
                                    f"📭 [{self.symbol}] 哨兵确认空仓待命 → 退出巡检"
                                )
                                self.monitoring = False
                                try:
                                    self._save_state()
                                except Exception:
                                    pass
                                break
                            if self.watched_qty > 0:
                                self._purge_all_defense_orders_on_flat(
                                    "哨兵感知空仓·抢先撤TP123",
                                )
                                if not self._confirm_position_flat():
                                    logger.warning(
                                        "⚠️ [哨兵] 首次无仓但复核仍有持仓/查询失败 → 跳过误清场"
                                    )
                                    continue
                                try:
                                    flat_meta = self._infer_flat_close_meta(
                                        curr_px=last_px,
                                        hint_reason="",
                                    )
                                    # 禁止含糊「止盈/人工/止损」并列；归因结果写入 reason
                                    src_lab = flat_meta.get("exit_source_label") or ""
                                    note = flat_meta.get("tv_reason") or ""
                                    if src_lab and src_lab not in note:
                                        flat_meta["tv_reason"] = (
                                            f"{src_lab} · {note}" if note else src_lab
                                        )
                                    elif not note:
                                        flat_meta["tv_reason"] = (
                                            src_lab or "仓位归零（来源未明·请查交易所成交）"
                                        )
                                except Exception as e:
                                    logger.error(
                                        f"⚠️ [哨兵] 空仓归因失败仍强制清账本: {e}"
                                    )
                                    flat_meta = {
                                        "tv_reason": "仓位归零（归因异常·请查交易所成交）",
                                        "close_type": "",
                                    }
                                self._handle_manual_flat_detected(
                                    flat_meta.get(
                                        "tv_reason",
                                        "仓位归零（来源未明·请查交易所成交）",
                                    ),
                                    close_meta=flat_meta,
                                    curr_px=last_px,
                                )
                            break

                        if self.watched_qty > 0 and self._should_finalize_tp_victory(real_amt):
                            self._sweep_dust_and_finalize(
                                "TP余仓/蚂蚁仓扫尾（哨兵判定可收网）"
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

                        # ATR 每 5 分钟刷新（阶梯 step_count 不回溯）
                        try:
                            self._maybe_refresh_atr()
                        except Exception as e:
                            logger.debug(f"ATR刷新跳过: {e}")
                        # 挂单超时 5 分钟 → 取消移交雷达
                        try:
                            self._check_tp_order_timeouts(curr_px)
                        except Exception as e:
                            logger.debug(f"挂单超时检查跳过: {e}")

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
                    logger.error(f"哨兵异常: {e}", exc_info=True)
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

        # 查单失败禁止撤/补：否则会撤光 TP 再盲挂叠出几十张
        if not self._orders_book_readable():
            logger.error(
                f"🛡️ [{self.symbol}] 重建防线中止：挂单不可读 → 禁止撤挂/盲补"
            )
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
                    self._mark_tp_order_placed(int(lv["level"]), order_res=res)
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
        【保留·安全网】开仓后硬止损挂不上 → 立即市价平掉，禁止裸仓持有。
        非旧版「保护性全平」；属呼吸止损武装失败的硬中止（自查清单允许的安全路径）。
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
        """先撤全部挂单再阶梯强平；返回是否已空仓。
        持仓 QUERY_FAILED → fail-closed 返回 False（禁止 float(None) 崩溃 / 禁止当空仓）。
        """
        prev_side = self.current_side
        self._purge_all_defense_orders_on_flat(reason or "强平前撤单")
        closed_successfully = False
        query_failed = False

        for round_i in range(6):
            pos = position_manager.get_position(self.symbol)
            if is_position_query_failed(pos):
                query_failed = True
                logger.error(
                    f"❌ [{self.symbol}] 强平中持仓查询失败 → fail-closed 中止"
                    f"（禁当空仓/禁 float(None)）| {reason}"
                )
                closed_successfully = False
                break
            amt_raw = None if not pos else pos.get("positionAmt")
            if amt_raw is None and pos:
                # 非哨兵但缺字段：同样 fail-closed，勿 float(None)
                query_failed = True
                logger.error(
                    f"❌ [{self.symbol}] 强平中 positionAmt 缺失 → fail-closed | {reason}"
                )
                closed_successfully = False
                break
            if not pos or float(amt_raw or 0) == 0:
                closed_successfully = True
                break

            amt = float(amt_raw)
            close_side = "SELL" if amt > 0 else "BUY"
            live_sz = round(abs(amt), 3)
            logger.info(f"🔪 强平第 {round_i + 1}/6 轮: {close_side} {live_sz} ETH reduceOnly")
            binance_client.place_market_order(close_side, live_sz, symbol=self.symbol, reduce_only=True)
            time.sleep(1.5)

        if query_failed:
            self._last_sterile_flat_fail_detail = (
                f"持仓=QUERY_FAILED | 强平中止 | {reason}"
            )
            if reset_state:
                # 不明仓位：保留账本，禁止假装归零
                try:
                    self._save_state()
                except Exception:
                    pass
            return False

        if not closed_successfully:
            residual = self._get_active_position()
            if residual == "QUERY_FAILED":
                logger.error(
                    f"❌ [{self.symbol}] 强平后复核 QUERY_FAILED → fail-closed | {reason}"
                )
                self._last_sterile_flat_fail_detail = (
                    f"持仓=QUERY_FAILED | 强平后复核失败 | {reason}"
                )
                if reset_state:
                    try:
                        self._save_state()
                    except Exception:
                        pass
                return False
            residual_sz = residual["size"] if residual else 0.0
            if residual_sz > 0 and self._is_dust_qty(residual_sz):
                close_side = "SELL" if residual["side"] == "LONG" else "BUY"
                logger.warning(f"🐜 强平后残 {residual_sz} ETH，触发蚂蚁仓扫尾")
                binance_client.place_market_order(close_side, residual_sz, symbol=self.symbol, reduce_only=True)
                time.sleep(1.0)
                closed_successfully = self._verify_flat()
            if not closed_successfully:
                residual = self._get_active_position()
                if residual == "QUERY_FAILED":
                    residual_sz = -1.0
                    logger.error(f"❌ 6 轮强平后持仓不可读 (QUERY_FAILED)")
                else:
                    residual_sz = residual["size"] if residual else 0.0
                    logger.error(f"❌ 6 轮强平后仍有残单: {residual_sz} ETH")
                dingtalk.report_system_alert(
                    "强平未完全归零",
                    f"6 轮市价平仓后仍剩 {residual_sz} ETH，请人工核查币安盘口",
                )

        if reset_state:
            if closed_successfully:
                self._reset_breath_ledger_on_flat(source=reason or "强平归零")
                self._snapshot_sizing_principal("全平后本金重置")
            else:
                residual = self._get_active_position()
                if residual and residual != "QUERY_FAILED":
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
            # 但必须先热加载账本，禁止用构造默认 ATR=30 发明宽止损互相打架
            logger.warning(
                f"🔄 [{self.symbol}] 跳过重复接管进程，仍启动哨兵巡检实盘"
            )
            hydrated = self._hydrate_ledger_from_state_file(
                source="跳过重复接管·热加载"
            )
            if not hydrated:
                logger.error(
                    f"⛔ [{self.symbol}] 跳过接管且热加载失败 → "
                    f"哨兵只巡检，禁止 invent/改挂止损"
                )
                self._stop_write_blocked = True
            self.monitoring = True
            self._ensure_sentinel_running_quiet()
            return
        try:
            saved_monitoring = False
            had_state_file = os.path.exists(self.state_file)
            if had_state_file:
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
                    self.open_atr = float(s.get("open_atr", 0) or 0)
                    if self.open_atr <= 0:
                        # 禁止静默灌入默认30；仅当文件显式有 current_atr 时作候选
                        cand = float(s.get("current_atr", 0) or 0)
                        if cand > 0:
                            self.open_atr = cand
                    self.shield_active = bool(s.get("shield_active", False))
                    self.shield_tiers_consumed = list(s.get("shield_tiers_consumed", []) or [])
                    self.tp_levels_consumed = list(s.get("tp_levels_consumed", []) or [])
                    self.tp_levels_radar_handoff = list(
                        s.get("tp_levels_radar_handoff", []) or []
                    )
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
                    # 旧 state 可能含 add_count，已废除加仓，忽略不恢复
                    self.tv_suggested_qty = float(s.get("tv_suggested_qty", 0) or 0)
                    self.tv_qty1 = float(s.get("tv_qty1", 0) or 0)
                    self.tv_qty2 = float(s.get("tv_qty2", 0) or 0)
                    self.tv_qty3 = float(s.get("tv_qty3", 0) or 0)
                    self.radar_step_count = int(s.get("radar_step_count", 0) or 0)
                    self.radar_activated = bool(s.get("radar_activated", False))
                    self.breakeven_phase = bool(s.get("breakeven_phase", False))
                    self.initial_stop = float(s.get("initial_stop", 0) or 0)
                    self.last_adx = float(s.get("last_adx", ADX_FALLBACK) or ADX_FALLBACK)
                    self.remaining_qty_pct = float(s.get("remaining_qty_pct", 1.0) or 1.0)
                    self.breathing_coefficient = float(
                        s.get("breathing_coefficient", 1.0) or 1.0
                    )
                    self.early_be_done = bool(s.get("early_be_done", False))
                    self._breath_ratio_history = list(
                        s.get("atr_1h_ratio_history", []) or []
                    )
                    self._last_open_exec_ts = float(
                        s.get("last_open_exec_ts", 0) or 0
                    )
                    # 旧 schema 识别：有 activated/stepCount 等旧字段，但缺 initialAtr/breakevenPhase/initial_stop
                    # → 禁止自动转换；后面有仓则暂停交易
                    _has_legacy_radar_keys = any(
                        k in s for k in (
                            "activated", "stepCount", "radar_step_count", "radar_activated",
                        )
                    )
                    _has_breath_schema = (
                        "breakeven_phase" in s
                        and ("open_atr" in s or "initialAtr" in s)
                        and "initial_stop" in s
                    )
                    self._state_old_schema = bool(
                        _has_legacy_radar_keys and not _has_breath_schema
                    )
                    if self._state_old_schema:
                        logger.error(
                            f"⛔ [{self.symbol}] 检测到旧雷达 schema "
                            f"(缺 breakeven_phase/open_atr/initial_stop) → 禁止自动转换"
                        )
                    # 仅新 schema 允许：initial_stop 缺失时从 current_sl 回填（非旧字段转换）
                    if (not self._state_old_schema
                            and self.initial_stop <= 0
                            and float(getattr(self, "current_sl", 0) or 0) > 0):
                        self.initial_stop = float(self.current_sl)
                    if not self.radar_activated and float(getattr(self, "current_sl", 0) or 0) > 0:
                        self.radar_activated = True
                    self._atr_last_update_ts = float(s.get("atr_last_update_ts", 0) or 0)
                    raw_tp_ts = s.get("tp_order_placed_ts") or {}
                    self._tp_order_placed_ts = {
                        str(k): float(v) for k, v in dict(raw_tp_ts).items()
                    }
                    raw_oids = s.get("defense_order_ids") or {}
                    self._defense_order_ids = {
                        "tp1": str((raw_oids or {}).get("tp1") or ""),
                        "tp2": str((raw_oids or {}).get("tp2") or ""),
                        "tp3": str((raw_oids or {}).get("tp3") or ""),
                        "hard_stop": str((raw_oids or {}).get("hard_stop") or ""),
                        "radar_stop": str(
                            (raw_oids or {}).get("radar_stop")
                            or (raw_oids or {}).get("stop") or ""
                        ),
                        "stop": str(
                            (raw_oids or {}).get("stop")
                            or (raw_oids or {}).get("radar_stop") or ""
                        ),
                    }
                    self.frozen_hard_sl_px = float(
                        s.get("frozen_hard_sl_px", 0) or 0
                    )
                    self.trading_paused = bool(s.get("trading_paused", False))
                    self.trading_pause_reason = str(
                        s.get("trading_pause_reason", "") or ""
                    )
                    self._atr_div_streak = int(s.get("atr_div_streak", 0) or 0)
                    self.atr_source = str(s.get("atr_source", "vps") or "vps")
                    self.atr_degraded = bool(s.get("atr_degraded", False))
                    self._atr_scenario = int(s.get("atr_scenario", 0) or 0)
                    self._tp3_fallback_active = bool(
                        s.get("tp3_fallback_active", False)
                    )
                    self._temp_stop_active = bool(s.get("temp_stop_active", False))
                    self._last_bar_time_ms = int(s.get("last_bar_time_ms", 0) or 0)
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
            if pos == "QUERY_FAILED" or (pos is not None and not isinstance(pos, dict)):
                logger.error(
                    f"🚨 [{self.symbol}] 重启持仓探测返回非持仓哨兵 {pos!r} "
                    f"→ fail-closed 启哨兵，禁止当有仓/空仓"
                )
                self._on_position_query_failed("VPS重启·探测哨兵")
                self.monitoring = True
                self._ensure_sentinel_running_quiet()
                self._last_idle_takeover_ts = 0.0
                return

            # 权威规格 §六：旧 schema / 呼吸态缺失 + 有持仓 → 告警暂停，禁止自动转换
            breath_incomplete = (
                float(getattr(self, "initial_stop", 0) or 0) <= 0
                or float(getattr(self, "open_atr", 0) or 0) <= 0
                or float(getattr(self, "current_sl", 0) or 0) <= 0
            )
            old_schema = bool(getattr(self, "_state_old_schema", False))
            if (
                pos
                and isinstance(pos, dict)
                and float(pos.get("size") or 0) > 0
                and (not had_state_file or breath_incomplete or old_schema)
            ):
                self.trading_paused = True
                if old_schema:
                    self.trading_pause_reason = "restart_old_schema_no_auto_migrate"
                elif not had_state_file:
                    self.trading_pause_reason = "restart_no_persistence_with_position"
                else:
                    self.trading_pause_reason = "restart_breath_state_incomplete"
                self._save_state()
                logger.error(
                    f"⛔ [{self.symbol}] 持久化缺失/旧schema/呼吸态不全但实盘有仓 → 暂停交易 "
                    f"(old_schema={old_schema} incomplete={breath_incomplete})"
                )
                try:
                    dingtalk.report_system_alert(
                        f"重启无有效呼吸态·已暂停 [{self.symbol}]",
                        (
                            f"had_state={had_state_file} old_schema={old_schema} "
                            f"initial_stop={float(getattr(self, 'initial_stop', 0) or 0):.2f} "
                            f"open_atr={float(getattr(self, 'open_atr', 0) or 0):.2f} | "
                            f"实盘 {pos.get('side')} {pos.get('size')} → 暂停自动交易；"
                            f"旧schema禁止自动转换；人工核对后解除 trading_paused"
                        ),
                        level="紧急",
                        suggestion="确认状态文件含 initial_stop/open_atr/breakeven_phase 后恢复；FORCE_ALIGN仅用于方向不一致",
                    )
                except Exception as e:
                    logger.warning(f"无持久化暂停钉钉失败: {e}")
                # 旧 schema：禁止用 tv_sl/行情自动灌入伪装成新态
                if old_schema:
                    self.monitoring = True
                    self._ensure_sentinel_running_quiet()
                    return
                # 尽量用 1h ATR 补 initialAtr（不作新开仓权威；仅孤儿仓恢复）
                try:
                    atr = 0.0
                    adx = 0.0
                    try:
                        eng = self._atr_1h_engine()
                        atr = float(eng.refresh(force=True) or 0)
                    except Exception:
                        atr = 0.0
                    if atr <= 0:
                        atr, adx = self._refresh_market_metrics(force=True)
                    if atr > 0 and float(getattr(self, "open_atr", 0) or 0) <= 0:
                        self.open_atr = atr
                    if adx > 0:
                        self.last_adx = adx
                    entry = float(pos.get("entry_price") or 0)
                    side = str(pos.get("side") or "")
                    if entry > 0 and atr > 0 and float(getattr(self, "initial_stop", 0) or 0) <= 0:
                        self.initial_stop = initial_stop_price(
                            side, entry, atr, profile=getattr(self, "breath_profile", None),
                        )
                        self.current_sl = float(self.initial_stop)
                        self.best_price = entry
                except Exception as e:
                    logger.warning(f"重启补算呼吸态失败: {e}")

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
                    # 主接管进程：允许止损写入
                    self._stop_write_blocked = False
                    # 持仓存在：锁定档位 → 按恢复的 currentStop 重挂呼吸止损
                    self.watched_entry = float(
                        pos.get("entry_price") or self.watched_entry or 0
                    )
                    self.current_side = pos.get("side") or self.current_side
                    self._lock_open_regime_from_sources()
                    self._sanitize_vps_hard_sl_ledger(source="重启接管消毒")
                    self._sync_exchange_stop(
                        float(pos.get("size") or 0),
                        radar_sl=None,
                        reason="重启强制呼吸止损",
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
                        # TV 方向为准：实盘反向 → 强制市价全平 + 钉钉，不以暂停留仓处理
                        opp = self._strict_tv_opposite_side(side)
                        logger.error(
                            f"🚨 [重启] 实盘 {side} vs TV {opp} 反向 → "
                            f"强制平仓对齐 TV（不以暂停留仓）"
                        )
                        flattened = self._enforce_tv_direction_or_flat(
                            pos, source="VPS重启·TV方向为准",
                        )
                        self.trading_paused = False
                        self.trading_pause_reason = ""
                        if flattened:
                            recover_ok = True
                            self._recover_in_progress = False
                            return
                        # 兜底：信源瞬时抖动时仍强制市价全平，禁止以暂停留仓
                        logger.error(
                            f"🚨 [重启] enforce未触发 → 兜底市价全平 "
                            f"实盘{side} vs TV{opp}"
                        )
                        self._close_all(
                            f"TV方向为准·重启兜底全平：实盘({side})≠TV({opp})",
                            force_align=(side, opp),
                            force_verify_note=(
                                f"触发源: VPS重启兜底 | 实盘 {side} vs TV {opp} | "
                                "已市价全平对齐 TV"
                            ),
                        )
                        recover_ok = True
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
                    # 接管成功且呼吸态齐全：清掉粘性 restart_* 暂停 + 持仓期假ATR污染
                    breath_ok = (
                        float(getattr(self, "initial_stop", 0) or 0) > 0
                        and float(getattr(self, "open_atr", 0) or 0) > 0
                        and float(getattr(self, "current_sl", 0) or 0) > 0
                    )
                    prev_reason = str(getattr(self, "trading_pause_reason", "") or "")
                    if breath_ok and (
                        prev_reason.startswith("restart_")
                        or prev_reason.startswith("ATR_DEGRADE")
                        or bool(getattr(self, "atr_degraded", False))
                    ):
                        self.trading_paused = False
                        self.trading_pause_reason = ""
                        self._atr_div_streak = 0
                        self.atr_degraded = False
                        self.atr_source = "vps"
                        self._pending_atr_degrade = None
                        try:
                            self._save_state()
                        except Exception:
                            pass
                        logger.info(
                            f"✅ [{self.symbol}] 接管成功·已解除粘性暂停 "
                            f"(was={prev_reason or '—'}) 并清零假ATR降级污染"
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
                # 确认空仓：禁止误平仓；完整清零呼吸账本（禁止半清理残留 entry/sl/atr）
                logger.info(
                    f"🔄 [{self.symbol}] 系统重启点火：REST确认无持仓，账本复位为空仓待命。"
                )
                self._reset_breath_ledger_on_flat(source="重启确认空仓")
                self._open_regime_sticky = False
                self._save_state()
                flat_ok = self._wait_verify(
                    lambda: self._get_active_position(prefer_ws=False) is None,
                    retries=6,
                    delay=0.5,
                )
                # 空仓后再清挂单；若清场前又冒出持仓 → 立刻改接管
                resurfaced = self._get_active_position(prefer_ws=False)
                if resurfaced == "QUERY_FAILED":
                    logger.error(
                        f"🚨 [{self.symbol}] 空仓确认阶段 QUERY_FAILED "
                        f"→ 禁止清挂单，哨兵接力"
                    )
                    self._on_position_query_failed("VPS重启·空仓确认")
                    self.monitoring = True
                    self._ensure_sentinel_running_quiet()
                    return
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
            if isinstance(pos, dict) and float(pos.get("size") or 0) > 0:
                self.monitoring = True
                self._post_recover_radar_pulse = True
                if not self._sentinel_active:
                    threading.Thread(
                        target=self._sentinel_loop, daemon=True, name="sentinel",
                    ).start()
            elif pos == "QUERY_FAILED":
                self.monitoring = True
                self._ensure_sentinel_running_quiet()
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
                # 空仓且账本无仓：禁止因脏 state 误标 trading_paused
                # QUERY_FAILED 是 str 哨兵，禁止 (before or {}).get
                try:
                    if before == "QUERY_FAILED":
                        flat = False  # 不明 → 不清 restart 暂停标记
                    elif isinstance(before, dict):
                        flat = float(before.get("size") or 0) <= 0
                    else:
                        flat = not before
                    if flat and float(getattr(sup, "watched_qty", 0) or 0) <= 0:
                        if getattr(sup, "trading_paused", False) and str(
                            getattr(sup, "trading_pause_reason", "") or ""
                        ).startswith("restart_"):
                            logger.warning(
                                f"🧹 [{sym}] 空仓清除陈旧 restart 暂停标记 "
                                f"({getattr(sup, 'trading_pause_reason', '')})"
                            )
                            sup.trading_paused = False
                            sup.trading_pause_reason = ""
                            # 清脏 entry=1.0 等残留
                            if float(getattr(sup, "watched_entry", 0) or 0) <= 1.01:
                                sup.watched_entry = 0.0
                            sup._save_state()
                except Exception as e:
                    logger.debug(f"空仓清暂停跳过: {e}")
                sup.recover_state_on_startup()
                after = None
                try:
                    after = sup._get_active_position(prefer_ws=False)
                except Exception:
                    after = None
                if after == "QUERY_FAILED":
                    summaries.append(f"{sym}:QUERY_FAILED·哨兵接力")
                elif isinstance(after, dict) and float(after.get("size") or 0) > 0:
                    summaries.append(
                        f"{sym}:有仓 {after.get('side')} {after.get('size')} "
                        f"@ {after.get('entry_price')} monitoring={sup.monitoring}"
                    )
                elif isinstance(before, dict) and float(before.get("size") or 0) > 0:
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
                immediate=True,
            )
        except Exception as e:
            logger.warning(f"多品种重启汇总钉钉跳过: {e}")
    return SUPERVISORS


# 单测/脚本可设 BINANCE_SKIP_BOOTSTRAP=1，禁止 import 副作用触发实盘恢复
if os.environ.get("BINANCE_SKIP_BOOTSTRAP", "").strip() not in ("1", "true", "TRUE", "yes"):
    bootstrap_supervisors()
else:
    logger.info("⏭️ BINANCE_SKIP_BOOTSTRAP=1 → 跳过 import 时启动恢复")
