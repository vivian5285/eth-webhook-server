#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# position_supervisor_binance.py — 与深币 VPS 逻辑对齐（币安 ETH 数量/15x 适配）
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
    format_vps_sizing_note,
    VPS_RISK_PCT,
    ADD_QTY_RATIO,
    MAX_ADD_TIMES,
    normalize_entry_type,
    ENTRY_TYPE_OPEN,
    ENTRY_TYPE_PYRAMID,
    ENTRY_TYPE_PROFIT_ADD,
    CLOSE_TYPE_TP3,
    CLOSE_TYPE_BREAKEVEN,
    CLOSE_TYPE_VPS_SHIELD,
)

if not os.path.exists('logs'):
    os.makedirs('logs')
handler = RotatingFileHandler('logs/binance_brain.log', maxBytes=5 * 1024 * 1024, backupCount=3)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] Brain: %(message)s',
    handlers=[handler, logging.StreamHandler()],
)
logger = logging.getLogger(__name__)

BINANCE_VPS_VERSION = "v13.14.0-adaptive-recover"
EXCHANGE_LEVERAGE = 5  # 交易所实盘固定 5x；TV payload leverage 仅用于比例 sizing
SENTINEL_POLL_NORMAL = 6
SENTINEL_POLL_ARMING = 3
SENTINEL_POLL_RADAR = 2
DUST_QTY_ETH = 0.004
TP_COMPLETE_RESIDUAL_RATIO = 0.12
OPEN_OVERSIZE_RATIO = 1.10  # 与 QTY_ALIGN_MIN_PCT 一致：偏离 ≥10% 才裁减
SIGNAL_DEDUP_SEC = 45
DEFENSE_ALIGN_COOLDOWN_SEC = 60
SENTINEL_GRACE_AFTER_RECOVER_SEC = 45
RECOVER_LOCK_FILE = "logs/.recover_singleton.lock"
RECOVER_LOCK_TTL_SEC = 180
REGIME_CAP_COOLDOWN_SEC = 90
REGIME_CAP_TOLERANCE_ETH = 0.001
CAP_MIN_RETAIN_RATIO = 0.25
CAP_TRIM_MAX_ROUNDS = 4
QTY_DRIFT_TOLERANCE_PCT = 0.015  # 微漂 ≤1.5%：仅同步账本，不对齐
QTY_ALIGN_MIN_PCT = 0.10         # 偏离 ≥10% 才视为离谱，触发对齐/档位裁减
SHIELD_HARD_STOP_PCT = 0.10  # 历史常量（仅哨兵成交分类标签）；止损价 exclusively TV tv_sl
SHIELD_TIER_PCTS = (SHIELD_HARD_STOP_PCT,)
SHIELD_TIER_RATIOS = (1.0,)
SHIELD_STOP_TOLERANCE = 2.0
SHIELD_MAINTAIN_COOLDOWN_SEC = 60
SHIELD_FAIL_BACKOFF_BASE_SEC = 45
SHIELD_FAIL_BACKOFF_MAX_SEC = 300
SHIELD_QTY_TOLERANCE_PCT = 0.04
SHIELD_MAX_TIER_ORDERS = 1
RADAR_DINGTALK_COOLDOWN_SEC = 120
# 同向 TV 智能筛选：① ATR 变化 → 先平后开；② 价差低于该百分比 → 不重复开仓，仅刷新 TP123
SAME_DIR_MIN_SPREAD_PCT = 0.15
SAME_DIR_DEDUP_SEC = 300
ATR_SIMILAR_RATIO = 0.03  # 持仓 ATR 与 TV ATR 偏差 ≤3% 视为未变
TV_JOURNAL = "logs/binance_tv_journal.jsonl"
OPEN_JOURNAL = "logs/binance_open_journal.jsonl"


