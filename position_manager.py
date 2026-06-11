# position_manager.py（完整最终版）
import json
import os
import logging
import threading
from datetime import datetime

class PositionManager:
    def __init__(self, file_path: str = "current_position.json"):
        self.file_path = file_path
        self.lock = threading.Lock()
        self._ensure_file_exists()

    def _ensure_file_exists(self):
        if not os.path.exists(self.file_path):
            self._save(self._default_state())

    def _load(self):
        try:
            with open(self.file_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logging.error(f"[PositionManager] 加载状态失败: {e}")
            return self._default_state()

    def _save(self, data: dict):
        try:
            data["last_update"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            with open(self.file_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logging.error(f"[PositionManager] 保存状态失败: {e}")

    def _default_state(self):
        return {
            "side": "NONE",
            "symbol": "ETHUSDT",
            "qty": 0.0,
            "avg_price": 0.0,
            "tp_levels": {"tp1": 0.0, "tp2": 0.0, "tp3": 0.0},
            "tp_hit": {"tp1": False, "tp2": False, "tp3": False},
            "last_update": None
        }

    def get_current_position(self):
        """获取当前持仓（如果没有持仓返回 None）"""
        with self.lock:
            data = self._load()
            if data.get("side") == "NONE" or data.get("qty", 0) == 0:
                return None
            return data

    def update_position(self, side: str, symbol: str, qty: float, avg_price: float,
                        tp1: float = 0.0, tp2: float = 0.0, tp3: float = 0.0):
        """更新持仓信息（开仓或 WS 更新时调用）"""
        with self.lock:
            data = self._load()
            data.update({
                "side": side,
                "symbol": symbol,
                "qty": qty,
                "avg_price": avg_price,
                "tp_levels": {"tp1": tp1, "tp2": tp2, "tp3": tp3},
                "tp_hit": {"tp1": False, "tp2": False, "tp3": False}
            })
            self._save(data)
            logging.info(f"[PositionManager] 持仓已更新: {side} {qty} @ {avg_price}")

    def mark_tp_hit(self, level: str):
        """标记某个 TP 级别已触发（供 tp_monitor 调用）"""
        with self.lock:
            data = self._load()
            if level in ["tp1", "tp2", "tp3"]:
                data["tp_hit"][level] = True
                self._save(data)
                logging.info(f"[PositionManager] {level.upper()} 已标记为已触发")

    def clear_position(self):
        """清空持仓状态（全平或重置时调用）"""
        with self.lock:
            data = self._default_state()
            self._save(data)
            logging.info("[PositionManager] 持仓已清空")

    def update_tp_levels(self, tp1: float = None, tp2: float = None, tp3: float = None):
        """更新 TP 价格（可选，用于动态调整）"""
        with self.lock:
            data = self._load()
            if tp1 is not None:
                data["tp_levels"]["tp1"] = tp1
            if tp2 is not None:
                data["tp_levels"]["tp2"] = tp2
            if tp3 is not None:
                data["tp_levels"]["tp3"] = tp3
            self._save(data)


# 全局实例
position_manager = PositionManager()
