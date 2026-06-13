# tp_monitor.py（4H 适配 + 人工干预走监督层完整版）
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

# ==================== 4H 适配 TP 参数 ====================
TP1_MULT = 1.35
TP2_MULT = 2.4
TP3_MULT = 3.3
TRAIL_MULT = 1.3


class TPMonitor:
    def __init__(self, check_interval=6):
        self.check_interval = check_interval
        self.running = False
        self.thread = None
        self.initial_qty = None
        self.last_qty = 0
        self.tp1_done = False
        self.tp2_done = False
        self.trailing_active = False
        self.trailing_stop = None

    def start(self):
        if self.running:
            return
        self.running = True
        self.thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self.thread.start()
        logging.info("[TP监控] 已启动（4H 适配版 + 轻追踪止盈 + 人工干预走监督层）")

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

        # ==================== 人工干预检测（走监督层推送钉钉） ====================
        if self.last_qty > 0 and abs(current_qty - self.last_qty) / self.last_qty > 0.15:
            change_type = "加仓" if current_qty > self.last_qty else "减仓/平仓"
            logging.info(f"[TP监控] 检测到人工{change_type}，通知监督层处理")

            self.initial_qty = current_qty
            self.tp1_done = False
            self.tp2_done = False
            self.trailing_active = False
            self.trailing_stop = None

            if avg_price:
                atr = binance_client._get_atr(symbol) or (avg_price * 0.008)
                new_tp1 = round(avg_price + atr * TP1_MULT if side == "LONG" else avg_price - atr * TP1_MULT, 2)
                new_tp2 = round(avg_price + atr * TP2_MULT if side == "LONG" else avg_price - atr * TP2_MULT, 2)
                new_tp3 = round(avg_price + atr * TP3_MULT if side == "LONG" else avg_price - atr * TP3_MULT, 2)

                position_manager.update_position(side, symbol, current_qty, avg_price, new_tp1, new_tp2, new_tp3)

                # 通知监督层（由监督层核实实盘后推送钉钉）
                supervisor.notify_manual_intervention(
                    change_type=change_type,
                    symbol=symbol,
                    side=side,
                    current_qty=current_qty,
                    new_tp1=new_tp1,
                    new_tp2=new_tp2,
                    new_tp3=new_tp3
                )

        self.last_qty = current_qty
        if self.initial_qty is None:
            self.initial_qty = current_qty

        try:
            ticker = binance_client.client.futures_symbol_ticker(symbol=symbol)
            current_price = float(ticker["price"])
        except Exception as e:
            logging.error(f"[TP监控] 获取价格失败: {e}")
            return

        is_long = side == "LONG"

        # ==================== 追踪止盈逻辑 ====================
        if self.trailing_active and self.trailing_stop is not None:
            if (is_long and current_price <= self.trailing_stop) or (not is_long and current_price >= self.trailing_stop):
                logging.info("[TP监控] 追踪止盈触发 → 全平剩余仓位")
                binance_client.close_all_positions(symbol)
                supervisor.notify_tp_hit("3", current_qty, current_price)
                position_manager.clear_position()
                self._reset_state()
                return

        # ==================== 固定 TP 判断 ====================
        hit_tp3 = (is_long and current_price >= tp3) or (not is_long and current_price <= tp3)
        hit_tp2 = (is_long and current_price >= tp2) or (not is_long and current_price <= tp2)
        hit_tp1 = (is_long and current_price >= tp1) or (not is_long and current_price <= tp1)

        if hit_tp3:
            logging.info("[TP监控] TP3 触发 → 全平")
            binance_client.close_all_positions(symbol)
            supervisor.notify_tp_hit("3", current_qty, current_price)
            position_manager.clear_position()
            self._reset_state()

        elif hit_tp2 and not self.tp2_done:
            target_close = round(self.initial_qty * 0.30, 3)
            self._execute_fixed_qty(target_close, "2", current_price, symbol, is_long)

        elif hit_tp1 and not self.tp1_done:
            target_close = round(self.initial_qty * 0.30, 3)
            self._execute_fixed_qty(target_close, "1", current_price, symbol, is_long)

    def _execute_fixed_qty(self, close_qty, level, current_price, symbol, is_long):
        if close_qty < 0.001:
            return

        result = binance_client.close_partial_position(symbol, close_qty)
        if result.get("status") == "success":
            supervisor.notify_tp_hit(level, close_qty, current_price)

            if level == "1":
                self.tp1_done = True
            elif level == "2":
                self.tp2_done = True
                # TP2 触发后启动追踪止盈
                position = position_manager.get_position()
                if position and position.get("avg_price"):
                    avg_price = position["avg_price"]
                    atr = binance_client._get_atr(symbol) or (avg_price * 0.008)
                    if is_long:
                        self.trailing_stop = current_price - atr * TRAIL_MULT
                    else:
                        self.trailing_stop = current_price + atr * TRAIL_MULT
                    self.trailing_active = True
                    logging.info(f"[TP监控] TP2 已触发，启动追踪止盈，追踪止损价: {self.trailing_stop}")

    def _reset_state(self):
        self.initial_qty = None
        self.last_qty = 0
        self.tp1_done = False
        self.tp2_done = False
        self.trailing_active = False
        self.trailing_stop = None


tp_monitor = TPMonitor(check_interval=6)
