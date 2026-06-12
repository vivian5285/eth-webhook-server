# position_manager.py - 增强版（支持人工干预）

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

    def clear_position(self):
        with lock:
            self.state.update({
                "has_position": False, "side": None,
                "entry_price": 0, "qty": 0,
                "tp1": 0, "tp2": 0, "tp3": 0,
                "last_update": datetime.now().isoformat()
            })
            self._save_state()
            logging.info("[PositionManager] 持仓已清空")

    def get_current_state(self):
        with lock:
            return self.state.copy()

    def set_tp_levels(self, tp1, tp2, tp3):
        with lock:
            self.state["tp1"] = tp1
            self.state["tp2"] = tp2
            self.state["tp3"] = tp3
            self.state["last_update"] = datetime.now().isoformat()
            self._save_state()

    def sync_with_exchange(self, real_position: dict):
        """
        与交易所实时持仓同步（处理人工干预）
        """
        with lock:
            if not real_position or real_position.get("positionAmt", 0) == 0:
                if self.state["has_position"]:
                    logging.info("[PositionManager] 检测到手动全平，已清空状态")
                    self.clear_position()
                return

            real_side = real_position["side"]
            real_entry = real_position["avg_price"]
            real_qty = abs(real_position["positionAmt"])

            stored_qty = self.state.get("qty", 0)
            stored_entry = self.state.get("entry_price", 0)

            # 数量或入场价发生变化 → 认为是人工干预
            if abs(real_qty - stored_qty) > 0.001 or abs(real_entry - stored_entry) > 0.01:
                logging.info(f"[PositionManager] 检测到人工干预 → 同步最新状态")

                # 如果是加仓（entry_price 变化），重新计算止盈
                if self.state["has_position"] and self.state.get("tp1", 0) > 0:
                    # 重新按新入场价计算止盈（保持原有倍数逻辑）
                    is_long = real_side == "long"
                    if is_long:
                        new_tp1 = round(real_entry * 1.006, 2)
                        new_tp2 = round(real_entry * 1.012, 2)
                        new_tp3 = round(real_entry * 1.020, 2)
                    else:
                        new_tp1 = round(real_entry * 0.994, 2)
                        new_tp2 = round(real_entry * 0.988, 2)
                        new_tp3 = round(real_entry * 0.980, 2)

                    self.state.update({
                        "entry_price": real_entry,
                        "qty": real_qty,
                        "tp1": new_tp1,
                        "tp2": new_tp2,
                        "tp3": new_tp3,
                        "last_update": datetime.now().isoformat()
                    })
                    logging.info(f"[PositionManager] 加仓后重新计算止盈目标")
                else:
                    # 普通更新
                    self.state.update({
                        "entry_price": real_entry,
                        "qty": real_qty,
                        "last_update": datetime.now().isoformat()
                    })

                self._save_state()
