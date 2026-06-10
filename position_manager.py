# position_manager.py（已加强 - 支持保存 ATR）
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
                pass
        return {"status": "closed"}

    def _save_position(self):
        with open(POSITION_FILE, "w", encoding="utf-8") as f:
            json.dump(self.position, f, indent=2, ensure_ascii=False)

    def save_position(self, symbol: str, entry_price: float, atr: float, tp_prices: dict, side: str):
        self.position = {
            "symbol": symbol,
            "side": side.lower(),
            "entry_price": round(entry_price, 2),
            "entry_atr": round(atr, 2),           # 新增：保存开仓时的 ATR
            "atr": atr,
            "tp_prices": {
                "tp1": round(tp_prices["tp1"], 2),
                "tp2": round(tp_prices["tp2"], 2),
                "tp3": round(tp_prices["tp3"], 2),
            },
            "open_time": datetime.now().isoformat(),
            "status": "open",
            "tp_hit": []
        }
        self._save_position()
        logging.info(f"[持仓保存成功] {symbol} {side} | ATR: {atr}")

    def get_position(self):
        return self.position if self.position.get("status") == "open" else None

    def clear_position(self):
        self.position = {"status": "closed"}
        self._save_position()
        logging.info("[持仓已清空]")

    def mark_tp_hit(self, level: str):
        if "tp_hit" not in self.position:
            self.position["tp_hit"] = []
        if level not in self.position["tp_hit"]:
            self.position["tp_hit"].append(level)
            self._save_position()
