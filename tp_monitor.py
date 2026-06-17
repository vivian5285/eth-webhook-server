#!/usr/bin/env python3
# tp_monitor.py（V2.7 终极修复版 - 解决无限循环与平仓残留死穴）
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
        try:
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
                    logger.info("[TPMonitor] 从持久化状态恢复监控")
        except Exception as e:
            logger.error(f"[TPMonitor] 恢复状态失败: {e}")

    def set_tp_levels(self, tp1: float, tp2: float, tp3: float, side: str, qty: float, entry_price: float = 0):
        try:
            with self._lock:
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
        except Exception as e:
            logger.error(f"[TPMonitor] 设置 TP 水平失败: {e}")

    def clear_tp_levels(self):
        try:
            with self._lock:
                self.tp1_price = self.tp2_price = self.tp3_price = None
                self.position_side = None
                self.position_qty = 0.0
                self.entry_price = 0.0
                self.is_monitoring = False
            state_manager.clear_state()
        except Exception as e:
            logger.error(f"[TPMonitor] 清空 TP 水平失败: {e}")

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self._thread.start()
        logger.info("[TPMonitor] 监控线程已启动")

    def stop(self):
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5)
        self.clear_tp_levels()
        logger.info("[TPMonitor] 监控已停止")

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
                logger.error(f"[TPMonitor] 主循环异常: {e}", exc_info=True)
                time.sleep(self.check_interval)
            time.sleep(self.check_interval)

    def _reconcile_position(self):
        try:
            real_pos = self.position_manager.get_position()
            if not real_pos:
                return

            real_side = self.position_manager.get_position_side()
            real_qty = self.position_manager.get_position_qty()

            if real_side and real_side != self.position_side:
                dingtalk.report_force_align(real_side, self.position_side)
                self.executor.close_position("监控层检测到反向持仓，强制清空")
                self.clear_tp_levels()
                return

            if real_qty > 0 and abs(real_qty - self.position_qty) > 0.01:
                self._handle_quantity_change(real_qty)

        except Exception as e:
            logger.error(f"[TPMonitor] 持仓核对异常: {e}")

    def _handle_quantity_change(self, new_qty: float):
        try:
            current_atr = self.client.get_atr("ETHUSDT", "1h", 50, 14) or 22.0
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
            logger.error(f"[TPMonitor] 处理数量变化异常: {e}")

    def _check_tp_trigger(self, current_price: float) -> Optional[str]:
        try:
            with self._lock:
                side, tp1, tp2, tp3 = self.position_side, self.tp1_price, self.tp2_price, self.tp3_price

            if side == "LONG":
                if tp3 and current_price >= tp3: return "TP3"
                if tp2 and current_price >= tp2: return "TP2"
                if tp1 and current_price >= tp1: return "TP1"
            else:
                if tp3 and current_price <= tp3: return "TP3"
                if tp2 and current_price <= tp2: return "TP2"
                if tp1 and current_price <= tp1: return "TP1"
            return None
        except Exception as e:
            logger.error(f"[TPMonitor] 检查 TP 触发异常: {e}")
            return None

    def _handle_tp_trigger(self, level: str, current_price: float):
        try:
            if level == "TP1":
                success, real_pnl = self.executor.partial_close(0.40, f"{level} 触发")
                if success:
                    time.sleep(1.5)  # 等待币安接口刷新
                    new_qty = self.position_manager.get_position_qty()
                    with self._lock:
                        self.tp1_price = None  # 销毁 TP1，绝对防止无限循环触发！
                        self.position_qty = new_qty  # 同步自己平仓后的仓位，防止误判为人工干预
                    self._move_tp3_after_partial(current_price)
                    dingtalk.report_supervisor_tp_trigger(level, current_price, real_pnl, "已落袋 40%，TP1 防线完成使命，成功移动 TP3。")

            elif level == "TP2":
                # 数学修复：因为此时剩下的已经是初始的 60%（TP1触发过），要平掉初始的 40%，即需要平掉当前剩余的 2/3 (0.6667)
                success, real_pnl = self.executor.partial_close(0.6667, f"{level} 触发")
                if success:
                    time.sleep(1.5)
                    new_qty = self.position_manager.get_position_qty()
                    with self._lock:
                        self.tp1_price = None # 以防跳空暴涨导致 TP1 没被置空
                        self.tp2_price = None # 销毁 TP2
                        self.position_qty = new_qty
                    self._move_tp3_after_partial(current_price)
                    dingtalk.report_supervisor_tp_trigger(level, current_price, real_pnl, "已落袋 40%，TP2 防线完成使命，成功移动 TP3。")

            elif level == "TP3":
                # 最后一重防线，直接调用全平接口，绝对不留任何仓位残渣！
                success, real_pnl = self.executor.close_position(f"{level} 触发")
                if success:
                    self.clear_tp_levels()
                    dingtalk.report_supervisor_tp_trigger(level, current_price, real_pnl, "最终防线到达，本轮交易闭环全平。")

        except Exception as e:
            logger.error(f"[TPMonitor] 处理 {level} 触发异常: {e}")

    def _move_tp3_after_partial(self, current_price: float):
        try:
            atr = self.client.get_atr("ETHUSDT", "1h", 50, 14) or 22.0
            new_tp3 = round(current_price + atr * 2.3, 2) if self.position_side == "LONG" else round(current_price - atr * 2.3, 2)

            with self._lock:
                self.tp3_price = new_tp3

            state_manager.save_state({
                "tp1": self.tp1_price,
                "tp2": self.tp2_price,
                "tp3": self.tp3_price,
                "side": self.position_side,
                "remaining_qty": self.position_qty,  # 修复：使用已更新的真实内部目标仓位
                "entry_price": self.entry_price,
                "is_monitoring": True
            })
        except Exception as e:
            logger.error(f"[TPMonitor] 移动 TP3 异常: {e}")


tp_monitor = TPMonitor()
