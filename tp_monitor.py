# tp_monitor.py - 最终完整版（含人工干预智能处理 + 监督层报告）

import logging
import time
import threading
from binance_client import BinanceClient
from position_manager import position_manager
from position_supervisor import supervisor

binance_client = BinanceClient()

class TPMonitor:
    def __init__(self):
        self.running = False
        self.thread = None

    def start(self):
        if self.running:
            return
        self.running = True
        self.thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self.thread.start()
        logging.info("[TP监控] 后台监控已启动（支持人工干预检测）")

    def _monitor_loop(self):
        while self.running:
            try:
                self._check_and_execute_tp()
            except Exception as e:
                logging.error(f"[TP监控] 异常: {e}")
            time.sleep(4)

    def _check_and_execute_tp(self):
        # 1. 获取同步前的状态
        state_before = position_manager.get_current_state()

        # 2. 获取交易所实时持仓并同步（内部会处理状态更新）
        real_pos = binance_client.get_current_position("ETHUSDT")
        position_manager.sync_with_exchange(real_pos)

        # 3. 获取同步后的状态
        state_after = position_manager.get_current_state()

        # 4. 检测并处理人工干预
        self._handle_manual_intervention(state_before, state_after, real_pos)

        # 5. 如果没有持仓或没有止盈目标，则跳过TP检查
        if not state_after.get("has_position") or state_after.get("tp1", 0) == 0:
            return

        if not real_pos or real_pos.get("positionAmt", 0) == 0:
            position_manager.clear_position()
            return

        # 6. 执行止盈检查
        current_price = float(binance_client.client.futures_symbol_ticker(symbol="ETHUSDT")["price"])
        is_long = real_pos["side"] == "long"
        remaining_qty = abs(real_pos["positionAmt"])

        tp1 = state_after["tp1"]
        tp2 = state_after["tp2"]
        tp3 = state_after["tp3"]

        # 多单逻辑
        if is_long:
            if current_price >= tp3:
                binance_client.close_all_positions("ETHUSDT")
                supervisor.notify_tp_hit("tp3", remaining_qty, 0)
                position_manager.clear_position()
            elif current_price >= tp2:
                binance_client.close_partial_position("ETHUSDT", 0.5)
                supervisor.notify_tp_hit("tp2", remaining_qty * 0.5, remaining_qty * 0.5)
            elif current_price >= tp1:
                binance_client.close_partial_position("ETHUSDT", 0.3)
                supervisor.notify_tp_hit("tp1", remaining_qty * 0.3, remaining_qty * 0.7)

        # 空单逻辑
        else:
            if current_price <= tp3:
                binance_client.close_all_positions("ETHUSDT")
                supervisor.notify_tp_hit("tp3", remaining_qty, 0)
                position_manager.clear_position()
            elif current_price <= tp2:
                binance_client.close_partial_position("ETHUSDT", 0.5)
                supervisor.notify_tp_hit("tp2", remaining_qty * 0.5, remaining_qty * 0.5)
            elif current_price <= tp1:
                binance_client.close_partial_position("ETHUSDT", 0.3)
                supervisor.notify_tp_hit("tp1", remaining_qty * 0.3, remaining_qty * 0.7)

    def _handle_manual_intervention(self, state_before: dict, state_after: dict, real_pos: dict):
        """检测并处理人工干预"""
        before_has = state_before.get("has_position", False)
        after_has = state_after.get("has_position", False)

        before_qty = state_before.get("qty", 0)
        after_qty = state_after.get("qty", 0)

        # 情况1: 从有仓位 → 无仓位（手动全平）
        if before_has and not after_has:
            supervisor.notify_manual_close()
            return

        # 情况2: 仓位数量发生明显变化（加仓或减仓）
        if before_has and after_has and abs(before_qty - after_qty) > 0.001:
            action = "add" if after_qty > before_qty else "reduce"
            entry_price = state_after.get("entry_price", 0)
            supervisor.notify_manual_position_change(action, before_qty, after_qty, entry_price)

    def stop(self):
        self.running = False
        logging.info("[TP监控] 已停止")


# 全局实例
tp_monitor = TPMonitor()
