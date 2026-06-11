# position_manager.py（最终完整强壮版）
import json
import os
import logging
from datetime import datetime

POSITION_FILE = "current_position.json"

class PositionManager:
    def __init__(self):
        self.position = self._load_position()

    def _load_position(self):
        """加载当前持仓信息（包含 last_signal_direction）"""
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

    # ==================== 持仓管理 ====================
    def save_position(self, symbol: str, entry_price: float, atr: float, tp_prices: dict, side: str):
        """保存新开仓信息"""
        self.position = {
            "symbol": symbol,
            "side": side.lower(),
            "entry_price": round(entry_price, 2),
            "entry_atr": round(atr, 2),
            "atr": atr,
            "tp_prices": {
                "tp1": round(tp_prices["tp1"], 2),
                "tp2": round(tp_prices["tp2"], 2),
                "tp3": round(tp_prices["tp3"], 2),
            },
            "open_time": datetime.now().isoformat(),
            "status": "open",
            "tp_hit": [],
            "last_signal_direction": self.position.get("last_signal_direction")  # 保留原有方向
        }
        self._save_position()
        logging.info(f"[持仓保存成功] {symbol} {side} | 入场价: {entry_price} | ATR: {atr}")

    def get_position(self):
        """获取当前持仓信息"""
        if self.position.get("status") == "open":
            return self.position
        return None

    def clear_position(self):
        """清空持仓记录（保留 last_signal_direction）"""
        last_dir = self.position.get("last_signal_direction")
        self.position = {"status": "closed"}
        if last_dir:
            self.position["last_signal_direction"] = last_dir
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

    # ==================== last_signal_direction 持久化 ====================
    def save_last_signal_direction(self, direction: str):
        """保存最后收到的 TV 信号方向（持久化）"""
        if self.position.get("status") != "open":
            self.position = {"status": "closed"}
        self.position["last_signal_direction"] = direction
        self._save_position()
        logging.info(f"[持久化成功] last_signal_direction 已更新为: {direction}")

    def get_last_signal_direction(self):
        """获取最后收到的 TV 信号方向"""
        return self.position.get("last_signal_direction")

    def clear_last_signal_direction(self):
        """清除 last_signal_direction（可选使用）"""
        if "last_signal_direction" in self.position:
            del self.position["last_signal_direction"]
            self._save_position()
            logging.info("[持久化] last_signal_direction 已清除")
