# tp_monitor.py（优化版 - 配合 1.0/2.0/3.0 ATR 倍数）
import time
import logging
from binance_client import binance_client
from position_manager import position_manager

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')

class TPMonitor:
    def __init__(self):
        self.running = False
        self.breakeven_triggered = False

    def start(self):
        if self.running:
            logging.warning("[TPMonitor] 已经在运行中")
            return
        self.running = True
        logging.info("[TPMonitor] TP监控已启动（每3秒检查一次）")
        while self.running:
            try:
                self._check_tp()
                time.sleep(3)
            except Exception as e:
                logging.error(f"[TPMonitor] 循环异常: {e}")
                time.sleep(5)

    def stop(self):
        self.running = False
        logging.info("[TPMonitor] TP监控已停止")

    def _check_tp(self):
        position = position_manager.get_position()
        if not position:
            self.breakeven_triggered = False
            return

        symbol = position.get("symbol", "ETHUSDT")
        side = position.get("side")
        entry_price = float(position.get("avg_price", 0))
        tp1 = position.get("tp1")
        tp2 = position.get("tp2")
        tp3 = position.get("tp3")

        if not tp1 or not tp2 or not tp3:
            return

        current_price = self._get_current_price(symbol)
        if not current_price:
            return

        is_long = side == "LONG"

        # ==================== TP1 触发（40%） ====================
        if not self.breakeven_triggered:
            if (is_long and current_price >= tp1) or (not is_long and current_price <= tp1):
                logging.info(f"[TP1触发] 价格到达 {tp1}，平仓 40%")
                binance_client.close_partial_position(symbol, position.get("qty", 0) * 0.4)
                
                # 移动到保本（带缓冲）
                buffer = (tp1 - entry_price) * 0.45   # 使用 0.45 倍 TP1 距离作为缓冲
                new_sl = entry_price + buffer if is_long else entry_price - buffer
                
                position_manager.update_position(
                    side=side,
                    symbol=symbol,
                    qty=position.get("qty", 0) * 0.6,   # 剩余60%
                    avg_price=entry_price,
                    tp1=None,   # TP1 已触发
                    tp2=tp2,
                    tp3=tp3,
                    stop_loss=round(new_sl, 2)
                )
                self.breakeven_triggered = True
                logging.info(f"[保本已设置] 新止损价: {round(new_sl, 2)}")

        # ==================== TP2 触发（40%） ====================
        if self.breakeven_triggered and tp2:
            if (is_long and current_price >= tp2) or (not is_long and current_price <= tp2):
                logging.info(f"[TP2触发] 价格到达 {tp2}，平仓 40%")
                binance_client.close_partial_position(symbol, position.get("qty", 0) * 0.4)
                
                position_manager.update_position(
                    side=side,
                    symbol=symbol,
                    qty=position.get("qty", 0) * 0.2,   # 剩余20%
                    avg_price=entry_price,
                    tp1=None,
                    tp2=None,
                    tp3=tp3,
                    stop_loss=position.get("stop_loss")
                )

        # ==================== TP3 触发（剩余20%） ====================
        if tp3:
            if (is_long and current_price >= tp3) or (not is_long and current_price <= tp3):
                logging.info(f"[TP3触发] 价格到达 {tp3}，平仓剩余 20%")
                binance_client.close_all_positions(symbol)
                position_manager.clear_position()
                self.breakeven_triggered = False

    def _get_current_price(self, symbol):
        try:
            ticker = binance_client.client.futures_symbol_ticker(symbol=symbol)
            return float(ticker['price'])
        except Exception as e:
            logging.error(f"[获取当前价失败] {e}")
            return None


# 全局实例
tp_monitor = TPMonitor()
