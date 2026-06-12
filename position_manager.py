# position_manager.py - 最终稳定版（支持 initial_qty）

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
            "side": None,
            "entry_price": 0.0,
            "qty": 0.0,
            "initial_qty": 0.0,
            "tp1": 0.0, "tp2": 0.0, "tp3": 0.0,
            "last_update": None
        }
        self._load_state()

    def _load_state(self):
        if os.path.exists(STATE_FILE):
            try:
                with open(STATE_FILE, "r", encoding="utf-8") as f:
                    self.state.update(json.load(f))
            except Exception as e:
                logging.error(f"[PositionManager] 加载失败: {e}")

    def _save_state(self):
        try:
            with open(STATE_FILE, "w", encoding="utf-8") as f:
                json.dump(self.state, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logging.error(f"[PositionManager] 保存失败: {e}")

    def update_position(self, side, entry_price, qty, tp1, tp2, tp3):
        with lock:
            is_new = not self.state.get("has_position", False)
            self.state.update({
                "has_position": True, "side": side, "entry_price": entry_price,
                "qty": qty, "tp1": tp1, "tp2": tp2, "tp3": tp3,
                "last_update": datetime.now().isoformat()
            })
            if is_new or self.state.get("initial_qty", 0) == 0:
                self.state["initial_qty"] = qty
            self._save_state()

    def clear_position(self):
        with lock:
            self.state.update({
                "has_position": False, "side": None, "entry_price": 0,
                "qty": 0, "initial_qty": 0, "tp1": 0, "tp2": 0, "tp3": 0,
                "last_update": datetime.now().isoformat()
            })
            self._save_state()

    def get_current_state(self):
        with lock:
            return self.state.copy()

    def set_tp_levels(self, tp1, tp2, tp3):
        with lock:
            self.state.update({"tp1": tp1, "tp2": tp2, "tp3": tp3, "last_update": datetime.now().isoformat()})
            self._save_state()

    def sync_with_exchange(self, real_position):
        with lock:
            if not real_position or real_position.get("positionAmt", 0) == 0:
                if self.state.get("has_position"):
                    self.clear_position()
                return
            self.state.update({
                "side": real_position["side"],
                "entry_price": real_position["avg_price"],
                "qty": abs(real_position["positionAmt"]),
                "last_update": datetime.now().isoformat()
            })
            self._save_state()


position_manager = PositionManager()
