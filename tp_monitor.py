# tp_monitor.py（最终加强版 - 30/30/40 分批止盈）
import time
import threading
import logging
from binance_client import BinanceClient
from position_manager import PositionManager

class TPMonitor:
    def __init__(self, check_interval: int = 8):
        self.client = BinanceClient()
        self.pm = PositionManager()
        self.check_interval = check_interval
        self.running = False

    def start(self):
        if self.running: return
        self.running = True
        threading.Thread(target=self._run, daemon=True).start()
        logging.info("[TP监控] 后台监控线程已启动")

    def _run(self):
        while self.running:
            try:
                pos = self.pm.get_position()
                if not pos: 
                    time.sleep(self.check_interval)
                    continue

                price = self._get_current_price(pos["symbol"])
                if not price: 
                    time.sleep(self.check_interval)
                    continue

                tp = pos.get("tp_prices", {})
                side = pos.get("side")
                hit = pos.get("tp_hit", [])

                if side == "long":
                    if "tp1" not in hit and price >= tp.get("tp1", 0):
                        self._execute_tp_level("tp1", price, pos, percent=0.30)
                    elif "tp2" not in hit and price >= tp.get("tp2", 0):
                        self._execute_tp_level("tp2", price, pos, percent=0.30)
                    elif "tp3" not in hit and price >= tp.get("tp3", 0):
                        self._execute_tp_level("tp3", price, pos, percent=1.0)  # 最后全平
                else:
                    if "tp1" not in hit and price <= tp.get("tp1", 999999):
                        self._execute_tp_level("tp1", price, pos, percent=0.30)
                    elif "tp2" not in hit and price <= tp.get("tp2", 999999):
                        self._execute_tp_level("tp2", price, pos, percent=0.30)
                    elif "tp3" not in hit and price <= tp.get("tp3", 999999):
                        self._execute_tp_level("tp3", price, pos, percent=1.0)
            except Exception as e:
                logging.error(f"[TP监控异常] {e}")
            time.sleep(self.check_interval)

    def _get_current_price(self, symbol):
        try:
            return float(self.client.client.futures_symbol_ticker(symbol=symbol)["price"])
        except:
            return None

    def _execute_tp_level(self, level: str, price: float, pos: dict, percent: float):
        logging.info(f"[TP触发] {level} @ {price}，准备平 {percent*100}%")
        self.pm.mark_tp_hit(level)

        if percent >= 1.0:
            self.client.close_all_positions(pos["symbol"])
            self.pm.clear_position()
        else:
            result = self.client.close_partial_position(pos["symbol"], percent)
            if result.get("status") == "success":
                logging.info(f"[TP执行] {level} 部分平仓成功")

        # 发送钉钉报表
        try:
            from app import send_tp_hit_report
            report = self.client.get_detailed_report()
            send_tp_hit_report(level, price, report)
        except Exception as e:
            logging.error(f"[TP报表发送失败] {e}")
