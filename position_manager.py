# position_manager.py - 持仓与止盈状态持久化管理

import json
import os
import logging
import threading
from datetime import datetime

STATE_FILE = "current_position.json"
lock = threading.Lock()

class PositionManager:
    def __init__(self):
        self.state = {
            "has_position": False,
            "side": None,           # "long" 或 "short"
            "entry_price": 0.0,
            "qty": 0.0,
            "tp1": 0.0,
            "tp2": 0.0,
            "tp3": 0.0,
            "last_update": None
        }
        self._load_state()

    def _load_state(self):
        if os.path.exists(STATE_FILE):
            try:
                with open(STATE_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.state.update(data)
                logging.info("[PositionManager] 已从文件加载持仓状态")
            except Exception as e:
                logging.error(f"[PositionManager] 加载状态失败: {e}")

    def _save_state(self):
        try:
            with open(STATE_FILE, "w", encoding="utf-8") as f:
                json.dump(self.state, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logging.error(f"[PositionManager] 保存状态失败: {e}")

    def update_position(self, side: str, entry_price: float, qty: float, 
                        tp1: float, tp2: float, tp3: float):
        with lock:
            self.state.update({
                "has_position": True,
                "side": side,
                "entry_price": entry_price,
                "qty": qty,
                "tp1": tp1,
                "tp2": tp2,
                "tp3": tp3,
                "last_update": datetime.now().isoformat()
            })
            self._save_state()
            logging.info(f"[PositionManager] 持仓已更新: {side} @ {entry_price}")

    def clear_position(self):
        with lock:
            self.state.update({
                "has_position": False,
                "side": None,
                "entry_price": 0.0,
                "qty": 0.0,
                "tp1": 0.0,
                "tp2": 0.0,
                "tp3": 0.0,
                "last_update": datetime.now().isoformat()
            })
            self._save_state()
            logging.info("[PositionManager] 持仓已清空")

    def get_current_state(self):
        with lock:
            return self.state.copy()

    def set_tp_levels(self, tp1: float, tp2: float, tp3: float):
        with lock:
            self.state["tp1"] = tp1
            self.state["tp2"] = tp2
            self.state["tp3"] = tp3
            self.state["last_update"] = datetime.now().isoformat()
            self._save_state()
            logging.info(f"[PositionManager] 止盈目标已更新: TP1={tp1}, TP2={tp2}, TP3={tp3}")


# 全局单例
position_manager = PositionManager()
