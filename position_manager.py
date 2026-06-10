# position_manager.py（强化版 - 支持 TP 存储和状态管理）
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
                    return json.load(f)
            except:
                return {}
        return {}

    def _save_position(self):
        with open(POSITION_FILE, "w", encoding="utf-8") as f:
            json.dump(self.position, f, indent=2, ensure_ascii=False)

    def save_position(self, symbol: str, entry_price: float, atr: float, tp_prices: dict, side: str):
        """开仓后保存持仓 + TP 价格"""
        self.position = {
            "symbol": symbol,
            "side": side,
            "entry_price": entry_price,
            "atr": atr,
            "tp_prices": tp_prices,           # ← 关键：保存实际 TP 价格
            "open_time": datetime.now().isoformat(),
            "status": "open"
        }
        self._save_position()
        logging.info(f"[持仓保存] {symbol} {side} @ {entry_price} | TP: {tp_prices}")

    def get_position(self):
        return self.position if self.position.get("status") == "open" else None

    def clear_position(self, symbol: str = None):
        self.position = {"status": "closed"}
        self._save_position()
        logging.info("[持仓清空]")

    def update_tp_hit(self, level: str):
        """记录哪一档 TP 被触发（后续监控线程用）"""
        if "tp_hit" not in self.position:
            self.position["tp_hit"] = []
        if level not in self.position["tp_hit"]:
            self.position["tp_hit"].append(level)
            self._save_position()
