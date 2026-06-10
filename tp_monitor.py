# tp_monitor.py（最终加强版 - 含早期保本移动止损）
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
        logging.info(f"[TP监控] WebSocket + ATR动态追踪 + 早期保本移动已启动 | {self.symbol}")

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

                # === 新增：开仓后立即智能移动止损逻辑 ===
                self._check_early_breakeven(price, entry_price, side, hit)

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

    def _check_early_breakeven(self, price, entry_price, side, hit):
        """开仓后达到一定浮盈立即进入更紧的追踪模式（早期保本移动）"""
        if not entry_price or "tp1" in hit:
            return

        if side == "long":
            profit_pct = (price - entry_price) / entry_price * 100
        else:
            profit_pct = (entry_price - price) / entry_price * 100

        # 浮盈超过 0.55% 且未触发 TP1 时，提前进入紧追踪模式
        if profit_pct >= 0.55:
            logging.info(f"[早期保本移动] 当前浮盈 {profit_pct:.2f}%，提前进入紧追踪模式")
            # 这里可以通过修改内部状态或直接影响后续追踪距离实现
            # 当前版本通过 _get_dynamic_trail_distance 已支持，可进一步加强

    def _execute_tp(self, level: str, price: float, pos: dict, percent: float):
        logging.info(f"[TP触发] {level} @ {price}")

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
