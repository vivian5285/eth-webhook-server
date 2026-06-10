# position_manager.py
import json
import os
from datetime import datetime

class PositionManager:
    def __init__(self, file_path="positions.json"):
        self.file_path = file_path
        self.positions = self._load()

    def _load(self):
        if os.path.exists(self.file_path):
            with open(self.file_path, "r", encoding="utf-8") as f:
                return json.load(f)
        return {}

    def _save(self):
        with open(self.file_path, "w", encoding="utf-8") as f:
            json.dump(self.positions, f, indent=2, ensure_ascii=False)

    def save_position(self, symbol: str, entry_price: float, atr: float, tp_prices: dict, direction: str):
        self.positions[symbol] = {
            "entry_price": entry_price,
            "atr": atr,
            "tp_prices": tp_prices,
            "direction": direction,
            "open_time": datetime.now().isoformat()
        }
        self._save()

    def get_position(self, symbol: str):
        return self.positions.get(symbol)

    def clear_position(self, symbol: str):
        if symbol in self.positions:
            del self.positions[symbol]
            self._save()

    def get_all_active_positions(self):
        return self.positions.copy()
