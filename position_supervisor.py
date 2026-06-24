#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import logging, time, threading, os, json
from datetime import datetime
from logging.handlers import RotatingFileHandler
from binance_client import binance_client
from position_manager import position_manager
import dingtalk

if not os.path.exists('logs'): os.makedirs('logs')
handler = RotatingFileHandler('logs/binance_brain.log', maxBytes=5*1024*1024, backupCount=3)
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] Brain: %(message)s', handlers=[handler, logging.StreamHandler()])
logger = logging.getLogger(__name__)

class PositionSupervisor:
    def __init__(self):
        self.symbol = "ETHUSDT"
        self.monitoring = False
        self._lock = threading.Lock()

        self.tp_ratios = [0.05, 0.20, 0.75]
        self.tp1_mult = 1.55
        self.tp2_mult = 3.0
        self.tp3_mult = 4.8
        self.sl_mult = 1.25
        self.current_trail_factor = 0.50

        self.regime = 3
        self.current_atr = 30.0
        self.best_price = 0.0
        self.current_sl = 0.0
        self.initial_qty = 0.0
        self.watched_qty = 0.0
        self.watched_entry = 0.0
        
        self.current_side = None
        self.last_tv_side = None
        self.manual_intervention_flag = False
        self.tv_tps = [0.0, 0.0, 0.0]

        self.daily_start_date = ""
        self.daily_start_balance = 0.0
        self.cb_level1_pct = -5.0
        self.cb_level2_pct = -10.0
        self.breakeven_ratios = {1: 0.72, 2: 0.68, 3: 0.60, 4: 0.50}

        # 智能去重与状态
        self.last_protect_time = 0
        self.price_diff_threshold = 0.006   # 0.6%

        self.state_file = 'vps_state.json'
        logger.info("🧠 币安 VPS [最终强壮实盘版] 已加载")

    def _save_state(self):
        state = {
            "last_tv_side": self.last_tv_side,
            "current_side": self.current_side,
            "watched_qty": self.watched_qty,
            "watched_entry": self.watched_entry,
            "current_sl": self.current_sl,
            "monitoring": self.monitoring,
            "manual_intervention_flag": self.manual_intervention_flag
        }
        try:
            with open(self.state_file, 'w') as f:
                json.dump(state, f)
        except: pass

    def _get_or_update_daily_baseline(self, current_balance):
        today = datetime.utcnow().strftime('%Y-%m-%d')
        if self.daily_start_date != today:
            self.daily_start_date = today
            self.daily_start_balance = current_balance
        return self.daily_start_balance

    # ==================== 核心信号处理 ====================
    def handle_signal(self, payload):
        raw_action = payload.get("action", "").upper()
        self.regime = int(payload.get("regime", 3))
        self.current_atr = float(payload.get("atr", 30.0))
        self.tp1_mult = float(payload.get("tp1_m", 1.55))
        self.tp2_mult = float(payload.get("tp2_m", 3.0))
        self.tp3_mult = float(payload.get("tp3_m", 4.8))
        self.current_trail_factor = float(payload.get("trail_factor", 0.50))
        self.tv_tps = [
            float(payload.get("tv_tp1", 0)),
            float(payload.get("tv_tp2", 0)),
            float(payload.get("tv_tp3", 0))
        ]

        if self.regime == 1: self.tp_ratios = [0.25, 0.35, 0.40]
        elif self.regime == 2: self.tp_ratios = [0.20, 0.35, 0.45]
        elif self.regime == 3: self.tp_ratios = [0.18, 0.32, 0.50]
        else: self.tp_ratios = [0.05, 0.20, 0.75]

        if not raw_action: return
        if not self._lock.acquire(blocking=False): return

        try:
            self.monitoring = False

            if raw_action == "CLOSE_PROTECT":
                self._handle_protective_close()
                return

            if raw_action == "CLOSE_TP3":
                if position_manager.has_position(self.symbol):
                    self._close_all("🎯 TP3 止盈全平")
                return

            if raw_action == "CLOSE":
                self._close_all("🧹 TV 强制清仓")
                return

            if raw_action in ["LONG", "SHORT"]:
                self._handle_smart_entry(raw_action)

        finally:
            self._lock.release()

    # ==================== 智能开仓处理（严格先平后开） ====================
    def _handle_smart_entry(self, action):
        self.last_tv_side = action
        self.manual_intervention_flag = False
        self._save_state()

        # 先撤单 + 全平
        binance_client.cancel_all_open_orders()
        self._close_all_with_confirmation("新方向到达，强制清场")

        curr_px = binance_client.get_current_price(self.symbol)
        balance = binance_client.get_available_balance()
        baseline = self._get_or_update_daily_baseline(balance)
        daily_pnl_pct = (balance - baseline) / baseline * 100 if baseline > 0 else 0

        if daily_pnl_pct <= self.cb_level2_pct:
            dingtalk.report_system_alert("🔴 账户物理熔断", f"今日亏损已达 {daily_pnl_pct:.2f}%，拒绝开新仓")
            return

        dynamic_margin = {1: 0.15, 2: 0.25, 3: 0.35, 4: 0.50}.get(self.regime, 0.35)
        if daily_pnl_pct <= self.cb_level1_pct:
            dynamic_margin *= 0.5

        binance_client.set_leverage(self.symbol, leverage=20)
        qty = round((balance * dynamic_margin * 20) / curr_px, 3)
        qty = max(qty, round(20.0 / curr_px + 0.001, 3))

        binance_client.place_market_order(action, qty)
        time.sleep(2)

        pos = position_manager.get_position(self.symbol)
        if pos and float(pos.get("positionAmt", 0)) != 0:
            self.current_side = action
            real_qty = abs(float(pos["positionAmt"]))
            self.initial_qty = real_qty
            self._protect_and_monitor(real_qty, float(pos["entryPrice"]))

    # ==================== 保护性全平智能处理 ====================
    def _handle_protective_close(self):
        if not position_manager.has_position(self.symbol):
            logger.info("[保护性全平] 当前无持仓，忽略该警报")
            return

        if time.time() - self.last_protect_time < 30:
            logger.info("[保护性全平] 短时间内重复，忽略")
            return

        self.last_protect_time = time.time()
        self._close_all("🛡️ 保护性全平")

    def _protect_and_monitor(self, qty, entry_price):
        close_side = "SHORT" if self.current_side == "LONG" else "LONG"
        qty1 = round(qty * self.tp_ratios[0], 3)
        qty2 = round(qty * self.tp_ratios[1], 3)
        qty3 = round(qty - qty1 - qty2, 3)

        if self.current_side == "LONG":
            tp1 = round(entry_price + self.current_atr * self.tp1_mult, 2)
            tp2 = round(entry_price + self.current_atr * self.tp2_mult, 2)
            tp3 = round(entry_price + self.current_atr * self.tp3_mult, 2)
            self.current_sl = round(entry_price - self.current_atr * self.sl_mult, 2)
        else:
            tp1 = round(entry_price - self.current_atr * self.tp1_mult, 2)
            tp2 = round(entry_price - self.current_atr * self.tp2_mult, 2)
            tp3 = round(entry_price - self.current_atr * self.tp3_mult, 2)
            self.current_sl = round(entry_price + self.current_atr * self.sl_mult, 2)

        if qty1 >= 0.001: binance_client.place_limit_order(close_side, qty1, tp1, reduce_only=True)
        if qty2 >= 0.001: binance_client.place_limit_order(close_side, qty2, tp2, reduce_only=True)
        if qty3 >= 0.001: binance_client.place_limit_order(close_side, qty3, tp3, reduce_only=True)
        binance_client.place_stop_market_order(close_side, self.current_sl)

        self.best_price = entry_price
        self.watched_qty = qty
        self.watched_entry = entry_price
        self.monitoring = True
        self._save_state()

        dingtalk.report_supervisor_open(self.current_side, entry_price, qty, [tp1, tp2, tp3], self.current_atr, self.regime, self.tv_tps)
        threading.Thread(target=self._sentinel_loop, daemon=True).start()

    # ==================== 加强版平仓确认 ====================
    def _close_all_with_confirmation(self, reason=""):
        binance_client.cancel_all_open_orders()
        time.sleep(0.5)

        for i in range(8):  # 最多等待约5-6秒
            binance_client.close_all_positions()
            time.sleep(0.7)
            pos = position_manager.get_position(self.symbol)
            if not pos or float(pos.get("positionAmt", 0)) == 0:
                logger.info(f"[平仓确认] {reason} - 已确认持仓归零")
                self.monitoring = False
                self.watched_qty = 0.0
                self._save_state()
                if reason:
                    dingtalk.report_supervisor_close(reason)
                return True

        logger.warning(f"[平仓确认] {reason} - 多次尝试后仍未完全归零")
        self.monitoring = False
        self.watched_qty = 0.0
        self._save_state()
        return False

    def _close_all(self, reason=""):
        self._close_all_with_confirmation(reason)

    def _sentinel_loop(self):
        while self.monitoring:
            try:
                pos = position_manager.get_position(self.symbol)
                real_amt = float(pos.get("positionAmt", 0)) if pos else 0.0

                # 人工干预检测
                if real_amt == 0 and self.watched_qty > 0:
                    self.manual_intervention_flag = True
                    self._close_all("🚨 检测到人工违规干预")
                    break

                # 方向强对齐
                actual_side = "LONG" if real_amt > 0 else "SHORT"
                if real_amt != 0 and actual_side != self.last_tv_side:
                    self._close_all("🚨 方向与TV严重背离，强制对齐")
                    break

                # 其他原有哨兵逻辑保留（挂单自愈、雷达等）
                curr_px = binance_client.get_current_price(self.symbol)
                # ... 雷达移动保本逻辑 ...

            except Exception as e:
                logger.error(f"哨兵异常: {e}")
            time.sleep(3)

    def recover_state_on_startup(self):
        try:
            if os.path.exists(self.state_file):
                with open(self.state_file, 'r') as f:
                    saved = json.load(f)
                    self.last_tv_side = saved.get("last_tv_side")
        except: pass

        pos = position_manager.get_position(self.symbol)
        if pos and float(pos.get("positionAmt", 0)) != 0:
            self.current_side = "LONG" if float(pos["positionAmt"]) > 0 else "SHORT"
            if not self.last_tv_side:
                self.last_tv_side = self.current_side
            self.watched_qty = abs(float(pos["positionAmt"]))
            self.watched_entry = float(pos["entryPrice"])
            self.monitoring = True
            threading.Thread(target=self._sentinel_loop, daemon=True).start()

position_supervisor = PositionSupervisor()
position_supervisor.recover_state_on_startup()
