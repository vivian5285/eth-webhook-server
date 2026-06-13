# position_manager.py（最终完整版）
import json
import os
import logging
from datetime import datetime

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')

POSITION_FILE = "current_position.json"


class PositionManager:
    def __init__(self):
        self.position = self._load_position()

    def _load_position(self):
        if os.path.exists(POSITION_FILE):
            try:
                with open(POSITION_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    logging.info("[PositionManager] 从文件加载持仓状态")
                    return data
            except Exception as e:
                logging.error(f"[PositionManager] 加载持仓文件失败: {e}")
        return None

    def _save_position(self):
        try:
            with open(POSITION_FILE, "w", encoding="utf-8") as f:
                json.dump(self.position, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logging.error(f"[PositionManager] 保存持仓文件失败: {e}")

    def update_position(self, side, symbol, qty, avg_price, tp1=None, tp2=None, tp3=None):
        self.position = {
            "side": side,
            "symbol": symbol,
            "qty": qty,
            "avg_price": avg_price,
            "tp1": tp1,
            "tp2": tp2,
            "tp3": tp3,
            "update_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
        self._save_position()
        logging.info(f"[PositionManager] 持仓已更新: {side} {qty} @ {avg_price}")

    def get_position(self):
        return self.position

    def clear_position(self):
        self.position = None
        if os.path.exists(POSITION_FILE):
            try:
                os.remove(POSITION_FILE)
            except:
                pass
        logging.info("[PositionManager] 持仓已清空")

    def reconcile(self, real_position):
        """
        核对实盘持仓与内存持仓是否一致
        返回 True 表示有变化（需要更新）
        """
        if not real_position:
            if self.position:
                logging.info("[PositionManager] 实盘无持仓，内存有持仓 → 清空内存")
                self.clear_position()
                return True
            return False

        real_qty = float(real_position.get("positionAmt", 0))
        real_side = "LONG" if real_qty > 0 else "SHORT"
        real_avg_price = float(real_position.get("entryPrice", 0))

        if not self.position:
            logging.info("[PositionManager] 内存无持仓，实盘有持仓 → 更新内存")
            self.update_position(
                side=real_side,
                symbol=real_position.get("symbol"),
                qty=abs(real_qty),
                avg_price=real_avg_price
            )
            return True

        # 检测数量或方向是否有明显变化（>10%）
        memory_qty = self.position.get("qty", 0)
        memory_side = self.position.get("side")

        qty_change = abs(real_qty - memory_qty) / memory_qty if memory_qty > 0 else 1

        if qty_change > 0.10 or real_side != memory_side:
            logging.info(f"[PositionManager] 检测到持仓明显变化（{qty_change*100:.1f}%），更新内存")
            self.update_position(
                side=real_side,
                symbol=real_position.get("symbol"),
                qty=abs(real_qty),
                avg_price=real_avg_price,
                tp1=self.position.get("tp1"),
                tp2=self.position.get("tp2"),
                tp3=self.position.get("tp3")
            )
            return True

        return False


# 全局单例
position_manager = PositionManager()
