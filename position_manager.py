# position_manager.py（最终完整版 - 支持人工干预 TP 自动更新）
import logging
import json
import os
from typing import Optional, Dict, Any

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')

POSITION_FILE = "current_position.json"


class PositionManager:
    def __init__(self):
        self.position: Dict[str, Any] = {
            "symbol": None,
            "side": None,
            "qty": 0.0,
            "avg_price": 0.0,
            "tp1": None,
            "tp2": None,
            "tp3": None,
            "last_update_time": None
        }
        self._load_from_file()

    def _load_from_file(self):
        """从文件加载持仓状态（重启后恢复）"""
        if os.path.exists(POSITION_FILE):
            try:
                with open(POSITION_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.position.update(data)
                    logging.info("[PositionManager] 从文件恢复持仓状态")
            except Exception as e:
                logging.error(f"[PositionManager] 加载文件失败: {e}")

    def _save_to_file(self):
        """保存持仓状态到文件"""
        try:
            with open(POSITION_FILE, "w", encoding="utf-8") as f:
                json.dump(self.position, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logging.error(f"[PositionManager] 保存文件失败: {e}")

    def update_position(self, side: str, symbol: str, qty: float, avg_price: float,
                        tp1: Optional[float] = None, tp2: Optional[float] = None, tp3: Optional[float] = None):
        """更新持仓信息（开仓或手动干预后调用）"""
        self.position.update({
            "symbol": symbol,
            "side": side,
            "qty": round(qty, 3),
            "avg_price": round(avg_price, 2),
            "tp1": tp1,
            "tp2": tp2,
            "tp3": tp3,
            "last_update_time": self._get_current_time()
        })
        self._save_to_file()
        logging.info(f"[PositionManager] 持仓已更新: {side} {qty} @ {avg_price}")

    def reconcile(self, real_position: Optional[Dict]) -> bool:
        """
        与币安真实持仓对账
        返回 True 表示仓位发生显著变化（>10%）
        """
        if not real_position:
            if self.position.get("qty", 0) > 0:
                logging.info("[PositionManager] 检测到仓位已清空")
                self.clear_position()
                return True
            return False

        real_qty = abs(float(real_position.get("positionAmt", 0)))
        real_side = "LONG" if float(real_position.get("positionAmt", 0)) > 0 else "SHORT"
        real_avg_price = float(real_position.get("entryPrice", 0))

        current_qty = self.position.get("qty", 0)

        # 更新当前持仓信息
        self.position.update({
            "symbol": real_position.get("symbol"),
            "side": real_side,
            "qty": round(real_qty, 3),
            "avg_price": round(real_avg_price, 2),
            "last_update_time": self._get_current_time()
        })
        self._save_to_file()

        # 判断是否发生显著变化（>10%）
        if current_qty > 0:
            change_ratio = abs(real_qty - current_qty) / current_qty
            if change_ratio > 0.10:
                logging.info(f"[PositionManager] 检测到显著变化: {current_qty} → {real_qty} ({change_ratio:.1%})")
                return True

        return False

    def get_position(self) -> Dict[str, Any]:
        """获取当前持仓信息"""
        return self.position.copy()

    def clear_position(self):
        """清空持仓（全平后调用）"""
        self.position.update({
            "symbol": None,
            "side": None,
            "qty": 0.0,
            "avg_price": 0.0,
            "tp1": None,
            "tp2": None,
            "tp3": None,
            "last_update_time": self._get_current_time()
        })
        self._save_to_file()
        logging.info("[PositionManager] 持仓已清空")

    def get_tp_levels(self):
        """获取当前 TP 价格"""
        return {
            "tp1": self.position.get("tp1"),
            "tp2": self.position.get("tp2"),
            "tp3": self.position.get("tp3")
        }

    def _get_current_time(self):
        from datetime import datetime
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# 全局单例
position_manager = PositionManager()
