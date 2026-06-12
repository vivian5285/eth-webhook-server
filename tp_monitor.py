# tp_monitor.py - 最终稳定版（经典30/30/40分批止盈）

import logging
import time
import threading
from binance_client import BinanceClient
from position_manager import PositionManager
from position_supervisor import supervisor

binance_client = BinanceClient()
position_manager = PositionManager()

class TPMonitor:
    def __init__(self):
        self.running = False
        self.thread = None

    def start(self):
        if self.running: return
        self.running = True
        self.thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self.thread.start()
        logging.info("[TP监控] 后台监控已启动")

    def set_tp_levels(self, tp1, tp2, tp3, entry_price, is_long):
        position_manager.set_tp_levels(tp1, tp2, tp3)

    def reset_tp(self):
        position_manager.set_tp_levels(0, 0, 0)

    def _monitor_loop(self):
        while self.running:
            try:
                self._check_and_execute_tp()
            except Exception as e:
                logging.error(f"[TP监控] 异常: {e}")
            time.sleep(4)

    def _check_and_execute_tp(self):
        state_before = position_manager.get_current_state()
        real_pos = binance_client.get_current_position("ETHUSDT")
        position_manager.sync_with_exchange(real_pos)
        state_after = position_manager.get_current_state()

        self._handle_manual_intervention(state_before, state_after, real_pos)

        if not state_after.get("has_position") or state_after.get("tp1", 0) == 0:
            return
        if not real_pos or real_pos.get("positionAmt", 0) == 0:
            position_manager.clear_position()
            return

        current_price = float(binance_client.client.futures_symbol_ticker(symbol="ETHUSDT")["price"])
        is_long = real_pos["side"] == "long"
        remaining_qty = abs(real_pos["positionAmt"])
        initial_qty = state_after.get("initial_qty", state_after.get("qty", remaining_qty))

        tp1, tp2, tp3 = state_after["tp1"], state_after["tp2"], state_after["tp3"]

        if is_long:
            if current_price >= tp3:
                binance_client.close_all_positions("ETHUSDT")
                supervisor.notify_tp_hit("tp3", remaining_qty, 0)
                position_manager.clear_position()
            elif current_price >= tp2:
                close_qty = min(initial_qty * 0.30, remaining_qty)
                if close_qty > 0.001:
                    binance_client.close_partial_position("ETHUSDT", close_qty / remaining_qty)
                    supervisor.notify_tp_hit("tp2", close_qty, remaining_qty - close_qty)
            elif current_price >= tp1:
                close_qty = min(initial_qty * 0.30, remaining_qty)
                if close_qty > 0.001:
                    binance_client.close_partial_position("ETHUSDT", close_qty / remaining_qty)
                    supervisor.notify_tp_hit("tp1", close_qty, remaining_qty - close_qty)
        else:
            if current_price <= tp3:
                binance_client.close_all_positions("ETHUSDT")
                supervisor.notify_tp_hit("tp3", remaining_qty, 0)
                position_manager.clear_position()
            elif current_price <= tp2:
                close_qty = min(initial_qty * 0.30, remaining_qty)
                if close_qty > 0.001:
                    binance_client.close_partial_position("ETHUSDT", close_qty / remaining_qty)
                    supervisor.notify_tp_hit("tp2", close_qty, remaining_qty - close_qty)
            elif current_price <= tp1:
                close_qty = min(initial_qty * 0.30, remaining_qty)
                if close_qty > 0.001:
                    binance_client.close_partial_position("ETHUSDT", close_qty / remaining_qty)
                    supervisor.notify_tp_hit("tp1", close_qty, remaining_qty - close_qty)

    def _handle_manual_intervention(self, state_before, state_after, real_pos):
        before_has = state_before.get("has_position", False)
        after_has = state_after.get("has_position", False)
        before_qty = state_before.get("qty", 0)
        after_qty = state_after.get("qty", 0)

        if before_has and not after_has:
            supervisor.notify_manual_close()
            return
        if before_has and after_has and abs(before_qty - after_qty) > 0.001:
            action = "add" if after_qty > before_qty else "reduce"
            supervisor.notify_manual_position_change(action, before_qty, after_qty, state_after.get("entry_price", 0))

    def stop(self):
        self.running = False


tp_monitor = TPMonitor()
