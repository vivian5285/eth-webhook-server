#!/usr/bin/env python3
# position_supervisor.py（最终版 - 混合模式）

import time
from typing import Optional, Dict, Any
from threading import Lock

from binance_client import binance_client
from position_manager import position_manager
from config import get_tp_multipliers
from utils.dingtalk import send_dingtalk_message


class PositionSupervisor:
    def __init__(self):
        self._lock = Lock()
        self.client = binance_client
        self.pm = position_manager

    # ==================== 开仓成功后处理 ====================
    def notify_open_success(self, signal: Dict[str, Any], filled_qty: float, avg_price: float):
        """
        开仓成功后调用
        - 更新持仓状态
        - 计算 TP1/TP2/TP3
        - 只挂 TP3 限价单（混合模式核心）
        """
        side = signal.get("side")
        symbol = signal.get("symbol", "ETHUSDT")

        # 更新持仓
        self.pm.update_position(side, filled_qty, avg_price)

        # 计算 TP 价格
        tp_multipliers = get_tp_multipliers()
        atr = signal.get("atr", 25)

        tp1_price = avg_price + (atr * tp_multipliers.get("tp1", 0.8)) * (1 if side == "LONG" else -1)
        tp2_price = avg_price + (atr * tp_multipliers.get("tp2", 1.4)) * (1 if side == "LONG" else -1)
        tp3_price = avg_price + (atr * tp_multipliers.get("tp3", 2.0)) * (1 if side == "LONG" else -1)

        self.pm.tp1_price = tp1_price
        self.pm.tp2_price = tp2_price
        self.pm.tp3_price = tp3_price

        # === 混合模式：只挂 TP3 限价单 ===
        self._place_tp3_limit_order(symbol, side, tp3_price, filled_qty)

        # 推送通知
        msg = (f"【开仓成功】{side} {symbol}\n"
               f"数量: {filled_qty} | 均价: {avg_price}\n"
               f"TP1: {tp1_price:.2f} | TP2: {tp2_price:.2f} | TP3: {tp3_price:.2f}\n"
               f"已挂 TP3 限价单")
        send_dingtalk_message(msg)

    def _place_tp3_limit_order(self, symbol: str, side: str, tp3_price: float, qty: float):
        """内部方法：挂 TP3 限价单"""
        try:
            order_side = "SELL" if side == "LONG" else "BUY"
            order = self.client.place_limit_order(
                symbol=symbol,
                side=order_side,
                price=tp3_price,
                qty=qty,
                reduce_only=True
            )
            if order and order.get("orderId"):
                self.pm.set_tp3_limit_order(
                    order_id=str(order["orderId"]),
                    price=tp3_price,
                    qty=qty
                )
                print(f"[Supervisor] TP3 限价单已挂出，OrderID: {order['orderId']}")
        except Exception as e:
            print(f"[Supervisor] 挂 TP3 限价单失败: {e}")
            send_dingtalk_message(f"【警告】TP3 限价单挂单失败: {e}")

    # ==================== TP3 限价单管理 ====================
    def cancel_tp3_limit_order(self, reason: str = "new_signal"):
        """取消 TP3 限价单"""
        tp3_info = self.pm.get_tp3_limit_order()
        if not tp3_info:
            return

        try:
            self.client.cancel_order(symbol="ETHUSDT", order_id=tp3_info["order_id"])
            print(f"[Supervisor] TP3 限价单已取消，原因: {reason}")
            self.pm.clear_tp3_limit_order()
        except Exception as e:
            print(f"[Supervisor] 取消 TP3 限价单失败: {e}")

    def on_tp3_limit_filled(self, filled_qty: float, fill_price: float):
        """TP3 限价单成交回调"""
        self.pm.clear_tp3_limit_order()
        send_dingtalk_message(f"【TP3 成交】限价单已成交 | 数量: {filled_qty} | 价格: {fill_price}")

    # ==================== 人工仓位变化处理 ====================
    def handle_manual_position_change(self, current_qty: float, current_avg_price: float):
        """处理人工加减仓"""
        if not self.pm.has_significant_position_change(current_qty):
            # 变化较小，只更新状态 + 通知
            self.pm.update_position(self.pm.side, current_qty, current_avg_price)
            send_dingtalk_message("【人工调整】检测到小幅仓位变化（<30%），已更新状态，未重挂 TP3")
            return

        # 变化较大 → 取消旧 TP3 → 更新状态 → 重新挂 TP3
        self.cancel_tp3_limit_order(reason="manual_position_change")
        self.pm.update_position(self.pm.side, current_qty, current_avg_price)

        if current_qty > 0 and self.pm.tp3_price:
            self._place_tp3_limit_order("ETHUSDT", self.pm.side, self.pm.tp3_price, current_qty)

        send_dingtalk_message(
            f"【人工调整】检测到较大仓位变化（≥30%），已重新处理 TP3 限价单\n"
            f"新数量: {current_qty} | 新均价: {current_avg_price}"
        )

    # ==================== TP 命中通知 ====================
    def notify_tp_hit(self, tp_level: int, filled_qty: float, fill_price: float):
        """TP1 / TP2 命中"""
        if tp_level == 1:
            # TP1 命中后移动止损到保本
            breakeven_price = self.pm.avg_price
            self.pm.set_stop_loss(breakeven_price)
            msg = f"【TP1 命中】已移动止损至保本价 {breakeven_price}"
        else:
            msg = f"【TP{tp_level} 命中】数量: {filled_qty} | 价格: {fill_price}"

        send_dingtalk_message(msg)

    def notify_full_close(self, reason: str = "signal"):
        """全平通知"""
        self.pm.clear_position()
        send_dingtalk_message(f"【全平】原因: {reason}")

    # ==================== 查询接口 ====================
    def get_current_position_info(self) -> Optional[Dict[str, Any]]:
        return self.pm.get_position()


# 全局单例
position_supervisor = PositionSupervisor()
