#!/usr/bin/env python3
# tp_monitor.py（V3.0 币安 WS 毫秒级光缆雷达版）
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
                    logger.info("[TPMonitor] 从持久化状态恢复监控")
                # 如果恢复时是监控状态，直接拉起光缆
                self._start_websocket_radar()
        except Exception as e:
            logger.error(f"[TPMonitor] 恢复状态失败: {e}")

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
            # 状态锁定后，立即点火光缆
            self._start_websocket_radar()
        except Exception as e:
            logger.error(f"[TPMonitor] 设置 TP 水平失败: {e}")

    def clear_tp_levels(self):
        try:
            with self._lock:
                self.tp1_price = self.tp2_price = self.tp3_price = None
                self.position_side = None
                self.position_qty = 0.0
                self.entry_price = 0.0
                self.is_monitoring = False
            state_manager.clear_state()
            
            # 清除任务时，切断光缆省电
            if self.ws:
                self.ws.close()
                self.ws = None
        except Exception as e:
            logger.error(f"[TPMonitor] 清空 TP 水平失败: {e}")

    # ==========================================
    # WebSocket 毫秒级雷达核心逻辑 (币安直连版)
    # ==========================================
    def _start_websocket_radar(self):
        """启动币安长连接光缆"""
        if self.ws is not None:
            return # 防止重复启动
            
        symbol = "ethusdt"
        ws_url = f"wss://fstream.binance.com/ws/{symbol}@ticker"
        
        logger.info(f"🔌 正在连接币安 WebSocket 实时行情光缆: {ws_url}")
        websocket.enableTrace(False)
        self.ws = websocket.WebSocketApp(
            ws_url,
            on_open=self._on_ws_open,
            on_message=self._on_ws_message,
            on_error=self._on_ws_error,
            on_close=self._on_ws_close
        )
        
        # 放入后台独立线程运行
        wst = threading.Thread(target=self.ws.run_forever, kwargs={"ping_interval": 30, "ping_timeout": 10})
        wst.daemon = True
        wst.start()

    def _on_ws_open(self, ws):
        logger.info("✅ 币安 WebSocket 光缆连接成功！进入 0 延迟盯盘模式...")

    def _on_ws_message(self, ws, message):
        if not self.is_monitoring or not self.position_side:
            return 
            
        try:
            data = json.loads(message)
            # 币安 @ticker 推送中，'c' 代表最新现价 (Last price)
            current_price_str = data.get("c")
            if not current_price_str:
                return
                
            current_price = float(current_price_str)
            triggered_level = self._check_tp_trigger(current_price)
            
            if triggered_level:
                # 瞬间起一个微线程去开枪，绝对不能卡住 WebSocket 接收下一条价格的心跳！
                threading.Thread(target=self._handle_tp_trigger, args=(triggered_level, current_price), daemon=True).start()
        except Exception:
            pass

    def _on_ws_error(self, ws, error):
        logger.error(f"⚠️ 币安 WS 雷达受干扰: {error}")

    def _on_ws_close(self, ws, close_status_code, close_msg):
        logger.info("🔌 币安 WebSocket 行情光缆已断开。")
        self.ws = None
        # 如果还在监控状态中意外断开，2秒后尝试重新拉起
        if self.is_monitoring:
            time.sleep(2)
            self._start_websocket_radar()

    # ==========================================
    # 以下为后台兜底与防呆逻辑
    # ==========================================

    def start(self):
        """只负责启动低频的后台兜底对账线程"""
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._reconcile_loop, daemon=True)
        self._thread.start()
        logger.info("[TPMonitor] 兜底核实与状态监控线程已启动")

    def stop(self):
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5)
        self.clear_tp_levels()
        logger.info("[TPMonitor] 监控已停止")

    def _reconcile_loop(self):
        """低频容错机制：每 10 秒查一次实盘，防止用户手动平仓导致程序死等"""
        while not self._stop_event.is_set():
            try:
                if self.is_monitoring and self.position_side:
                    self._reconcile_position()
            except Exception as e:
                pass
            time.sleep(self.check_interval)

    def _reconcile_position(self):
        try:
            real_pos = self.position_manager.get_position()
            if not real_pos:
                return

            real_side = self.position_manager.get_position_side()
            real_qty = self.position_manager.get_position_qty()

            if real_side and real_side != self.position_side:
                dingtalk.report_force_align(real_side, self.position_side)
                self.executor.close_position("监控层检测到反向持仓，强制清空")
                self.clear_tp_levels()
                return

            if real_qty > 0 and abs(real_qty - self.position_qty) > 0.01:
                self._handle_quantity_change(real_qty)

        except Exception as e:
            logger.error(f"[TPMonitor] 持仓核对异常: {e}")

    def _handle_quantity_change(self, new_qty: float):
        # (保持原逻辑：人工干预修正)
        try:
            current_atr = self.client.get_atr("ETHUSDT", "1h", 50, 14) or 22.0
            current_entry = round(float(self.position_manager.get_position().get("entryPrice", self.entry_price)), 2)

            if self.position_side == "LONG":
                tps = {
                    "tp1": round(current_entry + current_atr * 1.3, 2),
                    "tp2": round(current_entry + current_atr * 2.6, 2),
                    "tp3": round(current_entry + current_atr * 4.2, 2)
                }
            else:
                tps = {
                    "tp1": round(current_entry - current_atr * 1.3, 2),
                    "tp2": round(current_entry - current_atr * 2.6, 2),
                    "tp3": round(current_entry - current_atr * 4.2, 2)
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
        except Exception as e:
            logger.error(f"[TPMonitor] 处理数量变化异常: {e}")

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
            # 【致命漏洞终极防御】：先加锁，拿到开火权后立马把靶子撕了，绝不允许系统打第二枪！
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
                    
            logger.info(f"✨ [WS毫秒触发] {level} 破局！现价: {current_price}，立即执行闪电平仓！")

            if level == "TP1":
                success, real_pnl = self.executor.partial_close(0.40, f"{level} 触发")
                if success:
                    time.sleep(1.5)  
                    new_qty = self.position_manager.get_position_qty()
                    with self._lock:
                        self.position_qty = new_qty  
                    self._move_tp3_after_partial(current_price)
                    dingtalk.report_supervisor_tp_trigger(level, current_price, real_pnl, "已落袋 40%，TP1 防线完成使命，成功移动 TP3。")

            elif level == "TP2":
                # 数学修复：剩下60%，平初始40%，即平当前仓位的 0.6667 (三分之二)
                success, real_pnl = self.executor.partial_close(0.6667, f"{level} 触发")
                if success:
                    time.sleep(1.5)
                    new_qty = self.position_manager.get_position_qty()
                    with self._lock:
                        self.position_qty = new_qty
                    self._move_tp3_after_partial(current_price)
                    dingtalk.report_supervisor_tp_trigger(level, current_price, real_pnl, "已落袋 40%，TP2 防线完成使命，成功移动 TP3。")

            elif level == "TP3":
                success, real_pnl = self.executor.close_position(f"{level} 触发")
                if success:
                    self.clear_tp_levels()
                    dingtalk.report_supervisor_tp_trigger(level, current_price, real_pnl, "最终防线到达，本轮交易闭环全平。")

        except Exception as e:
            logger.error(f"[TPMonitor] 处理 {level} 触发异常: {e}")

    def _move_tp3_after_partial(self, current_price: float):
        try:
            atr = self.client.get_atr("ETHUSDT", "1h", 50, 14) or 22.0
            new_tp3 = round(current_price + atr * 2.3, 2) if self.position_side == "LONG" else round(current_price - atr * 2.3, 2)

            with self._lock:
                self.tp3_price = new_tp3

            state_manager.save_state({
                "tp1": self.tp1_price,
                "tp2": self.tp2_price,
                "tp3": self.tp3_price,
                "side": self.position_side,
                "remaining_qty": self.position_qty,  
                "entry_price": self.entry_price,
                "is_monitoring": True
            })
        except Exception as e:
            logger.error(f"[TPMonitor] 移动 TP3 异常: {e}")


tp_monitor = TPMonitor()
