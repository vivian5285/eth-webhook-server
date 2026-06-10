# tp_monitor.py（最终加强版 - 中等强度 ATR 动态追踪）
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
        if self.running:
            return
        self.running = True

        self.twm = ThreadedWebsocketManager(
            api_key=self.client.client.API_KEY,
            api_secret=self.client.client.API_SECRET
        )
        self.twm.start()
        self.twm.start_aggtrade_socket(callback=self._on_price_update, symbol=self.symbol.lower())

        threading.Thread(target=self._check_tp_loop, daemon=True).start()
        logging.info(f"[TP监控] WebSocket + ATR动态追踪已启动 | {self.symbol}")

    def _on_price_update(self, msg):
        try:
            if "p" in msg:
                self.current_price = float(msg["p"])
        except Exception as e:
            logging.error(f"[价格更新异常] {e}")

    def _check_tp_loop(self):
        while self.running:
            try:
                pos = self.pm.get_position()
                if not pos or self.current_price is None:
                    time.sleep(self.check_interval)
                    continue

                if time.time() - self.last_action_time < 2.5:
                    time.sleep(0.8)
                    continue

                price = self.current_price
                tp = pos.get("tp_prices", {})
                side = pos.get("side")
                hit = pos.get("tp_hit", [])
                entry_price = pos.get("entry_price", 0)
                entry_atr = pos.get("entry_atr", atr if 'atr' in pos else 30)

                # 动态计算当前应使用的追踪距离
                trail_distance = self._get_dynamic_trail_distance(price, entry_price, side, hit, entry_atr)

                if side == "long":
                    if "tp1" not in hit and price >= tp.get("tp1", 0):
                        self._execute_tp("tp1", price, pos, 0.30, trail_distance)
                    elif "tp2" not in hit and price >= tp.get("tp2", 0):
                        self._execute_tp("tp2", price, pos, 0.30, trail_distance)
                    elif "tp3" not in hit and price >= tp.get("tp3", 0):
                        self._execute_tp("tp3", price, pos, 1.0, trail_distance)
                else:
                    if "tp1" not in hit and price <= tp.get("tp1", 999999):
                        self._execute_tp("tp1", price, pos, 0.30, trail_distance)
                    elif "tp2" not in hit and price <= tp.get("tp2", 999999):
                        self._execute_tp("tp2", price, pos, 0.30, trail_distance)
                    elif "tp3" not in hit and price <= tp.get("tp3", 999999):
                        self._execute_tp("tp3", price, pos, 1.0, trail_distance)

            except Exception as e:
                logging.error(f"[TP监控循环异常] {e}")

            time.sleep(self.check_interval)

    def _get_dynamic_trail_distance(self, price, entry_price, side, hit, entry_atr):
        """中等强度 ATR 动态追踪距离计算"""
        if not entry_price or entry_atr <= 0:
            return entry_atr * 2.2 if entry_atr > 0 else 40

        if side == "long":
            profit_pct = (price - entry_price) / entry_price * 100
        else:
            profit_pct = (entry_price - price) / entry_price * 100

        # 根据盈利程度和已触发的 TP 动态调整追踪距离
        if "tp2" in hit or profit_pct > 2.2:
            multiplier = 1.3          # 激进模式
        elif "tp1" in hit or profit_pct > 1.0:
            multiplier = 1.7          # 收紧模式
        else:
            multiplier = 2.4          # 正常模式

        return round(entry_atr * multiplier, 2)

    def _execute_tp(self, level: str, price: float, pos: dict, percent: float, trail_distance: float):
        logging.info(f"[TP触发] {level} @ {price} | 追踪距离: {trail_distance}")

        self.pm.mark_tp_hit(level)
        self.last_action_time = time.time()

        if percent >= 1.0:
            self.client.close_all_positions(pos["symbol"])
            self.pm.clear_position()
        else:
            self.client.close_partial_position(pos["symbol"], percent)

        try:
            from app import send_tp_hit_report
            report = self.client.get_detailed_report()
            send_tp_hit_report(level, price, report)
        except Exception as e:
            logging.error(f"[TP报表发送失败] {e}")

    def stop(self):
        self.running = False
        if self.twm:
            self.twm.stop()
        logging.info("[TP监控] 已停止")
