# tp_monitor.py（已加强人工干预应对 - 2026-06-12）
import logging
import time
import threading
from binance_client import BinanceClient
from position_manager import PositionManager
from position_supervisor import supervisor

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')

binance_client = BinanceClient(
    api_key=..., 
    api_secret=...
)
position_manager = PositionManager()


class TPMonitor:
    def __init__(self, check_interval=6):
        self.check_interval = check_interval
        self.running = False
        self.thread = None

    def start(self):
        if self.running:
            return
        self.running = True
        self.thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self.thread.start()
        logging.info("[TP监控] 已启动（已加强人工干预检测）")

    def stop(self):
        self.running = False
        logging.info("[TP监控] 已停止")

    def _monitor_loop(self):
        while self.running:
            try:
                self._reconcile_and_check()
            except Exception as e:
                logging.error(f"[TP监控] 异常: {e}", exc_info=True)
            time.sleep(self.check_interval)

    def _reconcile_and_check(self):
        """先对账实盘，再检查 TP"""
        # 1. 获取实盘最新持仓
        real_position = binance_client.get_current_position("ETHUSDT")

        # 2. 与 position_manager 对账
        stored_position = position_manager.get_position()

        if not real_position:
            # 实盘无仓位
            if stored_position:
                logging.warning("[TP监控] 检测到人工全平或仓位归零，清除本地记录")
                position_manager.clear_position()
            return

        # 实盘有仓位，更新本地记录（以实盘为准）
        if stored_position:
            if abs(stored_position.get("qty", 0) - real_position["qty"]) > 0.001:
                logging.warning(f"[TP监控] 检测到人工加减仓！实盘数量: {real_position['qty']}, 本地记录: {stored_position.get('qty')}")
                position_manager.update_position_qty(real_position["qty"])
        else:
            # 本地无记录但实盘有（可能是手动加仓）
            logging.warning("[TP监控] 检测到可能的手动加仓，已同步实盘仓位")
            position_manager.update_position(
                side=real_position["side"],
                symbol=real_position["symbol"],
                qty=real_position["qty"],
                avg_price=real_position["avg_price"]
            )

        # 3. 继续执行 TP 检查
        self._check_tp_levels(real_position)

    def _check_tp_levels(self, real_position):
        """检查 TP 并执行"""
        tp1 = real_position.get("tp1")
        tp2 = real_position.get("tp2")
        tp3 = real_position.get("tp3")

        if not all([tp1, tp2, tp3]):
            return

        current_price = float(binance_client.client.futures_symbol_ticker(symbol="ETHUSDT")["price"])
        is_long = real_position["side"] == "LONG"
        current_qty = real_position["qty"]

        hit_tp3 = (is_long and current_price >= tp3) or (not is_long and current_price <= tp3)
        hit_tp2 = (is_long and current_price >= tp2) or (not is_long and current_price <= tp2)
        hit_tp1 = (is_long and current_price >= tp1) or (not is_long and current_price <= tp1)

        if hit_tp3:
            logging.info("[TP监控] TP3 触发 → 全平")
            result = binance_client.close_all_positions("ETHUSDT")
            if result.get("status") == "success":
                supervisor.notify_tp_hit("3", current_qty, current_price)
                position_manager.clear_position()

        elif hit_tp2:
            self._partial_close(0.30, "2", current_qty, current_price)

        elif hit_tp1:
            self._partial_close(0.30, "1", current_qty, current_price)

    def _partial_close(self, percent, level, current_qty, current_price):
        close_qty = round(current_qty * percent, 3)
        if close_qty < 0.001:
            return

        result = binance_client.close_partial_position("ETHUSDT", close_qty)
        if result.get("status") == "success":
            logging.info(f"[TP监控] TP{level} 平仓成功: {close_qty} 张")
            supervisor.notify_tp_hit(level, close_qty, current_price)

            new_qty = current_qty - close_qty
            if new_qty > 0.001:
                position_manager.update_position_qty(new_qty)
            else:
                position_manager.clear_position()


# 全局实例
tp_monitor = TPMonitor(check_interval=6)
