# tp_monitor.py（最终加强版 - 适配激进 reconcile + 人工干预）
import time
import logging
from binance_client import binance_client
from position_manager import position_manager

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')


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
        logging.info("[TPMonitor] TP监控已启动（最终加强版）")
        while self.running:
            try:
                self._check_and_execute()
                time.sleep(3)
            except Exception as e:
                logging.error(f"[TPMonitor] 循环异常: {e}", exc_info=True)
                time.sleep(5)

    def stop(self):
        self.running = False
        logging.info("[TPMonitor] TP监控已停止")

    def _reset_state(self):
        """重置 TP 执行状态"""
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
        current_sl = position.get("stop_loss")

        if not entry_price:
            return

        current_price = self._get_current_price(symbol)
        if not current_price:
            return

        is_long = side == "LONG"

        # ==================== TP1 执行（40%） ====================
        if not self.tp1_hit and tp1 is not None:
            if (is_long and current_price >= tp1) or (not is_long and current_price <= tp1):
                logging.info(f"[TP1触发] 价格到达 {tp1}，准备平仓 40%")
                self._execute_partial_close(symbol, 0.40, "TP1")

                # 设置保本止损（带缓冲）
                if entry_price > 0:
                    buffer = abs(tp1 - entry_price) * 0.40   # 缓冲系数 0.40
                    new_sl = entry_price + buffer if is_long else entry_price - buffer

                    position_manager.update_position(
                        side=side,
                        symbol=symbol,
                        qty=position.get("qty", 0) * 0.6,
                        avg_price=entry_price,
                        tp1=None,           # TP1 已执行
                        tp2=tp2,
                        tp3=tp3,
                        stop_loss=round(new_sl, 2)
                    )
                    logging.info(f"[保本已设置] 新止损价: {round(new_sl, 2)}")

                self.tp1_hit = True

        # ==================== TP2 执行（40%） ====================
        if self.tp1_hit and not self.tp2_hit and tp2 is not None:
            if (is_long and current_price >= tp2) or (not is_long and current_price <= tp2):
                logging.info(f"[TP2触发] 价格到达 {tp2}，准备平仓 40%")
                self._execute_partial_close(symbol, 0.40, "TP2")

                position_manager.update_position(
                    side=side,
                    symbol=symbol,
                    qty=position.get("qty", 0) * 0.2,
                    avg_price=entry_price,
                    tp1=None,
                    tp2=None,
                    tp3=tp3,
                    stop_loss=current_sl
                )
                self.tp2_hit = True

        # ==================== TP3 执行（剩余20%） ====================
        if tp3 is not None:
            if (is_long and current_price >= tp3) or (not is_long and current_price <= tp3):
                logging.info(f"[TP3触发] 价格到达 {tp3}，平仓剩余仓位")
                binance_client.close_all_positions(symbol)
                position_manager.clear_position()
                self._reset_state()

    def _execute_partial_close(self, symbol, percent, reason):
        """安全执行部分平仓"""
        try:
            real_pos = binance_client.get_current_position(symbol)
            if not real_pos:
                logging.warning(f"[{reason}] 实盘已无持仓，跳过平仓")
                return

            current_qty = abs(float(real_pos.get("positionAmt", 0)))
            close_qty = round(current_qty * percent, 3)

            if close_qty < 0.001:
                logging.warning(f"[{reason}] 计算平仓数量过小 ({close_qty})，跳过")
                return

            result = binance_client.close_partial_position(symbol, close_qty)
            logging.info(f"[{reason}] 已执行部分平仓 {close_qty} 张，结果: {result.get('status')}")
        except Exception as e:
            logging.error(f"[{reason}] 部分平仓失败: {e}")

    def _get_current_price(self, symbol):
        try:
            ticker = binance_client.client.futures_symbol_ticker(symbol=symbol)
            return float(ticker["price"])
        except Exception as e:
            logging.error(f"[获取当前价失败] {e}")
            return None


# 全局实例
tp_monitor = TPMonitor()
