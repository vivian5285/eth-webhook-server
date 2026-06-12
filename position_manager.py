# position_manager.py - 完整最终版（支持 initial_qty）

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
            "side": None,              # "long" 或 "short"
            "entry_price": 0.0,
            "qty": 0.0,                # 当前剩余仓位
            "initial_qty": 0.0,        # 开仓时的原始总仓位（用于分批止盈计算）
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
        """
        开仓或更新持仓时调用（会记录 initial_qty）
        """
        with lock:
            is_new_position = not self.state.get("has_position", False)

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

            # 只有新开仓时才记录 initial_qty
            if is_new_position or self.state.get("initial_qty", 0) == 0:
                self.state["initial_qty"] = qty

            self._save_state()
            logging.info(f"[PositionManager] 持仓已更新: {side} @ {entry_price}, initial_qty={self.state['initial_qty']}")

    def clear_position(self):
        with lock:
            self.state.update({
                "has_position": False,
                "side": None,
                "entry_price": 0.0,
                "qty": 0.0,
                "initial_qty": 0.0,
                "tp1": 0.0,
                "tp2": 0.0,
                "tp3": 0.0,
                "last_update": datetime.now().isoformat()
            })
            self._save_state()
            logging.info("[PositionManager] 持仓已清空（包含 initial_qty）")

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

    def sync_with_exchange(self, real_position: dict):
        """
        与交易所实时持仓同步（用于检测人工干预）
        注意：不会修改 initial_qty
        """
        with lock:
            if not real_position or real_position.get("positionAmt", 0) == 0:
                if self.state.get("has_position"):
                    logging.info("[PositionManager] 检测到手动全平，已清空状态")
                    self.clear_position()
                return

            real_side = real_position["side"]
            real_entry = real_position["avg_price"]
            real_qty = abs(real_position["positionAmt"])

            stored_qty = self.state.get("qty", 0)

            # 数量或入场价发生明显变化 → 人工干预
            if abs(real_qty - stored_qty) > 0.001 or abs(real_entry - self.state.get("entry_price", 0)) > 0.01:
                logging.info(f"[PositionManager] 检测到人工干预，同步最新状态")

                self.state.update({
                    "side": real_side,
                    "entry_price": real_entry,
                    "qty": real_qty,
                    "last_update": datetime.now().isoformat()
                })
                self._save_state()


# 全局单例
position_manager = PositionManager()