class PositionSupervisorBinance:
    def __init__(self):
        self.symbol = "ETHUSDT"
        self.monitoring = False
        self._lock = threading.Lock()

        self.regime_settings = {
            1: {"margin": 0.15, "ratios": [0.25, 0.35, 0.40], "activation": 0.40, "trail_offset": 0.40},
            2: {"margin": 0.25, "ratios": [0.20, 0.35, 0.45], "activation": 0.50, "trail_offset": 0.60},
            3: {"margin": 0.35, "ratios": [0.18, 0.32, 0.50], "activation": 0.60, "trail_offset": 0.90},
            4: {"margin": 0.50, "ratios": [0.05, 0.20, 0.75], "activation": 0.70, "trail_offset": 1.30},
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
        self._last_radar_report_ts = 0.0
        self._last_radar_report_sl = 0.0
        self.sizing_principal = 0.0
        self.tv_sl = 0.0
        self._last_applied_exchange_sl = 0.0
        self.tv_risk_pct = 0.0
        self.tv_qty_ratio = 1.0
        self.tv_entry_type = ENTRY_TYPE_OPEN
        self.base_qty = 0.0
        self.add_count = 0

        self.state_file = 'binance_vps_state.json'
        logger.info(
            f"🧠 币安 VPS [{BINANCE_VPS_VERSION}] 军师托管版已加载："
            f"双轨智慧雷达 · {self.leverage}x 杠杆"
        )
        self._start_signal_worker()
        self._start_idle_flat_patrol()

    def _start_idle_flat_patrol(self):
        """空仓待命时后台巡检：发现孤立蚂蚁仓 → 自动扫尾 + 钉钉"""
        def loop():
            while True:
                time.sleep(30)
                if self.monitoring:
                    continue
                if not self._lock.acquire(timeout=2.0):
                    continue
                try:
                    if self.monitoring:
                        continue
                    pos = self._get_active_position()
                    if not pos or pos["size"] <= 0:
                        continue
                    if not self._is_dust_qty(pos["size"]) and not self._should_finalize_tp_victory(pos["size"]):
                        continue
                    if not self.current_side:
                        self.current_side = pos["side"]
                    logger.warning(
                        f"🐜 [空闲巡检] 发现残量 {pos['side']} {pos['size']} ETH → 扫尾"
                    )
                    self._sweep_dust_and_finalize("重启扫描：盘口蚂蚁仓自动扫平")
                except Exception as e:
                    logger.error(f"空闲巡检异常: {e}")
                finally:
                    self._lock.release()

        threading.Thread(target=loop, daemon=True, name="idle-flat-patrol").start()

    @staticmethod
    def _call_dingtalk(fn, **kwargs):
        """兼容 VPS 旧版 dingtalk.py（缺少 verified / swept_dust / radar_sl_ok 等新参数）"""
        try:
            fn(**kwargs)
        except TypeError as exc:
            if "unexpected keyword argument" not in str(exc):
                raise
            legacy = {
                k: v for k, v in kwargs.items()
                if k not in ("verified", "swept_dust", "radar_sl_ok", "action_type")
            }
            logger.warning(f"钉钉旧版降级播报 {getattr(fn, '__name__', 'dingtalk')}: {exc}")
            fn(**legacy)

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
            "ts": time.time(),
        }
        self.last_tv_signal = entry
        self._append_journal(TV_JOURNAL, entry)
        sizing_note = ""
        et = normalize_entry_type(payload.get("entry_type"))
        if et == ENTRY_TYPE_OPEN and self.tv_price > 0:
            _, sm = self._calc_vps_open_qty(self.tv_price)
            sizing_note = " | " + format_vps_sizing_note(sm, entry_type=ENTRY_TYPE_OPEN)
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
            leverage=payload.get("leverage"),
            qty_ratio=payload.get("qty_ratio"),
            reason=payload.get("reason", ""),
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
            self.last_tv_side = side
        elif side != self.last_tv_side and not reconcile["tv_close"]:
            reconcile["direction_mismatch"] = True
            if not any("方向背离" in n for n in notes):
                notes.append(f"方向背离: 实盘{side} vs TV指令{self.last_tv_side}")

        if saved_initial <= 0 and real_amt > 0:
            self.initial_qty = real_amt

        for n in notes:
            logger.warning(f"🔎 重启对账: {n}")
        return reconcile

    def _resolve_open_initial_qty(self, live_qty):
        """开单原始头寸：state initial_qty 优先，否则开仓日志（部分止盈后现仓 < 开单）"""
        live_qty = float(live_qty or 0)
        saved = float(self.initial_qty or 0)
        if saved > live_qty + 0.001:
            return saved
        last_open = self._load_last_journal_entry(OPEN_JOURNAL)
        if last_open:
            jq = float(last_open.get("qty", 0) or 0)
            if jq > live_qty + 0.001:
                logger.info(
                    f"📖 开单头寸取自开仓日志 {jq} ETH "
                    f"(state initial={saved}, 现仓 {live_qty})"
                )
                return jq
        return saved if saved > 0 else live_qty

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
        """全平/开仓前：锁定 USDT 合约本金余额，供本周期开仓与超标核查共用"""
        principal = binance_client.get_principal_wallet_balance()
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
                    dingtalk.report_principal_snapshot(
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
            else binance_client.get_principal_wallet_balance()
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

    def _apply_tv_sizing_params(self, payload):
        """解析 entry_type；加仓固定 ADD_QTY_RATIO，TV risk_pct/qty_ratio 不参与 sizing"""
        self.tv_entry_type = normalize_entry_type(payload.get("entry_type"))
        self.tv_qty_ratio = ADD_QTY_RATIO if self.tv_entry_type in (
            ENTRY_TYPE_PYRAMID, ENTRY_TYPE_PROFIT_ADD,
        ) else 1.0
        self.leverage = EXCHANGE_LEVERAGE
        self._save_state()
        logger.info(
            f"📐 TV参数: type={self.tv_entry_type} "
            f"| VPS风险={VPS_RISK_PCT}% R{self.regime} "
            f"| 加仓固定={ADD_QTY_RATIO}×base (最多{MAX_ADD_TIMES}次) "
            f"| 交易所={EXCHANGE_LEVERAGE}x"
        )

    def _calc_vps_open_qty(self, curr_px, regime=None):
        principal = self._resolve_cap_sizing_base()
        px = float(curr_px or self.tv_price or 0)
        sl = float(getattr(self, "tv_sl", 0) or 0)
        qty, meta = compute_vps_open_qty(
            principal, px, sl, int(regime if regime is not None else self.regime),
            leverage=EXCHANGE_LEVERAGE,
        )
        meta["principal"] = principal
        return float(qty or 0), meta

    def _calc_vps_add_qty(self, qty_ratio=None):
        base = float(getattr(self, "base_qty", 0) or 0)
        if base <= 0:
            base = float(getattr(self, "initial_qty", 0) or getattr(self, "watched_qty", 0) or 0)
        qty, meta = compute_vps_add_qty(base, ADD_QTY_RATIO)
        meta["principal"] = self._resolve_cap_sizing_base()
        meta["add_count"] = int(getattr(self, "add_count", 0) or 0)
        meta["max_add_times"] = MAX_ADD_TIMES
        return float(qty or 0), meta

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
            binance_client.place_market_order(close_side, slice_trim, reduce_only=True)
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
        binance_client.cancel_all_open_orders(self.symbol)
        time.sleep(0.4)
        for round_i in range(4):
            pos = self._get_active_position()
            if not pos or pos["size"] <= 0:
                break
            close_side = "SELL" if pos["side"] == "LONG" else "BUY"
            logger.info(f"🐜 扫尾第 {round_i + 1}/4: {close_side} {pos['size']} ETH reduceOnly")
            binance_client.place_market_order(close_side, pos["size"], reduce_only=True)
            time.sleep(1.0)
        self.watched_qty = 0.0
        self.base_qty = 0.0
        self.add_count = 0
        self.current_side = None
        self._save_state()
        binance_client.cancel_all_open_orders(self.symbol)
        self._report_flat_close(reason, swept_dust=True)

    def _apply_recover_live_alignment(self, side, reconcile):
        """重启以实盘为准：不回放 TV 平仓，不因日志方向差异核武全平"""
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
        if reconcile.get("direction_mismatch") or (
                self.last_tv_side and side != self.last_tv_side
        ):
            old_tv = self.last_tv_side
            self.last_tv_side = side
            msg = f"方向以实盘为准: {side} (TV日志={old_tv})"
            logger.warning(f"🔄 [重启] {msg}")
            extra_notes.append(msg)
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
        例：1.234→0.405 减 0.829 → 覆盖 TP1+TP2，未覆盖 TP3 → [1,2]
        """
        initial_qty = float(initial_qty or 0)
        live_qty = float(live_qty or 0)
        if initial_qty <= live_qty + 0.001:
            return []

        reduced = round(initial_qty - live_qty, 3)
        consumed = []
        cum = 0.0

        for sl in self._tp_slices_for_initial(initial_qty):
            if sl["qty"] <= 0.0005 or sl["price"] <= 0:
                continue
            cum = round(cum + sl["qty"], 3)
            tol = max(0.003, sl["qty"] * 0.08)
            if reduced >= cum - tol:
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
                    f"({self.regime_settings[self.regime]['ratios']})"
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
        audit = self._audit_tp_levels(live_qty, tolerance)
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
                    res = binance_client.place_limit_order(
                        close_side, target_q, price, reduce_only=True,
                    )
                    if res:
                        actions += 1
                        logger.info(
                            f"🔧 重启纠偏 TP{lv['level']} @{price:.2f} → {target_q} ETH"
                        )
                    time.sleep(0.35)
                continue

            res = binance_client.place_limit_order(
                close_side, target_q, price, reduce_only=True,
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
        """TV tv_sl 为唯一硬止损价"""
        tv = round(float(getattr(self, "tv_sl", 0) or 0), 2)
        return tv if tv > 0 else None

    def _apply_tv_sl_from_payload(self, payload, source=""):
        """解析并持久化 TV 动态硬止损价"""
        raw = payload.get("tv_sl")
        if raw is None or raw == "":
            return False
        px = round(self._safe_float(raw, 0), 2)
        if px <= 0:
            return False
        old = round(float(getattr(self, "tv_sl", 0) or 0), 2)
        self.tv_sl = px
        if abs(px - old) > SHIELD_STOP_TOLERANCE:
            self._last_applied_exchange_sl = 0.0
        self._save_state()
        logger.info(
            f"📡 TV硬止损 tv_sl={px:.2f}"
            + (f" ({source})" if source else "")
            + (f" | 原 {old:.2f}" if old > 0 and abs(px - old) > SHIELD_STOP_TOLERANCE else "")
        )
        return True

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

    def _sync_exchange_stop(self, live_qty, radar_sl=None, reason="", force=False):
        """
        挂/更新交易所 closePosition 止损。
        单槽合并：effective = max/min(雷达, tv_sl)。
        """
        live_qty = self._resolve_live_qty(live_qty)
        if live_qty <= 0 or not self.current_side or not self.watched_entry:
            return {"ok": False, "skipped": True, "reason": "no_position"}

        target = self._effective_exchange_stop(radar_sl)
        if not target or target <= 0:
            return {"ok": False, "skipped": True, "reason": "no_stop_price"}
        target = round(float(target), 2)

        last = round(float(getattr(self, "_last_applied_exchange_sl", 0) or 0), 2)
        if (
            not force
            and last > 0
            and abs(target - last) <= SHIELD_STOP_TOLERANCE
            and self._has_stop_sl_near(target, exclude_shield=False)
        ):
            return {"ok": True, "skipped": True, "target": target, "reason": "idempotent"}

        purged = self._purge_all_close_position_stops()
        if purged:
            logger.info(f"🛡️ 撤旧 closePosition 止损 {purged} 笔 → 重挂 @ {target:.2f}")
            time.sleep(0.5)

        close_side = "SHORT" if self.current_side == "LONG" else "LONG"
        res = binance_client.place_stop_market_order(close_side, target, quantity=None)
        time.sleep(0.35)
        ok = res is not None and self._has_stop_sl_near(target, exclude_shield=False)
        if ok:
            self._last_applied_exchange_sl = target
            self.shield_active = True
            self.shield_sized_qty = live_qty
            self._shield_fail_streak = 0
            self._save_state()
            tv_floor = round(float(getattr(self, "tv_sl", 0) or 0), 2)
            logger.warning(
                f"🛡️ [TV硬止损/合并] {reason or '同步止损'} | closePosition @ {target:.2f} "
                f"| tv_sl={tv_floor or 'fallback'} | 撤 {purged} 笔"
            )
        else:
            self._record_shield_maintain(success=False)
        return {"ok": ok, "skipped": False, "target": target, "purged": purged}

    def _handle_tv_sl_update(self, payload):
        """UPDATE_SL：撤旧挂新 tv_sl（幂等），雷达线独立继续运行"""
        side = str(payload.get("side") or "").strip().upper()
        if not self._apply_tv_sl_from_payload(payload, source="UPDATE_SL"):
            logger.warning("UPDATE_SL 无效或未携带 tv_sl")
            return

        pos = self._get_active_position()
        if not pos or pos.get("size", 0) <= 0:
            logger.info("UPDATE_SL 到达但盘口已空仓 → 仅更新账本 tv_sl")
            return
        if side and side != pos["side"]:
            logger.warning(f"UPDATE_SL side={side} 与实盘 {pos['side']} 不符，已忽略")
            return

        radar_sl = None
        if self._is_radar_active() or self._should_radar_trail(
            binance_client.get_current_price(self.symbol) or 0
        ):
            radar_sl = self._clamp_radar_to_tv_floor(self.current_sl)

        result = self._sync_exchange_stop(
            pos["size"],
            radar_sl=radar_sl,
            reason=f"TV UPDATE_SL @ {self.tv_sl:.2f}",
            force=True,
        )
        if result.get("skipped") and result.get("reason") == "idempotent":
            logger.info(f"UPDATE_SL 幂等跳过 tv_sl={self.tv_sl:.2f} 已在盘口")
        elif result.get("ok"):
            exchange_stop = float(
                result.get("target")
                or self._effective_exchange_stop(radar_sl)
                or self.tv_sl
            )
            verified = self._wait_verify(
                lambda: self._has_stop_sl_near(exchange_stop, exclude_shield=False),
                retries=8,
                delay=0.4,
            )
            verify_note = (
                f"UPDATE_SL tv_sl={self.tv_sl:.2f}"
                + (f" → 合并 @ {exchange_stop:.2f}" if radar_sl else "")
                + f" | 持仓 {pos['size']} ETH @ {self.watched_entry:.2f}"
            )
            if not verified:
                verify_note += f" | {dingtalk.VERIFY_DELAY_MARK}"
            self._call_dingtalk(
                dingtalk.report_tv_sl_updated,
                side=self.current_side or pos["side"],
                live_qty=pos["size"],
                entry=self.watched_entry,
                tv_sl=self.tv_sl,
                exchange_stop=exchange_stop,
                radar_active=self._is_radar_active(),
                radar_sl=radar_sl,
                regime=self.regime,
                verify_note=verify_note,
                verified=verified,
            )
        else:
            dingtalk.report_system_alert(
                "TV硬止损更新失败",
                f"UPDATE_SL tv_sl={self.tv_sl:.2f} | 核实未通过，哨兵将继续重试",
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

    def _force_disarm_shield_before_radar(self, curr_px, reason="", notify=True):
        """
        雷达接管前强制撤净 TV 硬止损 closePosition 单，再挂雷达移动保本。
        返回: dict(cancelled, cleared, verified)
        """
        stop_px = self._shield_stop_price()
        had_flag = getattr(self, "shield_active", False)
        had_exchange = self._shield_present_on_exchange()
        if not had_flag and not had_exchange:
            return {"cancelled": 0, "cleared": True, "verified": True}

        n = self._cancel_stop_orders(scope="shield")
        if self._shield_present_on_exchange():
            n += self._purge_shield_stop_orders()
            time.sleep(0.5)
        if self._shield_present_on_exchange():
            n += self._purge_shield_stop_orders()
            time.sleep(0.5)
        cleared = self._wait_shield_cleared(retries=8, delay=0.4)

        self.shield_active = False
        self.shield_tiers_consumed = []
        self.shield_sized_qty = 0.0
        self._shield_arm_notified = False
        self._save_state()

        still_there = self._shield_present_on_exchange()
        verified = cleared and not still_there
        progress = self._radar_activation_progress(curr_px) if curr_px > 0 else 0.0
        if reason and (had_flag or had_exchange or n):
            logger.info(
                f"🛡️ [雷达交棒] {reason} | 撤 {n} 笔硬止损"
                + (f" | ⚠️ 盘口仍检测到硬止损" if still_there else " | 硬止损已净")
                + f" | 雷达进度 {progress:.0%}"
            )
        if notify and (n > 0 or ((had_flag or had_exchange) and not getattr(self, "_shield_handoff_notified", False))):
            verify_note = (
                f"撤 {n} 笔 TV硬止损 @ {stop_px:.2f}"
                if stop_px else f"撤 {n} 笔 TV硬止损"
            )
            if still_there:
                verify_note += " | ⚠️ 盘口仍残留，雷达挂止损暂缓"
            else:
                verify_note += " | 硬止损已净，交棒雷达移动保本"
            verify_note += (
                f" | {'雷达已激活' if progress >= 1.0 else f'雷达进度 {progress:.0%}'}"
            )
            self._call_dingtalk(
                dingtalk.report_shield_disarmed,
                side=self.current_side,
                live_qty=self._resolve_live_qty(self.watched_qty or 0),
                entry=self.watched_entry,
                cancelled_count=n,
                reason=reason or "雷达激活前撤硬止损",
                radar_progress=progress,
                verify_note=verify_note,
                verified=verified,
            )
            self._shield_handoff_notified = True
        return {"cancelled": n, "cleared": not still_there, "verified": verified}

    def _should_disarm_shield_for_favorable(self, curr_px):
        """雷达达到 TP1 激活比例 → 撤 TV 硬止损，交棒移动保本"""
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
            pnl_label = f"浮盈·雷达区 (进度 {radar_progress:.0%})"
            defense_plan = "雷达移动保本 + TV硬止损底线 (合并 closePosition)"
        elif adverse > 0.001:
            pnl_label = f"浮亏 {adverse:.1%}"
            defense_plan = "持有 TP123 + TV硬止损全平"
        elif favorable > 0.001:
            pnl_label = f"微盈 {favorable:.1%}·未达雷达激活"
            defense_plan = "持有 TP123 + TV硬止损 (朝TP1迈进中)"
        else:
            pnl_label = "保本附近"
            defense_plan = "持有 TP123 + TV硬止损"

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
        live_qty = self._resolve_live_qty(self.watched_qty or 0)
        entry = self.watched_entry
        self.shield_active = False
        self.shield_tiers_consumed = []
        self.shield_sized_qty = 0.0
        self._shield_arm_notified = False
        self._save_state()
        if reason and (had or n):
            logger.info(f"🛡️ [硬止损解除] {reason} | 撤销 {n} 笔 TV硬止损")
        if notify and n > 0:
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
                    f"{'雷达已激活，专注移动保本' if progress >= 1.0 else f'雷达进度 {progress:.0%}，推升止损防回吐'}"
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
            purged = self._purge_shield_stop_orders(tier_prices)
            self._record_shield_maintain(success=False)
            logger.warning(
                f"🛡️ 防护盾叠单清理：撤 {purged} 笔，冷却后再按实盘 {live_qty} ETH 补挂"
            )
            return False

        purged = self._purge_shield_stop_orders(tier_prices)
        if purged:
            logger.warning(
                f"🛡️ 撤净旧硬止损 {purged} 笔 → 按实盘 {live_qty} ETH 重挂 @ tv_sl"
            )
            time.sleep(0.6)

        close_side = "SHORT" if self.current_side == "LONG" else "LONG"
        placed = 0
        for idx in remaining:
            tp = tier_prices[idx]
            if tp <= 0:
                continue
            # closePosition 全平：不与 TP123 reduceOnly 额度冲突
            res = binance_client.place_stop_market_order(close_side, tp, quantity=None)
            if res:
                placed += 1
                logger.info(
                    f"🛡️ TV硬止损: "
                    f"closePosition 全平 @ {tp:.2f} (实盘 {live_qty} ETH)"
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
                f"🛡️ [TV硬止损] 已挂 | closePosition @ {stop_px:.2f} | "
                f"新挂 {placed} 笔 | 雷达激活后自动撤销"
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
                    verify_note=(
                        (reason or f"TV硬止损 tv_sl @ {stop_px:.2f}")
                        + f" | closePosition @ {stop_px:.2f} | 仅播报一次"
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

    def _maintain_hard_shield(self, real_amt, curr_px=None, force=False, radar_sl=None):
        """维护 TV tv_sl 硬止损；雷达激活时合并为 max/min(雷达, tv_sl) 单 closePosition"""
        if real_amt <= 0 or not self.watched_entry:
            return False
        curr_px = float(curr_px or 0)
        if radar_sl is None and (
            self._is_radar_active() or (curr_px > 0 and self._should_radar_trail(curr_px))
        ):
            radar_sl = self._clamp_radar_to_tv_floor(self.current_sl)

        if getattr(self, "tv_sl", 0) > 0 or radar_sl:
            if not force and not self._can_maintain_shield_now(force=force):
                return getattr(self, "shield_active", False)
            return self._sync_exchange_stop(
                real_amt,
                radar_sl=radar_sl,
                reason="维护TV硬止损/雷达合并",
                force=force,
            ).get("ok", False)

        if real_amt > 0 and not getattr(self, "_tv_sl_missing_alerted", False):
            logger.error("维护TV硬止损失败：缺少 tv_sl，拒绝 fallback 旧逻辑")
            dingtalk.report_system_alert(
                "TV硬止损缺失",
                f"持仓 {real_amt} ETH 但未收到 tv_sl，无法挂止损",
                suggestion="请确认 TV 策略已透传 tv_sl，或发送 UPDATE_SL",
            )
            self._tv_sl_missing_alerted = True
        return False

    def _process_adverse_shield(self, real_amt, curr_px):
        """兼容旧调用 → 维护硬止损"""
        return self._maintain_hard_shield(real_amt, curr_px)

    def _is_radar_active(self):
        if not self.watched_entry or not self.current_sl:
            return False
        if self.current_side == "LONG":
            return self.current_sl > self.watched_entry
        if self.current_side == "SHORT":
            return self.current_sl < self.watched_entry
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
            if binance_client.place_limit_order(close_side, q, px, reduce_only=True):
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

    def _ensure_radar_sl(self, dynamic_sl, live_qty=None):
        if not dynamic_sl:
            return False
        clamped = self._clamp_radar_to_tv_floor(dynamic_sl)
        if self._has_stop_sl_near(clamped, exclude_shield=False):
            return True
        result = self._sync_exchange_stop(
            live_qty or self.watched_qty,
            radar_sl=clamped,
            reason=f"雷达保本 @ {clamped:.2f}",
            force=True,
        )
        return result.get("ok", False)

    def _report_radar_first_activation(self, real_amt, curr_px, new_sl, sl_placed):
        """雷达首次激活：核实实盘后推送（硬止损已撤 + 保本止损已挂）"""
        if getattr(self, "_radar_activation_notified", False):
            return
        verified = self._wait_verify(
            lambda: self._has_stop_sl_near(new_sl),
            retries=10,
            delay=0.45,
        )
        progress = self._radar_activation_progress(curr_px) if curr_px > 0 else 1.0
        tv_floor = round(float(getattr(self, "tv_sl", 0) or 0), 2)
        verify_note = (
            f"雷达进度 {progress:.0%} | 合并止损 @ {new_sl:.2f} | "
            f"TV底线 tv_sl={tv_floor or 'fallback'} | "
            f"持仓 {real_amt} ETH @ {self.watched_entry:.2f}"
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
        核武级止盈对齐：每轮先撤净限价 TP → 按比例重挂 TP123 → 雷达止损单独保留/重挂。
        解决重复单堆积、多档缺失时增量补挂权限不足的问题。
        """
        sl_preserve = dynamic_sl is not None
        last_audit = self._audit_tp_levels(live_qty)
        for r in range(rounds):
            logger.warning(
                f"☢️ 核武级止盈清场重挂 {r + 1}/{rounds} | 持仓 {live_qty} ETH | "
                f"当前 {last_audit['matched_full']}/{last_audit['expected']} | "
                f"{self._format_audit_summary(last_audit)}"
            )
            if sl_preserve:
                self._cancel_all_tp_limit_orders()
            else:
                binance_client.cancel_all_open_orders(self.symbol)
            time.sleep(1.0)
            tp_sl = None if sl_preserve else dynamic_sl
            placed = self._rebuild_defenses(live_qty, entry, dynamic_sl=tp_sl)
            logger.info(f"☢️ 核武轮 {r + 1} 新挂 {placed} 笔限价止盈")
            if sl_preserve:
                time.sleep(0.6)
                self._ensure_radar_sl(dynamic_sl, live_qty)
            time.sleep(1.0)
            last_audit = self._audit_tp_levels(live_qty)
            if self._defenses_fully_ok(live_qty, dynamic_sl):
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

            sl_preserve = dynamic_sl if (dynamic_sl and self._is_radar_active() and not recover_mode) else None
            audit = self._nuclear_realign_tp(
                live_qty, entry, dynamic_sl=sl_preserve, rounds=rounds,
            )
            if audit["matched_full"] < audit["expected"]:
                logger.warning("☢️ 首轮核武未齐，追加一轮重挂")
                if recover_mode:
                    self._scorched_earth_cancel_for_recover()
                else:
                    self._cancel_all_tp_limit_orders(max_rounds=4)
                time.sleep(0.6)
                audit = self._nuclear_realign_tp(
                    live_qty, entry, dynamic_sl=sl_preserve, rounds=max(2, rounds - 1),
                )
            if dynamic_sl and not recover_mode and not self._has_stop_sl_near(dynamic_sl):
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
            if sl and not self._has_stop_sl_near(sl):
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

    def _realign_radar_defenses(self, live_qty, entry, new_sl):
        """雷达推升：TP 异常才核武；止损单独换，不动已齐 TP / 防护盾"""
        self._cancel_stop_orders(scope="radar")
        time.sleep(0.35)
        audit = self._audit_tp_levels(live_qty)
        if self._defense_needs_immediate_fix(audit):
            self._enforce_defense_alignment(
                live_qty, entry, dynamic_sl=new_sl,
                reason="雷达推升前 TP 纠偏", rounds=2,
            )
        sl_placed = self._ensure_radar_sl(new_sl, live_qty)
        if not sl_placed:
            close_side = "SHORT" if self.current_side == "LONG" else "LONG"
            sl_placed = binance_client.place_stop_market_order(close_side, new_sl) is not None
        time.sleep(0.4)
        return sl_placed

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
                binance_client.place_stop_market_order(close_side, dynamic_sl)
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
                binance_client.place_stop_market_order(close_side, dynamic_sl)
            return matched, audit["pending_prices"], expected, True

        logger.warning(
            f"⚠️ 增量补挂仍不足 ({matched}/{expected}) {audit['issues']}，升级核武级重挂"
        )
        audit = self._nuclear_realign_tp(live_qty, entry, dynamic_sl=dynamic_sl, rounds=3)
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
        sl = dynamic_sl if dynamic_sl is not None else self._radar_sl_to_pass()
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

    def _detect_tp_fills(self, old_qty, new_qty, curr_px=0.0):
        """识别 TP 成交：按 initial 累计减仓顺序推断新成交档"""
        if new_qty >= old_qty - 0.0005:
            return []
        initial = float(getattr(self, "initial_qty", 0) or old_qty)
        consumed_before = set(getattr(self, "tp_levels_consumed", []) or [])
        new_consumed = self._infer_tp_consumed_sequential(initial, new_qty, curr_px)
        slices = {sl["level"]: sl for sl in self._tp_slices_for_initial(initial)}
        fills = []
        for lv in new_consumed:
            if lv in consumed_before:
                continue
            sl = slices.get(lv)
            if not sl or sl["price"] <= 0:
                continue
            fills.append({
                "level": lv,
                "price": sl["price"],
                "qty": round(sl["qty"], 3),
            })
        return fills

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
        stop_px = self._shield_stop_price()
        if not stop_px:
            return []
        if self._has_shield_stop_at_price(stop_px):
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

    def _advance_radar_on_tp_fill(self, tp_fills, curr_px, live_qty):
        if not tp_fills:
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
        new_sl = self._compute_radar_sl()
        fee_buffer = self.watched_entry * 0.0015
        if new_sl is not None:
            if self.current_side == "LONG":
                floor = self.watched_entry + fee_buffer
                self.current_sl = max(self.current_sl or floor, new_sl, floor)
            else:
                ceiling = self.watched_entry - fee_buffer
                self.current_sl = min(self.current_sl or ceiling, new_sl, ceiling)
        elif max_level >= 1:
            if self.current_side == "LONG":
                self.current_sl = max(self.current_sl or 0, self.watched_entry + fee_buffer)
            else:
                self.current_sl = min(
                    self.current_sl or self.watched_entry,
                    self.watched_entry - fee_buffer,
                )
        note = f"TP{max_level}成交"
        if max_level >= 2 and tp3 > 0:
            note += f" → 雷达止损向 TP3({tp3:.2f}) 动态收紧"
        elif max_level == 1:
            note += " → 雷达保本启动，静默守 TP2/TP3"
        logger.info(
            f"📈 [雷达推进] {note} | SL={self.current_sl:.2f} | best={self.best_price:.2f}"
        )
        self._save_state()
        return self.current_sl if self.current_sl else None

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
            logger.info(
                f"🎯 [智慧大脑] {levels} 成交减仓 {old_qty} ➔ {new_qty} → 雷达推进 + 守剩余TP"
            )
            self._mark_tp_levels_consumed([f["level"] for f in change["tp_fills"]])
            curr_px_safe = curr_px or binance_client.get_current_price(self.symbol) or 0
            sl_to_pass = self._advance_radar_on_tp_fill(
                change["tp_fills"], curr_px, new_qty,
            )
            result = self._realign_remaining_tps_after_fill(
                new_qty, dynamic_sl=sl_to_pass,
                reason=f"{levels} 成交静默对齐",
            )
            if sl_to_pass:
                clamped = self._clamp_radar_to_tv_floor(sl_to_pass)
                self._ensure_radar_sl(clamped, new_qty)
            if (
                sl_to_pass
                and not getattr(self, "_radar_activation_notified", False)
            ):
                self._report_radar_first_activation(
                    new_qty, curr_px_safe, self._clamp_radar_to_tv_floor(sl_to_pass),
                    self._has_stop_sl_near(
                        self._clamp_radar_to_tv_floor(sl_to_pass), exclude_shield=False,
                    ),
                )
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
            retry_fills = self._detect_tp_fills(old_qty, new_qty, curr_px)
            if retry_fills:
                change = {"kind": "tp_fill", "tp_fills": retry_fills, "shield_fills": []}
                self._save_state()
                return self._handle_smart_qty_change(old_qty, new_qty, curr_px)
            action_msg = (
                "手动加仓" if new_qty > old_qty
                else "部分止盈吃单 / 手动减仓"
            )
            logger.info(
                f"🔄 [智慧大脑] 仓位变化 {old_qty} ➔ {new_qty} ({pct:.1%})，通用重对齐"
            )
            self._bump_best_on_tp_fill(old_qty, new_qty, curr_px)
            self._sync_radar_sl_from_best(curr_px)
            sl_to_pass = self._radar_sl_to_pass()
            result = self._smart_realign_defenses(
                new_qty, self.watched_entry, dynamic_sl=sl_to_pass,
                reason=f"人工异动: {action_msg}",
            )
            if self._should_disarm_shield_for_favorable(curr_px):
                self._disarm_shield("行情转有利，切换雷达保本", notify=True)
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
            f"止损 @ {new_sl:.2f} | 持仓 {real_amt} ETH | 轮询 {SENTINEL_POLL_RADAR}s"
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
        if getattr(self, "_radar_activation_notified", False) or self._is_radar_active():
            est = self._estimate_pnl_pct(curr_px)
            return self._build_close_meta(
                "CLOSE_STOPLOSS", self.current_side, est, "防回吐保本平仓",
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
        self.regime = self._safe_int(payload.get("regime"), 3)
        if self.regime not in self.regime_settings:
            self.regime = 3

        self.current_atr = self._safe_float(payload.get("atr"), 30.0)
        self.tv_price = self._safe_float(payload.get("price"), 0.0)
        self.tv_tps = self._sanitize_tp_prices([
            self._safe_float(payload.get("tv_tp1"), 0),
            self._safe_float(payload.get("tv_tp2"), 0),
            self._safe_float(payload.get("tv_tp3"), 0),
        ])
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
        if raw_action in ("LONG", "SHORT", "CLOSE", "CLOSE_PROTECT", "CLOSE_TP3", "CLOSE_STOPLOSS", "UPDATE_SL") or \
                raw_action.startswith("CLOSE"):
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
                        else "硬止损"
                    )
                    self._close_all(
                        f"🛑 {tag}：{tv_reason}{close_extra}",
                        close_meta=close_meta,
                    )
            elif raw_action == "CLOSE":
                self._close_all(f"🧹 换防清场：{close_reason}{close_extra}", close_meta=close_meta)
            elif raw_action == "UPDATE_SL":
                self._handle_tv_sl_update(payload)
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
        meta = self._enrich_close_meta_live(
            close_meta or self._infer_flat_close_meta(curr_px, hint_reason=reason),
            curr_px,
        )
        logger.info(f"📭 感知空仓: {meta.get('tv_reason') or reason}")
        self.monitoring = False
        self.watched_qty = 0.0
        self.base_qty = 0.0
        self.add_count = 0
        self.current_side = None
        binance_client.cancel_all_open_orders(self.symbol)
        self._save_state()
        self._report_flat_close(
            meta.get("tv_reason") or reason or "仓位归零",
            close_meta=meta,
            curr_px=curr_px,
        )

    def _add_to_position(self, action, payload):
        """PYRAMID / PROFIT_ADD：固定 base×0.5 追加，只更新 tv_sl，不改 TP123"""
        entry_type = normalize_entry_type(payload.get("entry_type"))
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
        if int(getattr(self, "add_count", 0) or 0) >= MAX_ADD_TIMES:
            logger.warning(
                f"{entry_type} 跳过：已达最大加仓次数 {MAX_ADD_TIMES} "
                f"(base={getattr(self, 'base_qty', 0):.3f})"
            )
            dingtalk.report_system_alert(
                f"{entry_type} 加仓跳过",
                f"已达最大加仓 {MAX_ADD_TIMES} 次 | base={getattr(self, 'base_qty', 0):.3f} "
                f"| 现仓 {pos['size']} ETH",
            )
            return

        curr_px = binance_client.get_current_price(self.symbol) or self.tv_price
        old_qty = float(pos["size"])
        old_entry = float(pos["entry_price"])
        add_qty, meta = self._calc_vps_add_qty()
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
        order = binance_client.place_market_order(action, add_qty)
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

        sl_ok = self._maintain_hard_shield(new_qty, curr_px, force=True)
        self.add_count = int(getattr(self, "add_count", 0) or 0) + 1
        self._save_state()
        type_label = "浮盈加仓" if entry_type == ENTRY_TYPE_PROFIT_ADD else "金字塔加仓"
        verify_note = (
            f"{type_label} | {self._tv_sizing_note(add_qty, meta, entry_type=entry_type)} "
            f"| base={getattr(self, 'base_qty', 0):.3f} "
            f"| 加仓次数 {self.add_count}/{MAX_ADD_TIMES} "
            f"| 持仓 {old_qty:.3f}→{new_qty:.3f} ETH @ {new_entry:.2f} "
            f"| tv_sl={getattr(self, 'tv_sl', 0):.2f} "
            f"| {'止损已核实' if sl_ok else '止损待核实'}"
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
            qty_ratio=ADD_QTY_RATIO,
            base_qty=getattr(self, "base_qty", 0),
            vps_sizing_meta=meta,
            add_count=self.add_count,
            max_add_times=MAX_ADD_TIMES,
            verify_note=verify_note,
            verified=sl_ok,
        )
        self._ensure_sentinel_running()

    def _handle_smart_entry(self, action, payload=None):
        """VPS sizing：OPEN 先平后开；PYRAMID/PROFIT_ADD 只追加；未标 entry_type 走智能筛选"""
        payload = payload or {}
        entry_type = normalize_entry_type(payload.get("entry_type"))

        if entry_type in (ENTRY_TYPE_PYRAMID, ENTRY_TYPE_PROFIT_ADD):
            self._add_to_position(action, payload)
            self._touch_entry_signal_signature(action)
            return

        if entry_type == ENTRY_TYPE_OPEN:
            pos = self._get_active_position()
            if pos and pos.get("size", 0) > 0:
                logger.info(f"📡 TV OPEN → 先平后开 [{action}]")
                self._full_reentry(action, "TV OPEN 先平后开")
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
            logger.info(f"📐 仓位预算: {budget_txt} (名义 ~{notional:.0f}U)")

            if not self._wait_verify(self._verify_flat, retries=4, delay=0.35):
                logger.error("开仓中止：市价下单前盘口仍非空")
                dingtalk.report_system_alert(
                    "开仓中止 · 下单前盘口非空",
                    f"TV {action} 目标 {qty} ETH，下单前 REST 仍显示持仓，已拒绝叠仓",
                )
                return

            open_side = "BUY" if action == "LONG" else "SELL"
            logger.info(f"🚀 [唯一主仓] 极速开仓: {open_side} {qty} 个ETH | 档位 {self.regime}")
            order = binance_client.place_market_order(action, qty)
            if not order:
                logger.error("开仓失败：市价单未成交")
                dingtalk.report_system_alert("开仓失败", f"TV {action} {qty} ETH 市价单失败")
                return
            time.sleep(2.0)

            pos = self._get_active_position()
            if not pos or pos["size"] <= 0:
                logger.error("开仓失败：成交后 REST 无持仓")
                return

            real_qty = pos["size"]
            if real_qty > qty * OPEN_OVERSIZE_RATIO:
                logger.error(
                    f"🚨 持仓超标: 目标 {qty} ETH，实盘 {real_qty} ETH "
                    f"(>{qty * OPEN_OVERSIZE_RATIO:.3f})，启动裁减"
                )
                dingtalk.report_system_alert(
                    "持仓超标 · 自动裁减",
                    f"目标 {qty} ETH (保证金 {margin_usdt:.0f}U)，"
                    f"实盘 {real_qty} ETH @ {pos['entry_price']:.2f}，正在 reduceOnly 裁减",
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
                budget_note=f"{budget_txt} | ",
                target_qty=qty,
                sizing_meta=sizing_meta,
            )
        finally:
            self._open_in_progress = False

    def _protect_and_monitor(self, qty, entry_price, budget_note="", target_qty=0.0, sizing_meta=None):
        tp_pxs = self.tv_tps
        self.current_sl = entry_price
        self.best_price = entry_price
        self.shield_active = False
        self.shield_tiers_consumed = []
        self.tp_levels_consumed = []
        self._radar_activation_notified = False
        self._shield_handoff_notified = False
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
                self._save_state()

            self._scorched_earth_cancel_for_recover()
            self._enforce_defense_alignment(
                live_qty, verified["entry_price"],
                dynamic_sl=None, reason="开仓后防线对齐", rounds=4,
                recover_mode=True,
            )
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
            curr_px = binance_client.get_current_price(self.symbol) or entry_price
            if self._should_activate_shield(curr_px):
                shield_ok = self._maintain_hard_shield(live_qty, curr_px, force=True)
                stop_px = self._shield_stop_price(verified["entry_price"])
                tv_sl_note = (
                    f"TV硬止损 @ {self.tv_sl:.2f}"
                    if getattr(self, "tv_sl", 0) > 0
                    else "TV tv_sl 待透传"
                )
                if shield_ok:
                    verify_note += (
                        f" | {tv_sl_note}已核实"
                        + (f" @ {stop_px:.2f}" if stop_px else "")
                    )
                else:
                    shield_audit = self._audit_shield_orders(live_qty, verified["entry_price"])
                    verify_note += (
                        f" | {tv_sl_note}待核实"
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
                regime=self.regime,
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
            )
            if expected > 0 and matched < expected:
                self._open_tp_unconfirmed = True
                dupes = [lv for lv in audit.get("levels", []) if lv.get("status") == "duplicate"]
                hint = (
                    "重复 TP 占满可减仓额度 | 雷达将接力纠偏"
                    if dupes else "请查 logs/binance_brain.log"
                )
                dingtalk.report_system_alert(
                    "开仓后限价止盈未全部挂上",
                    f"{self.current_side} {live_qty} ETH | 仅 {matched}/{expected} 档 | "
                    f"{self._format_audit_summary(audit)} | {hint}",
                )
        else:
            logger.warning("开仓钉钉跳过：实盘持仓核查未通过")

        self._ensure_sentinel_running()

    def _refresh_radar_state_on_recover(self, curr_px, entry):
        """重启：按现价恢复 best_price / 雷达激活 / 追踪止损位"""
        if curr_px <= 0 or not entry:
            return
        fee_buffer = entry * 0.0015
        trail_offset = self.current_atr * self.regime_settings[self.regime]["trail_offset"]

        if self.best_price == 0.0:
            self.best_price = entry
        if self.current_side == "LONG":
            self.best_price = max(self.best_price, curr_px)
        else:
            self.best_price = min(self.best_price, curr_px)

        progress = self._radar_activation_progress(curr_px)
        if progress >= 1.0:
            if self.current_side == "LONG":
                breakeven_floor = entry + fee_buffer
                trail_sl = max(round(self.best_price - trail_offset, 2), breakeven_floor)
                if not self._is_radar_active() or trail_sl > self.current_sl:
                    self.current_sl = max(self.current_sl or entry, trail_sl)
            else:
                breakeven_floor = entry - fee_buffer
                trail_sl = min(round(self.best_price + trail_offset, 2), breakeven_floor)
                if not self._is_radar_active() or trail_sl < self.current_sl:
                    self.current_sl = min(self.current_sl or entry, trail_sl)
            logger.info(
                f"📡 重启雷达恢复: 进度 {progress:.0%} | best={self.best_price:.2f} | "
                f"SL={self.current_sl:.2f}"
            )
        elif self.current_sl == 0.0:
            self.current_sl = entry

    def _ensure_price_ws(self):
        """雷达/哨兵用 WebSocket 推价，REST 仅兜底"""
        binance_client.start_public_price_ws(self.symbol)

    def _tp1_distance(self):
        if self.tv_tps[0] > 0 and self.watched_entry:
            return abs(self.tv_tps[0] - self.watched_entry)
        return self.current_atr * 1.5

    def _radar_activation_price(self):
        activation_ratio = self.regime_settings[self.regime]["activation"]
        tp1_dist = self._tp1_distance()
        if self.current_side == "LONG":
            return self.watched_entry + tp1_dist * activation_ratio
        return self.watched_entry - tp1_dist * activation_ratio

    def _should_radar_trail(self, curr_px):
        if self._is_radar_active():
            return True
        if curr_px <= 0 or not self.watched_entry:
            return False
        if self.current_side == "LONG":
            return curr_px >= self._radar_activation_price()
        return curr_px <= self._radar_activation_price()

    def _compute_radar_sl(self):
        if not self.watched_entry or self.best_price <= 0:
            return None
        trail_offset = self.current_atr * self.regime_settings[self.regime]["trail_offset"]
        fee_buffer = self.watched_entry * 0.0015
        if self.current_side == "LONG":
            raw = max(round(self.best_price - trail_offset, 2), self.watched_entry + fee_buffer)
        elif self.current_side == "SHORT":
            raw = min(round(self.best_price + trail_offset, 2), self.watched_entry - fee_buffer)
        else:
            return None
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

    def _radar_activation_progress(self, curr_px):
        """0~1：价格向 TP1 激活线推进的进度"""
        if curr_px <= 0 or not self.watched_entry:
            return 0.0
        tp1_dist = self._tp1_distance()
        activation_ratio = self.regime_settings[self.regime]["activation"]
        if self.current_side == "LONG":
            required = self.watched_entry + tp1_dist * activation_ratio
            span = required - self.watched_entry
            if span <= 0:
                return 0.0
            return max(0.0, min(1.0, (curr_px - self.watched_entry) / span))
        required = self.watched_entry - tp1_dist * activation_ratio
        span = self.watched_entry - required
        if span <= 0:
            return 0.0
        return max(0.0, min(1.0, (self.watched_entry - curr_px) / span))

    def _sentinel_poll_sec(self, curr_px=0.0):
        """雷达已激活=2s；接近激活/逆势逼近=3s；常态=6s"""
        if self._is_radar_active():
            return SENTINEL_POLL_RADAR
        if curr_px > 0:
            if self._radar_activation_progress(curr_px) >= 0.5:
                return SENTINEL_POLL_ARMING
        return SENTINEL_POLL_NORMAL

    def _process_radar_trailing(self, real_amt, curr_px):
        """实时雷达：跟踪 best_price，推升/下压保本止损（不低于 TV tv_sl）"""
        if not self._should_radar_trail(curr_px):
            return False
        new_sl = self._compute_radar_sl()
        if new_sl is None:
            return False

        if not self._is_radar_active():
            fee_buffer = self.watched_entry * 0.0015
            if self.current_side == "LONG":
                boot_sl = max(new_sl, self.watched_entry + fee_buffer)
                if boot_sl > self.current_sl:
                    self.current_sl = boot_sl
            else:
                boot_sl = min(new_sl, self.watched_entry - fee_buffer)
                if boot_sl < self.current_sl or self.current_sl >= self.watched_entry:
                    self.current_sl = boot_sl
            self._save_state()
            sl_placed = False
            if not self._has_stop_sl_near(self.current_sl):
                sl_placed = self._ensure_radar_sl(self.current_sl, real_amt)
            logger.info(
                f"📡 雷达首次激活：保本止损 @ {self.current_sl:.2f} | best={self.best_price:.2f}"
            )
            self._report_radar_first_activation(
                real_amt, curr_px, self.current_sl, sl_placed,
            )
            return True

        moved = False

        if self.current_side == "LONG":
            if new_sl > self.current_sl + 1.0:
                self.current_sl = new_sl
                self._save_state()
                sl_placed = self._realign_radar_defenses(real_amt, self.watched_entry, new_sl)
                self._report_radar_intervention(
                    real_amt, new_sl,
                    f"🚀 档位{self.regime} 雷达实时跟踪：保本盾推升至 {new_sl:.2f}",
                    sl_placed=sl_placed,
                )
                moved = True
        else:
            if self.current_sl >= self.watched_entry or new_sl < self.current_sl - 1.0:
                self.current_sl = new_sl
                self._save_state()
                sl_placed = self._realign_radar_defenses(real_amt, self.watched_entry, new_sl)
                self._report_radar_intervention(
                    real_amt, new_sl,
                    f"🚀 档位{self.regime} 雷达实时跟踪：保本顶线下压至 {new_sl:.2f}",
                    sl_placed=sl_placed,
                )
                moved = True
        return moved

    def _sentinel_loop(self):
        """哨兵：持仓/TP 防线 + 雷达移动保本（自适应轮询 2~6 秒）"""
        self._sentinel_active = True
        last_px = 0.0
        try:
            while self.monitoring:
                try:
                    if not self._lock.acquire(timeout=2.0):
                        continue
                    try:
                        pos = self._get_active_position()
                        real_amt = pos["size"] if pos else 0.0
                        actual_side = pos["side"] if pos else None

                        if not pos or real_amt == 0:
                            if self.watched_qty > 0:
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

                        if actual_side != self.last_tv_side:
                            reason = f"致命方向背离：实盘({actual_side}) vs TV({self.last_tv_side})"
                            self._close_all(reason, force_align=(actual_side, self.last_tv_side))
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
                        progress = self._radar_activation_progress(curr_px)
                        if (
                            progress >= 0.5
                            and not self._is_radar_active()
                            and self._scan_ticks % 5 == 0
                        ):
                            logger.info(
                                f"📡 雷达预热: 进度 {progress:.0%} | 现价 {curr_px:.2f} | "
                                f"轮询 {SENTINEL_POLL_ARMING}s"
                            )
                    finally:
                        self._lock.release()
                except Exception as e:
                    logger.error(f"哨兵异常: {e}")
                if self.monitoring:
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
                res = binance_client.place_limit_order(close_side, q, px, reduce_only=True)
                if res:
                    placed += 1
                time.sleep(0.35)

        if dynamic_sl:
            binance_client.place_stop_market_order(close_side, dynamic_sl)
        return placed

    def _close_all(self, reason="", force_align=None, reset_state=True, close_meta=None):
        """先撤全部挂单再阶梯强平；返回是否已空仓"""
        binance_client.cancel_all_open_orders(self.symbol)
        time.sleep(0.5)
        self._cancel_all_tp_limit_orders()
        time.sleep(0.3)
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
            binance_client.place_market_order(close_side, live_sz, reduce_only=True)
            time.sleep(1.5)

        if not closed_successfully:
            residual = self._get_active_position()
            residual_sz = residual["size"] if residual else 0.0
            if residual_sz > 0 and self._is_dust_qty(residual_sz):
                close_side = "SELL" if residual["side"] == "LONG" else "BUY"
                logger.warning(f"🐜 强平后残 {residual_sz} ETH，触发蚂蚁仓扫尾")
                binance_client.place_market_order(close_side, residual_sz, reduce_only=True)
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

        binance_client.cancel_all_open_orders(self.symbol)

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
                    verify_note=verify_note,
                    verified=flat,
                )
            else:
                self._report_flat_close(reason, close_meta=close_meta)

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
                if not self._lock.acquire(timeout=120.0):
                    logger.error("❌ 重启接管无法获取锁，跳过")
                    self._recover_in_progress = False
                    return
                try:
                    reconcile = self._reconcile_context_on_recover(pos)
                    reconcile_notes = reconcile["notes"]
                    real_amt = pos["size"]
                    side = pos["side"]
                    self.current_side = side

                    align_notes = self._apply_recover_live_alignment(side, reconcile)
                    reconcile_notes.extend(align_notes)

                    saved_initial = self._resolve_open_initial_qty(real_amt)
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
                    tp_repair = self._repair_partial_tp_on_recover(
                        real_amt, self.watched_entry, saved_initial, curr_px or 0,
                    )
                    tp_repair_note = ""
                    self._refresh_radar_state_on_recover(curr_px, self.watched_entry)
                    if tp_repair.get("repaired"):
                        tp_repair_note = " | TP修复: " + " · ".join(tp_repair["actions"])
                        logger.info(f"🎯 重启部分止盈修复: {tp_repair_note}")

                    radar_active = self._is_radar_active()
                    saved_sl = self.current_sl if radar_active else None

                    logger.info(
                        f"🔄 [系统重启点火] 检测到实盘持仓 {self.current_side} {real_amt} ETH @ "
                        f"{self.watched_entry:.2f} | 开单 {saved_initial} ETH | "
                        f"已成交 TP{getattr(self, 'tp_levels_consumed', []) or '无'} | "
                        f"雷达={'已激活' if radar_active else '待命'} | "
                        f"TV对齐 {self.last_tv_side} | 对账 {len(reconcile_notes)} 项"
                    )

                    cap = self._radar_enforce_regime_cap(real_amt, curr_px, force=True)
                    if cap:
                        real_amt = cap["new_qty"]
                        pos = self._get_active_position() or pos
                        if pos:
                            self.watched_qty = real_amt
                            if float(self.initial_qty or 0) <= real_amt + 0.001:
                                self.initial_qty = real_amt

                    if tp_repair.get("repaired") and tp_repair.get("result"):
                        result = tp_repair["result"]
                        sl = self._radar_sl_to_pass()
                        if sl and not self._has_stop_sl_near(sl):
                            sl_ok = self._ensure_radar_sl(sl, real_amt)
                        elif saved_sl and radar_active:
                            sl_ok = self._ensure_radar_sl(saved_sl, real_amt)
                        else:
                            sl_ok = True
                    else:
                        result = self._enforce_defense_alignment(
                            real_amt, self.watched_entry, dynamic_sl=None,
                            reason="重启闪电接管 · 核武撤单重挂",
                            rounds=4, recover_mode=True,
                        )
                        if saved_sl and radar_active:
                            sl_ok = self._ensure_radar_sl(saved_sl, real_amt)
                        else:
                            sl_ok = True

                    _rebuilt = result["rebuilt"]
                    audit = self._wait_defense_settled(
                        real_amt, saved_sl if radar_active else None,
                    )
                    matched = audit["matched_full"]
                    expected = audit["expected"]

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
                    entry_px = float(
                        (verified or pos)["entry_price"]
                    )
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
                        f"止盈 {matched}/{expected} 档 | "
                        f"{self._format_audit_summary(audit)}{skip_note}{tp_repair_note}{tv_note}{reconcile_txt}"
                    )
                    if not verified:
                        verify_note += " | REST 同步略延迟"
                    if qty_change and not tp_repair.get("repaired"):
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
                    else:
                        self._mark_defense_align_ok()

                    health = self._build_recover_health_report(
                        {"side": self.current_side, "size": real_amt, "entry_price": entry_px},
                        curr_px, audit,
                    )
                    bootstrap = self._bootstrap_live_defenses_after_recover(
                        real_amt, curr_px, audit=audit,
                    )
                    audit = bootstrap.get("audit") or audit
                    health = bootstrap.get("health") or health
                    policy_actions = bootstrap.get("actions") or []
                    radar_active = health["radar_active"] or health["should_radar"]
                    sl_ok = (
                        not radar_active
                        or self._has_stop_sl_near(self.current_sl)
                    )
                    policy_txt = " | 防线: " + " · ".join(policy_actions) if policy_actions else ""
                    health_txt = (
                        f" | 盈亏态 {health['pnl_label']} | "
                        f"硬止损 {health['shield_status']} | "
                        f"策略 {health['defense_plan']}"
                    )
                    verify_note = verify_note + health_txt + policy_txt

                    self._sentinel_grace_until = time.time() + SENTINEL_GRACE_AFTER_RECOVER_SEC

                    if tp_repair.get("repaired"):
                        self._call_dingtalk(
                            dingtalk.report_recover_tp_repair,
                            side=self.current_side,
                            initial_qty=saved_initial,
                            live_qty=real_amt,
                            entry=entry_px,
                            consumed_levels=tp_repair.get("consumed") or [],
                            tp_audit=audit,
                            verify_note=" · ".join(tp_repair.get("actions") or []),
                            verified=bool(verified),
                        )

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
                        pnl_label=health["pnl_label"],
                        defense_plan=health["defense_plan"],
                        shield_status=health["shield_status"],
                        radar_progress=health["radar_progress"],
                        tv_aligned=health["tv_match"],
                        qty_aligned=health["qty_match"],
                        initial_qty=saved_initial,
                        tp_consumed_levels=getattr(self, "tp_levels_consumed", []) or [],
                    )
                    logger.info(
                        f"  -> 🎉 实盘阵地接管完毕 | {health['pnl_label']} | "
                        f"防线 {' · '.join(policy_actions) if policy_actions else '已核实'}"
                    )
                finally:
                    self._recover_in_progress = False
                    self._lock.release()

                if radar_active:
                    logger.info(
                        f"📡 [重启] 雷达哨兵已点火 | SL={self.current_sl:.2f} | "
                        f"止损={'已挂/已确认' if sl_ok else '待哨兵补挂'}"
                    )

                if not self._sentinel_active:
                    threading.Thread(
                        target=self._sentinel_loop, daemon=True, name="sentinel",
                    ).start()
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
            logger.error(f"❌ 闪电接管异常: {e}")
            dingtalk.report_system_alert("重启接管失败", str(e))


position_supervisor = PositionSupervisorBinance()

if __name__ != "__main__":
    position_supervisor.recover_state_on_startup()
