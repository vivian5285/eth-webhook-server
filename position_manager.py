# position_manager.py（最终版 - 支持保存 entry_atr）
import json
import os
import logging
from datetime import datetime

POSITION_FILE = "current_position.json"

class PositionManager:
    def __init__(self):
        self.position = self._load_position()

    def _load_position(self):
        """加载当前持仓信息"""
        if os.path.exists(POSITION_FILE):
            try:
                with open(POSITION_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                logging.error(f"[持仓文件加载失败] {e}")
        return {"status": "closed"}

    def _save_position(self):
        """保存持仓信息到本地文件"""
        try:
            with open(POSITION_FILE, "w", encoding="utf-8") as f:
                json.dump(self.position, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logging.error(f"[持仓文件保存失败] {e}")

    def save_position(self, symbol: str, entry_price: float, atr: float, tp_prices: dict, side: str):
        """
        保存新开仓信息
        :param atr: 开仓时的 ATR（用于 VPS 动态追踪止盈）
        """
        self.position = {
            "symbol": symbol,
            "side": side.lower(),
            "entry_price": round(entry_price, 2),
            "entry_atr": round(atr, 2),                    # 新增：保存开仓 ATR，用于动态追踪
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
        logging.info(f"[持仓保存成功] {symbol} {side} | 入场价: {entry_price} | ATR: {atr}")

    def get_position(self):
        """获取当前持仓信息"""
        if self.position.get("status") == "open":
            return self.position
        return None

    def clear_position(self):
        """清空持仓记录"""
        self.position = {"status": "closed"}
        self._save_position()
        logging.info("[持仓已清空]")

    def mark_tp_hit(self, level: str):
        """标记某个 TP 已触发"""
        if "tp_hit" not in self.position:
            self.position["tp_hit"] = []
        if level not in self.position["tp_hit"]:
            self.position["tp_hit"].append(level)
            self._save_position()
            logging.info(f"[TP已触发标记] {level}")
