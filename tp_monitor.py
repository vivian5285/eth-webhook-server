# tp_monitor.py（最终完整版 - 适配收紧TP - 2026-06-12）
import logging
import time
import threading
from binance_client import BinanceClient
from position_manager import PositionManager
from position_supervisor import supervisor

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')

binance_client = BinanceClient(
    api_key=...,      # 从环境变量或 config 读取
    api_secret=...
)
position_manager = PositionManager()


class TPMonitor:
    def __init__(self, check_interval=5):
        self.check_interval = check_interval  # 检查间隔（秒）
        self.running = False
        self.thread = None

    def start(self):
        if self.running:
            logging.warning("[TP监控] 已经在运行中")
            return

        self.running = True
        self.thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self.thread.start()
        logging.info("[TP监控] 已启动")

    def stop(self):
        self.running = False
        if self.thread:
            self.thread.join(timeout=5)
        logging.info("[TP监控] 已停止")

    def _monitor_loop(self):
        while self.running:
            try:
                self.check_and_execute_partial_tp()
            except Exception as e:
                logging.error(f"[TP监控] 循环异常: {e}", exc_info=True)
            time.sleep(self.check_interval)

    def check_and_execute_partial_tp(self):
        """检查并执行分批止盈"""
        position = position_manager.get_position()
        if not position or position.get("qty", 0) <= 0:
            return  # 无持仓则跳过

        symbol = position.get("symbol", "ETHUSDT")
        side = position.get("side")  # LONG 或 SHORT
        current_qty = position.get("qty", 0)
        entry_price = position.get("avg_price", 0)
        tp1 = position.get("tp1")
        tp2 = position.get("tp2")
        tp3 = position.get("tp3")

        if not all([tp1, tp2, tp3]):
            logging.warning("[TP监控] TP价格不完整，跳过检查")
            return

        # 获取当前最新价格
        try:
            ticker = binance_client.client.futures_symbol_ticker(symbol=symbol)
            current_price = float(ticker["price"])
        except Exception as e:
            logging.error(f"[TP监控] 获取价格失败: {e}")
            return

        is_long = side == "LONG"

        # 判断是否触发 TP
        hit_tp1 = (is_long and current_price >= tp1) or (not is_long and current_price <= tp1)
        hit_tp2 = (is_long and current_price >= tp2) or (not is_long and current_price <= tp2)
        hit_tp3 = (is_long and current_price >= tp3) or (not is_long and current_price <= tp3)

        if hit_tp3:
            logging.info(f"[TP监控] TP3 触发 → 全平剩余仓位")
            result = binance_client.close_all_positions(symbol)
            if result.get("status") == "success":
                supervisor.notify_tp_hit(level="3", closed_qty=current_qty, avg_price=current_price)
                position_manager.clear_position()
            return

        elif hit_tp2:
            close_percent = 0.30
            logging.info(f"[TP监控] TP2 触发 → 平 {close_percent*100}%")
            self._execute_partial_close(symbol, current_qty, close_percent, "2", current_price)

        elif hit_tp1:
            close_percent = 0.30
            logging.info(f"[TP监控] TP1 触发 → 平 {close_percent*100}%")
            self._execute_partial_close(symbol, current_qty, close_percent, "1", current_price)

    def _execute_partial_close(self, symbol, current_qty, close_percent, level, current_price):
        """执行部分平仓"""
        close_qty = round(current_qty * close_percent, 3)
        if close_qty < 0.001:
            logging.warning(f"[TP监控] 平仓数量过小，跳过")
            return

        try:
            result = binance_client.close_partial_position(symbol, close_qty)
            if result.get("status") == "success":
                logging.info(f"[TP监控] TP{level} 平仓成功: {close_qty} 张")
                supervisor.notify_tp_hit(level=level, closed_qty=close_qty, avg_price=current_price)

                # 更新剩余仓位到 position_manager
                new_qty = current_qty - close_qty
                if new_qty > 0.001:
                    position_manager.update_position_qty(new_qty)
                else:
                    position_manager.clear_position()
            else:
                logging.error(f"[TP监控] TP{level} 平仓失败: {result}")

        except Exception as e:
            logging.error(f"[TP监控] 执行部分平仓异常: {e}", exc_info=True)


# ==================== 全局实例 ====================
tp_monitor = TPMonitor(check_interval=5)
