# position_manager.py（完整更新加强版）
import json
import os
import logging
from datetime import datetime

POSITION_FILE = "current_position.json"

class PositionManager:
    def __init__(self):
        self.position = self._load_position()

    def _load_position(self):
        if os.path.exists(POSITION_FILE):
            try:
                with open(POSITION_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if data.get("status") == "open":
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

    def update_position(self, entry_price: float, side: str, qty: float, tp_prices: dict):
        """更新/新建持仓"""
        self.position = {
            "status": "open",
            "side": side,                    # long / short
            "entry_price": entry_price,
            "qty": qty,
            "tp_prices": tp_prices,          # {"tp1": xxx, "tp2": xxx, "tp3": xxx}
            "tp_hit": [],                    # 已触发的止盈等级
            "update_time": datetime.now().isoformat()
        }
        self._save_position()
        logging.info(f"[PositionManager] 持仓已更新 | {side} @ {entry_price}")

    def mark_tp_hit(self, level: str):
        """标记某个TP已触发"""
        if not self.position:
            return
        if level not in self.position.get("tp_hit", []):
            self.position["tp_hit"].append(level)
            self._save_position()
            logging.info(f"[PositionManager] {level} 已标记命中")

    def get_position(self):
        """获取当前持仓"""
        return self.position

    def clear_position(self):
        """清空持仓（全平或手动平完后调用）"""
        self.position = None
        if os.path.exists(POSITION_FILE):
            try:
                os.remove(POSITION_FILE)
            except:
                pass
        logging.info("[PositionManager] 持仓已清空")

    def has_open_position(self) -> bool:
        return self.position is not None and self.position.get("status") == "open"
