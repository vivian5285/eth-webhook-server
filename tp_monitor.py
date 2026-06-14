#!/usr/bin/env python3
# tp_monitor.py（最终版 - 混合模式）

import time
import threading
from typing import Optional

from binance_client import binance_client
from position_manager import position_manager
from position_supervisor import position_supervisor


class TPMonitor:
    def __init__(self):
        self.client = binance_client
        self.pm = position_manager
        self.supervisor = position_supervisor
        self.running = False
        self.thread: Optional[threading.Thread] = None

        # 配置
        self.check_interval = 2.5          # 价格检查频率（秒）
        self.reconcile_interval = 28       # 人工仓位变化检测节流间隔（秒）
        self.last_reconcile_ts = 0

    def start(self):
        if self.running:
            return
        self.running = True
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        print("[TPMonitor] 启动成功（混合模式 - 只监控 TP1/TP2 + 节流检测人工变化）")

    def stop(self):
        self.running = False
        if self.thread:
            self.thread.join(timeout=5)
        print("[TPMonitor] 已停止")

    def _run(self):
        """主循环"""
        while self.running:
            try:
                self._check_and_execute()
                self._throttled_position_check()
            except Exception as e:
                print(f"[TPMonitor] 循环异常: {e}")
            time.sleep(self.check_interval)

    # ==================== 节流式人工仓位变化检测 ====================
    def _throttled_position_check(self):
        """每 28 秒检查一次是否发生人工加减仓"""
        now = time.time()
        if now - self.last_reconcile_ts < self.reconcile_interval:
            return

        self.last_reconcile_ts = now
        self.pm.record_reconcile_time()

        try:
            position = self.client.get_position("ETHUSDT")
            if not position:
                return

            current_qty = float(position.get("positionAmt", 0))
            current_avg_price = float(position.get("entryPrice", 0))

            if self.pm.has_significant_position_change(current_qty):
                print("[TPMonitor] 检测到较大人工仓位变化，触发处理...")
                self.supervisor.handle_manual_position_change(current_qty, current_avg_price)

        except Exception as e:
            print(f"[TPMonitor] 仓位变化检查异常: {e}")

    # ==================== 核心检查与执行 ====================
    def _check_and_execute(self):
        """检查止损、TP1、TP2"""
        pos = self.pm.get_position()
        if not pos:
            return

        current_price = self.client.get_current_price("ETHUSDT")
        if not current_price:
            return

        side = pos["side"]
        qty = pos["qty"]
        stop_loss = pos.get("stop_loss")
        tp1 = pos.get("tp1_price")
        tp2 = pos.get("tp2_price")

        # ========== 最高优先级：止损 ==========
        if stop_loss:
            if (side == "LONG" and current_price <= stop_loss) or \
               (side == "SHORT" and current_price >= stop_loss):
                self._execute_full_close("stop_loss_hit")
                return

        # ========== TP1 ==========
        if tp1 and qty > 0:
            if (side == "LONG" and current_price >= tp1) or \
               (side == "SHORT" and current_price <= tp1):
                self._execute_tp(1, tp1)
                return

        # ========== TP2 ==========
        if tp2 and qty > 0:
            if (side == "LONG" and current_price >= tp2) or \
               (side == "SHORT" and current_price <= tp2):
                self._execute_tp(2, tp2)
                return

    def _execute_tp(self, tp_level: int, target_price: float):
        """执行 TP1 或 TP2 分批平仓"""
        pos = self.pm.get_position()
        if not pos:
            return

        total_qty = pos["qty"]
        close_ratio = 0.4 if tp_level == 1 else 0.4
        close_qty = round(total_qty * close_ratio, 3)

        if close_qty <= 0:
            return

        side = "SELL" if pos["side"] == "LONG" else "BUY"

        try:
            self.client.close_position("ETHUSDT", side, close_qty)

            remaining_qty = max(0, total_qty - close_qty)
            if remaining_qty > 0:
                self.pm.update_position(pos["side"], remaining_qty, pos["avg_price"])
            else:
                self.pm.clear_position()

            self.supervisor.notify_tp_hit(tp_level, close_qty, target_price)

            # TP1 命中后移动止损到保本
            if tp_level == 1 and remaining_qty > 0:
                breakeven = pos["avg_price"]
                self.pm.set_stop_loss(breakeven)
                print(f"[TPMonitor] TP1 命中，已移动止损至保本价: {breakeven}")

        except Exception as e:
            print(f"[TPMonitor] 执行 TP{tp_level} 失败: {e}")

    def _execute_full_close(self, reason: str):
        """全平"""
        try:
            pos = self.pm.get_position()
            if pos and pos["qty"] > 0:
                side = "SELL" if pos["side"] == "LONG" else "BUY"
                self.client.close_position("ETHUSDT", side, pos["qty"])

            self.pm.clear_position()
            self.supervisor.notify_full_close(reason)
            print(f"[TPMonitor] 全平完成，原因: {reason}")
        except Exception as e:
            print(f"[TPMonitor] 全平失败: {e}")


# 全局实例
tp_monitor = TPMonitor()
