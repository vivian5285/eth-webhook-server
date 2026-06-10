# tp_monitor.py（最终版 - 支持 ATR 动态追踪止盈）
import time
import threading
import logging
from binance import ThreadedWebsocketManager
from binance_client import BinanceClient
from position_manager import PositionManager

class TPMonitor:
    def __init__(self, symbol: str = "ETHUSDT", check_interval: int = 4):
        self.symbol = symbol
        self.client = BinanceClient()
        self.pm = PositionManager()
        self.check_interval = check_interval
        self.current_price = None
        self.running = False
        self.twm = None
        self.last_action_time = 0

    def start(self):
        """启动 WebSocket 实时价格监控 + TP 检查"""
        if self.running:
            return
        self.running = True

        self.twm = ThreadedWebsocketManager(
            api_key=self.client.client.API_KEY,
            api_secret=self.client.client.API_SECRET
        )
        self.twm.start()

        # 使用 aggTrade 获取实时成交价格（延迟低）
        self.twm.start_aggtrade_socket(callback=self._on_price_update, symbol=self.symbol.lower())

        # 启动 TP 检查主循环
        threading.Thread(target=self._check_tp_loop, daemon=True).start()
        logging.info(f"[TP监控] WebSocket + ATR动态追踪已启动 | {self.symbol}")

    def _on_price_update(self, msg):
        """WebSocket 回调，实时更新最新价格"""
        try:
            if "p" in msg:
                self.current_price = float(msg["p"])
        except Exception as e:
            logging.error(f"[价格更新异常] {e}")

    def _check_tp_loop(self):
        """TP 检查主循环"""
        while self.running:
            try:
                pos = self.pm.get_position()
                if not pos or self.current_price is None:
                    time.sleep(self.check_interval)
                    continue

                # 防止频繁操作
                if time.time() - self.last_action_time < 2.5:
                    time.sleep(0.8)
                    continue

                price = self.current_price
                tp = pos.get("tp_prices", {})
                side = pos.get("side")
                hit = pos.get("tp_hit", [])
                entry_price = pos.get("entry_price", 0)
                entry_atr = pos.get("entry_atr", 30)

                if side == "long":
                    if "tp1" not in hit and price >= tp.get("tp1", 0):
                        self._execute_tp("tp1", price, pos, 0.30)
                    elif "tp2" not in hit and price >= tp.get("tp2", 0):
                        self._execute_tp("tp2", price, pos, 0.30)
                    elif "tp3" not in hit and price >= tp.get("tp3", 0):
                        self._execute_tp("tp3", price, pos, 1.0)
                else:
                    if "tp1" not in hit and price <= tp.get("tp1", 999999):
                        self._execute_tp("tp1", price, pos, 0.30)
                    elif "tp2" not in hit and price <= tp.get("tp2", 999999):
                        self._execute_tp("tp2", price, pos, 0.30)
                    elif "tp3" not in hit and price <= tp.get("tp3", 999999):
                        self._execute_tp("tp3", price, pos, 1.0)

            except Exception as e:
                logging.error(f"[TP检查循环异常] {e}")

            time.sleep(self.check_interval)

    def _execute_tp(self, level: str, price: float, pos: dict, percent: float):
        """
        执行 TP 平仓
        - TP1 / TP2：按比例平仓
        - TP3：全平剩余仓位
        """
        logging.info(f"[TP触发] {level} @ {price}")

        self.pm.mark_tp_hit(level)
        self.last_action_time = time.time()

        if percent >= 1.0:
            self.client.close_all_positions(pos["symbol"])
            self.pm.clear_position()
        else:
            self.client.close_partial_position(pos["symbol"], percent)

        # 发送钉钉详细报表
        try:
            from app import send_tp_hit_report
            report = self.client.get_detailed_report()
            send_tp_hit_report(level, price, report)
        except Exception as e:
            logging.error(f"[TP报表发送失败] {e}")

    def stop(self):
        """停止监控"""
        self.running = False
        if self.twm:
            self.twm.stop()
        logging.info("[TP监控] 已停止")
