# tp_monitor.py（最终更新版 - 适配监督层）
import logging
import time
from binance import ThreadedWebsocketManager
from binance_client import BinanceClient
from position_manager import PositionManager
from position_supervisor import supervisor   # ← 引入监督层

binance_client = BinanceClient()
position_manager = PositionManager()

class TPMonitor:
    def __init__(self):
        self.twm = ThreadedWebsocketManager(api_key=binance_client.api_key, 
                                            api_secret=binance_client.api_secret)
        self.symbol = "ETHUSDT"
        self.is_running = False

    def start(self):
        if self.is_running:
            return
        self.twm.start()
        self.twm.start_kline_socket(
            callback=self._on_kline_message,
            symbol=self.symbol,
            interval='1m'   # 1分钟K线足够用于TP监控
        )
        self.is_running = True
        logging.info("[TP监控] WebSocket 监控已启动")

    def _on_kline_message(self, msg):
        try:
            if msg.get('e') != 'kline':
                return

            kline = msg['k']
            close_price = float(kline['c'])

            current_pos = position_manager.get_current_position()
            if not current_pos:
                return

            side = current_pos.get("side")
            tp_levels = current_pos.get("tp_levels", {})

            if not tp_levels:
                return

            # 检查是否触发 TP
            triggered = False
            hit_level = None

            if side == "long":
                if close_price >= tp_levels.get("tp1", 0) and not current_pos.get("tp_hit", {}).get("tp1"):
                    hit_level = "tp1"
                    triggered = True
                elif close_price >= tp_levels.get("tp2", 0) and not current_pos.get("tp_hit", {}).get("tp2"):
                    hit_level = "tp2"
                    triggered = True
                elif close_price >= tp_levels.get("tp3", 0) and not current_pos.get("tp_hit", {}).get("tp3"):
                    hit_level = "tp3"
                    triggered = True

            elif side == "short":
                if close_price <= tp_levels.get("tp1", 999999) and not current_pos.get("tp_hit", {}).get("tp1"):
                    hit_level = "tp1"
                    triggered = True
                elif close_price <= tp_levels.get("tp2", 999999) and not current_pos.get("tp_hit", {}).get("tp2"):
                    hit_level = "tp2"
                    triggered = True
                elif close_price <= tp_levels.get("tp3", 999999) and not current_pos.get("tp_hit", {}).get("tp3"):
                    hit_level = "tp3"
                    triggered = True

            if triggered and hit_level:
                self._execute_tp(hit_level, current_pos)

        except Exception as e:
            logging.error(f"[TP监控消息处理异常] {e}")

    def _execute_tp(self, level: str, current_pos: dict):
        try:
            symbol = current_pos["symbol"]
            total_qty = current_pos["qty"]

            # 根据不同 TP 级别决定平仓比例
            if level == "tp1":
                close_percent = 0.30
            elif level == "tp2":
                close_percent = 0.30
            elif level == "tp3":
                close_percent = 1.0   # TP3 全平
            else:
                return

            close_qty = round(total_qty * close_percent, 3)
            if close_qty <= 0:
                return

            side = "SELL" if current_pos["side"] == "long" else "BUY"

            order = binance_client.client.futures_create_order(
                symbol=symbol,
                side=side,
                type="MARKET",
                quantity=close_qty,
                reduceOnly=True
            )

            logging.info(f"[TP执行成功] {level.upper()} 平仓 {close_qty}")

            # 更新本地状态
            position_manager.mark_tp_hit(level)

            if level == "tp3":
                position_manager.clear_position()

            # 通知监督层（由监督层决定是否推送报告）
            supervisor.notify_tp_hit(level, close_qty, current_pos.get("avg_price", 0))

        except Exception as e:
            logging.error(f"[TP执行失败] level={level} | {e}")

    def stop(self):
        if self.twm:
            self.twm.stop()
        self.is_running = False
        logging.info("[TP监控] 已停止")


# 全局实例
tp_monitor = TPMonitor()
