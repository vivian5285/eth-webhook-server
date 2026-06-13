# position_manager.py（推荐替换版 - reconcile 已优化）
import logging
from datetime import datetime
from binance_client import get_binance_client

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')

binance_client = get_binance_client()


class PositionManager:
    def __init__(self):
        self.position = None

    def update_position(self, side, symbol, qty, avg_price, 
                        tp1=None, tp2=None, tp3=None, stop_loss=None):
        self.position = {
            "side": side,
            "symbol": symbol,
            "qty": round(qty, 3),
            "avg_price": round(avg_price, 2),
            "tp1": tp1,
            "tp2": tp2,
            "tp3": tp3,
            "stop_loss": stop_loss,
            "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }

    def get_position(self):
        return self.position

    def clear_position(self):
        self.position = None

    def reconcile(self, real_position):
        if not real_position:
            if self.position:
                self.clear_position()
            return

        real_qty = abs(float(real_position.get('positionAmt', 0)))
        real_side = "LONG" if float(real_position.get('positionAmt', 0)) > 0 else "SHORT"
        real_entry = float(real_position.get('entryPrice', 0))
        symbol = real_position.get('symbol', 'ETHUSDT')

        if not self.position:
            self._aggressive_recalculate(real_side, symbol, real_qty, real_entry)
            return

        memory_qty = self.position.get("qty", 0)
        memory_side = self.position.get("side")

        qty_diff_ratio = abs(real_qty - memory_qty) / memory_qty if memory_qty > 0 else 1.0
        is_significant = qty_diff_ratio > 0.15 or real_side != memory_side

        if not is_significant:
            self.position["qty"] = round(real_qty, 3)
            return

        # 关键区分：减仓 vs 加仓/恢复
        if real_qty < memory_qty and real_side == memory_side:
            # 减仓：只更新数量，保留 TP 和 stop_loss
            self.position["qty"] = round(real_qty, 3)
            logging.info("[Reconcile] 检测到减仓，仅更新数量，保留原有 TP 和 stop_loss")
        else:
            # 加仓或恢复：激进重算
            self._aggressive_recalculate(real_side, symbol, real_qty, real_entry)

    def _aggressive_recalculate(self, side, symbol, qty, entry_price):
        atr = binance_client._get_atr(symbol) or (entry_price * 0.035)

        if side == "LONG":
            tp1 = round(entry_price + atr * 1.0, 2)
            tp2 = round(entry_price + atr * 2.0, 2)
            tp3 = round(entry_price + atr * 3.0, 2)
        else:
            tp1 = round(entry_price - atr * 1.0, 2)
            tp2 = round(entry_price - atr * 2.0, 2)
            tp3 = round(entry_price - atr * 3.0, 2)

        self.update_position(
            side=side, symbol=symbol, qty=qty, avg_price=entry_price,
            tp1=tp1, tp2=tp2, tp3=tp3, stop_loss=None
        )
        logging.warning("[Reconcile] 检测到加仓或恢复，已激进重算 TP")


position_manager = PositionManager()
