# position_manager.py（最终完整加强版）
import json
import os
import logging
from datetime import datetime

class PositionManager:
    def __init__(self, file_path: str = "current_position.json"):
        self.file_path = file_path
        self.position = self._load_position()

    def _load_position(self):
        """从文件加载持仓状态"""
        if os.path.exists(self.file_path):
            try:
                with open(self.file_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    logging.info("[PositionManager] 已加载持仓状态")
                    return data
            except Exception as e:
                logging.error(f"[PositionManager] 加载持仓失败: {e}")
        return self._get_empty_position()

    def _get_empty_position(self):
        """返回空持仓模板"""
        return {
            "side": "NONE",
            "symbol": "ETHUSDT",
            "qty": 0,
            "avg_price": 0.0,
            "tp_levels": {
                "tp1": 0,
                "tp2": 0,
                "tp3": 0
            },
            "tp_hit": {
                "tp1": False,
                "tp2": False,
                "tp3": False
            },
            "last_update": None
        }

    def _save_position(self):
        """保存持仓状态到文件"""
        try:
            with open(self.file_path, "w", encoding="utf-8") as f:
                json.dump(self.position, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logging.error(f"[PositionManager] 保存持仓失败: {e}")

    def update_position(self, side: str, symbol: str, qty: float, avg_price: float,
                        tp1: float = 0, tp2: float = 0, tp3: float = 0):
        """
        更新持仓信息（开仓成功后调用）
        """
        self.position = {
            "side": side.upper(),
            "symbol": symbol,
            "qty": qty,
            "avg_price": avg_price,
            "tp_levels": {
                "tp1": tp1,
                "tp2": tp2,
                "tp3": tp3
            },
            "tp_hit": {
                "tp1": False,
                "tp2": False,
                "tp3": False
            },
            "last_update": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
        self._save_position()
        logging.info(f"[PositionManager] 持仓已更新: {side} {qty} @ {avg_price}")

    def mark_tp_hit(self, level: str):
        """标记某个TP已触发"""
        if level in self.position.get("tp_hit", {}):
            self.position["tp_hit"][level] = True
            self._save_position()
            logging.info(f"[PositionManager] {level.upper()} 已标记为已触发")

    def get_current_position(self):
        """获取当前持仓（无持仓时返回 None）"""
        if self.position.get("side") == "NONE" or self.position.get("qty", 0) == 0:
            return None
        return self.position.copy()

    def clear_position(self):
        """清空持仓（全平时调用）"""
        self.position = self._get_empty_position()
        self._save_position()
        logging.info("[PositionManager] 持仓已清空")

    def get_tp_levels(self):
        """获取当前TP价格"""
        return self.position.get("tp_levels", {"tp1": 0, "tp2": 0, "tp3": 0})

    def is_tp_hit(self, level: str) -> bool:
        """检查某个TP是否已触发"""
        return self.position.get("tp_hit", {}).get(level, False)
