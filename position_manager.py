# position_manager.py（激进版 - 人工干预后自动重算 TP）
import logging
from binance_client import binance_client

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')

class PositionManager:
    def __init__(self):
        self.position = None
        logging.info("[PositionManager] 初始化完成")

    def update_position(self, side, symbol, qty, avg_price, 
                        tp1=None, tp2=None, tp3=None, stop_loss=None):
        """更新或创建持仓信息"""
        self.position = {
            "side": side,
            "symbol": symbol,
            "qty": round(qty, 3),
            "avg_price": round(avg_price, 2),
            "tp1": tp1,
            "tp2": tp2,
            "tp3": tp3,
            "stop_loss": stop_loss,
            "updated_at": self._get_time()
        }
        logging.info(f"[持仓更新] {side} {symbol} | Qty: {qty} | Entry: {avg_price} | TP1: {tp1} | TP2: {tp2} | TP3: {tp3}")

    def get_position(self):
        return self.position

    def clear_position(self):
        self.position = None
        logging.info("[持仓清空] 已清除内存持仓")

    def _get_time(self):
        from datetime import datetime
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def reconcile(self, real_position):
        """
        核对实盘持仓与内存持仓（激进版）
        检测到明显人工干预时，尝试重新计算 TP
        """
        if not real_position:
            if self.position:
                logging.info("[Reconcile] 实盘无持仓，内存持仓已清空")
                self.clear_position()
            return

        real_qty = abs(float(real_position.get('positionAmt', 0)))
        real_side = "LONG" if float(real_position.get('positionAmt', 0)) > 0 else "SHORT"
        real_entry = float(real_position.get('entryPrice', 0))
        symbol = real_position.get('symbol', 'ETHUSDT')

        memory_pos = self.position

        # 判断是否发生明显人工干预
        is_manual_intervention = False
        if not memory_pos:
            is_manual_intervention = True
        else:
            memory_qty = memory_pos.get("qty", 0)
            memory_side = memory_pos.get("side")

            qty_diff_ratio = abs(real_qty - memory_qty) / memory_qty if memory_qty > 0 else 1.0
            if qty_diff_ratio > 0.15 or real_side != memory_side:
                is_manual_intervention = True

        if is_manual_intervention:
            logging.warning(f"[Reconcile] 检测到人工干预！实盘 Qty: {real_qty}, Side: {real_side}")

            if real_qty > 0.001:  # 仍有持仓
                # === 激进做法：重新计算 TP ===
                atr = binance_client._get_atr(symbol, interval="240", limit=14) or (real_entry * 0.035)

                if real_side == "LONG":
                    new_tp1 = round(real_entry + atr * 1.0, 2)
                    new_tp2 = round(real_entry + atr * 2.0, 2)
                    new_tp3 = round(real_entry + atr * 3.0, 2)
                else:
                    new_tp1 = round(real_entry - atr * 1.0, 2)
                    new_tp2 = round(real_entry - atr * 2.0, 2)
                    new_tp3 = round(real_entry - atr * 3.0, 2)

                self.update_position(
                    side=real_side,
                    symbol=symbol,
                    qty=real_qty,
                    avg_price=real_entry,
                    tp1=new_tp1,
                    tp2=new_tp2,
                    tp3=new_tp3,
                    stop_loss=None
                )
                logging.info(f"[人工干预后重算TP] TP1={new_tp1} | TP2={new_tp2} | TP3={new_tp3}")
            else:
                self.clear_position()
        else:
            # 正常同步（数量小幅变化，保留原有 TP）
            self.position["qty"] = round(real_qty, 3)
            self.position["avg_price"] = round(real_entry, 2)
            logging.info(f"[Reconcile] 正常同步持仓，数量已更新为 {real_qty}")


# 全局实例
position_manager = PositionManager()
