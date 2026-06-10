# position_manager.py（最终适配版）
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
                    return data if isinstance(data, dict) else {"status": "closed"}
            except Exception as e:
                logging.error(f"[持仓文件加载失败] {e}")
        return {"status": "closed"}

    def _save_position(self):
        try:
            with open(POSITION_FILE, "w", encoding="utf-8") as f:
                json.dump(self.position, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logging.error(f"[持仓文件保存失败] {e}")

    def save_position(self, symbol: str, entry_price: float, atr: float, tp_prices: dict, side: str):
        """
        保存开仓信息（必须传入真实 TP 价格）
        """
        self.position = {
            "symbol": symbol,
            "side": side.lower(),
            "entry_price": round(entry_price, 2),
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
        logging.info(f"[持仓保存成功] {symbol} {side} @ {entry_price} | TP: {self.position['tp_prices']}")

    def get_position(self):
        """获取当前持仓（只返回 open 状态的）"""
        if self.position.get("status") == "open":
            return self.position
        return None

    def clear_position(self):
        """清空持仓"""
        self.position = {"status": "closed"}
        self._save_position()
        logging.info("[持仓已清空]")

    def mark_tp_hit(self, level: str):
        """记录 TP 触发状态"""
        if "tp_hit" not in self.position:
            self.position["tp_hit"] = []
        if level not in self.position["tp_hit"]:
            self.position["tp_hit"].append(level)
            self._save_position()
            logging.info(f"[TP状态更新] {level} 已触发")
