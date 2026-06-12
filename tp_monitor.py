# tp_monitor.py（加强人工干预版 - 推荐使用）
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
        self.initial_qty = None
        self.last_qty = 0
        self.tp1_done = False
        self.tp2_done = False

    def start(self):
        if self.running:
            return
        self.running = True
        self.thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self.thread.start()
        logging.info("[TP监控] 已启动（支持人工干预自动更新）")

    def stop(self):
        self.running = False

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
            self._reset_state()
            return

        current_qty = position.get("qty", 0)
        symbol = position.get("symbol", "ETHUSDT")
        side = position.get("side")
        avg_price = position.get("avg_price")
        tp1 = position.get("tp1")
        tp2 = position.get("tp2")
        tp3 = position.get("tp3")

        # 检测人工干预（数量显著变化）
        if self.last_qty > 0 and abs(current_qty - self.last_qty) / self.last_qty > 0.15:
            logging.info(f"[TP监控] 检测到人工干预，仓位变化超过15%，重新计算 TP")
            self.initial_qty = current_qty
            self.tp1_done = False
            self.tp2_done = False
            # 重新计算 TP（基于新均价）
            if avg_price:
                atr = binance_client._get_atr(symbol) or (avg_price * 0.008)
                new_tp1 = round(avg_price + atr * 1.05 if side == "LONG" else avg_price - atr * 1.05, 2)
                new_tp2 = round(avg_price + atr * 1.85 if side == "LONG" else avg_price - atr * 1.85, 2)
                new_tp3 = round(avg_price + atr * 2.55 if side == "LONG" else avg_price - atr * 2.55, 2)
                position_manager.update_position(side, symbol, current_qty, avg_price, new_tp1, new_tp2, new_tp3)
                logging.info(f"[TP监控] 已根据新仓位重新计算 TP: {new_tp1} / {new_tp2} / {new_tp3}")

        self.last_qty = current_qty

        if self.initial_qty is None:
            self.initial_qty = current_qty

        # 获取当前价格并判断是否触发 TP
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
        result = binance_client.close_partial_position(symbol, close_qty)
        if result.get("status") == "success":
            supervisor.notify_tp_hit(level, close_qty, current_price)
            if level == "1":
                self.tp1_done = True
            elif level == "2":
                self.tp2_done = True

    def _reset_state(self):
        self.initial_qty = None
        self.last_qty = 0
        self.tp1_done = False
        self.tp2_done = False


tp_monitor = TPMonitor(check_interval=6)
