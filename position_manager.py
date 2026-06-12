# position_manager.py（最终加强版 - 2026-06-12）
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
        """从文件加载仓位状态"""
        if os.path.exists(POSITION_FILE):
            try:
                with open(POSITION_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    logging.info("[PositionManager] 从文件加载仓位状态")
                    return data
            except Exception as e:
                logging.error(f"[PositionManager] 加载文件失败: {e}")
        return None

    def _save_position(self):
        """保存仓位状态到文件"""
        try:
            with open(POSITION_FILE, "w", encoding="utf-8") as f:
                json.dump(self.position, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logging.error(f"[PositionManager] 保存文件失败: {e}")

    # ==================== 获取当前仓位 ====================
    def get_position(self):
        return self.position

    # ==================== 完整更新仓位 ====================
    def update_position(self, side, symbol, qty, avg_price, tp1=None, tp2=None, tp3=None):
        self.position = {
            "side": side,
            "symbol": symbol,
            "qty": round(qty, 3),
            "avg_price": round(avg_price, 2),
            "tp1": round(tp1, 2) if tp1 else None,
            "tp2": round(tp2, 2) if tp2 else None,
            "tp3": round(tp3, 2) if tp3 else None,
            "update_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
        self._save_position()
        logging.info(f"[PositionManager] 仓位已更新: {self.position}")

    # ==================== 只更新数量（保留 TP 和开仓价） ====================
    def update_position_qty(self, new_qty):
        if not self.position:
            logging.warning("[PositionManager] 当前无仓位，无法更新数量")
            return False

        old_qty = self.position.get("qty", 0)
        self.position["qty"] = round(new_qty, 3)
        self.position["update_time"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        self._save_position()
        logging.info(f"[PositionManager] 数量已更新: {old_qty} → {new_qty}")
        return True

    # ==================== 清空仓位 ====================
    def clear_position(self):
        self.position = None
        if os.path.exists(POSITION_FILE):
            try:
                os.remove(POSITION_FILE)
            except Exception as e:
                logging.error(f"[PositionManager] 删除文件失败: {e}")
        logging.info("[PositionManager] 仓位已清空")

    # ==================== 与实盘对账（核心方法） ====================
    def reconcile(self, real_position: dict):
        """
        与币安实盘持仓对账
        real_position 来自 binance_client.get_current_position()
        """
        if not real_position:
            if self.position:
                logging.warning("[PositionManager] 实盘无持仓，本地有记录 → 执行清空")
                self.clear_position()
            return

        # 实盘有仓位
        if not self.position:
            # 本地无记录，可能是手动加仓
            logging.warning("[PositionManager] 检测到可能的手动加仓，已同步实盘")
            self.position = {
                "side": real_position["side"],
                "symbol": real_position["symbol"],
                "qty": real_position["qty"],
                "avg_price": real_position["avg_price"],
                "tp1": None,
                "tp2": None,
                "tp3": None,
                "update_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            }
            self._save_position()
            return

        # 本地有记录，对比数量
        stored_qty = self.position.get("qty", 0)
        real_qty = real_position["qty"]

        if abs(stored_qty - real_qty) > 0.001:
            logging.warning(f"[PositionManager] 检测到人工加减仓！实盘: {real_qty}, 本地: {stored_qty}")
            self.position["qty"] = real_qty
            self.position["update_time"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self._save_position()

        # 更新最新均价（防止手动加仓导致均价变化）
        if abs(self.position.get("avg_price", 0) - real_position["avg_price"]) > 0.01:
            self.position["avg_price"] = real_position["avg_price"]
            self._save_position()

    # ==================== 辅助方法 ====================
    def has_position(self):
        return self.position is not None and self.position.get("qty", 0) > 0


# 全局单例
position_manager = PositionManager()
