# tp_monitor.py（最终完整加强版）
import threading
import time
import logging
from binance import ThreadedWebsocketManager
from binance_client import BinanceClient
from position_manager import PositionManager

class TPMonitor:
    def __init__(self):
        self.client = BinanceClient()
        self.position_manager = PositionManager()
        self.twm = ThreadedWebsocketManager(
            api_key=self.client.api_key, 
            api_secret=self.client.api_secret
        )
        self.symbol = "ETHUSDT"
        self.running = False
        self.ws = None

    def start(self):
        if self.running:
            return
        self.running = True
        self.twm.start()
        self._start_websocket()
        logging.info("[TP监控] 已启动 WebSocket 监控")

    def _start_websocket(self):
        self.ws = self.twm.start_kline_socket(
            callback=self._on_price_update,
            symbol=self.symbol,
            interval='45m'
        )

    def _reconnect(self):
        logging.warning("[TP监控] WebSocket 断开，正在重连...")
        try:
            if self.ws:
                self.twm.stop_socket(self.ws)
            time.sleep(3)
            self._start_websocket()
            logging.info("[TP监控] WebSocket 重连成功")
        except Exception as e:
            logging.error(f"[TP监控重连失败] {e}")
            time.sleep(10)
            self._reconnect()

    def _on_price_update(self, msg):
        try:
            if msg.get('e') != 'kline':
                return

            kline = msg['k']
            close_price = float(kline['c'])

            position = self.position_manager.get_current_position()
            if not position or position.get('side') == 'NONE':
                return

            side = position['side']
            tp_levels = position.get('tp_levels', {})

            # TP1 检查
            if side == 'LONG' and close_price >= tp_levels.get('tp1', 0):
                self._execute_tp('tp1', close_price, position)
            elif side == 'SHORT' and close_price <= tp_levels.get('tp1', 999999):
                self._execute_tp('tp1', close_price, position)

            # 可在此处扩展 TP2 / TP3 检查 + 追踪止盈逻辑

        except Exception as e:
            logging.error(f"[TP监控回调异常] {e}")

    def _get_adaptive_trail_distance(self, base_atr: float) -> float:
        """自适应追踪距离（可根据 ADX 动态调整）"""
        try:
            # 这里可接入真实 ADX 计算，当前简化处理
            adx = 22
            if adx > 28:
                return base_atr * 1.6   # 强趋势，保护利润
            elif adx > 20:
                return base_atr * 2.0
            else:
                return base_atr * 2.6   # 弱势，留空间
        except:
            return base_atr * 2.2

    def _execute_tp(self, level: str, current_price: float, position: dict):
        """执行分批止盈"""
        try:
            percent_map = {'tp1': 0.30, 'tp2': 0.30, 'tp3': 1.0}
            close_percent = percent_map.get(level, 0.3)

            result = self.client.close_partial_position(
                symbol=self.symbol,
                percent=close_percent
            )

            if result.get('status') == 'success':
                logging.info(f"[TP执行成功] {level.upper()} | 价格: {current_price}")
                self.position_manager.mark_tp_hit(level)

                # 发送钉钉通知
                self.client._send_dingtalk(
                    title=f"💰 {level.upper()} 分批止盈触发",
                    content=(
                        f"**币种**：{self.symbol}\n"
                        f"**触发价格**：{current_price}\n"
                        f"**平仓比例**：{int(close_percent * 100)}%\n"
                        f"**执行时间**：{time.strftime('%Y-%m-%d %H:%M:%S')}"
                    )
                )
            else:
                logging.warning(f"[TP执行失败] {level} | {result.get('message')}")

        except Exception as e:
            logging.error(f"[执行TP异常] {level} | {e}")

    def stop(self):
        self.running = False
        if self.twm:
            self.twm.stop()
        logging.info("[TP监控] 已停止")
