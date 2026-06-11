# tp_monitor.py（完整最终加强版）
import time
import threading
import logging
from binance import ThreadedWebsocketManager
from binance_client import BinanceClient
from position_manager import PositionManager

class TPMonitor:
    def __init__(self, symbol: str = "ETHUSDT", check_interval: int = 5):
        self.symbol = symbol
        self.client = BinanceClient()
        self.pm = PositionManager()
        self.check_interval = check_interval
        self.current_price = None
        self.running = False
        self.twm = None
        self.last_qty = 0
        self.reconnect_attempts = 0
        self.max_reconnect_attempts = 15

    def start(self):
        if self.running:
            return
        self.running = True
        self._start_websocket()
        threading.Thread(target=self._check_tp_loop, daemon=True).start()
        logging.info(f"[TP监控] 加强重连版TP监控已启动 | {self.symbol}")

    def _start_websocket(self):
        if self.twm:
            try:
                self.twm.stop()
            except Exception:
                pass

        self.twm = ThreadedWebsocketManager(
            api_key=self.client.client.API_KEY,
            api_secret=self.client.client.API_SECRET
        )

        try:
            self.twm.start()
            self.twm.start_aggtrade_socket(
                callback=self._on_price_update, 
                symbol=self.symbol.lower()
            )
            self.reconnect_attempts = 0
            logging.info("[TP监控] WebSocket 连接成功")
        except Exception as e:
            logging.error(f"[WebSocket启动失败] {e}")
            self._reconnect_websocket()

    def _reconnect_websocket(self):
        self.reconnect_attempts += 1
        if self.reconnect_attempts > self.max_reconnect_attempts:
            logging.error("[TP监控] WebSocket 重连次数过多，停止重连")
            return

        wait_time = min(self.reconnect_attempts * 2, 30)
        logging.warning(f"[TP监控] WebSocket 断开，{wait_time}秒后尝试重连... (第{self.reconnect_attempts}次)")
        time.sleep(wait_time)
        self._start_websocket()

    def _on_price_update(self, msg):
        try:
            if "p" in msg:
                self.current_price = float(msg["p"])
        except Exception as e:
            logging.error(f"[价格更新异常] {e}")
            # 触发重连
            if self.twm:
                try:
                    self.twm.stop()
                except:
                    pass
            self._reconnect_websocket()

    def _check_tp_loop(self):
        while self.running:
            try:
                real_pos = self.client.get_current_position(self.symbol)
                cached_pos = self.pm.get_position()

                # 检测手动全平
                if not real_pos and cached_pos:
                    logging.warning("[TP监控] 检测到手动全平，清理缓存")
                    self.pm.clear_position()
                    self.last_qty = 0
                    time.sleep(self.check_interval)
                    continue

                if real_pos:
                    current_qty = abs(float(real_pos["positionAmt"]))
                    side = "long" if float(real_pos["positionAmt"]) > 0 else "short"

                    # 检测手动加减仓
                    if self.last_qty > 0 and abs(current_qty - self.last_qty) > max(self.last_qty * 0.05, 0.01):
                        self._handle_manual_position_change(cached_pos, real_pos, current_qty, self.last_qty)

                    self.last_qty = current_qty

                    if not cached_pos or self.current_price is None:
                        time.sleep(self.check_interval)
                        continue

                    price = self.current_price
                    tp = cached_pos.get("tp_prices", {})
                    hit = cached_pos.get("tp_hit", [])

                    if side == "long":
                        if "tp1" not in hit and price >= tp.get("tp1", 0):
                            self._execute_tp("tp1", price, cached_pos, 0.30)
                        elif "tp2" not in hit and price >= tp.get("tp2", 0):
                            self._execute_tp("tp2", price, cached_pos, 0.30)
                        elif "tp3" not in hit and price >= tp.get("tp3", 0):
                            self._execute_tp("tp3", price, cached_pos, 1.0)
                    else:
                        if "tp1" not in hit and price <= tp.get("tp1", 999999):
                            self._execute_tp("tp1", price, cached_pos, 0.30)
                        elif "tp2" not in hit and price <= tp.get("tp2", 999999):
                            self._execute_tp("tp2", price, cached_pos, 0.30)
                        elif "tp3" not in hit and price <= tp.get("tp3", 999999):
                            self._execute_tp("tp3", price, cached_pos, 1.0)

            except Exception as e:
                logging.error(f"[TP检查循环异常] {e}")

            time.sleep(self.check_interval)

    def _handle_manual_position_change(self, cached_pos, real_pos, current_qty, last_qty):
        change_qty = current_qty - last_qty
        if change_qty > 0:
            logging.info(f"[手动加仓检测] 加仓数量: {change_qty}")
            # 可在此处扩展重置TP逻辑
        else:
            logging.info(f"[手动减仓检测] 减仓数量: {abs(change_qty)}")

    def _execute_tp(self, level: str, price: float, pos: dict, percent: float):
        logging.info(f"[TP触发] {level.upper()} @ {price}")
        self.pm.mark_tp_hit(level)

        if percent >= 1.0:
            result = self.client.close_all_positions(self.symbol)
            if result.get("status") == "success":
                self.pm.clear_position()
                logging.info(f"[TP执行] {level} 全平成功")
        else:
            result = self.client.close_partial_position(self.symbol, percent)
            if result.get("status") == "success":
                logging.info(f"[TP执行] {level} 部分平仓 {percent*100}% 成功")

    def stop(self):
        self.running = False
        if self.twm:
            try:
                self.twm.stop()
            except Exception:
                pass
        logging.info("[TP监控] 已停止")
