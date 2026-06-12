# tp_monitor.py（最终完整版 - 逻辑已检查优化）
import logging
import time
import threading
import os
from dotenv import load_dotenv
from binance_client import BinanceClient
from position_manager import position_manager
from position_supervisor import supervisor

load_dotenv()

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')

binance_client = BinanceClient(
    api_key=os.getenv("BINANCE_API_KEY"),
    api_secret=os.getenv("BINANCE_API_SECRET")
)


class TPMonitor:
    def __init__(self, check_interval=6):
        self.check_interval = check_interval
        self.running = False
        self.thread = None
        self.initial_qty = None          # 记录初始开仓数量
        self.tp1_done = False
        self.tp2_done = False

    def start(self):
        if self.running:
            return
        self.running = True
        self.thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self.thread.start()
        logging.info("[TP监控] 已启动（30%/30%/40% 模式）")

    def stop(self):
        self.running = False
        logging.info("[TP监控] 已停止")

    def _monitor_loop(self):
        while self.running:
            try:
                self._reconcile_and_check_tp()
            except Exception as e:
                logging.error(f"[TP监控] 异常: {e}", exc_info=True)
            time.sleep(self.check_interval)

    def _reconcile_and_check_tp(self):
        real_position = binance_client.get_current_position("ETHUSDT")
        position_manager.reconcile(real_position)

        position = position_manager.get_position()
        if not position or position.get("qty", 0) <= 0:
            self.initial_qty = None
            self.tp1_done = False
            self.tp2_done = False
            return

        current_qty = position.get("qty", 0)
        symbol = position.get("symbol", "ETHUSDT")
        side = position.get("side")
        tp1 = position.get("tp1")
        tp2 = position.get("tp2")
        tp3 = position.get("tp3")

        if not all([tp1, tp2, tp3]):
            logging.warning("[TP监控] TP价格不完整，跳过检查")
            return

        # 第一次检测到仓位时记录初始数量
        if self.initial_qty is None:
            self.initial_qty = current_qty
            self.tp1_done = False
            self.tp2_done = False
            logging.info(f"[TP监控] 记录初始仓位数量: {self.initial_qty}")

        try:
            ticker = binance_client.client.futures_symbol_ticker(symbol=symbol)
            current_price = float(ticker["price"])
        except Exception as e:
            logging.error(f"[TP监控] 获取价格失败: {e}")
            return

        is_long = side == "LONG"

        hit_tp3 = (is_long and current_price >= tp3) or (not is_long and current_price <= tp3)
        hit_tp2 = (is_long and current_price >= tp2) or (not is_long and current_price <= tp2)
        hit_tp1 = (is_long and current_price >= tp1) or (not is_long and current_price <= tp1)

        if hit_tp3:
            logging.info("[TP监控] TP3 触发 → 全平剩余仓位")
            binance_client.close_all_positions(symbol)
            supervisor.notify_tp_hit("3", current_qty, current_price)
            position_manager.clear_position()
            self._reset_state()

        elif hit_tp2 and not self.tp2_done:
            target_close = round(self.initial_qty * 0.30, 3)
            self._execute_fixed_qty(target_close, "2", current_price, symbol)

        elif hit_tp1 and not self.tp1_done:
            target_close = round(self.initial_qty * 0.30, 3)
            self._execute_fixed_qty(target_close, "1", current_price, symbol)

    def _execute_fixed_qty(self, close_qty, level, current_price, symbol):
        if close_qty < 0.001:
            return

        logging.info(f"[TP监控] TP{level} 触发 → 平 {close_qty} 张（基于初始仓位 30%）")

        result = binance_client.close_partial_position(symbol, close_qty)
        if result.get("status") == "success":
            logging.info(f"[TP监控] TP{level} 平仓成功")
            supervisor.notify_tp_hit(level, close_qty, current_price)

            if level == "1":
                self.tp1_done = True
            elif level == "2":
                self.tp2_done = True

    def _reset_state(self):
        self.initial_qty = None
        self.tp1_done = False
        self.tp2_done = False


# 全局单例
tp_monitor = TPMonitor(check_interval=6)
