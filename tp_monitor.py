#!/usr/bin/env python3
# tp_monitor.py（V5.0 哨兵巡更版 - 专抓限价单成交与人工平仓）
import logging
import time
import threading
from binance_client import binance_client
from position_manager import position_manager
import dingtalk
from state_manager import state_manager

logger = logging.getLogger(__name__)

class TPMonitor:
    def __init__(self, check_interval: float = 5.0):
        self.client = binance_client
        self.position_manager = position_manager
        self.check_interval = check_interval
        self._thread = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()

        self.position_side = None
        self.position_qty = 0.0
        self.is_monitoring = False

        self._restore_from_state()

    def _restore_from_state(self):
        try:
            state = state_manager.load_state()
            if state and state.get("is_monitoring"):
                with self._lock:
                    self.position_side = state.get("side")
                    self.position_qty = state.get("remaining_qty", 0)
                    self.is_monitoring = True
                self.start()
        except Exception:
            pass

    def set_watch_levels(self, side: str, qty: float):
        try:
            with self._lock:
                self.position_side = side
                self.position_qty = round(abs(qty), 3)
                self.is_monitoring = True

                state_manager.save_state({
                    "side": side,
                    "remaining_qty": self.position_qty,
                    "is_monitoring": True
                })
        except Exception as e:
            logger.error(f"[TPMonitor] 状态保存异常: {e}")

    def clear_tp_levels(self):
        try:
            with self._lock:
                self.position_side = None
                self.position_qty = 0.0
                self.is_monitoring = False
            state_manager.clear_state()
        except Exception:
            pass

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
        logger.info("👀 [TPMonitor] 哨兵巡更系统启动，开始监控币安限价单自动成交状况...")
        while not self._stop_event.is_set():
            try:
                if self.is_monitoring and self.position_side:
                    self._check_position_changes()
            except Exception as e:
                pass
            time.sleep(self.check_interval)

    def _check_position_changes(self):
        real_pos = self.position_manager.get_position()
        real_qty = round(abs(float(real_pos.get("positionAmt", 0))), 3) if real_pos else 0.0

        # 如果实际仓位数量发生变化（包括清零！）
        if abs(real_qty - self.position_qty) > 0.001:
            
            # 【完美修复】如果实盘仓位变成 0，说明要么 TP3 彻底吃完，要么是你在手机上人工全平了！
            if real_qty == 0:
                logger.info("💥 [哨兵报告] 发现仓位已归零！触发清场与战报发送！")
                
                # 安全清理可能遗留的残余挂单
                self.client.cancel_all_open_orders("ETHUSDT")
                
                dingtalk.send_markdown_message(
                    title="阵地清空报告",
                    text=f"### 🛡️ 仓位归零确认\n- **状态**：实盘仓位已变为 0。\n- **原因**：可能为终极止盈单命中，或执行了人工手动全平。\n- **动作**：已自动撤销盘口全部残余挂单，系统重置为纯净空仓等待新信号。"
                )
                self.clear_tp_levels()
                
            # 如果仓位变少了，说明 TP1 或 TP2 被币安限价单自动吃到了！或你人工减仓了
            elif real_qty < self.position_qty:
                logger.info(f"✨ [哨兵报告] 发现仓位缩减: 从 {self.position_qty} 变为 {real_qty}！利润已落袋！")
                
                dingtalk.send_markdown_message(
                    title="阶段止盈落地",
                    text=f"### 💰 阶段利润落袋\n- **侦测结果**：币安服务器已自动撮合限价单 / 或检测到人工减仓。\n- **仓位变化**：剩余 `{real_qty}` ETH。\n- **状态**：系统继续自动看守剩余残单。"
                )
                with self._lock:
                    self.position_qty = real_qty
                
                # 更新状态文件
                state_manager.save_state({
                    "side": self.position_side,
                    "remaining_qty": self.position_qty,
                    "is_monitoring": True
                })

tp_monitor = TPMonitor()
