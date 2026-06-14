#!/usr/bin/env python3
# order_executor.py（执行层 - 完整版：混合模式TP + TP3限价单）

import logging
import time
from binance_client import binance_client
from position_manager import position_manager
from dingtalk import send_dingtalk_message

logger = logging.getLogger(__name__)


class OrderExecutor:
    def __init__(self):
        # ==================== 可配置参数（建议后续移到 config.py） ====================
        self.tp1_ratio = 0.30          # TP1 平仓比例
        self.tp2_ratio = 0.30          # TP2 平仓比例
        self.tp3_ratio = 0.40          # TP3 留仓比例（挂限价单）
        self.atr_sl_multiplier = 1.0   # 止损 ATR 倍数
        self.atr_tp1_multiplier = 1.3  # TP1 ATR 倍数
        self.atr_tp2_multiplier = 2.5  # TP2 ATR 倍数
        self.atr_tp3_multiplier = 3.8  # TP3 ATR 倍数
        self.risk_percent = 0.90       # 单笔风险比例（%）

    # ==================== 主入口：开新仓 ====================
    def open_position(self, side: str, signal_data: dict):
        logger.info(f"[OrderExecutor] 开始处理开新 {side} 仓信号")

        try:
            # 1. 撤销旧 TP3 限价单
            self._cancel_existing_tp3()

            # 2. 如果有旧仓位，先全平
            self._close_current_position_if_any()

            # 3. 获取 ATR（假设 signal_data 里带了 ATR）
            atr = float(signal_data.get("atr", 0))
            if atr <= 0:
                logger.warning("[OrderExecutor] ATR 无效，跳过开仓")
                return

            # 4. 计算仓位数量
            qty = self._calculate_position_size(side, atr)
            if qty <= 0:
                logger.warning("[OrderExecutor] 计算出的仓位数量无效")
                return

            # 5. 下市价单开仓
            entry_price = self._place_market_order(side, qty)
            if not entry_price:
                return

            # 6. 计算 TP/SL 价格
            tp1_price, tp2_price, tp3_price, sl_price = self._calculate_tp_sl_prices(
                side, entry_price, atr
            )

            # 7. 设置 TP1/TP2（市价分批） + TP3（限价单）
            self._setup_take_profits(side, qty, tp1_price, tp2_price, tp3_price)

            # 8. 更新内存状态
            position_manager.set_position({
                "side": side,
                "qty": qty,
                "entry_price": entry_price,
                "sl_price": sl_price,
                "tp1_price": tp1_price,
                "tp2_price": tp2_price,
                "tp3_price": tp3_price,
            })

            # 9. 发送通知
            position_supervisor = __import__('position_supervisor').position_supervisor
            position_supervisor.notify_open_success(
                side, qty, entry_price, tp1_price, tp2_price, tp3_price
            )

            logger.info(f"[OrderExecutor] {side} 开仓完成")

        except Exception as e:
            logger.error(f"[OrderExecutor] 开新仓失败: {e}")
            send_dingtalk_message(f"【开新仓失败】{side}\n错误: {str(e)}")

    # ==================== 内部方法 ====================
    def _cancel_existing_tp3(self):
        if position_manager.has_tp3_limit_order():
            try:
                # TODO: 这里接入你原来的撤销 TP3 限价单逻辑
                position_manager.clear_tp3_limit_order()
                logger.info("[OrderExecutor] 已撤销旧 TP3 限价单")
            except Exception as e:
                logger.error(f"[OrderExecutor] 撤销 TP3 失败: {e}")

    def _close_current_position_if_any(self):
        current = position_manager.get_position()
        if current and current.get("qty", 0) > 0:
            try:
                binance_client.close_position()
                position_manager.clear_position()
                time.sleep(0.6)
                logger.info("[OrderExecutor] 已全平旧仓位")
            except Exception as e:
                logger.error(f"[OrderExecutor] 全平旧仓位失败: {e}")

    def _calculate_position_size(self, side: str, atr: float) -> float:
        """计算仓位数量（简化版，实际可接入更复杂的风控）"""
        try:
            equity = float(binance_client.get_account_balance().get("USDT", 0))
            risk_amount = equity * (self.risk_percent / 100)
            # 简化计算：风险金额 / ATR
            qty = risk_amount / atr if atr > 0 else 0
            return round(qty, 3)
        except Exception as e:
            logger.error(f"[OrderExecutor] 计算仓位失败: {e}")
            return 0

    def _place_market_order(self, side: str, qty: float) -> float:
        """市价开仓，返回成交均价"""
        try:
            order = binance_client.place_market_order(side, qty)
            # TODO: 从 order 里提取实际成交均价
            avg_price = float(order.get("avgPrice", 0)) if order else 0
            logger.info(f"[OrderExecutor] 市价单成交，均价: {avg_price}")
            return avg_price
        except Exception as e:
            logger.error(f"[OrderExecutor] 市价单下单失败: {e}")
            return 0

    def _calculate_tp_sl_prices(self, side: str, entry_price: float, atr: float):
        """计算 TP1/TP2/TP3/SL 价格"""
        if side == "LONG":
            tp1 = entry_price + atr * self.atr_tp1_multiplier
            tp2 = entry_price + atr * self.atr_tp2_multiplier
            tp3 = entry_price + atr * self.atr_tp3_multiplier
            sl = entry_price - atr * self.atr_sl_multiplier
        else:  # SHORT
            tp1 = entry_price - atr * self.atr_tp1_multiplier
            tp2 = entry_price - atr * self.atr_tp2_multiplier
            tp3 = entry_price - atr * self.atr_tp3_multiplier
            sl = entry_price + atr * self.atr_sl_multiplier

        return round(tp1, 2), round(tp2, 2), round(tp3, 2), round(sl, 2)

    def _setup_take_profits(self, side: str, total_qty: float, tp1_price: float, tp2_price: float, tp3_price: float):
        """设置分批止盈"""
        try:
            tp1_qty = round(total_qty * self.tp1_ratio, 3)
            tp2_qty = round(total_qty * self.tp2_ratio, 3)
            tp3_qty = round(total_qty * self.tp3_ratio, 3)

            # TP1 市价平仓（示例，实际应在 TPMonitor 里触发）
            if tp1_qty > 0:
                binance_client.place_market_order("SELL" if side == "LONG" else "BUY", tp1_qty)
                logger.info(f"[OrderExecutor] TP1 市价平仓 {tp1_qty}")

            # TP2 市价平仓
            if tp2_qty > 0:
                binance_client.place_market_order("SELL" if side == "LONG" else "BUY", tp2_qty)
                logger.info(f"[OrderExecutor] TP2 市价平仓 {tp2_qty}")

            # TP3 挂限价单（剩余仓位）
            if tp3_qty > 0:
                binance_client.place_limit_order(
                    side="SELL" if side == "LONG" else "BUY",
                    qty=tp3_qty,
                    price=tp3_price
                )
                position_manager.set_tp3_limit_order(True)
                logger.info(f"[OrderExecutor] TP3 限价单已挂出 @ {tp3_price}")

        except Exception as e:
            logger.error(f"[OrderExecutor] 设置止盈失败: {e}")

    def close_position(self, reason: str = "手动平仓"):
        """全平"""
        try:
            self._cancel_existing_tp3()
            binance_client.close_position()
            position_manager.clear_position()
            send_dingtalk_message(f"【全平】{reason}")
            logger.info(f"[OrderExecutor] 全平完成，原因: {reason}")
        except Exception as e:
            logger.error(f"[OrderExecutor] 全平失败: {e}")

    def move_to_breakeven(self):
        """移动止损到保本（在 TP1 命中后调用）"""
        # TODO: 实现移动止损逻辑
        logger.info("[OrderExecutor] 移动止损到保本（待实现）")


# 全局单例
order_executor = OrderExecutor()
