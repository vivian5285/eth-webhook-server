# tp_monitor.py（最终版 - 已适配 get_binance_client）
import time
import logging
from binance_client import get_binance_client
from position_manager import position_manager
from position_supervisor import supervisor

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')

binance_client = get_binance_client()


class TPMonitor:
    def __init__(self):
        self.running = False
        self.tp1_hit = False
        self.tp2_hit = False

    def start(self):
        if self.running:
            logging.warning("[TPMonitor] 已经在运行中")
            return
        self.running = True
        logging.info("[TPMonitor] TP监控已启动（最终版）")
        while self.running:
            try:
                self._check_and_execute()
                time.sleep(3)
            except Exception as e:
                logging.error(f"[TPMonitor] 循环异常: {e}", exc_info=True)
                time.sleep(5)

    def stop(self):
        self.running = False

    def _reset_state(self):
        self.tp1_hit = False
        self.tp2_hit = False

    def _check_and_execute(self):
        position = position_manager.get_position()
        if not position:
            self._reset_state()
            return

        symbol = position.get("symbol", "ETHUSDT")
        side = position.get("side")
        entry_price = float(position.get("avg_price", 0))
        tp1 = position.get("tp1")
        tp2 = position.get("tp2")
        tp3 = position.get("tp3")
        stop_loss = position.get("stop_loss")

        if not entry_price:
            return

        current_price = self._get_current_price(symbol)
        if not current_price:
            return

        is_long = side == "LONG"

        # 保本止损（最高优先级）
        if stop_loss is not None:
            if (is_long and current_price <= stop_loss) or (not is_long and current_price >= stop_loss):
                logging.warning(f"🚨 [保本损触发] 现价 {current_price} 击穿止损线 {stop_loss}")
                binance_client.close_all_positions(symbol)
                supervisor.notify_close_all("触发动态保本损")
                position_manager.clear_position()
                self._reset_state()
                return

        # TP1
        if not self.tp1_hit and tp1 is not None:
            if (is_long and current_price >= tp1) or (not is_long and current_price <= tp1):
                closed_qty = self._execute_partial_close(symbol, 0.40)
                if closed_qty > 0:
                    supervisor.notify_tp_hit(level="1", closed_qty=closed_qty, current_price=current_price)
                    buffer = 10.0
                    new_sl = entry_price + buffer if is_long else entry_price - buffer
                    position_manager.update_position(
                        side=side, symbol=symbol,
                        qty=position.get("qty", 0) * 0.6,
                        avg_price=entry_price,
                        tp1=None, tp2=tp2, tp3=tp3,
                        stop_loss=round(new_sl, 2)
                    )
                self.tp1_hit = True

        # TP2
        if self.tp1_hit and not self.tp2_hit and tp2 is not None:
            if (is_long and current_price >= tp2) or (not is_long and current_price <= tp2):
                closed_qty = self._execute_partial_close(symbol, 0.40)
                if closed_qty > 0:
                    supervisor.notify_tp_hit(level="2", closed_qty=closed_qty, current_price=current_price)
                    position_manager.update_position(
                        side=side, symbol=symbol,
                        qty=position.get("qty", 0) * 0.2,
                        avg_price=entry_price,
                        tp1=None, tp2=None, tp3=tp3,
                        stop_loss=position.get("stop_loss")
                    )
                self.tp2_hit = True

        # TP3
        if tp3 is not None:
            if (is_long and current_price >= tp3) or (not is_long and current_price <= tp3):
                binance_client.close_all_positions(symbol)
                supervisor.notify_tp_hit(level="3", closed_qty=position.get("qty", 0), current_price=current_price)
                position_manager.clear_position()
                self._reset_state()

    def _execute_partial_close(self, symbol, percent):
        try:
            real_pos = binance_client.get_current_position(symbol)
            if not real_pos:
                return 0
            current_qty = abs(float(real_pos.get("positionAmt", 0)))
            close_qty = round(current_qty * percent, 3)
            if close_qty < 0.001:
                return 0
            result = binance_client.close_partial_position(symbol, close_qty)
            return close_qty if result.get("status") == "success" else 0
        except Exception as e:
            logging.error(f"[部分平仓失败] {e}")
            return 0

    def _get_current_price(self, symbol):
        try:
            ticker = binance_client.client.futures_symbol_ticker(symbol=symbol)
            return float(ticker["price"])
        except:
            return None


tp_monitor = TPMonitor()
