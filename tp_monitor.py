#!/usr/bin/env python3
# tp_monitor.py（V2.5 监控层 - 精度统一修复版）
import logging
import time
import threading
from typing import Optional
from binance_client import binance_client
from order_executor import order_executor
from position_manager import position_manager
import dingtalk
from state_manager import state_manager

logger = logging.getLogger(__name__)


class TPMonitor:
    def __init__(self, check_interval: float = 5.0):
        self.client = binance_client
        self.executor = order_executor
        self.position_manager = position_manager
        self.check_interval = check_interval
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()

        self.tp1_price = self.tp2_price = self.tp3_price = None
        self.position_side = None
        self.position_qty = 0.0
        self.entry_price = 0.0
        self.is_monitoring = False

        self._restore_from_state()

    def _restore_from_state(self):
        state = state_manager.load_state()
        if state and state.get("is_monitoring"):
            with self._lock:
                self.tp1_price = state.get("tp1")
                self.tp2_price = state.get("tp2")
                self.tp3_price = state.get("tp3")
                self.position_side = state.get("side")
                self.position_qty = state.get("remaining_qty", 0)
                self.entry_price = state.get("entry_price", 0)
                self.is_monitoring = True

    def set_tp_levels(self, tp1: float, tp2: float, tp3: float, side: str, qty: float, entry_price: float = 0):
        with self._lock:
            # 统一精度：价格保留2位，数量保留3位
            self.tp1_price = round(tp1, 2)
            self.tp2_price = round(tp2, 2)
            self.tp3_price = round(tp3, 2)
            self.position_side = side
            self.position_qty = round(qty, 3)
            self.entry_price = round(entry_price or self.position_manager.get_position().get("entryPrice", 0), 2)
            self.is_monitoring = True

            state_manager.save_state({
                "tp1": self.tp1_price,
                "tp2": self.tp2_price,
                "tp3": self.tp3_price,
                "side": side,
                "remaining_qty": self.position_qty,
                "entry_price": self.entry_price,
                "is_monitoring": True
            })

    def clear_tp_levels(self):
        with self._lock:
            self.tp1_price = self.tp2_price = self.tp3_price = None
            self.position_side = None
            self.position_qty = 0.0
            self.entry_price = 0.0
            self.is_monitoring = False
        state_manager.clear_state()

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5)
        self.clear_tp_levels()

    def _monitor_loop(self):
        while not self._stop_event.is_set():
            try:
                if not self.is_monitoring or not self.position_side:
                    time.sleep(self.check_interval)
                    continue

                self._reconcile_position()
                if not self.is_monitoring:
                    time.sleep(self.check_interval)
                    continue

                current_price = self.client.get_current_price()
                if current_price <= 0:
                    time.sleep(self.check_interval)
                    continue

                triggered_level = self._check_tp_trigger(current_price)
                if triggered_level:
                    self._handle_tp_trigger(triggered_level, current_price)

            except Exception as e:
                logger.error(f"[TPMonitor] 异常: {e}", exc_info=True)
            time.sleep(self.check_interval)

    def _reconcile_position(self):
        real_pos = self.position_manager.get_position()
        if not real_pos:
            return

        real_side = self.position_manager.get_position_side()
        real_qty = self.position_manager.get_position_qty()

        if real_side and real_side != self.position_side:
            dingtalk.report_force_align(real_side, self.position_side)
            self.executor.close_position("监控层强制清空逆向持仓")
            self.clear_tp_levels()
            return

        if real_qty > 0 and abs(real_qty - self.position_qty) > 0.01:
            self._handle_quantity_change(real_qty)

    def _handle_quantity_change(self, new_qty: float):
        try:
            current_atr = self.client.get_atr("ETHUSDT", "3h", 50, 14) or 22.0
            current_entry = round(float(self.position_manager.get_position().get("entryPrice", self.entry_price)), 2)

            if self.position_side == "LONG":
                tps = {
                    "tp1": round(current_entry + current_atr * 1.3, 2),
                    "tp2": round(current_entry + current_atr * 2.6, 2),
                    "tp3": round(current_entry + current_atr * 4.2, 2)
                }
            else:
                tps = {
                    "tp1": round(current_entry - current_atr * 1.3, 2),
                    "tp2": round(current_entry - current_atr * 2.6, 2),
                    "tp3": round(current_entry - current_atr * 4.2, 2)
                }

            with self._lock:
                self.position_qty = round(new_qty, 3)
                self.tp1_price = tps['tp1']
                self.tp2_price = tps['tp2']
                self.tp3_price = tps['tp3']

            state_manager.save_state({
                "tp1": self.tp1_price,
                "tp2": self.tp2_price,
                "tp3": self.tp3_price,
                "side": self.position_side,
                "remaining_qty": self.position_qty,
                "entry_price": self.entry_price,
                "is_monitoring": True
            })

            dingtalk.report_supervisor_intervention(self.position_qty, new_qty, tps)

        except Exception as e:
            logger.error(f"[TPMonitor] 重新计算 TP 失败: {e}")

    def _check_tp_trigger(self, current_price: float) -> Optional[str]:
        with self._lock:
            side, tp1, tp2, tp3 = self.position_side, self.tp1_price, self.tp2_price, self.tp3_price

        if side == "LONG":
            if tp3 and current_price >= tp3:
                return "TP3"
            if tp2 and current_price >= tp2:
                return "TP2"
            if tp1 and current_price >= tp1:
                return "TP1"
        else:
            if tp3 and current_price <= tp3:
                return "TP3"
            if tp2 and current_price <= tp2:
                return "TP2"
            if tp1 and current_price <= tp1:
                return "TP1"
        return None

    def _handle_tp_trigger(self, level: str, current_price: float):
        try:
            if level in ["TP1", "TP2"]:
                success, real_pnl = self.executor.partial_close(0.40, f"{level} 触发")
                if success:
                    self._move_tp3_after_partial(current_price)
                    dingtalk.report_supervisor_tp_trigger(level, current_price, real_pnl,
                                                          "已落袋40%并强制移动 TP3 建立防线。")

            elif level == "TP3":
                success, real_pnl = self.executor.partial_close(0.20, f"{level} 触发")
                if success:
                    self.clear_tp_levels()
                    dingtalk.report_supervisor_tp_trigger(level, current_price, real_pnl,
                                                          "最终防线到达，本轮交易闭环完成，监控层休眠。")

        except Exception as e:
            logger.error(f"[TPMonitor] 处理 {level} 失败: {e}")

    def _move_tp3_after_partial(self, current_price: float):
        try:
            atr = self.client.get_atr("ETHUSDT", "3h", 50, 14) or 22.0
            new_tp3 = round(current_price + atr * 2.3, 2) if self.position_side == "LONG" else round(current_price - atr * 2.3, 2)

            with self._lock:
                self.tp3_price = new_tp3

            state_manager.save_state({
                "tp1": self.tp1_price,
                "tp2": self.tp2_price,
                "tp3": new_tp3,
                "side": self.position_side,
                "remaining_qty": round(self.position_qty * 0.2, 3),
                "entry_price": self.entry_price,
                "is_monitoring": True
            })
        except Exception as e:
            logger.error(f"[TPMonitor] 移动 TP3 失败: {e}")


tp_monitor = TPMonitor()
