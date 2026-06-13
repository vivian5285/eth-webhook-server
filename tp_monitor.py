# tp_monitor.py（完整更新版 - 40-40-20 + TP1后自动保本）
import time
import logging
import threading
from datetime import datetime
from binance_client import binance_client
from position_manager import position_manager
from position_supervisor import supervisor

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')


class TPMonitor:
    def __init__(self):
        self.binance_client = binance_client
        self.position_manager = position_manager
        self.supervisor = supervisor
        self.running = False
        self.thread = None

    def start(self):
        if self.running:
            logging.warning("[TP监控] 已在运行中")
            return

        self.running = True
        self.thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self.thread.start()
        logging.info("[TP监控] 已启动（4H适配 + 40-40-20分仓 + TP1后自动保本）")

    def stop(self):
        self.running = False
        if self.thread:
            self.thread.join(timeout=5)
        logging.info("[TP监控] 已停止")

    def _get_current_price(self, symbol="ETHUSDT"):
        try:
            ticker = self.binance_client.client.futures_symbol_ticker(symbol=symbol)
            return float(ticker['price'])
        except Exception as e:
            logging.error(f"[获取价格失败] {e}")
            return None

    def _monitor_loop(self):
        while self.running:
            try:
                position = self.position_manager.get_position()
                if not position or position.get("qty", 0) <= 0:
                    time.sleep(6)
                    continue

                symbol = position.get("symbol", "ETHUSDT")
                side = position.get("side")
                entry_price = position.get("avg_price")
                remaining_qty = position.get("qty", 0)
                current_price = self._get_current_price(symbol)

                if current_price is None:
                    time.sleep(6)
                    continue

                # ==================== TP1：平40% + 自动设置保本止损 ====================
                if position.get("tp1") and not position.get("tp1_hit"):
                    hit_tp1 = (side == "LONG" and current_price >= position["tp1"]) or \
                              (side == "SHORT" and current_price <= position["tp1"])

                    if hit_tp1:
                        close_qty = round(remaining_qty * 0.40, 3)
                        result = self.binance_client.close_partial_position(symbol, close_qty)

                        if result.get("status") == "success":
                            position["tp1_hit"] = True
                            remaining_qty = remaining_qty - close_qty

                            # === 自动设置保本止损（固定缓冲10美金） ===
                            buffer = 10
                            if side == "LONG":
                                breakeven_sl = entry_price + buffer
                            else:
                                breakeven_sl = entry_price - buffer

                            position["stop_loss"] = breakeven_sl
                            position["qty"] = remaining_qty

                            self.position_manager.update_position(
                                side=side,
                                symbol=symbol,
                                qty=remaining_qty,
                                avg_price=entry_price,
                                tp1=position.get("tp1"),
                                tp2=position.get("tp2"),
                                tp3=position.get("tp3"),
                                stop_loss=breakeven_sl
                            )

                            self.supervisor.notify_tp_hit("1", close_qty, current_price)
                            logging.info(f"[TP1] 平40%成功，剩余仓位止损已移至保本价 {breakeven_sl}")

                # ==================== TP2：平40% ====================
                if position.get("tp2") and position.get("tp1_hit") and not position.get("tp2_hit"):
                    hit_tp2 = (side == "LONG" and current_price >= position["tp2"]) or \
                              (side == "SHORT" and current_price <= position["tp2"])

                    if hit_tp2:
                        close_qty = round(remaining_qty * 0.40, 3)
                        result = self.binance_client.close_partial_position(symbol, close_qty)
                        if result.get("status") == "success":
                            position["tp2_hit"] = True
                            remaining_qty = remaining_qty - close_qty
                            position["qty"] = remaining_qty

                            self.position_manager.update_position(
                                side=side, symbol=symbol, qty=remaining_qty,
                                avg_price=entry_price,
                                tp1=position.get("tp1"),
                                tp2=position.get("tp2"),
                                tp3=position.get("tp3"),
                                stop_loss=position.get("stop_loss")
                            )
                            self.supervisor.notify_tp_hit("2", close_qty, current_price)
                            logging.info(f"[TP2] 平40%成功")

                # ==================== TP3：平剩余20% ====================
                if position.get("tp3") and position.get("tp2_hit"):
                    hit_tp3 = (side == "LONG" and current_price >= position["tp3"]) or \
                              (side == "SHORT" and current_price <= position["tp3"])

                    if hit_tp3:
                        result = self.binance_client.close_partial_position(symbol, remaining_qty)
                        if result.get("status") == "success":
                            self.position_manager.clear_position()
                            self.supervisor.notify_tp_hit("3", remaining_qty, current_price)
                            logging.info("[TP3] 剩余20%已平")

                # ==================== 检查保本止损 ====================
                if position.get("stop_loss"):
                    sl_price = position["stop_loss"]
                    hit_sl = (side == "LONG" and current_price <= sl_price) or \
                             (side == "SHORT" and current_price >= sl_price)

                    if hit_sl:
                        self.binance_client.close_partial_position(symbol, remaining_qty)
                        self.position_manager.clear_position()
                        self.supervisor.notify_close_all("breakeven_after_tp1")
                        logging.info(f"[保本止损] 价格触及 {sl_price}，已平掉剩余仓位")

            except Exception as e:
                logging.error(f"[TP监控异常] {e}", exc_info=True)

            time.sleep(6)


# 全局实例
tp_monitor = TPMonitor()
