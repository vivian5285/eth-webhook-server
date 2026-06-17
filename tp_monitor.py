#!/usr/bin/env python3
# tp_monitor.py（V4.0 币安 WS 毫秒雷达 + 静态靶子定点引爆版）
import logging
import time
import threading
import json
import websocket
from typing import Optional
from binance_client import binance_client
from order_executor import order_executor
from position_manager import position_manager
import dingtalk
from state_manager import state_manager

logger = logging.getLogger(__name__)

class TPMonitor:
    def __init__(self, check_interval: float = 10.0):
        self.client = binance_client
        self.executor = order_executor
        self.position_manager = position_manager
        self.check_interval = check_interval
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()

        self.tp1_price = self.tp2_price = self.tp3_price = None
        self.position_side = None
        self.position_qty = 0.0
        self.entry_price = 0.0
        self.is_monitoring = False
        
        self.ws = None
        self.last_ws_msg_time = 0

        self._restore_from_state()

    def _restore_from_state(self):
        try:
            state = state_manager.load_state()
            if state and state.get("is_monitoring"):
                with self._lock:
                    self.tp1_price = state.get("tp1")
                    self.tp2_price = state.get("tp2")
                    self.tp3_price = state.get("tp3")
                    self.position_side = state.get("side")
                    self.position_qty = state.get("remaining_qty", 0)
                    self.entry_price = state.get("entry_price", 0)
                    self.is_monitoring = True
                self._start_websocket_radar()
        except Exception:
            pass

    def set_tp_levels(self, tp1: float, tp2: float, tp3: float, side: str, qty: float, entry_price: float = 0):
        try:
            with self._lock:
                self.tp1_price = round(tp1, 2)
                self.tp2_price = round(tp2, 2)
                self.tp3_price = round(tp3, 2)
                self.position_side = side
                self.position_qty = round(qty, 3)
                self.entry_price = round(entry_price or self.position_manager.get_position().get("entryPrice", 0), 2)
                self.is_monitoring = True

                state_manager.save_state({
                    "tp1": self.tp1_price,
                    "tp2": self.tp2_price,
                    "tp3": self.tp3_price,
                    "side": side,
                    "remaining_qty": self.position_qty,
                    "entry_price": self.entry_price,
                    "is_monitoring": True
                })
            self._start_websocket_radar()
        except Exception:
            pass

    def clear_tp_levels(self):
        try:
            with self._lock:
                self.tp1_price = self.tp2_price = self.tp3_price = None
                self.position_side = None
                self.position_qty = 0.0
                self.entry_price = 0.0
                self.is_monitoring = False
            state_manager.clear_state()
            if self.ws:
                self.ws.close()
                self.ws = None
        except Exception:
            pass

    def _start_websocket_radar(self):
        if self.ws is not None: return 
        symbol = "ethusdt"
        ws_url = f"wss://fstream.binance.com/ws/{symbol}@ticker"
        self.last_ws_msg_time = time.time()  
        
        websocket.enableTrace(False)
        self.ws = websocket.WebSocketApp(
            ws_url,
            on_open=self._on_ws_open,
            on_message=self._on_ws_message,
            on_error=self._on_ws_error,
            on_close=self._on_ws_close
        )
        wst = threading.Thread(target=self.ws.run_forever, kwargs={"ping_interval": 30, "ping_timeout": 10})
        wst.daemon = True
        wst.start()

    def _on_ws_open(self, ws):
        logger.info("✅ 币安 WebSocket 光缆连接成功！进入 0 延迟盯盘模式...")

    def _on_ws_message(self, ws, message):
        self.last_ws_msg_time = time.time()  
        if not self.is_monitoring or not self.position_side: return 
            
        try:
            data = json.loads(message)
            current_price_str = data.get("c")
            if not current_price_str: return
                
            current_price = float(current_price_str)
            triggered_level = self._check_tp_trigger(current_price)
            
            if triggered_level:
                threading.Thread(target=self._handle_tp_trigger, args=(triggered_level, current_price), daemon=True).start()
        except Exception:
            pass

    def _on_ws_error(self, ws, error):
        pass

    def _on_ws_close(self, ws, close_status_code, close_msg):
        self.ws = None
        if self.is_monitoring:
            time.sleep(2)
            self._start_websocket_radar()

    def start(self):
        if self._thread and self._thread.is_alive(): return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._reconcile_loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop_event.set()
        if self._thread: self._thread.join(timeout=5)
        self.clear_tp_levels()

    def _reconcile_loop(self):
        while not self._stop_event.is_set():
            try:
                if self.is_monitoring and self.position_side:
                    self._reconcile_position()
                    if self.ws is not None:
                        if time.time() - self.last_ws_msg_time > 60:
                            self.ws.close()
                            self.ws = None
                            self._start_websocket_radar()
            except Exception:
                pass
            time.sleep(self.check_interval)

    def _reconcile_position(self):
        try:
            real_pos = self.position_manager.get_position()
            if not real_pos: return

            real_side = self.position_manager.get_position_side()
            real_qty = self.position_manager.get_position_qty()

            if real_side and real_side != self.position_side:
                dingtalk.report_force_align(real_side, self.position_side)
                self.executor.close_position("监控层检测到反向持仓，强制清空")
                self.clear_tp_levels()
                return

            if real_qty > 0 and abs(real_qty - self.position_qty) > 0.01:
                self._handle_quantity_change(real_qty)

        except Exception:
            pass

    def _handle_quantity_change(self, new_qty: float):
        try:
            current_entry = round(float(self.position_manager.get_position().get("entryPrice", self.entry_price)), 2)

            if self.position_side == "LONG":
                tps = {
                    "tp1": round(current_entry + 15.0, 2),
                    "tp2": round(current_entry + 30.0, 2),
                    "tp3": round(current_entry + 50.0, 2)
                }
            else:
                tps = {
                    "tp1": round(current_entry - 15.0, 2),
                    "tp2": round(current_entry - 30.0, 2),
                    "tp3": round(current_entry - 50.0, 2)
                }

            with self._lock:
                self.position_qty = round(new_qty, 3)
                self.tp1_price = tps['tp1']
                self.tp2_price = tps['tp2']
                self.tp3_price = tps['tp3']

            state_manager.save_state({
                "tp1": self.tp1_price,
                "tp2": self.tp2_price,
                "tp3": self.tp3_price,
                "side": self.position_side,
                "remaining_qty": self.position_qty,
                "entry_price": self.entry_price,
                "is_monitoring": True
            })
            dingtalk.report_supervisor_intervention(self.position_qty, new_qty, tps)
        except Exception:
            pass

    def _check_tp_trigger(self, current_price: float) -> Optional[str]:
        try:
            with self._lock:
                side, tp1, tp2, tp3 = self.position_side, self.tp1_price, self.tp2_price, self.tp3_price

            if side == "LONG":
                if tp3 and current_price >= tp3: return "TP3"
                if tp2 and current_price >= tp2: return "TP2"
                if tp1 and current_price >= tp1: return "TP1"
            elif side == "SHORT":
                if tp3 and current_price <= tp3: return "TP3"
                if tp2 and current_price <= tp2: return "TP2"
                if tp1 and current_price <= tp1: return "TP1"
            return None
        except Exception:
            return None

    def _handle_tp_trigger(self, level: str, current_price: float):
        try:
            with self._lock:
                if level == "TP1":
                    if self.tp1_price is None: return
                    self.tp1_price = None 
                elif level == "TP2":
                    if self.tp2_price is None: return
                    self.tp1_price = None
                    self.tp2_price = None
                elif level == "TP3":
                    if self.tp3_price is None: return
                    self.tp3_price = None
                    
            logger.info(f"✨ [WS触发] {level} 破局！现价: {current_price}，立即执行闪电平仓！")

            if level == "TP1":
                # 平 40%
                success, real_pnl = self.executor.partial_close(0.40, f"{level} 触发")
                if success:
                    time.sleep(1.5)
                    new_qty = self.position_manager.get_position_qty()
                    with self._lock: self.position_qty = new_qty  
                    dingtalk.report_supervisor_tp_trigger(level, current_price, real_pnl, "已落袋 40%，继续盯防 TP2 与 TP3。")

            elif level == "TP2":
                # 当前剩下 60% 的仓位，我们要平掉最初的 40%，等于平掉剩余的 66.67%
                success, real_pnl = self.executor.partial_close(0.6667, f"{level} 触发")
                if success:
                    time.sleep(1.5)
                    new_qty = self.position_manager.get_position_qty()
                    with self._lock: self.position_qty = new_qty
                    dingtalk.report_supervisor_tp_trigger(level, current_price, real_pnl, "再次落袋 40%，当前保留最终 20% 底仓冲刺 TP3。")

            elif level == "TP3":
                # 全平最后 20%
                success, real_pnl = self.executor.close_position(f"{level} 触发")
                if success:
                    self.clear_tp_levels()
                    dingtalk.report_supervisor_tp_trigger(level, current_price, real_pnl, "最终 50U 目标到达，本轮战役完美收官全平。")

        except Exception as e:
            logger.error(f"[TPMonitor] 处理 {level} 触发异常: {e}")

tp_monitor = TPMonitor()
