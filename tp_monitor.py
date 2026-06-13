# tp_monitor.py（完整最终健壮版 - WebSocket 实时 + 异常保护）
import time
import logging
import threading
from binance import ThreadedWebsocketManager
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
        self.twm = None
        self.current_price = None
        self.symbol = "ETHUSDT"
        self._started = False

    def start(self):
        if self._started:
            return

        try:
            self.running = True
            self.twm = ThreadedWebsocketManager(
                api_key=self.binance_client.api_key,
                api_secret=self.binance_client.api_secret
            )
            self.twm.start()

            self.twm.start_symbol_ticker_socket(
                callback=self._handle_price_update, 
                symbol=self.symbol
            )
            self.twm.start_user_socket(callback=self._handle_account_update)

            threading.Thread(target=self._monitor_loop, daemon=True).start()

            self._started = True
            logging.info("[TP监控] WebSocket 实时模式启动成功")

        except Exception as e:
            logging.error(f"[TP监控启动失败] {e}", exc_info=True)
            self.running = False

    def stop(self):
        self.running = False
        if self.twm:
            try:
                self.twm.stop()
            except:
                pass
        logging.info("[TP监控] 已停止")

    def _handle_price_update(self, msg):
        try:
            if msg.get('c'):
                self.current_price = float(msg['c'])
        except Exception as e:
            logging.error(f"[价格更新异常] {e}")

    def _handle_account_update(self, msg):
        try:
            if msg.get('e') == 'ACCOUNT_UPDATE':
                real_pos = self.binance_client.get_current_position(self.symbol)
                self.position_manager.reconcile(real_pos)
        except Exception as e:
            logging.error(f"[账户更新异常] {e}")

    def _monitor_loop(self):
        while self.running:
            try:
                position = self.position_manager.get_position()
                if not position or position.get("qty", 0) <= 0 or self.current_price is None:
                    time.sleep(2)
                    continue

                side = position.get("side")
                entry_price = position.get("avg_price")
                remaining_qty = position.get("qty", 0)
                current_price = self.current_price

                # ==================== TP1：平 40% + 自动设置保本止损 ====================
                if position.get("tp1") and not position.get("tp1_hit"):
                    hit_tp1 = (side == "LONG" and current_price >= position["tp1"]) or \
                              (side == "SHORT" and current_price <= position["tp1"])

                    if hit_tp1:
                        close_qty = round(remaining_qty * 0.40, 3)
                        result = self.binance_client.close_partial_position(self.symbol, close_qty)

                        if result.get("status") == "success":
                            position["tp1_hit"] = True
                            remaining_qty = remaining_qty - close_qty

                            buffer = 10
                            if side == "LONG":
                                breakeven_sl = entry_price + buffer
                            else:
                                breakeven_sl = entry_price - buffer

                            position["stop_loss"] = breakeven_sl
                            position["qty"] = remaining_qty

                            self.position_manager.update_position(
                                side=side, symbol=self.symbol, qty=remaining_qty,
                                avg_price=entry_price,
                                tp1=position.get("tp1"),
                                tp2=position.get("tp2"),
                                tp3=position.get("tp3"),
                                stop_loss=breakeven_sl
                            )

                            self.supervisor.notify_tp_hit("1", close_qty, current_price)
                            logging.info(f"[TP1] 平40%成功，保本止损已设为 {breakeven_sl}")

                # ==================== TP2：平 40% ====================
                if position.get("tp2") and position.get("tp1_hit") and not position.get("tp2_hit"):
                    hit_tp2 = (side == "LONG" and current_price >= position["tp2"]) or \
                              (side == "SHORT" and current_price <= position["tp2"])

                    if hit_tp2:
                        close_qty = round(remaining_qty * 0.40, 3)
                        result = self.binance_client.close_partial_position(self.symbol, close_qty)
                        if result.get("status") == "success":
                            position["tp2_hit"] = True
                            remaining_qty = remaining_qty - close_qty
                            position["qty"] = remaining_qty

                            self.position_manager.update_position(
                                side=side, symbol=self.symbol, qty=remaining_qty,
                                avg_price=entry_price,
                                tp1=position.get("tp1"),
                                tp2=position.get("tp2"),
                                tp3=position.get("tp3"),
                                stop_loss=position.get("stop_loss")
                            )
                            self.supervisor.notify_tp_hit("2", close_qty, current_price)
                            logging.info("[TP2] 平40%成功")

                # ==================== TP3：平剩余 20% ====================
                if position.get("tp3") and position.get("tp2_hit"):
                    hit_tp3 = (side == "LONG" and current_price >= position["tp3"]) or \
                              (side == "SHORT" and current_price <= position["tp3"])

                    if hit_tp3:
                        result = self.binance_client.close_partial_position(self.symbol, remaining_qty)
                        if result.get("status") == "success":
                            self.position_manager.clear_position()
                            self.supervisor.notify_tp_hit("3", remaining_qty, current_price)
                            logging.info("[TP3] 剩余20%已平")

                # ==================== 保本止损检查 ====================
                if position.get("stop_loss"):
                    sl_price = position["stop_loss"]
                    hit_sl = (side == "LONG" and current_price <= sl_price) or \
                             (side == "SHORT" and current_price >= sl_price)

                    if hit_sl:
                        self.binance_client.close_partial_position(self.symbol, remaining_qty)
                        self.position_manager.clear_position()
                        self.supervisor.notify_close_all("breakeven_after_tp1")
                        logging.info(f"[保本止损] 触及 {sl_price}，已平剩余仓位")

            except Exception as e:
                logging.error(f"[TP监控循环异常] {e}", exc_info=True)

            time.sleep(1)


# 全局实例
tp_monitor = TPMonitor()
