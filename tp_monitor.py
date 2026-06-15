#!/usr/bin/env python3
# tp_monitor.py（完整最终版 - 2026-06-15）
# 包含：状态持久化 + 人工干预自动检测与纠正 + 部分平仓后移动止盈
import logging
import time
import threading
from typing import Optional
from binance_client import binance_client
from order_executor import order_executor
from position_manager import position_manager
from dingtalk import report_anomaly, send_dingtalk_message
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

        # 当前监控状态
        self.tp1_price = self.tp2_price = self.tp3_price = None
        self.position_side = None
        self.position_qty = 0.0
        self.entry_price = 0.0
        self.is_monitoring = False

        # 启动时尝试从 state.json 恢复
        self._restore_from_state()

        logger.info("[TPMonitor] 完整最终版初始化完成（A+B+C 全功能）")

    # ==================== 状态持久化 ====================

    def _restore_from_state(self):
        """启动时从 state.json 恢复 TP 状态"""
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
            logger.info(f"[TPMonitor] 从状态文件恢复监控: {state}")

    def set_tp_levels(self, tp1: float, tp2: float, tp3: float, side: str, qty: float, entry_price: float = 0):
        with self._lock:
            self.tp1_price = tp1
            self.tp2_price = tp2
            self.tp3_price = tp3
            self.position_side = side
            self.position_qty = qty
            self.entry_price = entry_price or self.position_manager.get_position().get("entryPrice", 0)
            self.is_monitoring = True

            state_manager.save_state({
                "tp1": tp1, "tp2": tp2, "tp3": tp3,
                "side": side,
                "remaining_qty": qty,
                "entry_price": self.entry_price,
                "is_monitoring": True
            })
        logger.info(f"[TPMonitor] TP 已设置并持久化 | TP1={tp1} TP2={tp2} TP3={tp3}")

    def clear_tp_levels(self):
        with self._lock:
            self.tp1_price = self.tp2_price = self.tp3_price = None
            self.position_side = None
            self.position_qty = 0.0
            self.entry_price = 0.0
            self.is_monitoring = False
        state_manager.clear_state()
        logger.info("[TPMonitor] TP 状态已清空")

    # ==================== 线程控制 ====================

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self._thread.start()
        logger.info("[TPMonitor] 后台监控线程已启动")

    def stop(self):
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5)
        self.clear_tp_levels()
        logger.info("[TPMonitor] 监控线程已停止")

    # ==================== 核心监控循环 ====================

    def _monitor_loop(self):
        while not self._stop_event.is_set():
            try:
                if not self.is_monitoring or not self.position_side:
                    time.sleep(self.check_interval)
                    continue

                # 1. 实盘对账（检测人工干预）
                self._reconcile_position()

                if not self.is_monitoring:
                    time.sleep(self.check_interval)
                    continue

                # 2. 获取当前价格并判断是否触发 TP
                current_price = self.client.get_current_price()
                if current_price <= 0:
                    time.sleep(self.check_interval)
                    continue

                triggered_level = self._check_tp_trigger(current_price)
                if triggered_level:
                    self._handle_tp_trigger(triggered_level, current_price)

            except Exception as e:
                logger.error(f"[TPMonitor] 监控循环异常: {e}", exc_info=True)
                report_anomaly(f"TP 监控异常: {str(e)}")

            time.sleep(self.check_interval)

    # ==================== 人工干预检测 ====================

    def _reconcile_position(self):
        """实盘对账 + 人工干预检测与自动纠正"""
        real_pos = self.position_manager.get_position()
        if not real_pos:
            return

        real_side = self.position_manager.get_position_side()
        real_qty = self.position_manager.get_position_qty()

        # 情况1: 方向完全冲突（人工反向开仓）
        if real_side and real_side != self.position_side:
            logger.warning(f"[TPMonitor] 检测到反向持仓！系统={self.position_side}，实盘={real_side} → 自动平仓纠正")
            send_dingtalk_message(
                f"🚨 【检测到人工反向持仓】\n"
                f"系统方向: {self.position_side} | 实盘方向: {real_side}\n"
                f"系统将自动平掉冲突仓位，以 TV 信号为准。"
            )
            self.executor.close_position("检测到反向持仓，系统自动纠正")
            self.clear_tp_levels()
            return

        # 情况2: 仓位数量发生明显变化（加仓或减仓）
        if real_qty > 0 and abs(real_qty - self.position_qty) > 0.01:
            logger.info(f"[TPMonitor] 检测到仓位数量变化: {self.position_qty} → {real_qty}，自动重新计算 TP")
            self._handle_quantity_change(real_qty)

    def _handle_quantity_change(self, new_qty: float):
        """数量变化后自动重新计算 TP（激进策略）"""
        try:
            current_atr = self.client.get_atr("ETHUSDT", "3h", 50, 14) or 22.0
            current_entry = float(self.position_manager.get_position().get("entryPrice", self.entry_price))

            if self.position_side == "LONG":
                new_tp1 = round(current_entry + current_atr * 1.3, 2)
                new_tp2 = round(current_entry + current_atr * 2.6, 2)
                new_tp3 = round(current_entry + current_atr * 4.2, 2)
            else:
                new_tp1 = round(current_entry - current_atr * 1.3, 2)
                new_tp2 = round(current_entry - current_atr * 2.6, 2)
                new_tp3 = round(current_entry - current_atr * 4.2, 2)

            with self._lock:
                self.position_qty = new_qty
                self.tp1_price = new_tp1
                self.tp2_price = new_tp2
                self.tp3_price = new_tp3

            state_manager.save_state({
                "tp1": new_tp1, "tp2": new_tp2, "tp3": new_tp3,
                "side": self.position_side,
                "remaining_qty": new_qty,
                "entry_price": current_entry,
                "is_monitoring": True
            })

            send_dingtalk_message(
                f"🔄 【仓位变化自动调整 TP】\n"
                f"新数量: {new_qty}\n"
                f"新 TP1={new_tp1} | TP2={new_tp2} | TP3={new_tp3}"
            )

        except Exception as e:
            logger.error(f"[TPMonitor] 重新计算 TP 失败: {e}")

    # ==================== TP 判断与触发 ====================

    def _check_tp_trigger(self, current_price: float) -> Optional[str]:
        with self._lock:
            side = self.position_side
            tp1, tp2, tp3 = self.tp1_price, self.tp2_price, self.tp3_price

        if side == "LONG":
            if tp3 and current_price >= tp3: return "TP3"
            if tp2 and current_price >= tp2: return "TP2"
            if tp1 and current_price >= tp1: return "TP1"
        else:
            if tp3 and current_price <= tp3: return "TP3"
            if tp2 and current_price <= tp2: return "TP2"
            if tp1 and current_price <= tp1: return "TP1"
        return None

    def _handle_tp_trigger(self, level: str, current_price: float):
        try:
            if level == "TP1":
                self.executor.partial_close(0.40, f"{level} 触发")
                self._move_tp3_after_partial(current_price)      # C: 移动止盈

            elif level == "TP2":
                self.executor.partial_close(0.40, f"{level} 触发")
                self._move_tp3_after_partial(current_price)      # C: 移动止盈

            elif level == "TP3":
                self.executor.partial_close(0.20, f"{level} 触发")
                self.clear_tp_levels()

            pnl = self.position_manager.get_unrealized_pnl()
            send_dingtalk_message(f"🎯 【{level} 触发】 当前价 {current_price} | 未实现盈亏 {pnl:+.2f} USDT")

        except Exception as e:
            logger.error(f"[TPMonitor] 处理 {level} 触发失败: {e}")

    # ==================== C: 部分平仓后移动止盈 ====================

    def _move_tp3_after_partial(self, current_price: float):
        """部分平仓后移动 TP3（移动止盈）"""
        try:
            atr = self.client.get_atr("ETHUSDT", "3h", 50, 14) or 22.0

            if self.position_side == "LONG":
                new_tp3 = round(current_price + atr * 2.3, 2)
            else:
                new_tp3 = round(current_price - atr * 2.3, 2)

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

            send_dingtalk_message(f"📈 【TP3 已移动止盈】新 TP3 = {new_tp3}")
            logger.info(f"[TPMonitor] TP3 已移动至 {new_tp3}")

        except Exception as e:
            logger.error(f"[TPMonitor] 移动 TP3 失败: {e}")


# 全局单例
tp_monitor = TPMonitor()
