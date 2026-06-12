# tp_monitor.py（最终优化版 - 2026-06-12）
import logging
import time
import threading
from binance_client import BinanceClient
from position_manager import position_manager
from position_supervisor import supervisor

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')

binance_client = BinanceClient(
    api_key=..., 
    api_secret=...
)


class TPMonitor:
    def __init__(self, check_interval=6):
        self.check_interval = check_interval
        self.running = False
        self.thread = None

    def start(self):
        if self.running:
            logging.warning("[TP监控] 已经在运行中")
            return
        self.running = True
        self.thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self.thread.start()
        logging.info("[TP监控] 已启动（已加强实盘对账）")

    def stop(self):
        self.running = False
        logging.info("[TP监控] 已停止")

    def _monitor_loop(self):
        while self.running:
            try:
                self._reconcile_and_check_tp()
            except Exception as e:
                logging.error(f"[TP监控] 循环异常: {e}", exc_info=True)
            time.sleep(self.check_interval)

    def _reconcile_and_check_tp(self):
        """先对账实盘，再检查 TP（核心优化逻辑）"""
        
        # 1. 获取币安实盘最新持仓
        real_position = binance_client.get_current_position("ETHUSDT")

        # 2. 执行对账（核心）
        position_manager.reconcile(real_position)

        # 3. 获取对账后的最新仓位状态
        position = position_manager.get_position()
        if not position or position.get("qty", 0) <= 0:
            return  # 无有效仓位，跳过

        # 4. 提取必要信息
        symbol = position.get("symbol", "ETHUSDT")
        side = position.get("side")
        current_qty = position.get("qty", 0)
        tp1 = position.get("tp1")
        tp2 = position.get("tp2")
        tp3 = position.get("tp3")

        # 5. 检查 TP 价格是否完整
        if not all([tp1, tp2, tp3]):
            logging.warning("[TP监控] TP1/TP2/TP3 价格不完整，跳过本次检查")
            return

        # 6. 获取当前最新价格
        try:
            ticker = binance_client.client.futures_symbol_ticker(symbol=symbol)
            current_price = float(ticker["price"])
        except Exception as e:
            logging.error(f"[TP监控] 获取当前价格失败: {e}")
            return

        is_long = side == "LONG"

        # 7. 判断 TP 触发条件
        hit_tp1 = (is_long and current_price >= tp1) or (not is_long and current_price <= tp1)
        hit_tp2 = (is_long and current_price >= tp2) or (not is_long and current_price <= tp2)
        hit_tp3 = (is_long and current_price >= tp3) or (not is_long and current_price <= tp3)

        # 8. 执行对应操作
        if hit_tp3:
            logging.info(f"[TP监控] TP3 触发 → 全平剩余 {current_qty} 张")
            result = binance_client.close_all_positions(symbol)
            if result.get("status") == "success":
                supervisor.notify_tp_hit(level="3", closed_qty=current_qty, avg_price=current_price)
                position_manager.clear_position()

        elif hit_tp2:
            self._execute_partial_tp(0.30, "2", current_qty, current_price, symbol)

        elif hit_tp1:
            self._execute_partial_tp(0.30, "1", current_qty, current_price, symbol)

    def _execute_partial_tp(self, percent: float, level: str, current_qty: float, current_price: float, symbol: str):
        """执行部分止盈"""
        close_qty = round(current_qty * percent, 3)
        if close_qty < 0.001:
            logging.warning(f"[TP监控] TP{level} 计算平仓数量过小，跳过")
            return

        logging.info(f"[TP监控] TP{level} 触发 → 平 {close_qty} 张（{percent*100}%）")

        result = binance_client.close_partial_position(symbol, close_qty)

        if result.get("status") == "success":
            logging.info(f"[TP监控] TP{level} 平仓成功")
            supervisor.notify_tp_hit(level=level, closed_qty=close_qty, avg_price=current_price)

            # 更新剩余仓位
            new_qty = current_qty - close_qty
            if new_qty > 0.001:
                position_manager.update_position_qty(new_qty)
            else:
                position_manager.clear_position()
        else:
            logging.error(f"[TP监控] TP{level} 平仓失败: {result}")


# ==================== 全局单例 ====================
tp_monitor = TPMonitor(check_interval=6)
